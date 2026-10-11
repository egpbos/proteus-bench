"""Record sections from PROTEUS's timing log lines.

Under ``PROTEUS_TIMING=1`` PROTEUS logs ``[INIT_TIMING] <step>=<s> ... other=<s>
total=<s>`` just before the main loop, ``[IT_TIMING] iter=N init_stage=0|1
<component>=<s> ... other=<s> total=<s>`` after each iteration, and
``[BACKEND] <submodule> <key>=<value>`` when a module picks its solver
(FormingWorlds/PROTEUS#983; ``proteus-bench prepare`` patches older commits, back
to #678, to log the same).

The runner notes when each such line arrives, in seconds since spawn. An
iteration started at its line's arrival minus its total, so the loop runs from
the start of the first iteration to the arrival of the last line; init is
before it and shutdown after it. Init's steps come from ``[INIT_TIMING]``, and
start-up is the time before PROTEUS's own init began. A component's submodule
is the module the run's config selects for it.
"""

from __future__ import annotations

import math
import re

from proteus_bench.collect import ROUND, component_row

MARKS = ('[IT_TIMING]', '[INIT_TIMING]', '[BACKEND]')
ITER = re.compile(r'\[IT_TIMING\] iter=(\d+) init_stage=([01]) (.*)')
INIT = re.compile(r'\[INIT_TIMING\] (.*)')
BACKEND = re.compile(r'\[BACKEND\] (\w+) (\w+)=(\S+)')
FIELD = re.compile(r'(\w+)=(\S+)')
TOTALS = ('other', 'total')
# Component -> the config section whose ``module`` runs it
SECTIONS = {
    'atmos': 'atmos_clim',
    'chem': 'atmos_chem',
    'escape': 'escape',
    'interior': 'interior_energetics',
    'orbit': 'orbit',
    'outgas': 'outgas',
    'stellar': 'star',
    'structure': 'interior_struct',
}


def modules(settings: dict) -> dict[str, str]:
    """Component -> module name, from a run's flattened settings."""
    return {
        component: str(settings[f'{section}.module'])
        for component, section in SECTIONS.items()
        if f'{section}.module' in settings
    }


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
    """``iters`` [(arrival, iteration, init stage, fields)], ``init`` fields or None,
    ``backends`` and ``bad``, the number of unreadable timing lines (such as the torn last
    line of a killed run, or an [IT_TIMING] line without its init_stage flag).
    """
    parsed = {'iters': [], 'init': None, 'backends': {}, 'bad': 0}
    for arrival, text in lines:
        if match := ITER.search(text):
            fields = _seconds(match[3])
            if fields is None:
                parsed['bad'] += 1
            else:
                parsed['iters'].append((arrival, int(match[1]), match[2] == '1', fields))
        elif match := INIT.search(text):
            parsed['init'] = _seconds(match[1])
            parsed['bad'] += parsed['init'] is None
        elif match := BACKEND.search(text):
            parsed['backends'].setdefault(match[1], {})[match[2]] = match[3]
        elif any(mark in text for mark in MARKS):  # marked, but not in its line's form
            parsed['bad'] += 1
    return parsed


def timings_section(parsed: dict, wall_s: float, rusage: dict, names: dict[str, str]) -> dict:
    """The record's ``timings``; ``names`` maps components to their modules."""
    iters = parsed['iters']
    phases = dict.fromkeys(('setup', 'init', 'loop', 'shutdown'))
    components = []
    section = {'wall_s': round(wall_s, 3)}
    if iters:
        start = max(0.0, iters[0][0] - iters[0][3]['total'])
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
            *_rows('init', [init] if init else [], start, names),
            *_rows('loop', [fields for *_, fields in iters], end - start, names),
            component_row('shutdown', ('other', None, None), wall_s - end, 0),
        ]
    return section | {
        'phases': phases,
        'components': components,
        'per_iter': [
            {
                'iter': n,
                'dur_s': round(fields['total'], ROUND),
                'init_stage': init_stage,
                'components': {
                    k: round(v, ROUND) for k, v in fields.items() if k not in TOTALS
                },
            }
            for _, n, init_stage, fields in iters
        ],
        'rusage': rusage,
    }


def _rows(phase: str, steps: list[dict], phase_s: float, names: dict[str, str]) -> list[dict]:
    """Time per step over ``steps``, largest first, then 'other' for the rest of the phase."""
    totals, calls = {}, {}
    for fields in steps:
        for key, seconds in fields.items():
            if key not in TOTALS:
                totals[key] = totals.get(key, 0.0) + seconds
                calls[key] = calls.get(key, 0) + 1
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    rows = [component_row(phase, (k, names.get(k), None), s, calls[k]) for k, s in ranked]
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
    """An ``[INIT_TIMING]`` line, at least one ``[IT_TIMING]`` line and no unreadable one.

    PROTEUS before #983 logs neither the init line nor the init-stage flag:
    ``proteus-bench prepare`` patches such a commit first.
    """
    n, bad = len(parsed['iters']), parsed['bad']
    problems = [f'{bad} unreadable lines left out'] if bad else []
    if parsed['init'] is None:
        problems.append('no [INIT_TIMING] line; run proteus-bench prepare on this commit')
    detail = '; '.join([f'{n} [IT_TIMING] lines', *problems])
    return {'name': 'timing_log', 'ok': bool(n) and not problems, 'detail': detail}
