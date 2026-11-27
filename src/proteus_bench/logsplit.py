"""Time split of finished PROTEUS runs from their log files alone, without the runner.

A case is one PROTEUS run: the ``proteus_NN.log`` files of its output folder (one per
process start, so a resumed run has several), or another log file with PROTEUS's
header or timing lines, such as a grid's ``logs/<case>.log`` copy of the terminal
output. When both exist for a case, the folder's own logs are read.

Each process start (the header's ``Current time`` line) begins a segment. Its spans
are the ``[INIT_TIMING]`` steps (phase ``init``), the components of its first
``[IT_TIMING]`` line (``first``, where the compilation of a module's first call
lands) and those of the later lines (``loop``). Its clock runs from ``Current time``
to ``Simulation stopped at``; for the last segment of a file without that line (a
killed run) it runs to the file's modification time. ``unlogged`` is the clock
minus the spans: the shutdown, and an iteration that was still running when the run
was killed. The clock starts a little after init's timer and counts whole seconds, so
in a short segment the spans can exceed it; ``unlogged`` is then 0. Log times are the
run host's local time; the modification time is read in this machine's local time zone.
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime
from pathlib import Path

from proteus_bench.logtiming import MARKS, parse

HEADER = re.compile(r'Current time\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)')
STOP = re.compile(r'Simulation stopped at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)')
OWN_LOG = re.compile(r'proteus_\d\d\.log')
UNLOGGED = 'unlogged'


def find_cases(paths: list[Path]) -> dict[str, list[Path]]:
    """Case name -> its log files in order, from log files and folders searched for ``*.log``.

    Raises ValueError when two folders or two log copies give one case name.
    """
    files = [f for p in paths for f in (sorted(p.rglob('*.log')) if p.is_dir() else [p])]
    own: dict[Path, list[Path]] = {}
    copies: dict[str, list[Path]] = {}
    for file in files:
        if OWN_LOG.fullmatch(file.name):
            own.setdefault(file.parent, []).append(file)
        else:
            _add(copies, file.stem, [file])
    cases: dict[str, list[Path]] = {}
    for folder, logs in own.items():
        _add(cases, folder.name, logs)
    return dict(sorted((copies | cases).items()))


def _add(cases: dict[str, list[Path]], name: str, logs: list[Path]) -> None:
    if name in cases:
        raise ValueError(
            f'case {name!r} found twice, in {cases[name][0]} and {logs[0]}; '
            'pass the grids one at a time'
        )
    cases[name] = logs


def read_segments(logs: list[Path]) -> list[dict]:
    """Segments in log order: ``start`` and ``end`` (datetime or None), whether a stop line
    gave the end (``stopped``), and the ``parsed`` timing lines.
    """
    segments = []
    for log in logs:
        current = None
        with log.open(errors='replace') as lines:
            for line in lines:
                if match := HEADER.search(line):
                    current = _segment(_time(match[1]))
                    segments.append(current)
                elif any(mark in line for mark in MARKS):
                    if current is None:
                        current = _segment(None)
                        segments.append(current)
                    current['lines'].append((0.0, line))  # a log file has no arrival times
                elif current is not None and (match := STOP.search(line)):
                    current['end'], current['stopped'] = _time(match[1]), True
        if current is not None and current['end'] is None:
            current['end'] = datetime.fromtimestamp(log.stat().st_mtime).replace(microsecond=0)
    return [s | {'parsed': parse(s.pop('lines'))} for s in segments]


def _segment(start: datetime | None) -> dict:
    return {'start': start, 'end': None, 'stopped': False, 'lines': []}


def _time(text: str) -> datetime:
    return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')


def split(segments: list[dict]) -> tuple[dict, dict, list[str]]:
    """Spans ``(phase, module) -> [seconds, calls, longest call]``, the counts and the notes.

    Counts are ``segments``, ``iters`` and ``unlogged_s`` (None when a segment has no
    clock). A note says where the split is incomplete or the clock ends at a file time.
    """
    spans: dict[tuple[str, str], list] = {}
    notes, unlogged, iters = [], 0.0, 0
    for i, segment in enumerate(segments, 1):
        parsed = segment['parsed']
        steps = [('init', parsed['init'])] if parsed['init'] else []
        steps += [
            ('first' if n == 0 else 'loop', fields)
            for n, (*_, fields) in enumerate(parsed['iters'])
        ]
        logged = 0.0
        for phase, fields in steps:
            for module, seconds in fields.items():
                if module != 'total':
                    _count(spans, (phase, module), seconds)
                    logged += seconds
        iters += len(parsed['iters'])
        notes += _notes(i, segment)
        if unlogged is not None and segment['start'] and segment['end']:
            unlogged += max(0.0, (segment['end'] - segment['start']).total_seconds() - logged)
        else:
            unlogged = None
    if not iters:
        notes.append('no [IT_TIMING] line; was PROTEUS_TIMING=1 set?')
    counts = {'segments': len(segments), 'iters': iters, 'unlogged_s': unlogged}
    return spans, counts, notes


def _count(spans: dict, key: tuple[str, str], seconds: float) -> None:
    span = spans.setdefault(key, [0.0, 0, 0.0])
    span[0] += seconds
    span[1] += 1
    span[2] = max(span[2], seconds)


def _notes(i: int, segment: dict) -> list[str]:
    parsed, found = segment['parsed'], []
    if segment['start'] is None:
        found.append(f'segment {i}: no PROTEUS header, so no clock')
    elif segment['end'] is None:
        found.append(f'segment {i}: no stop line before the next segment, so no clock')
    elif not segment['stopped']:
        found.append(f'segment {i}: no stop line; the clock ends at the log file time')
    if parsed['iters'] and parsed['init'] is None:
        found.append(f'segment {i}: no [INIT_TIMING] line, so init is unlogged')
    if parsed['bad']:
        found.append(f'segment {i}: {parsed["bad"]} unreadable timing lines left out')
    return found


def status_code(logs: list[Path]) -> int | None:
    """The code on the first line of the case folder's ``status`` file, if there is one.

    The folder holds ``proteus_NN.log``; a grid's ``logs/<case>.log`` copy has it at
    ``<grid>/<case>``.
    """
    log = logs[0]
    folder = log.parent if OWN_LOG.fullmatch(log.name) else log.parent.parent / log.stem
    path = folder / 'status'
    if not path.is_file():
        return None
    first = path.read_text(errors='replace').split('\n', 1)[0].strip()
    try:
        return int(first)
    except ValueError:
        raise ValueError(
            f'{path}: expected a status code on the first line, found {first!r}'
        ) from None


def column(phase: str, module: str) -> str:
    return f'{phase}.{module}'


def case_row(name: str, logs: list[Path]) -> tuple[dict, list[dict]]:
    """The case's row of seconds per ``phase.module`` and ``unlogged``, and its span rows."""
    spans, counts, notes = split(read_segments(logs))
    seconds = {column(*key): round(span[0], 3) for key, span in spans.items()}
    unlogged = counts['unlogged_s']
    if unlogged is not None:
        seconds[UNLOGGED] = round(unlogged, 3)
    row = {
        'case': name,
        'status': status_code(logs),
        'segments': counts['segments'],
        'iters': counts['iters'],
        'total_s': round(sum(seconds.values()), 3),
        'notes': '; '.join(notes),
    } | seconds
    span_rows = [
        {
            'case': name,
            'phase': phase,
            'module': module,
            'total_s': round(total, 3),
            'calls': calls,
            'max_s': round(longest, 3),
        }
        for (phase, module), (total, calls, longest) in spans.items()
    ]
    return row, span_rows


def summary(rows: list[dict], columns: list[str]) -> list[dict]:
    """Per column, over cases with iterations: how it follows the spread in ``total_s``.

    ``fast_share`` and ``slow_share`` are its share of the summed total in the fastest
    and the slowest third of the cases; ``corr`` is its Pearson correlation with the
    total; ``spread_share`` is cov(column, total) / var(total). Since the columns add
    up to the total, the spread shares add up to 1. Statistics that need two cases
    with different totals are None. Sorted by spread share, largest first.
    """
    cases = sorted((r for r in rows if r['iters']), key=lambda r: r['total_s'])
    third = max(1, len(cases) // 3)
    fast, slow = cases[:third], cases[-third:]
    totals = [r['total_s'] for r in cases]
    spread = len(set(totals)) > 1
    out = []
    for name in columns:
        values = [r.get(name, 0.0) for r in cases]
        out.append(
            {
                'column': name,
                'median_s': round(statistics.median(values), 3) if values else None,
                'fast_share': _share(fast, name),
                'slow_share': _share(slow, name),
                'corr': _corr(values, totals) if spread else None,
                'spread_share': (
                    round(
                        statistics.covariance(values, totals) / statistics.variance(totals), 4
                    )
                    if spread
                    else None
                ),
            }
        )
    return sorted(out, key=lambda s: -(s['spread_share'] or 0.0))


def _share(cases: list[dict], name: str) -> float | None:
    total = sum(r['total_s'] for r in cases)
    return round(sum(r.get(name, 0.0) for r in cases) / total, 4) if total else None


def _corr(values: list[float], totals: list[float]) -> float | None:
    if len(set(values)) < 2:
        return None
    return round(statistics.correlation(values, totals), 4)
