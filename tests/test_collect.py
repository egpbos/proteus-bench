"""Tests for proteus_bench.collect: run-record sections from a run's output files.

Contract clauses: the fingerprint is the last complete helpfile row, a cut-off
last row skipped, with non-finite values reported; settings fall back to the input
config with a note.
"""

from __future__ import annotations

import pytest

from proteus_bench import collect

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_fingerprint_from_the_last_helpfile_row(run_fake, tmp_path):
    """The fake's row 4 gives T_magma 3000-4x50, F_atm 1e5/4; NaN is reported, not stored."""
    _, outdir, _ = run_fake()
    values, notes = collect.fingerprint(outdir / 'runtime_helpfile.csv')
    assert values == {
        'T_magma': pytest.approx(2800.0),
        'Phi_global': pytest.approx(0.96),
        'F_atm': pytest.approx(25000.0),
        'P_surf': pytest.approx(254.0),
    }
    assert notes == []
    odd = tmp_path / 'odd.csv'
    odd.write_text('Time\tT_magma\tF_atm\n1.0\t2000.0\tnan\n')
    values, notes = collect.fingerprint(odd)
    assert values == {'T_magma': pytest.approx(2000.0)}
    assert notes == ['fingerprint F_atm is nan']
    odd.write_text('Time\tT_magma\n')
    assert collect.fingerprint(odd)[0] == {}
    assert 'missing' in collect.fingerprint(tmp_path / 'absent.csv')[1][0]


def test_cut_last_helpfile_row_is_skipped(tmp_path):
    """A last row cut off by a killed write is skipped: values come from the row before it."""
    helpfile = tmp_path / 'runtime_helpfile.csv'
    helpfile.write_text('Time\tT_magma\tPhi_global\n1000.0\t2500.0\t0.9\n2000.0\t28\n')
    assert collect.last_complete_row(helpfile) == {
        'Time': '1000.0',
        'T_magma': '2500.0',
        'Phi_global': '0.9',
    }
    # zipping the cut row would give T_magma 28 and no Phi_global
    values, notes = collect.fingerprint(helpfile)
    assert values == {'T_magma': pytest.approx(2500.0), 'Phi_global': pytest.approx(0.9)}
    assert notes == []


def test_resolved_settings_prefer_init_coupler(run_fake, tmp_path):
    """The resolved file wins; without it the input config is used and a note says so."""
    _, outdir, _ = run_fake()
    flat, notes = collect.resolved_settings(outdir / 'init_coupler.toml', {'x': 1})
    assert flat['params.out.logging'] == 'INFO'  # a default only the resolved file has
    assert notes == []
    flat, notes = collect.resolved_settings(tmp_path / 'absent.toml', {'a': {'b': 1}})
    assert flat == {'a.b': 1}
    assert notes == ['init_coupler.toml missing, settings taken from config.toml']
