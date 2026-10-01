"""Read and check PROTEUS ``timing.jsonl`` files (interface version 1).

The JSON Schema in ``schemas/timing-v1.schema.json`` fixes the shape of each
line. This module checks what a schema cannot: the span tree (parents exist
and contain their children), the phase layout, iteration numbering, and the
rule that at most one span on any root-to-leaf path carries a ``component``,
so attributed times can be summed without double counting.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

SUPPORTED_VERSIONS = frozenset({1})
PHASES = ('setup', 'init', 'loop', 'shutdown')

# Timestamps are written rounded; allow this much slack in containment checks [s]
TOLERANCE_S = 1e-3

_REQUIRED = {
    'run_start': ('wall', 'pid'),
    'backend': ('t0', 'submodule', 'key', 'value'),
    'span': ('id', 'parent', 'name', 't0', 'dur'),
    'run_end': ('t0', 'status'),
}


def read_events(path: str | Path) -> list[dict]:
    """Parse a timing.jsonl file into a list of event dicts.

    A malformed final line is dropped, since a process killed mid-write
    leaves one. A malformed line anywhere else raises ``ValueError``.
    """
    lines = Path(path).read_text().splitlines()
    events = []
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as err:
            if lineno == len(lines):
                break
            raise ValueError(f'{path}:{lineno}: not valid JSON ({err.msg})') from err
    return events


def check_events(events: list[dict]) -> list[str]:
    """Return a list of problems; an empty list means the events are consistent.

    A run without a ``run_end`` event (a crash) is valid data: spans that were
    still open are missing, so references to absent parents are tolerated.
    """
    if not events:
        return ['no events']
    problems = _check_envelope(events)
    if problems:
        return problems
    spans = {ev['id']: ev for ev in events if ev['ev'] == 'span'}
    if len(spans) != sum(1 for ev in events if ev['ev'] == 'span'):
        problems.append('span ids are not unique')
    finished = events[-1]['ev'] == 'run_end'
    problems += _check_tree(spans, finished)
    problems += _check_attributed_overlap(spans)
    problems += _check_phases(spans)
    problems += _check_iterations(spans)
    return problems


def _check_envelope(events: list[dict]) -> list[str]:
    """Versions, event kinds, required keys, and run_start/run_end placement."""
    problems = []
    for i, ev in enumerate(events):
        if ev.get('v') not in SUPPORTED_VERSIONS:
            problems.append(f'event {i}: unsupported version {ev.get("v")!r}')
        kind = ev.get('ev')
        if kind not in _REQUIRED:
            problems.append(f'event {i}: unknown event kind {kind!r}')
            continue
        missing = [k for k in _REQUIRED[kind] if k not in ev]
        if missing:
            problems.append(f'event {i} ({kind}): missing {", ".join(missing)}')
    if problems:
        return problems
    kinds = [ev['ev'] for ev in events]
    if kinds[0] != 'run_start' or kinds.count('run_start') != 1:
        problems.append('run_start must be the first event and appear exactly once')
    if kinds.count('run_end') > 1 or ('run_end' in kinds and kinds[-1] != 'run_end'):
        problems.append('run_end must be the last event and appear at most once')
    return problems


def ancestors(span: dict, spans: dict[int, dict]):
    """Yield the known ancestors of a span, nearest first.

    Stops at a root, at a parent missing from ``spans`` (a crashed run), or
    where the parent chain would revisit a span (a corrupt, cyclic file).
    """
    seen = set()
    parent = span['parent']
    while parent is not None and parent in spans and parent not in seen:
        seen.add(parent)
        yield spans[parent]
        parent = spans[parent]['parent']


def _check_tree(spans: dict[int, dict], finished: bool) -> list[str]:
    """Parents exist and contain children; attribution is disjoint."""
    problems = []
    for sid, span in spans.items():
        parent = span['parent']
        if parent is not None and parent not in spans:
            if finished:
                problems.append(f'span {sid}: parent {parent} does not exist')
            continue
        if parent is not None and not _contains(spans[parent], span):
            problems.append(f'span {sid} ({span["name"]}) lies outside parent {parent}')
        if 'component' in span:
            clash = next((a for a in ancestors(span, spans) if 'component' in a), None)
            if clash is not None:
                problems.append(
                    f'span {sid} and its ancestor {clash["id"]} both carry a component'
                )
    return problems


def _check_attributed_overlap(spans: dict[int, dict]) -> list[str]:
    """Attributed spans never overlap in time, so their durations add up."""
    attributed = sorted((s for s in spans.values() if 'component' in s), key=lambda s: s['t0'])
    return [
        f'attributed spans {a["id"]} and {b["id"]} overlap in time'
        for a, b in pairwise(attributed)
        if b['t0'] < a['t0'] + a['dur'] - TOLERANCE_S
    ]


def _contains(outer: dict, inner: dict) -> bool:
    """Whether ``inner`` lies within ``outer`` in time, up to TOLERANCE_S."""
    start_ok = inner['t0'] >= outer['t0'] - TOLERANCE_S
    end_ok = inner['t0'] + inner['dur'] <= outer['t0'] + outer['dur'] + TOLERANCE_S
    return start_ok and end_ok


def _check_phases(spans: dict[int, dict]) -> list[str]:
    """Root spans are distinct phases that do not overlap."""
    problems = []
    roots = sorted((s for s in spans.values() if s['parent'] is None), key=lambda s: s['t0'])
    names = [s['name'] for s in roots]
    bad = [n for n in names if n not in PHASES]
    if bad:
        problems.append(f'root spans must be phases {PHASES}, got {bad}')
    if len(set(names)) != len(names):
        problems.append(f'a phase appears more than once: {names}')
    for a, b in pairwise(roots):
        if b['t0'] < a['t0'] + a['dur'] - TOLERANCE_S:
            problems.append(f'phases {a["name"]} and {b["name"]} overlap')
    return problems


def _check_iterations(spans: dict[int, dict]) -> list[str]:
    """Spans named 'iter' sit directly under 'loop' and are numbered in order."""
    problems = []
    iters = []
    for sid, span in spans.items():
        is_iter = span['name'] == 'iter'
        if is_iter != ('iter' in span):
            problems.append(f'span {sid}: the iter field belongs on spans named iter only')
            continue
        if not is_iter:
            continue
        parent = spans.get(span['parent'])
        if parent is not None and parent['name'] != 'loop':
            problems.append(f'span {sid}: iter span outside the loop phase')
        iters.append(span)
    numbers = [s['iter'] for s in sorted(iters, key=lambda s: s['t0'])]
    if any(b <= a for a, b in pairwise(numbers)):
        problems.append(f'iteration numbers are not increasing: {numbers}')
    return problems


def phase_of(span: dict, spans: dict[int, dict]) -> str | None:
    """Name of the phase (root span) a span belongs to, if known."""
    root = [span, *ancestors(span, spans)][-1]
    return root['name'] if root['parent'] is None else None


def attributed_totals(events: list[dict]) -> dict[str, dict]:
    """Sum attributed time per phase: the canonical reading of the attribution rule.

    Returns ``{phase: {'total': s, 'attributed': {(component, submodule,
    backend): [seconds, n_calls]}, 'other': s}}``. ``other`` is the phase time
    no attributed span covers, so ``sum(attributed) + other == total``.
    """
    spans = {ev['id']: ev for ev in events if ev['ev'] == 'span'}
    out = {}
    for span in spans.values():
        if span['parent'] is None:
            out[span['name']] = {'total': span['dur'], 'attributed': {}, 'other': span['dur']}
    for span in spans.values():
        if 'component' not in span:
            continue
        phase = out.get(phase_of(span, spans))
        if phase is None:
            continue
        key = (span['component'], span.get('submodule'), span.get('backend'))
        acc = phase['attributed'].setdefault(key, [0.0, 0])
        acc[0] += span['dur']
        acc[1] += 1
        phase['other'] -= span['dur']
    return out
