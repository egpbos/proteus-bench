"""Record sections from PROTEUS's timing log lines, for runs without timing.jsonl.

``PROTEUS_TIMING`` came to PROTEUS main with #678 (June 2026). With it set,
every version since then logs ``[IT_TIMING] iter=N <component>=<s> ...
other=<s> total=<s>`` after each main-loop iteration. Newer versions also log
``[INIT_TIMING] <step>=<s> ... other=<s> total=<s>`` just before the loop and
``[BACKEND] <submodule> <key>=<value>`` when a module picks its solver.

The runner notes when each such line arrives, in seconds since spawn. An
iteration started at its line's arrival minus its total, so the loop runs from
the start of the first iteration to the arrival of the last line; init is
before it and shutdown after it. Init covers start-up and setup as well; with
an ``[INIT_TIMING]`` line its steps are broken out, and start-up is the time
before PROTEUS's own init began. There are no submodules or init-stage flags.
"""

from __future__ import annotations

import math
import re

from proteus_bench.collect import ROUND, component_row

MARKS = ('[IT_TIMING]', '[INIT_TIMING]', '[BACKEND]')
ITER = re.compile(r'\[IT_TIMING\] iter=(\d+) (.*)')
INIT = re.compile(r'\[INIT_TIMING\] (.*)')
BACKEND = re.compile(r'\[BACKEND\] (\w+) (\w+)=(\S+)')
FIELD = re.compile(r'(\w+)=(\S+)')
TOTALS = ('other', 'total')


def _seconds(text: str) -> dict[str, float] | None:
    """``key=seconds`` fields, or None when any word is not such a field, a value is not a
    finite number, or there is no total.
    """
    matches = [FIELD.fullmatch(word) for word in text.split()]
    if not all(matches):
        return None
    try:
        fields = {m[1]: float(m[2]) for m in matches}
    except ValueError:
        return None
    if 'total' not in fields or not all(math.isfinite(v) for v in fields.values()):
        return None
    return fields


def parse(lines: list[tuple[float, str]]) -> dict:
    """``iters`` [(arrival, iteration, fields)], ``init`` fields or None, ``backends`` and
    ``bad``, the number of unreadable timing lines (such as the torn last line of a killed run).
    """
    parsed = {'iters': [], 'init': None, 'backends': {}, 'bad': 0}
    for arrival, text in lines:
        if match := ITER.search(text):
            fields = _seconds(match[2])
            if fields is None:
                parsed['bad'] += 1
            else:
                parsed['iters'].append((arrival, int(match[1]), fields))
        elif match := INIT.search(text):
            parsed['init'] = _seconds(match[1])
            parsed['bad'] += parsed['init'] is None
        elif match := BACKEND.search(text):
            parsed['backends'].setdefault(match[1], {})[match[2]] = match[3]
        elif any(mark in text for mark in MARKS):  # marked, but not in its line's form
            parsed['bad'] += 1
    return parsed


def timings_section(parsed: dict, wall_s: float, rusage: dict) -> dict:
    iters = parsed['iters']
    phases = dict.fromkeys(('setup', 'init', 'loop', 'shutdown'))
    components = []
    section = {'source': 'log', 'wall_s': round(wall_s, 3)}
    if iters:
        start = max(0.0, iters[0][0] - iters[0][2]['total'])
        end = max(start, iters[-1][0])
        phases |= {
            'init': round(start, ROUND),
            'loop': round(end - start, ROUND),
            'shutdown': round(max(0.0, wall_s - end), ROUND),
        }
        init = parsed['init']
        if init is not None:
            section['startup_s'] = round(max(0.0, start - init['total']), 3)
        components = [
            *_rows('init', [init] if init else [], start),
            *_rows('loop', [fields for _, _, fields in iters], end - start),
            component_row('shutdown', ('other', None, None), wall_s - end, 0),
        ]
    return section | {
        'phases': phases,
        'components': components,
        'per_iter': [
            {
                'iter': n,
                'dur_s': round(fields['total'], ROUND),
                'components': {
                    k: round(v, ROUND) for k, v in fields.items() if k not in TOTALS
                },
            }
            for _, n, fields in iters
        ],
        'rusage': rusage,
    }


def _rows(phase: str, steps: list[dict], phase_s: float) -> list[dict]:
    """Time per step over ``steps``, largest first, then 'other' for the rest of the phase."""
    totals, calls = {}, {}
    for fields in steps:
        for key, seconds in fields.items():
            if key not in TOTALS:
                totals[key] = totals.get(key, 0.0) + seconds
                calls[key] = calls.get(key, 0) + 1
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    rows = [component_row(phase, (k, None, None), s, calls[k]) for k, s in ranked]
    other = max(0.0, phase_s - sum(totals.values()))
    return [*rows, component_row(phase, ('other', None, None), other, 0)]


def outcome(parsed: dict, exit_code: int, timed_out: bool, timeout_s) -> dict:
    """Status from the exit code alone: no run_end event says how the run ended."""
    out = {'exit_code': exit_code, 'n_iters': len(parsed['iters'])}
    if timed_out:
        return out | {'status': 'timeout', 'error': f'killed after the {timeout_s} s timeout'}
    if exit_code < 0:
        return out | {'status': 'crashed', 'error': f'killed by signal {-exit_code}'}
    return out | {'status': 'ok' if exit_code == 0 else 'failed'}


def check(parsed: dict) -> dict:
    """At least one ``[IT_TIMING]`` line and no unreadable timing line; without any,
    PROTEUS_TIMING had no effect.
    """
    n, bad = len(parsed['iters']), parsed['bad']
    detail = f'{n} [IT_TIMING] lines, no timing.jsonl'
    if bad:
        detail += f'; {bad} unreadable lines left out'
    return {'name': 'timing_log', 'ok': bool(n) and not bad, 'detail': detail}
