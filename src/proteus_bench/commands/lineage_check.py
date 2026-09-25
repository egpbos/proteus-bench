"""``proteus-bench lineage-check``: is a carry-over run due after this run (D4)?

Compares the run's settings with the default-lineage run before it in the store
(same benchmark and machine class). Output is ``key=value`` lines, which can be
appended to ``$GITHUB_OUTPUT`` as they are:

    carry_over=none | needed
    carry_over_of=<run id>          (when needed: the new default run)
    lineage=sha256:<hex>            (when needed: the old settings, the carry-over's lineage)
    settings_toml=<path>            (when needed: the old settings file to replay)
    commit=<sha>                    (when needed: the PROTEUS commit to replay them at)
    changed_keys=<key,key,...>      (when needed)

Exit codes: 0 nothing to do, 3 carry-over needed, 1 error (unreadable run
directory, store not reachable, or the old settings file missing from the store).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from proteus_bench import lineage, publishing, store
from proteus_bench.commands.publish import add_store_arguments, store_location

CARRY_OVER_NEEDED = 3


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('run_dir', type=Path, metavar='RUN_DIR')
    parser.add_argument(
        '--store',
        type=Path,
        metavar='DIR',
        help='a store checkout to read as is (default: update the cached checkout)',
    )
    add_store_arguments(parser)


def _store_records(args: argparse.Namespace) -> tuple[Path, list[dict]]:
    if args.store:
        return args.store, store.read_records(args.store)
    remote, branch, checkout = store_location(args)
    with publishing.locked(checkout):
        publishing.update_checkout(checkout, remote, branch)
        return checkout, store.read_records(checkout)


def main(args: argparse.Namespace) -> int:
    record_file = args.run_dir / 'record.json'
    try:
        new = json.loads(record_file.read_text())
    except (OSError, json.JSONDecodeError) as err:
        print(f'{record_file}: cannot read the run record ({err})')
        return 1
    try:
        tree, records = _store_records(args)
    except (ValueError, publishing.PublishError) as err:
        print(err)
        return 1
    due = lineage.carry_over(records, new)
    if due is None:
        print('carry_over=none')
        return 0
    settings = (tree / due.settings_toml).resolve()
    if not settings.is_file():
        print(f'carry-over due, but the store has no settings file {due.settings_toml}')
        return 1
    print('carry_over=needed')
    print(f'carry_over_of={due.carry_over_of}')
    print(f'lineage={due.lineage}')
    print(f'settings_toml={settings}')
    print(f'commit={due.commit}')
    print(f'changed_keys={",".join(due.changed_keys)}')
    return CARRY_OVER_NEEDED
