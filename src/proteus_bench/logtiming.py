"""Record sections from PROTEUS's ``[IT_TIMING]`` log lines, for runs without timing.jsonl.

Under ``PROTEUS_TIMING=1`` every PROTEUS version since #678 (June 2026) logs
``[IT_TIMING] iter=N <component>=<s> ... other=<s> total=<s>`` after each
main-loop iteration. The runner notes when each such line arrives, in seconds
since spawn. An iteration started at its line's arrival minus its total, so the
loop runs from the start of the first iteration to the arrival of the last
line; init (start-up and setup included) is before it and shutdown after it.
Init gets no breakdown, and there are no backend events, submodules or
init-stage flags.
"""

from __future__ import annotations

import math
import re

from proteus_bench.collect import ROUND, component_row

LINE = re.compile(r'\[IT_TIMING\] iter=(\d+) (.*)')
FIELD = re.compile(r'(\w+)=(\S+)')


def iterations(lines: list[tuple[float, str]]) -> tuple[list, int]:
    """(arrival, iteration, seconds per key) of each ``[IT_TIMING]`` line, in order, and
    the number of lines left out for a missing ``total`` or a non-numeric or non-finite
    value, such as the torn last line of a killed run.
    """
    found, bad = [], 0
    for arrival, text in lines:
        match = LINE.search(text)
        if not match:
            continue
        try:
            fields = {key: float(value) for key, value in FIELD.findall(match[2])}
        except ValueError:
            fields = {}
        if 'total' in fields and all(math.isfinite(v) for v in fields.values()):
            found.append((arrival, int(match[1]), fields))
        else:
            bad += 1
    return found, bad


def timings_section(iters: list, wall_s: float, rusage: dict) -> dict:
    phases = dict.fromkeys(('setup', 'init', 'loop', 'shutdown'))
    components = []
    if iters:
        start = max(0.0, iters[0][0] - iters[0][2]['total'])
        end = max(start, iters[-1][0])
        phases |= {
            'init': round(start, ROUND),
            'loop': round(end - start, ROUND),
            'shutdown': round(max(0.0, wall_s - end), ROUND),
        }
        components = _loop_rows(iters, end - start)
        components.insert(0, component_row('init', ('other', None, None), start, 0))
        components.append(component_row('shutdown', ('other', None, None), wall_s - end, 0))
    return {
        'source': 'log',
        'wall_s': round(wall_s, 3),
        'phases': phases,
        'components': components,
        'per_iter': [
            {
                'iter': n,
                'dur_s': round(fields['total'], ROUND),
                'components': {
                    k: round(v, ROUND) for k, v in fields.items() if k not in ('other', 'total')
                },
            }
            for _, n, fields in iters
        ],
        'rusage': rusage,
    }


def _loop_rows(iters: list, loop_s: float) -> list[dict]:
    """Loop time per component, largest first, then 'other' for the rest of the loop."""
    totals, calls = {}, {}
    for _, _, fields in iters:
        for key, seconds in fields.items():
            if key not in ('other', 'total'):
                totals[key] = totals.get(key, 0.0) + seconds
                calls[key] = calls.get(key, 0) + 1
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    rows = [component_row('loop', (k, None, None), s, calls[k]) for k, s in ranked]
    other = max(0.0, loop_s - sum(totals.values()))
    return [*rows, component_row('loop', ('other', None, None), other, 0)]


def outcome(iters: list, exit_code: int, timed_out: bool, timeout_s) -> dict:
    """Status from the exit code alone: no run_end event says how the run ended."""
    out = {'exit_code': exit_code, 'n_iters': len(iters)}
    if timed_out:
        return out | {'status': 'timeout', 'error': f'killed after the {timeout_s} s timeout'}
    if exit_code < 0:
        return out | {'status': 'crashed', 'error': f'killed by signal {-exit_code}'}
    return out | {'status': 'ok' if exit_code == 0 else 'failed'}


def check(iters: list, bad: int) -> dict:
    """At least one ``[IT_TIMING]`` line and none unreadable; without any, PROTEUS_TIMING had
    no effect.
    """
    detail = f'{len(iters)} [IT_TIMING] lines, no timing.jsonl'
    if bad:
        detail += f'; {bad} unreadable lines left out'
    return {'name': 'timing_log', 'ok': bool(iters) and not bad, 'detail': detail}
