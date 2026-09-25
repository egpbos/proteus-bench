"""Shared fixtures: runs of the fake proteus stub in a temporary directory."""

from __future__ import annotations

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
