"""Tests for the fake proteus stub (proteus_bench.testing.fake_proteus).

Contract clauses: the stub writes the files the harness reads from a real run;
init_coupler.toml holds the merged resolved config without the [fake] table;
timing lines appear only when PROTEUS_TIMING is on, and in each the steps plus
other add up to total; an error exits 1 before shutdown and a hard kill leaves
a torn last line; scenario typos are errors.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib

import pytest

from proteus_bench.testing.fake_proteus import FAKE_DEFAULTS, scenario

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

FIELDS = re.compile(r'(\w+)=([0-9.]+)')


def timing_lines(out: str, mark: str) -> list[dict[str, float]]:
    return [
        {
            k: float(v)
            for k, v in FIELDS.findall(line.split(mark, 1)[1])
            if k not in ('iter', 'init_stage')
        }
        for line in out.splitlines()
        if mark in line
    ]


def test_outputs_and_resolved_config(run_fake):
    """All output files appear, and overrides replace only the keys they name."""
    code, outdir, _ = run_fake(iters=2)
    assert code == 0
    names = sorted(p.name for p in outdir.iterdir())
    assert names == ['init_coupler.toml', 'proteus_00.log', 'runtime_helpfile.csv']
    resolved = tomllib.loads((outdir / 'init_coupler.toml').read_text())
    assert resolved['params']['stop']['iters']['maximum'] == 2  # override applied
    assert resolved['params']['out']['logging'] == 'INFO'  # sibling default kept
    assert 'fake' not in resolved
    rows = (outdir / 'runtime_helpfile.csv').read_text().splitlines()
    assert rows[0].split('\t') == ['Time', 'T_magma', 'Phi_global', 'F_atm', 'P_surf']
    assert len(rows) == 3  # header plus one row per iteration


def test_timing_lines_add_up_and_follow_the_scenario(run_fake):
    """One [INIT_TIMING] line and one [IT_TIMING] line per iteration; steps plus other give total."""
    code, outdir, out = run_fake(iters=3)
    assert code == 0
    (init,) = timing_lines(out, '[INIT_TIMING]')
    iters = timing_lines(out, '[IT_TIMING]')
    assert len(iters) == 3
    for fields in [init, *iters]:
        steps = sum(v for k, v in fields.items() if k != 'total')
        assert steps == pytest.approx(fields['total'], abs=0.01)
    # init: structure 1138, equilibrate 2 x 1 outgas + 420 + 420/2, star 2
    assert init['total'] == pytest.approx(1138 + 2 + 420 + 210 + 2, abs=1e-3)
    assert iters[0]['atmos'] == pytest.approx(780.0, abs=1e-3)  # the long first AGNI solve
    assert '[IT_TIMING] iter=1 init_stage=1 ' in out
    assert '[IT_TIMING] iter=2 init_stage=0 ' in out
    assert '[BACKEND] aragog solver=cvode' in out
    assert out == (outdir / 'proteus_00.log').read_text()  # the log file has the same lines


def test_no_timing_lines_without_proteus_timing(run_fake):
    """Like PROTEUS, the stub logs timings only when asked to; the backend line stays."""
    code, _, out = run_fake(iters=1, timing=False)
    assert code == 0
    assert '_TIMING]' not in out
    assert '[BACKEND]' in out


def test_error_exits_1_before_shutdown(run_fake):
    """An exception in iteration 2's atmosphere ends the run: one full iteration, no helpfile."""
    code, outdir, out = run_fake({'fail': 'error', 'fail_at_iter': 2}, iters=4)
    assert code == 1
    assert len(timing_lines(out, '[IT_TIMING]')) == 1
    assert not (outdir / 'runtime_helpfile.csv').exists()


def test_hard_kill_leaves_a_torn_last_line(tmp_path):
    """A killed process exits 137 mid-line: the last [IT_TIMING] line has no total."""
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
    last = proc.stdout.splitlines()[-1]
    assert last.startswith('[ INFO  ] [IT_TIMING] iter=3 init_stage=0 ')
    assert 'total=' not in last and last.endswith(' atm')


def test_scenario_rejects_typos_instead_of_running_the_default():
    """A misspelt key or mode must fail loudly, or a test silently tests the wrong thing."""
    assert scenario({}) == FAKE_DEFAULTS
    assert scenario({})['fail'] == 'none'  # a default run must succeed
    assert scenario({'fail': 'kill'})['fail'] == 'kill'
    with pytest.raises(ValueError, match=r"fail = 'kil'"):
        scenario({'fail': 'kil'})
    with pytest.raises(ValueError, match=r"unknown \[fake\] keys \['atmos_first'\]"):
        scenario({'atmos_first': 1.0})
