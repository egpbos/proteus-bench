"""Tests for the compare page data (proteus_bench.report.comparepage).

Contract clauses: runs are listed newest first with their series group, so the
page can say when two runs come from different series; per-run totals add the
component rows of each phase and leave out phases that did not run.
"""

from __future__ import annotations

import pytest

from proteus_bench.report.comparepage import compare_data, run_totals

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_runs_carry_their_series_group(records):
    """The newest run is r8 on habrok-vink; the gha runs name their own group."""
    runs = compare_data(records)['runs']
    assert runs[0]['group'] == 'all_options / default / habrok-vink'
    assert {r['group'] for r in runs} == {
        'all_options / default / habrok-vink',
        'all_options / default / gha-ubuntu-epyc7763',
    }
    assert [r['id'] for r in runs] == [r['run_id'] for r in reversed(records)]


def test_totals_sum_component_rows_and_skip_null_phases(records):
    """r1: init structure 1768 s; loop atmos 780 + 5 x 15 = 855 s; setup is null, so absent."""
    totals = run_totals(records[0])
    assert totals['init.structure'] == pytest.approx(1768.0)
    assert totals['loop.atmos'] == pytest.approx(855.0)
    assert 'setup' not in totals
    assert totals['total'] == pytest.approx(records[0]['timings']['wall_s'])
