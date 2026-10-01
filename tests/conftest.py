"""Shared fixtures: runs of the fake proteus stub in a temporary directory."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from proteus_bench.testing import fake_proteus
from proteus_bench.timing import read_events


@pytest.fixture
def run_fake(tmp_path, capsys):
    """Run the stub in-process; return (exit code, output dir, events or None)."""

    def _run(fake: dict | None = None, iters: int = 4, timing: bool = True):
        cfg = {'params': {'out': {'path': 'run'}, 'stop': {'iters': {'maximum': iters}}}}
        if fake:
            cfg['fake'] = fake
        outdir = tmp_path / 'run'
        code = fake_proteus.run(cfg, outdir, timing=timing)
        capsys.readouterr()  # keep the stub's log lines out of test output
        path = outdir / 'timing.jsonl'
        return code, outdir, read_events(path) if path.exists() else None

    return _run


@pytest.fixture
def good_events(run_fake):
    """Events of a default four-iteration fake run that finished normally."""
    code, _, events = run_fake()
    assert code == 0
    return events


def _git(path: Path, *args: str) -> str:
    # A fixed throwaway identity, so fixture commits work where git has none configured
    env = {
        **os.environ,
        'GIT_AUTHOR_NAME': 'fixture',
        'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
        'GIT_COMMITTER_NAME': 'fixture',
        'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
    }
    proc = subprocess.run(
        ['git', '-C', str(path), '-c', 'commit.gpgsign=false', *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def git_repo():
    """Make ``path`` a git repository with its files in one commit on branch main.

    Returns the full commit sha.
    """

    def _make(path: Path) -> str:
        path.mkdir(parents=True, exist_ok=True)
        _git(path, 'init', '-q', '-b', 'main')
        _git(path, 'add', '-A')
        _git(path, 'commit', '-q', '--allow-empty', '-m', 'fixture')
        return _git(path, 'rev-parse', 'HEAD')

    return _make


@pytest.fixture
def cvode_stub(tmp_path, monkeypatch):
    """Put a stand-in scikits_odes_sundials first on PYTHONPATH for subprocesses.

    ``cvode_stub(True)`` makes the CVODE import succeed, ``cvode_stub(False)``
    makes it fail, independent of what the test environment has installed.
    """

    def _install(importable: bool) -> None:
        pkg = tmp_path / 'cvode_stub' / 'scikits_odes_sundials'
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / '__init__.py').write_text('')
        body = (
            'CVODE = CV_RootFunction = StatusEnum = object\n'
            if importable
            else "raise ImportError('stub: SUNDIALS library not found')\n"
        )
        (pkg / 'cvode.py').write_text(body)
        monkeypatch.setenv('PYTHONPATH', str(pkg.parent))

    return _install
