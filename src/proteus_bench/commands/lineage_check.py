"""``proteus-bench lineage-check``: is a carry-over run due after this run (D4)?

Compares the run's resolved settings with those of the default-lineage run
before it in the store (same benchmark and machine class; see
``proteus_bench.lineage``). Output is ``key=value`` lines, which can be
appended to ``$GITHUB_OUTPUT`` as they are:

    carry_over=none | needed
    carry_over_of=<run id>          (when needed: this run, the new default run)
    lineage=sha256:<hex>            (when needed: the old settings, the carry-over's lineage)
    settings_toml=<path>            (when needed: the old resolved settings to replay)
    config_toml=<path>              (when needed: the config the old run was given)
    commit=<sha>                    (when needed: the PROTEUS commit to replay them at)
    changed_keys=<key,key,...>      (when needed)

Paths are absolute. Exit codes: 0 nothing to do, 3 carry-over needed, 1 error
(the run directory fails the publish checks, the store cannot be read, or it
lacks a file the old run's record points to).
"""

from __future__ import annotations

import argparse
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


def _stored_file(tree: Path, path: str) -> Path:
    """Absolute path of a store file; raises FileNotFoundError if it is missing."""
    full = (tree / path).resolve()
    if not full.is_file():
        raise FileNotFoundError(f'carry-over due, but the store has no file {path}')
    return full


def main(args: argparse.Namespace) -> int:
    record, problems, _ = store.check_run(args.run_dir)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    # Compare in stored form, so the settings artifact reflects the files present
    new = {**record, 'artifacts': store.stored_artifacts(args.run_dir, record)}
    try:
        tree, records = _store_records(args)
    except (ValueError, publishing.PublishError) as err:
        print(err)
        return 1
    due = lineage.carry_over(records, new)
    if due is None:
        print('carry_over=none')
        return 0
    try:
        settings = _stored_file(tree, due.settings_toml)
        config = _stored_file(tree, due.config_toml)
    except FileNotFoundError as err:
        print(err)
        return 1
    print('carry_over=needed')
    print(f'carry_over_of={due.carry_over_of}')
    print(f'lineage={due.lineage}')
    print(f'settings_toml={settings}')
    print(f'config_toml={config}')
    print(f'commit={due.commit}')
    print(f'changed_keys={",".join(due.changed_keys)}')
    return CARRY_OVER_NEEDED
