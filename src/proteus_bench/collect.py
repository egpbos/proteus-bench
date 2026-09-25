"""Turn the files a proteus run leaves behind into run-record sections.

Inputs: the timing.jsonl events, ``init_coupler.toml`` and
``runtime_helpfile.csv``. Every duration here is derived from the raw spans,
through ``timing.attributed_totals`` for the phase split.
"""

from __future__ import annotations

import datetime as dt
import math
import tomllib
from collections import defaultdict
from pathlib import Path

from proteus_bench import settings
from proteus_bench.timing import PHASES, attributed_totals

FINGERPRINT_KEYS = ('T_magma', 'Phi_global', 'F_atm', 'P_surf')
ROUND = 6  # timing.jsonl resolution is one microsecond


def component_rows(events: list[dict]) -> list[dict]:
    """One row per (phase, component, submodule, backend), largest first, 'other' last.

    Rows of a phase add up to the phase total: 'other' is the unattributed rest.
    Only closed phases get rows. Spans of a phase that never closed (a crash)
    have no phase total to add up to; they still appear in ``per_iter_rows``.
    """
    totals_by_phase = attributed_totals(events)
    rows = []
    for phase in (p for p in PHASES if p in totals_by_phase):
        totals = totals_by_phase[phase]
        attributed = sorted(totals['attributed'].items(), key=lambda kv: -kv[1][0])
        rows += [_row(phase, key, total_s, n_calls) for key, (total_s, n_calls) in attributed]
        # Rounding can leave a remainder of -1e-7 s where nothing is unattributed
        rows.append(_row(phase, ('other', None, None), max(0.0, totals['other']), 0))
    return rows


def _row(phase: str, key: tuple, total_s: float, n_calls: int) -> dict:
    component, submodule, backend = key
    return {
        'phase': phase,
        'component': component,
        'submodule': submodule,
        'backend': backend,
        'total_s': round(total_s, ROUND),
        'n_calls': n_calls,
    }


def per_iter_rows(events: list[dict]) -> list[dict]:
    """Per main-loop iteration: duration, init_stage flag and attributed time per component."""
    spans = {ev['id']: ev for ev in events if ev['ev'] == 'span'}
    per_iter = defaultdict(lambda: defaultdict(float))
    for span in spans.values():
        if 'component' in span:
            it = _enclosing_iter(span, spans)
            if it is not None:
                per_iter[it['id']][span['component']] += span['dur']
    rows = []
    for it in sorted(
        (s for s in spans.values() if s['name'] == 'iter'), key=lambda s: s['iter']
    ):
        row = {'iter': it['iter'], 'dur_s': round(it['dur'], ROUND)}
        if 'init_stage' in it.get('extra', {}):
            row['init_stage'] = it['extra']['init_stage']
        row['components'] = {k: round(v, ROUND) for k, v in per_iter[it['id']].items()}
        rows.append(row)
    return rows


def _enclosing_iter(span: dict, spans: dict[int, dict]) -> dict | None:
    node = spans.get(span['parent'])
    while node is not None and node['name'] != 'iter':
        node = spans.get(node['parent'])
    return node


def phase_totals(events: list[dict]) -> dict:
    totals = attributed_totals(events)
    return {p: round(totals[p]['total'], ROUND) if p in totals else None for p in PHASES}


def startup_s(events: list[dict], started_at: dt.datetime) -> float | None:
    """Seconds from process spawn to the run_start event, or None without one."""
    start = next((ev for ev in events if ev['ev'] == 'run_start'), None)
    if start is None:
        return None
    wall = dt.datetime.fromisoformat(start['wall'])
    # run_start.wall has millisecond resolution, so a fast start can read as negative
    return round(max(0.0, (wall - started_at).total_seconds()), 3)


def backends(events: list[dict]) -> dict:
    """Backend events per submodule, plus per-call backend counts from attributed spans."""
    out = defaultdict(dict)
    for ev in events:
        if ev['ev'] == 'backend':
            out[ev['submodule']][ev['key']] = ev['value']
        elif ev['ev'] == 'span' and ev.get('submodule') and ev.get('backend'):
            calls = out[ev['submodule']].setdefault('calls', {})
            calls[ev['backend']] = calls.get(ev['backend'], 0) + 1
    return dict(out)


def outcome(events: list[dict], exit_code: int, timed_out: bool, timeout_s) -> dict:
    """Status is timeout, crashed (no run_end), failed (run_end not ok, or exit != 0) or ok."""
    end = events[-1] if events and events[-1]['ev'] == 'run_end' else {}
    n_iters = end.get('n_iters', sum(1 for ev in events if ev.get('name') == 'iter'))
    out = {'exit_code': exit_code, 'n_iters': n_iters}
    out |= {k: end[k] for k in ('termination', 'time_yr', 'error') if k in end}
    if timed_out:
        out |= {'status': 'timeout', 'error': f'killed after the {timeout_s} s timeout'}
    elif not end:
        detail = 'no run_end event in timing.jsonl' if events else 'no timing.jsonl events'
        out |= {'status': 'crashed', 'error': detail}
    elif end['status'] != 'ok' or exit_code != 0:
        out['status'] = 'failed'
    else:
        out['status'] = 'ok'
    return out


def fingerprint(helpfile: Path) -> tuple[dict, list[str]]:
    """Final-row physics values from runtime_helpfile.csv, and notes on unusable ones.

    Non-finite values are left out (JSON has no NaN) and reported as notes.
    """
    if not helpfile.is_file():
        return {}, ['runtime_helpfile.csv missing, no physics fingerprint']
    lines = helpfile.read_text().split('\n')
    rows = [line.split() for line in lines if line.strip()]
    if len(rows) < 2:
        return {}, ['runtime_helpfile.csv has no data rows, no physics fingerprint']
    last = dict(zip(rows[0], rows[-1], strict=False))
    values, notes = {}, []
    for key in FINGERPRINT_KEYS:
        if key in last:
            value = float(last[key])
            if math.isfinite(value):
                values[key] = value
            else:
                notes.append(f'fingerprint {key} is {value}')
    return values, notes


def resolved_settings(init_coupler: Path, run_config: dict) -> tuple[dict, list[str]]:
    """Flattened resolved config, falling back to the input config with a note."""
    if init_coupler.is_file():
        return settings.flatten(tomllib.loads(init_coupler.read_text())), []
    note = 'init_coupler.toml missing, settings taken from config.toml'
    return settings.flatten(run_config), [note]
