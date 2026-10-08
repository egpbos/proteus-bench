"""``proteus-bench publish``: add run directories to the results store and push.

Every run is checked first (the record against its schema, which needs the
``publish`` extra; the fields used in store paths, required files, settings
hash); if any run fails, nothing is published. Runs already in the store are
skipped. After a push the dashboard workflow is dispatched with ``gh`` when
possible. Exit code 0 on
success (including all runs skipped), 1 when a check or git step failed.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

from proteus_bench import publishing, store

STORE_REMOTE = 'https://github.com/egpbos/proteus-bench.git'


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('run_dirs', nargs='+', type=Path, metavar='RUN_DIR')
    add_store_arguments(parser)


def add_store_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--remote', default=STORE_REMOTE, help='store git URL (default: %(default)s)'
    )
    parser.add_argument(
        '--branch', default='results', help='store branch (default: %(default)s)'
    )


def cache_checkout() -> Path:
    """The local checkout of the store branch, a cache that publishing may reset."""
    # The XDG spec says relative values are invalid and must be ignored
    value = os.environ.get('XDG_CACHE_HOME', '').strip()
    root = Path(value) if value and Path(value).is_absolute() else Path.home() / '.cache'
    return root / 'proteus-bench' / 'store'


def check_runs(run_dirs: list[Path]) -> list[tuple[Path, dict]] | None:
    """Check every run dir and print its problems; None if any run is refused."""
    runs, failed = [], False
    for run_dir in run_dirs:
        record, problems = store.check_run(run_dir)
        for problem in problems:
            print(problem)
        failed = failed or bool(problems)
        runs.append((run_dir, record))
    if failed:
        return None
    twice = [i for i, n in Counter(r['run_id'] for _, r in runs).items() if n > 1]
    if twice:
        print(f'run ids given more than once: {", ".join(twice)}')
        return None
    return runs


def publish_run_dirs(run_dirs: list[Path], args: argparse.Namespace) -> int:
    """Check and publish ``run_dirs``; print the outcome; return the exit code."""
    runs = check_runs(run_dirs)
    if runs is None:
        print('nothing published')
        return 1
    try:
        result = publishing.publish(runs, args.remote, args.branch, cache_checkout())
    except publishing.PublishError as err:
        print(f'{err}\nnothing published')
        return 1
    for run_id, commit in result.skipped.items():
        print(f'{run_id}: already in the store (commit {commit[:12]}), skipped')
    if result.retries:
        print(f'the store moved on meanwhile; succeeded after {result.retries} retries')
    if result.commit:
        added = ', '.join(result.added)
        print(f'published {added} to {args.remote} {args.branch} as {result.commit[:12]}')
        print(publishing.trigger_dashboard(args.remote))
    return 0


def main(args: argparse.Namespace) -> int:
    return publish_run_dirs(args.run_dirs, args)
