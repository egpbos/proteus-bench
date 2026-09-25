"""``proteus-bench publish``: add run directories to the results store and push.

Every run is checked first (record shape when jsonschema is installed, the
fields used in store paths, required files, settings hash); if any run fails,
nothing is published. Runs already in the store are skipped. After a push the
dashboard workflow is dispatched with ``gh`` when possible. Exit code 0 on
success (including all runs skipped), 1 when a check or git step failed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from proteus_bench import publishing, store, userconfig


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('run_dirs', nargs='+', type=Path, metavar='RUN_DIR')
    add_store_arguments(parser)


def add_store_arguments(parser: argparse.ArgumentParser) -> None:
    """``--remote`` and ``--branch``, defaulting to the user config's [store]."""
    parser.add_argument('--remote', help='store git URL (default: store.remote in config)')
    parser.add_argument('--branch', help='store branch (default: store.branch, "results")')


def store_location(args: argparse.Namespace) -> tuple[str, str, Path]:
    """(remote, branch, cache checkout) from the options and user config.

    Raises ValueError when no remote is configured or the config is invalid.
    """
    cfg = userconfig.load()['store']
    remote = args.remote or cfg['remote']
    if not remote:
        raise ValueError(
            'no store remote: pass --remote URL, or set store.remote in '
            f'{userconfig.config_path()} (proteus-bench init --remote URL)'
        )
    return remote, args.branch or cfg['branch'], Path(cfg['cache_dir']).expanduser()


def check_runs(run_dirs: list[Path]) -> list[tuple[Path, dict]] | None:
    """Check every run dir and print its problems; None if any run is refused."""
    runs, failed = [], False
    for run_dir in run_dirs:
        record, problems, shape_checked = store.check_run(run_dir)
        for problem in problems:
            print(problem)
        failed = failed or bool(problems)
        if not shape_checked:
            print(f'{run_dir}: record shape not checked (install jsonschema)')
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
    try:
        remote, branch, checkout = store_location(args)
    except ValueError as err:
        print(err)
        return 1
    runs = check_runs(run_dirs)
    if runs is None:
        print('nothing published')
        return 1
    try:
        result = publishing.publish(runs, remote, branch, checkout)
    except publishing.PublishError as err:
        print(f'{err}\nnothing published')
        return 1
    for run_id, commit in result.skipped.items():
        print(f'{run_id}: already in the store (commit {commit[:12]}), skipped')
    if result.retries:
        print(f'the store moved on meanwhile; succeeded after {result.retries} retries')
    if result.commit:
        added = ', '.join(result.added)
        print(f'published {added} to {remote} {branch} as {result.commit[:12]}')
        print(publishing.trigger_dashboard(remote))
    return 0


def main(args: argparse.Namespace) -> int:
    return publish_run_dirs(args.run_dirs, args)
