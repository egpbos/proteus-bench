"""``proteus-bench validate``: check run-record files against the record schema.

Exit code 1 when any file has problems, so CI can use it as a contract check.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from proteus_bench import schema


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('files', nargs='+', type=Path)


def problems_for(path: Path) -> list[str]:
    """Problems found in one file; the shape is not checked without jsonschema."""
    try:
        record = json.loads(path.read_text())
    except json.JSONDecodeError as err:
        return [f'not valid JSON ({err.msg})']
    return schema.shape_problems('record', record) or []


def main(args: argparse.Namespace) -> int:
    if not schema.available():
        print('note: shape not checked (install proteus-bench[publish])')
    failed = False
    for path in args.files:
        found = problems_for(path)
        failed = failed or bool(found)
        print(f'{path}: {len(found)} problem(s)' if found else f'{path}: ok')
        for problem in found:
            print(f'  {problem}')
    return 1 if failed else 0
