"""Timing series, noise model, regression flags and change points over run records.

A series is keyed by (benchmark, lineage, machine class, metric) and holds one
point per run record, ordered by the run's start time (then run id). All
statistics are recomputed from the records on every call, so a better rule
applies to the whole history.

Only comparable runs (``comparability.ok``) enter the rules below. Other runs
stay in ``points`` with ``comparable: false`` and are otherwise ignored.

- Segments: a settings boundary is where ``benchmark.settings_hash`` changes
  between consecutive comparable runs. Baselines, flags and steps never reach
  across a boundary.
- Baseline for a run: median of the last ``BASELINE_WINDOW`` comparable runs
  before it in its segment, and sigma = ``MAD_TO_SIGMA`` x MAD. No baseline
  with fewer than ``MIN_BASELINE_POINTS`` prior runs.
- Flag when |value - median| > max(``SIGMA_FACTOR`` x sigma,
  ``MIN_DELTA_REL`` x median) and |value - median| > the absolute floor of the
  metric's unit. The 5 % rule is the smallest threshold; sigma only raises it
  for noisier series. Larger is a regression, smaller an improvement.
  ``confirmed`` is None until the segment has a next comparable run, then
  whether that run exceeds the same threshold in the same direction.
- Steps: asv's piecewise-constant fit. Levels shorter than
  ``MIN_LEVEL_POINTS`` runs are excursions, not steps: their runs are left
  out and the fit repeated. The new level starts at the first left-out run
  between two levels that is closer to it in value, so a step whose first run
  is an outlier is still attributed to that run.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from itertools import pairwise
from statistics import median

from proteus_bench._vendor.asv_step_detect import detect_steps
from proteus_bench.settings import changed_keys

SCHEMA = 'proteus-bench-analysis/1'

BASELINE_WINDOW = 10
MIN_BASELINE_POINTS = 3
MIN_LEVEL_POINTS = 3
# Scale factor from MAD to the standard deviation of a normal distribution
MAD_TO_SIGMA = 1.4826
SIGMA_FACTOR = 3.0
MIN_DELTA_REL = 0.05
# Smallest absolute change that can flag, per unit: sub-second jitter never flags
ABS_FLOOR = {'s': 1.0, 'count': 0.0}


def analyse(records: list[dict]) -> dict:
    """Build every series from run records (schema ``proteus-bench/1``).

    Returns the analysis structure described in ``docs/interface.md``. Raises
    ``ValueError`` naming the run when a record lacks a field or has one of the
    wrong type.
    """
    groups = defaultdict(list)
    for record in records:
        try:
            ident = _identity(record)
            metrics = record_metrics(record)
        except KeyError as err:
            raise ValueError(f'record {record.get("run_id")!r}: missing field {err}') from err
        except (TypeError, ValueError) as err:
            raise ValueError(f'record {record.get("run_id")!r}: malformed ({err})') from err
        for metric, value in metrics.items():
            groups[(*ident['key'], metric)].append((ident, value))
    series = [_series(key, rows) for key, rows in sorted(groups.items())]
    return {'schema': SCHEMA, 'series': series}


def record_metrics(record: dict) -> dict[str, float]:
    """Metric name -> value for one record; seconds, except ``n_iters`` (count).

    Every non-null phase is a metric. Iterations with ``init_stage`` true are
    left out of the per-iteration medians. ``loop.<component>.per_iter_median``
    is the median over the iterations in which the component ran, so a
    component that runs every few iterations (structure re-solves) reports its
    cost per call, not zero. Raises ``KeyError`` for a missing field and
    ``TypeError`` for a non-numeric value.
    """
    timings = record['timings']
    metrics = {'total': timings['wall_s']}
    metrics |= {phase: s for phase, s in timings['phases'].items() if s is not None}
    if record['outcome'].get('n_iters') is not None:
        metrics['n_iters'] = record['outcome']['n_iters']
    metrics |= _steady_iteration_metrics(timings['per_iter'])
    for row in timings['components']:
        if row['phase'] == 'init':
            name = f'init.{row["component"]}'
            metrics[name] = metrics.get(name, 0.0) + row['total_s']
        if row.get('submodule'):
            name = f'submodule.{row["submodule"]}.total'
            metrics[name] = metrics.get(name, 0.0) + row['total_s']
    bad = [
        m for m, v in metrics.items() if isinstance(v, bool) or not isinstance(v, int | float)
    ]
    if bad:
        raise TypeError(f'non-numeric metrics: {", ".join(bad)}')
    return metrics


def _steady_iteration_metrics(per_iter: list[dict]) -> dict[str, float]:
    steady = [row for row in per_iter if not row.get('init_stage')]
    if not steady:
        return {}
    per_component = defaultdict(list)
    for row in steady:
        for component, dur_s in row['components'].items():
            per_component[component].append(dur_s)
    return {'loop_per_iter_median': median(row['dur_s'] for row in steady)} | {
        f'loop.{component}.per_iter_median': median(durs)
        for component, durs in per_component.items()
    }


def _identity(record: dict) -> dict:
    """Series key parts and point fields shared by all metrics of one record."""
    bench = record['benchmark']
    started_at = record['trigger']['started_at']
    return {
        'key': (bench['name'], bench['lineage'], record['machine']['class']),
        'run_id': record['run_id'],
        'commit': record['code']['proteus'].get('sha'),
        'time': started_at,
        'sort': (datetime.fromisoformat(started_at), record['run_id']),
        'comparable': record['comparability']['ok'] is True,
        'settings_hash': bench['settings_hash'],
        'settings': bench['settings'],
    }


def _series(key: tuple, rows: list[tuple[dict, float]]) -> dict:
    rows = sorted(rows, key=lambda row: row[0]['sort'])
    unit = 'count' if key[3] == 'n_iters' else 's'
    points = [
        {name: ident[name] for name in ('run_id', 'commit', 'time', 'comparable')}
        | {'value': value}
        for ident, value in rows
    ]
    comparable = [
        (ident, p) for (ident, _), p in zip(rows, points, strict=True) if p['comparable']
    ]
    boundaries, segments = _split_at_settings_changes(comparable)
    flags, steps = [], []
    for segment in segments:
        flags += _flags(segment, ABS_FLOOR[unit])
        steps += _steps(segment)
    return {
        'key': dict(zip(('benchmark', 'lineage', 'machine_class', 'metric'), key, strict=True)),
        'unit': unit,
        'points': points,
        'baseline': baseline([p['value'] for p in segments[-1][-BASELINE_WINDOW:]]),
        'flags': flags,
        'steps': steps,
        'boundaries': boundaries,
    }


def _split_at_settings_changes(comparable: list[tuple[dict, dict]]):
    """Boundaries where the settings hash changes, and the comparable points between them."""
    boundaries, segments = [], [[]]
    for i, (ident, point) in enumerate(comparable):
        prev = comparable[i - 1][0] if i else ident
        if ident['settings_hash'] != prev['settings_hash']:
            boundaries.append(
                {
                    'run_id': ident['run_id'],
                    'reason': 'settings_changed',
                    'changed_keys': changed_keys(prev['settings'], ident['settings']),
                }
            )
            segments.append([])
        segments[-1].append(point)
    return boundaries, segments


def baseline(values: list[float]) -> dict | None:
    """Median and robust sigma (1.4826 x MAD) of prior values; None with too few."""
    if len(values) < MIN_BASELINE_POINTS:
        return None
    mid = median(values)
    mad = median(abs(v - mid) for v in values)
    return {'median': mid, 'sigma': MAD_TO_SIGMA * mad, 'n': len(values)}


def _judge(value: float, base: dict, abs_floor: float) -> tuple[int, float, float]:
    """(direction, delta, threshold): direction +1 above the threshold, -1 below, else 0."""
    delta = value - base['median']
    threshold = max(SIGMA_FACTOR * base['sigma'], MIN_DELTA_REL * abs(base['median']))
    exceeds = abs(delta) > threshold and abs(delta) > abs_floor
    return (1 if delta > 0 else -1) if exceeds else 0, delta, threshold


def _flags(segment: list[dict], abs_floor: float) -> list[dict]:
    """Flags for the comparable points of one settings segment, in order."""
    flags = []
    for i, point in enumerate(segment):
        base = baseline([p['value'] for p in segment[max(0, i - BASELINE_WINDOW) : i]])
        if base is None:
            continue
        direction, delta, threshold = _judge(point['value'], base, abs_floor)
        if direction == 0:
            continue
        confirmed = None
        if i + 1 < len(segment):
            confirmed = _judge(segment[i + 1]['value'], base, abs_floor)[0] == direction
        flags.append(
            {
                'run_id': point['run_id'],
                'kind': 'regression' if direction > 0 else 'improvement',
                'delta_rel': _relative(delta, base['median']),
                'delta_abs': delta,
                'threshold_rel': _relative(threshold, base['median']),
                'confirmed': confirmed,
            }
        )
    return flags


def _steps(segment: list[dict]) -> list[dict]:
    """Level changes found by asv's step detector in one settings segment."""
    values = [p['value'] for p in segment]
    steps = []
    for (_, end, before), (start, _, after) in pairwise(_levels(values)):
        # The new level starts at the first left-out run between the two that is
        # closer to it in value
        first = next(
            (i for i in range(end, start) if abs(values[i] - after) < abs(values[i] - before)),
            start,
        )
        steps.append(
            {
                'after_run_id': segment[first]['run_id'],
                'before': before,
                'after': after,
                'delta_rel': _relative(after - before, before),
            }
        )
    return steps


def _levels(values: list[float]) -> list[tuple[int, int, float]]:
    """(start, end, median) of asv's levels after leaving out excursion runs.

    Runs in levels shorter than ``MIN_LEVEL_POINTS`` are passed to asv as
    missing and the fit is repeated until every level is long enough or one
    level remains. Each pass leaves out at least one more run, so this ends.
    """
    fitted = list(values)
    while True:
        levels = [(start, end, level) for start, end, level, _, _ in detect_steps(fitted)]
        short = [
            (start, end)
            for start, end, _ in levels
            if sum(v is not None for v in fitted[start:end]) < MIN_LEVEL_POINTS
        ]
        if len(levels) <= 1 or not short:
            return levels
        for start, end in short:
            fitted[start:end] = [None] * (end - start)


def _relative(delta: float, reference: float) -> float | None:
    """delta / reference; None when the reference is zero (JSON has no infinity)."""
    return delta / reference if reference else None
