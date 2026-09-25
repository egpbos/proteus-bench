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


def problems_for(path: Path) -> tuple[list[str], bool]:
    """Problems found in one file, and whether the shape was checked."""
    if path.suffix == '.jsonl':
        try:
            events = timing.read_events(path)
        except ValueError as err:
            return [str(err)], True
        found, shape_checked = [], True
        for i, ev in enumerate(events):
            shape = schema.shape_problems('timing', ev)
            if shape is None:
                shape_checked = False
                break
            found += [f'event {i}: {p}' for p in shape]
        return found + timing.check_events(events), shape_checked
    try:
        record = json.loads(path.read_text())
    except json.JSONDecodeError as err:
        return [f'not valid JSON ({err.msg})'], True
    shape = schema.shape_problems('record', record)
    return (shape or []), shape is not None


def main(args: argparse.Namespace) -> int:
    failed = False
    for path in args.files:
        found, shape_checked = problems_for(path)
        note = '' if shape_checked else ' (shape not checked: install jsonschema)'
        if found:
            failed = True
            print(f'{path}: {len(found)} problem(s){note}')
            for problem in found:
                print(f'  {problem}')
        else:
            print(f'{path}: ok{note}')
    return 1 if failed else 0
