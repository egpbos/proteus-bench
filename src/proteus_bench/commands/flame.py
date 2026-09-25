"""``proteus-bench flame``: build a flame-graph page from a profile.

INPUT is a scalene JSON profile (``*.json``) or folded stacks (``.folded`` or
``.folded.gz``, e.g. py-spy ``--format raw``). Exit code 1 with a message on
stderr when the input cannot be read or holds no samples.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from proteus_bench import profiling


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('input', type=Path, help='scalene JSON, .folded or .folded.gz file')
    parser.add_argument('--out', type=Path, default=Path('flame.html'), help='page to write')
    parser.add_argument('--title', help='page title (default: PROTEUS CPU flame graph)')


def main(args: argparse.Namespace) -> int:
    meta = {'title': args.title, 'profiler': 'scalene' if args.input.suffix == '.json' else ''}
    try:
        lines = profiling.load_stacks(args.input)
        total = profiling.write_flame_page(lines, args.out, meta)
    except (OSError, ValueError) as err:
        print(f'proteus-bench flame: {args.input}: {err}', file=sys.stderr)
        return 1
    print(f'{args.out}: {total:,} samples in {len(lines):,} stacks')
    return 0
