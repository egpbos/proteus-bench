"""Shared fixtures: runs of the fake proteus stub, synthetic run histories and the
profiling fixture files."""

from __future__ import annotations

import copy
import json
import os
import random
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from proteus_bench.testing import fake_proteus
from proteus_bench.timing import read_events

EXAMPLE_RECORD = Path(__file__).parents[1] / 'examples' / 'record.json'


@pytest.fixture
def noisy_factors():
    """Per-run time factors: seeded Gaussian noise, times (1 + step_rel) from run step_at."""

    def _factors(n, *, noise_rel=0.015, step_at=None, step_rel=0.0, seed=42):
        rng = random.Random(seed)
        return [
            (1 + (step_rel if step_at is not None and i >= step_at else 0.0))
            * (1 + rng.gauss(0, noise_rel))
            for i in range(n)
        ]

    return _factors


def _scale_times(timings: dict, factor: float) -> None:
    """Multiply every duration in a record's timings by factor, in place."""
    timings['wall_s'] *= factor
    timings['startup_s'] *= factor
    timings['phases'] = {
        k: v if v is None else v * factor for k, v in timings['phases'].items()
    }
    for row in timings['components']:
        row['total_s'] *= factor
    for row in timings['per_iter']:
        row['dur_s'] *= factor
        row['components'] = {k: v * factor for k, v in row['components'].items()}


@pytest.fixture
def example_record():
    """A fresh copy of examples/record.json (wall_s 2961.2 s, 6 iterations)."""
    return json.loads(EXAMPLE_RECORD.read_text())


@pytest.fixture
def make_records(example_record):
    """Make a daily history from examples/record.json, one record per time factor.

    Run i starts i days after 2026-01-01 03:10 UTC, has run_id
    ``<start>-run{i:03d}`` and every duration multiplied by ``factors[i]``;
    ``edit(i, record)`` adjusts a record afterwards (comparability, settings,
    single components).
    """
    base = copy.deepcopy(example_record)
    start = datetime(2026, 1, 1, 3, 10, tzinfo=UTC)

    def _make(factors, edit=None):
        records = []
        for i, factor in enumerate(factors):
            record = copy.deepcopy(base)
            started = start + timedelta(days=i)
            record['run_id'] = f'{started:%Y%m%dT%H%M%SZ}-run{i:03d}'
            record['trigger']['started_at'] = started.isoformat()
            record['code']['proteus']['sha'] = f'{i:08x}'
            _scale_times(record['timings'], factor)
            if edit is not None:
                edit(i, record)
            records.append(record)
        return records

    return _make


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


@pytest.fixture
def profiles():
    """Profiling fixture files and their sample totals, counted outside proteus_bench."""
    fixtures = Path(__file__).parent / 'fixtures'
    return SimpleNamespace(
        scalene=fixtures / 'scalene-profile.json',
        scalene_total=40 + 25 + 12 + 8 + 3 + 2,  # hits of its six combined_stacks entries
        slice=fixtures / 'real-slice.folded',
        slice_total=22562,  # awk '{s += $NF} END {print s}' real-slice.folded
    )


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
