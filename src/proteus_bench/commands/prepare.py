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
    parser.add_argument('commit', help='PROTEUS commit to benchmark, e.g. a merge commit on main')
    parser.add_argument('--proteus', type=Path, default=Path(), help='PROTEUS checkout (default .)')


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)


def main(args: argparse.Namespace) -> int:
    root = args.proteus
    if git(root, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        print(f'proteus-bench prepare: {root} has uncommitted changes', file=sys.stderr)
        return 2
    found = git(root, 'rev-parse', '--short=8', f'{args.commit}^{{commit}}')
    if found.returncode:
        print(f'proteus-bench prepare: {found.stderr.strip()}', file=sys.stderr)
        return 2
    sha = found.stdout.strip()
    if MARK in git(root, 'show', f'{sha}:src/proteus/proteus.py').stdout:
        git(root, 'switch', '-q', '--detach', sha)
        print(f'{sha} already logs the timing lines; checked out unchanged')
        return 0
    branch = f'bench/{sha}'
    git(root, 'switch', '-q', '-C', branch, sha)
    for patch in sorted(p for p in PATCHES.iterdir() if p.name.endswith('.patch')):
        applied = git(root, 'apply', '-3', str(patch))
        if applied.returncode:
            git(root, 'reset', '-q', '--hard')
            print(f'proteus-bench prepare: {patch.name} does not apply to {sha}:', file=sys.stderr)
            print(applied.stderr.strip(), file=sys.stderr)
            return 1
    committed = git(root, 'commit', '-q', '-am', 'Log the timing lines of FormingWorlds/PROTEUS#983')
    if committed.returncode:
        git(root, 'reset', '-q', '--hard')
        print(f'proteus-bench prepare: {committed.stderr.strip()}', file=sys.stderr)
        return 1
    print(f'{branch}: {sha} with the timing lines')
    return 0
