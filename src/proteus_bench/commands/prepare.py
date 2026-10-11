"""``proteus-bench prepare``: make a PROTEUS commit from before #983 log the timing lines.

PROTEUS logs ``[INIT_TIMING]``, ``[BACKEND]`` and the init-stage flag on
``[IT_TIMING]`` since FormingWorlds/PROTEUS#983. For an older commit, back to
#678, this creates branch ``bench/<sha>`` at the commit in the given checkout,
applies the patches in ``proteus_bench/proteus_patches`` with a three-way merge
and commits them, so the tree is clean and the record's base commit is the
original one. A commit that already logs the lines is checked out unchanged.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from importlib import resources
from pathlib import Path

PATCHES = resources.files('proteus_bench') / 'proteus_patches'
MARK = '[INIT_TIMING]'


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        'commit', help='PROTEUS commit to benchmark, e.g. a merge commit on main'
    )
    parser.add_argument(
        '--proteus', type=Path, default=Path(), help='PROTEUS checkout (default .)'
    )


class Stop(Exception):
    """Why prepare stopped, with the exit code to leave with."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)


def checked(root: Path, *args: str, code: int = 2) -> str:
    """Stripped stdout of a git command that must succeed; Stop with its stderr otherwise."""
    done = git(root, *args)
    if done.returncode:
        raise Stop(done.stderr.strip(), code)
    return done.stdout.strip()


def main(args: argparse.Namespace) -> int:
    try:
        print(prepare(args.proteus, args.commit))
    except Stop as err:
        print(f'proteus-bench prepare: {err}', file=sys.stderr)
        return err.code
    return 0


def prepare(root: Path, commit: str) -> str:
    """Check ``commit`` out in ``root``, patched where needed; return what was done."""
    if git(root, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        raise Stop(f'{root} has uncommitted changes', 2)
    sha = checked(root, 'rev-parse', '--short=8', f'{commit}^{{commit}}')
    if MARK in git(root, 'show', f'{sha}:src/proteus/proteus.py').stdout:
        checked(root, 'switch', '-q', '--detach', sha)
        return f'{sha} already logs the timing lines; checked out unchanged'
    branch = f'bench/{sha}'
    checked(root, 'switch', '-q', '-C', branch, sha)
    try:
        for patch in sorted(p for p in PATCHES.iterdir() if p.name.endswith('.patch')):
            applied = git(root, 'apply', '-3', str(patch))
            if applied.returncode:
                raise Stop(
                    f'{patch.name} does not apply to {sha}:\n{applied.stderr.strip()}', 1
                )
        message = 'Log the timing lines of FormingWorlds/PROTEUS#983'
        checked(root, 'commit', '-q', '-am', message, code=1)
    except Stop:
        git(root, 'reset', '-q', '--hard')
        raise
    return f'{branch}: {sha} with the timing lines'
