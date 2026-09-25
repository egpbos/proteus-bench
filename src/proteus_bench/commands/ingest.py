"""``proteus-bench ingest``: fetch run directories from a GitHub Actions run.

Downloads the run's artifacts with ``gh run download`` (the user's own gh login)
into a staging directory under the runs dir, takes every directory that holds a
``record.json`` as a run directory, checks it like ``publish`` does, and moves it
to ``<runs-dir>/<run_id>``. With ``--publish`` the ingested runs are published.
Exit code 0 on success, 1 when gh fails, no run directory is found or a check
fails (the staging directory is then kept for inspection).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from proteus_bench import store, userconfig
from proteus_bench.commands.publish import add_store_arguments, publish_run_dirs


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--gha-run', required=True, type=int, metavar='RUN_ID')
    parser.add_argument('--repo', metavar='OWNER/NAME', help="default: gh's current repository")
    parser.add_argument('--publish', action='store_true', help='publish the ingested runs')
    add_store_arguments(parser)


def main(args: argparse.Namespace) -> int:
    if shutil.which('gh') is None:
        print('gh (GitHub CLI) is required to download run artifacts: https://cli.github.com')
        return 1
    runs_dir = Path(userconfig.load()['runs']['dir']).expanduser()
    staging = runs_dir / f'.gha-{args.gha_run}'
    shutil.rmtree(staging, ignore_errors=True)  # left over from an earlier failed ingest
    cmd = ['gh', 'run', 'download', str(args.gha_run), '-D', str(staging)]
    cmd += ['-R', args.repo] if args.repo else []
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        print(f'{" ".join(cmd)} failed (exit {proc.returncode}): {proc.stderr.strip()}')
        return 1
    run_dirs = _move_runs(staging, runs_dir)
    if run_dirs is None:
        return 1
    shutil.rmtree(staging, ignore_errors=True)  # a run may have been the staging dir itself
    if args.publish:
        return publish_run_dirs(run_dirs, args)
    return 0


def _move_runs(staging: Path, runs_dir: Path) -> list[Path] | None:
    """Check the downloaded run dirs and move them into ``runs_dir``; None on failure."""
    found = sorted(p.parent for p in staging.rglob('record.json'))
    if not found:
        print(f'no run directory (a directory with record.json) in the artifacts: {staging}')
        return None
    checked = []
    for run_dir in found:
        record, problems, _ = store.check_run(run_dir)
        for problem in problems:
            print(problem)
        if problems:
            print(f'not ingested; downloaded files kept in {staging}')
            return None
        checked.append((run_dir, record['run_id']))
    moved = []
    for run_dir, run_id in checked:
        target = runs_dir / run_id
        if target.exists():
            print(f'{target} exists; kept it and dropped the downloaded copy')
        else:
            shutil.move(run_dir, target)
            print(f'ingested {target}')
        moved.append(target)
    return moved
