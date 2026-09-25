"""Tests for the fake proteus stub (proteus_bench.testing.fake_proteus).

Contract clauses: the stub writes the files the harness reads from a real run;
timing.jsonl only when PROTEUS_TIMING is on; init_coupler.toml holds the merged
resolved config without the [fake] table; failures leave data the harness can
still read (clean error with run_end, or a hard kill with a torn last line);
the legacy [IT_TIMING] log lines agree with the spans.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib

import pytest

from proteus_bench.timing import attributed_totals, check_events, read_events

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_outputs_and_resolved_config(run_fake):
    """All output files appear, and overrides replace only the keys they name."""
    code, outdir, events = run_fake(iters=2)
    assert code == 0
    names = sorted(p.name for p in outdir.iterdir())
    assert names == [
        'init_coupler.toml',
        'proteus_00.log',
        'runtime_helpfile.csv',
        'timing.jsonl',
    ]
    resolved = tomllib.loads((outdir / 'init_coupler.toml').read_text())
    assert resolved['params']['stop']['iters']['maximum'] == 2  # override applied
    assert resolved['params']['out']['logging'] == 'INFO'  # sibling default kept
    assert 'fake' not in resolved
    rows = (outdir / 'runtime_helpfile.csv').read_text().splitlines()
    assert rows[0].split('\t') == ['Time', 'T_magma', 'Phi_global', 'F_atm', 'P_surf']
    assert len(rows) == 3  # header plus one row per iteration


def test_no_timing_file_without_proteus_timing(run_fake):
    """Like PROTEUS, the stub writes timing.jsonl only when asked to."""
    code, outdir, events = run_fake(iters=1, timing=False)
    assert code == 0
    assert events is None
    assert (outdir / 'runtime_helpfile.csv').exists()


def test_error_closes_spans_as_failed_and_ends_the_run(run_fake):
    """An exception in the atmosphere marks every open span failed and exits 1."""
    code, _, events = run_fake({'fail': 'error', 'fail_at_iter': 2}, iters=4)
    assert code == 1
    assert check_events(events) == []
    failed = {e['name'] for e in events if e['ev'] == 'span' and e.get('ok') is False}
    assert failed == {'atmos', 'iter', 'loop'}
    assert events[-1]['status'] == 'error'
    iters = [e['iter'] for e in events if e['ev'] == 'span' and e['name'] == 'iter']
    assert iters == [1, 2]  # stopped in iteration 2, no shutdown
    assert not any(e['ev'] == 'span' and e['name'] == 'shutdown' for e in events)


@pytest.mark.smoke
def test_hard_kill_leaves_a_readable_partial_file(tmp_path):
    """A killed process leaves a torn line and no run_end; the rest still checks out."""
    cfg = tmp_path / 'cfg.toml'
    cfg.write_text(
        '[params.out]\npath = "killed"\n[params.stop.iters]\nmaximum = 4\n'
        '[fake]\nfail = "kill"\nfail_at_iter = 3\n'
    )
    env = {**os.environ, 'PROTEUS_TIMING': '1', 'PROTEUS_OUTPUT_PATH': str(tmp_path / 'out')}
    proc = subprocess.run(
        [sys.executable, '-m', 'proteus_bench.testing.fake_proteus', 'start', '-c', str(cfg)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 137
    path = tmp_path / 'out' / 'killed' / 'timing.jsonl'
    assert path.read_text().endswith('"id": 9')  # torn final line present on disk
    events = read_events(path)
    assert events[-1]['ev'] != 'run_end'
    assert check_events(events) == []


def test_it_timing_log_lines_agree_with_spans(run_fake):
    """The [IT_TIMING] totals printed per iteration equal the iter span durations."""
    code, outdir, events = run_fake(iters=3)
    log = (outdir / 'proteus_00.log').read_text()
    printed = [float(m) for m in re.findall(r'\[IT_TIMING\] iter=\d+ .* total=([0-9.]+)', log)]
    spans = [e['dur'] for e in events if e['ev'] == 'span' and e['name'] == 'iter']
    assert code == 0
    assert printed == pytest.approx(spans, abs=1e-3)  # log rounds to 3 decimals
    # Iteration 1 carries the 780 s first AGNI solve, so it dwarfs the others.
    assert printed[0] > 10 * printed[1]
    loop = attributed_totals(events)['loop']
    assert loop['other'] == pytest.approx(3 * 0.2, abs=1e-6)  # bookkeeping per iteration
