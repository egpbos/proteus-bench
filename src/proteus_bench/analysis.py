"""Timing series, noise model, regression flags and change points over run records.

A series is keyed by (benchmark, lineage, machine class, metric) and holds one
point per run record, ordered by the run's start time. All statistics are
recomputed from the records on every call, so a better rule applies to the
whole history.

Rules, per series and within one settings segment (a boundary is where
``benchmark.settings_hash`` changes between consecutive points; nothing below
reaches across one):

- Only points of comparable runs (``comparability.ok``) enter baselines,
  flags and step detection. Other runs stay in ``points`` with
  ``comparable: false``.
- Baseline for a point: median of the last ``BASELINE_WINDOW`` comparable
  points before it; sigma = ``MAD_TO_SIGMA`` x MAD, floored at the machine
  class's relative noise times the median. No baseline with fewer than
  ``MIN_BASELINE_POINTS`` prior points.
- Flag when |value - median| > max(``SIGMA_FACTOR`` x sigma,
  ``MIN_DELTA_REL`` x median) and |value - median| > the absolute floor of the
  metric's unit. Larger is a regression, smaller an improvement. A flag is
  confirmed when the next comparable point exceeds the same threshold in the
  same direction.
- Steps: asv's piecewise-constant fit over the comparable values, reported
  between levels that each span at least ``MIN_LEVEL_POINTS`` runs.
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
# Run-to-run scatter of 13 identical runs on Habrok vink nodes (1.5 %). Below
# MIN_DELTA_REL / SIGMA_FACTOR (1.67 %) a floor never sets the flag threshold.
DEFAULT_NOISE_FLOOR_REL = 0.015
SIGMA_FACTOR = 3.0
MIN_DELTA_REL = 0.05
# Smallest absolute change that can flag, per unit: sub-second jitter never flags
ABS_FLOOR = {'s': 1.0, 'count': 0.0}

_PHASES = ('init', 'loop', 'shutdown')


def analyse(records: list[dict], *, noise_floor_rel: dict[str, float] | None = None) -> dict:
    """Build every series from run records (schema ``proteus-bench/1``).

    ``noise_floor_rel`` maps a machine class to its relative noise floor for
    sigma; classes not listed use ``DEFAULT_NOISE_FLOOR_REL``. Returns the
    ``proteus-bench-analysis/1`` structure (see ``docs/interface.md``).
    Raises ``ValueError`` naming the run when a record lacks a required field.
    """
    floors = noise_floor_rel or {}
    groups = defaultdict(list)
    for record in records:
        try:
            ident = _identity(record)
            metrics = record_metrics(record)
        except KeyError as err:
            raise ValueError(f'record {record.get("run_id")!r}: missing field {err}') from err
        for metric, value in metrics.items():
            groups[(*ident['key'], metric)].append((ident, value))
    series = [
        _series(key, rows, floors.get(key[2], DEFAULT_NOISE_FLOOR_REL))
        for key, rows in sorted(groups.items())
    ]
    return {'schema': SCHEMA, 'series': series}


def record_metrics(record: dict) -> dict[str, float]:
    """Metric name -> value for one record; seconds, except ``n_iters`` (count).

    Iterations with ``init_stage`` true are left out of the per-iteration
    medians. ``loop.<component>.per_iter_median`` is the median over the
    iterations in which the component ran, so a component that runs every few
    iterations (structure re-solves) reports its cost per call, not zero.
    """
    timings = record['timings']
    metrics = {'total': timings['wall_s']}
    metrics |= {
        p: timings['phases'][p] for p in _PHASES if timings['phases'].get(p) is not None
    }
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


def _series(key: tuple, rows: list[tuple[dict, float]], noise_floor_rel: float) -> dict:
    rows = sorted(rows, key=lambda row: row[0]['sort'])
    unit = 'count' if key[3] == 'n_iters' else 's'
    points = [
        {name: ident[name] for name in ('run_id', 'commit', 'time', 'comparable')}
        | {'value': value}
        for ident, value in rows
    ]
    boundaries, segments = _split_at_settings_changes([ident for ident, _ in rows], points)
    flags, steps = [], []
    for segment in segments:
        comparable = [p for p in segment if p['comparable']]
        flags += _flags(comparable, noise_floor_rel, ABS_FLOOR[unit])
        steps += _steps(comparable)
    latest = [p['value'] for p in segments[-1] if p['comparable']][-BASELINE_WINDOW:]
    return {
        'key': dict(zip(('benchmark', 'lineage', 'machine_class', 'metric'), key, strict=True)),
        'unit': unit,
        'points': points,
        'baseline': baseline(latest, noise_floor_rel),
        'flags': flags,
        'steps': steps,
        'boundaries': boundaries,
    }


def _split_at_settings_changes(idents: list[dict], points: list[dict]):
    """Boundaries where the settings hash changes, and the points between them."""
    boundaries, segments = [], [[points[0]]]
    for (prev, cur), point in zip(pairwise(idents), points[1:], strict=True):
        if cur['settings_hash'] != prev['settings_hash']:
            boundaries.append(
                {
                    'run_id': cur['run_id'],
                    'reason': 'settings_changed',
                    'changed_keys': changed_keys(prev['settings'], cur['settings']),
                }
            )
            segments.append([])
        segments[-1].append(point)
    return boundaries, segments


def baseline(values: list[float], noise_floor_rel: float) -> dict | None:
    """Median and robust sigma of prior values, or None with too few of them."""
    if len(values) < MIN_BASELINE_POINTS:
        return None
    mid = median(values)
    mad = median(abs(v - mid) for v in values)
    sigma = max(MAD_TO_SIGMA * mad, noise_floor_rel * abs(mid))
    return {'median': mid, 'sigma': sigma, 'n': len(values)}


def _threshold(base: dict) -> float:
    return max(SIGMA_FACTOR * base['sigma'], MIN_DELTA_REL * abs(base['median']))


def _direction(value: float, base: dict, abs_floor: float) -> int:
    """+1 above the flag threshold, -1 below it, 0 within it."""
    delta = value - base['median']
    if abs(delta) > _threshold(base) and abs(delta) > abs_floor:
        return 1 if delta > 0 else -1
    return 0


def _flags(comparable: list[dict], noise_floor_rel: float, abs_floor: float) -> list[dict]:
    """Flags for the comparable points of one settings segment, in order."""
    flags = []
    for i, point in enumerate(comparable):
        prior = [p['value'] for p in comparable[max(0, i - BASELINE_WINDOW) : i]]
        base = baseline(prior, noise_floor_rel)
        if base is None:
            continue
        direction = _direction(point['value'], base, abs_floor)
        if direction == 0:
            continue
        nxt = comparable[i + 1] if i + 1 < len(comparable) else None
        delta = point['value'] - base['median']
        flags.append(
            {
                'run_id': point['run_id'],
                'kind': 'regression' if direction > 0 else 'improvement',
                'delta_rel': _relative(delta, base['median']),
                'delta_abs': delta,
                'threshold_rel': _relative(_threshold(base), base['median']),
                'confirmed': nxt is not None
                and _direction(nxt['value'], base, abs_floor) == direction,
            }
        )
    return flags


def _steps(comparable: list[dict]) -> list[dict]:
    """Level changes found by asv's step detector in one settings segment.

    A step is reported only between two levels of at least ``MIN_LEVEL_POINTS``
    runs each: on short or spiky stretches the detector returns one-run levels.
    """
    if len(comparable) < 2 * MIN_LEVEL_POINTS:
        return []
    levels = detect_steps([p['value'] for p in comparable])
    return [
        {
            'after_run_id': comparable[start]['run_id'],
            'before': before,
            'after': after,
            'delta_rel': _relative(after - before, before),
        }
        for (prev_start, _, before, _, _), (start, end, after, _, _) in pairwise(levels)
        if min(start - prev_start, end - start) >= MIN_LEVEL_POINTS
    ]


def _relative(delta: float, reference: float) -> float | None:
    """delta / reference; None when the reference is zero (JSON has no infinity)."""
    return delta / reference if reference else None
