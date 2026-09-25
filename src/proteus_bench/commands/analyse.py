"""``proteus-bench analyse``: build timing series, flags and steps from stored records.

Reads every ``records/**/*.json`` under the results store and writes the
``proteus-bench-analysis/1`` JSON (see ``proteus_bench.analysis``). Exit code 1
when no record is found or a record cannot be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from proteus_bench.analysis import analyse


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('records_dir', type=Path, help='results store holding records/')
    parser.add_argument('--out', type=Path, default=Path('analysis.json'))


def main(args: argparse.Namespace) -> int:
    paths = sorted((args.records_dir / 'records').glob('**/*.json'))
    if not paths:
        print(f'no run records found under {args.records_dir / "records"}', file=sys.stderr)
        return 1
    records = []
    for path in paths:
        try:
            records.append(json.loads(path.read_text()))
        except json.JSONDecodeError as err:
            print(f'{path}: not valid JSON ({err.msg})', file=sys.stderr)
            return 1
    try:
        result = analyse(records)
    except ValueError as err:
        print(f'cannot analyse {args.records_dir}: {err}', file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(result, indent=1) + '\n')
    series = result['series']
    n_flags = sum(len(s['flags']) for s in series)
    n_steps = sum(len(s['steps']) for s in series)
    print(f'{len(records)} records, {len(series)} series, {n_flags} flags, {n_steps} steps')
    print(f'wrote {args.out}')
    return 0
