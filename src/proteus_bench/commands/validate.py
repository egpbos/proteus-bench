"""``proteus-bench validate``: check timing.jsonl and run-record files.

``*.jsonl`` files are read as timing events (shape per line, then the span
tree rules); other files as one run record (shape). Exit code 1 when any file
has problems, so CI can use it as a contract check.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from proteus_bench import schema, timing


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('files', nargs='+', type=Path)


def problems_for(path: Path) -> list[str]:
    """Problems found in one file; shape checks are skipped when unavailable."""
    if path.suffix == '.jsonl':
        try:
            events = timing.read_events(path)
        except ValueError as err:
            return [str(err)]
        shape = [
            f'event {i}: {p}'
            for i, ev in enumerate(events)
            for p in schema.shape_problems('timing', ev) or []
        ]
        return shape + timing.check_events(events)
    try:
        record = json.loads(path.read_text())
    except json.JSONDecodeError as err:
        return [f'not valid JSON ({err.msg})']
    return schema.shape_problems('record', record) or []


def main(args: argparse.Namespace) -> int:
    if not schema.available():
        print('note: shape not checked (install jsonschema); tree rules still apply')
    failed = False
    for path in args.files:
        found = problems_for(path)
        failed = failed or bool(found)
        print(f'{path}: {len(found)} problem(s)' if found else f'{path}: ok')
        for problem in found:
            print(f'  {problem}')
    return 1 if failed else 0
