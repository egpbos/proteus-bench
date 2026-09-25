"""Shared fixtures: runs of the fake proteus stub, and synthetic run histories."""

from __future__ import annotations

import copy
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
def make_records():
    """Make a daily history from examples/record.json, one record per time factor.

    Run i starts i days after 2026-01-01 03:10 UTC, has run_id
    ``<start>-run{i:03d}`` and
    every duration multiplied by ``factors[i]``; ``edit(i, record)`` adjusts a
    record afterwards (comparability, settings, single components).
    """
    base = json.loads(EXAMPLE_RECORD.read_text())
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
