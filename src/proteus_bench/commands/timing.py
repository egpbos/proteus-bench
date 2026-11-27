"""``proteus-bench timing``: the time split of finished PROTEUS runs, from their logs alone.

Takes log files, run output folders or grid folders and writes cases.csv, spans.csv
and summary.csv to ``--out`` (README, "Time split from logs"). Exit code 1 when no
log has a PROTEUS header or timing line, or when ``--out`` is not empty.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from proteus_bench.logsplit import UNLOGGED, case_row, find_cases, summary

PHASES = ('init', 'first', 'loop')
FIELDS = (
    'case',
    'status',
    'segments',
    'iters',
    'total_s',
    'sim_time_yr',
    'sim_yr_per_hour',
    'notes',
)
SUMMARY_FIELDS = ['column', 'spread_share', 'corr', 'fast_share', 'slow_share', 'median_s']


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        'paths', type=Path, nargs='+', help='log files, run output folders or grid folders'
    )
    parser.add_argument(
        '--out', type=Path, required=True, help='new or empty directory for the tables'
    )


def ordered_columns(rows: list[dict]) -> list[str]:
    """Span columns by phase, then by time summed over the cases, largest first; unlogged last."""
    totals: dict[str, float] = {}
    for row in rows:
        for name, value in row.items():
            if name not in FIELDS:
                totals[name] = totals.get(name, 0.0) + value
    spans = sorted(
        (n for n in totals if n != UNLOGGED),
        key=lambda n: (PHASES.index(n.partition('.')[0]), -totals[n]),
    )
    return spans + ([UNLOGGED] if UNLOGGED in totals else [])


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, restval='')
        writer.writeheader()
        writer.writerows(rows)


def main(args: argparse.Namespace) -> int:
    if args.out.exists() and any(args.out.iterdir()):
        print(f'{args.out} is not empty; write the tables into a new or empty directory')
        return 1
    found = [case_row(name, logs) for name, logs in find_cases(args.paths).items()]
    found = [(row, spans) for row, spans in found if row['segments']]
    if not found:
        print(
            'no log with a PROTEUS header or timing line under '
            + ' '.join(map(str, args.paths))
        )
        return 1
    rows = [row for row, _ in found]
    columns = ordered_columns(rows)
    spread = summary(rows, columns)
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / 'cases.csv', [*FIELDS, *columns], rows)
    write_csv(
        args.out / 'spans.csv',
        ['case', 'phase', 'module', 'total_s', 'calls', 'max_s'],
        [span for _, spans in found for span in spans],
    )
    write_csv(args.out / 'summary.csv', SUMMARY_FIELDS, spread)
    noted = sum(bool(r['notes']) for r in rows)
    top = ', '.join(
        f'{s["column"]} {s["spread_share"]:.2f}'
        for s in spread[:3]
        if s['spread_share'] is not None
    )
    print(
        f'{args.out}: {len(rows)} cases, {noted} with notes; largest spread shares: {top or "n/a"}'
    )
    return 0
