"""Tests for proteus_bench.collect: run-record sections from a run's output files.

Contract clauses: component rows per phase sum to the phase total with the
remainder as 'other'; per-iteration rows attribute nested spans to their
iteration; backends combine events and per-call counts; the outcome is
timeout before crashed before failed before ok; start-up time is spawn to
run_start and never negative; the fingerprint is the last helpfile row with
non-finite values reported; settings fall back to the input config with a note.

Expected values below are derived by hand from ``FAKE_DEFAULTS`` for a
four-iteration fake run, not read back from the implementation.
"""

from __future__ import annotations

import datetime as dt

import pytest

from proteus_bench import collect
from proteus_bench.timing import check_events

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

# init: structure 1138 + 420 + 420/2, outgas 2 x 1, star 2
INIT_S = 1138 + 1 + 420 + 1 + 210 + 2
# loop: 4 x (interior 3.5 + outgas 1 + 0.2 unattributed) + atmos 780 + 3 x 15 + one re-solve 140
LOOP_S = 4 * (3.5 + 1 + 0.2) + 780 + 3 * 15 + 140


def _rows(rows, phase):
    return {(r['component'], r['submodule'], r['backend']): (r['total_s'], r['n_calls'])
            for r in rows if r['phase'] == phase}  # fmt: skip


def test_component_rows_add_up_to_phases(good_events):
    """Each phase's rows sum to its total; unattributed loop time is 4 x 0.2 s."""
    rows = collect.component_rows(good_events)
    phases = collect.phase_totals(good_events)
    assert phases == {
        'setup': None,
        'init': pytest.approx(INIT_S, abs=1e-6),
        'loop': pytest.approx(LOOP_S, abs=1e-6),
        'shutdown': pytest.approx(5.0, abs=1e-6),
    }
    loop = _rows(rows, 'loop')
    assert loop[('atmos', 'agni', None)] == (pytest.approx(825.0, abs=1e-6), 4)
    assert loop[('structure', 'zalmoxis', 'jax')] == (pytest.approx(140.0, abs=1e-6), 1)
    assert loop[('other', None, None)][0] == pytest.approx(0.8, abs=1e-6)
    init = _rows(rows, 'init')
    assert init[('structure', 'zalmoxis', 'numpy')] == (pytest.approx(1768.0, abs=1e-6), 3)
    for phase in ('init', 'loop', 'shutdown'):
        assert sum(r['total_s'] for r in rows if r['phase'] == phase) == pytest.approx(
            phases[phase], abs=1e-5
        )
    loop_names = [r['component'] for r in rows if r['phase'] == 'loop']
    assert loop_names[0] == 'atmos'
    assert loop_names[-1] == 'other'  # largest first, rest last
    assert collect.component_rows([]) == []


def test_rows_skip_a_phase_that_never_closed(good_events):
    """A crash leaves loop spans without their phase: no loop rows, iterations still listed."""
    crashed = [
        ev
        for ev in good_events
        if ev['ev'] != 'run_end'
        and not (ev['ev'] == 'span' and ev['name'] in ('loop', 'shutdown'))
    ]
    rows = collect.component_rows(crashed)
    assert {r['phase'] for r in rows} == {'init'}
    assert len(collect.per_iter_rows(crashed)) == 4
    assert collect.phase_totals(crashed)['loop'] is None


def _span(sid: int, parent: int | None, name: str, t0: float, dur: float, **fields) -> dict:
    return {'v': 1, 'ev': 'span', 'id': sid, 'parent': parent, 'name': name, 't0': t0,
            'dur': dur, **fields}  # fmt: skip


def test_other_is_clamped_where_children_overrun_within_tolerance():
    """A child 0.5 ms longer than its phase is legal (1 ms slack); 'other' reads 0, not < 0."""
    events = [
        {'v': 1, 'ev': 'run_start', 'wall': '2026-09-25T10:00:00.000Z', 'pid': 1},
        _span(3, 2, 'atmos', 0.0, 1.0005, component='atmos', submodule='agni'),
        _span(2, 1, 'iter', 0.0, 1.0, iter=1),
        _span(1, None, 'loop', 0.0, 1.0),
        {'v': 1, 'ev': 'run_end', 't0': 1.0, 'status': 'ok'},
    ]
    assert check_events(events) == []
    other = [r for r in collect.component_rows(events) if r['component'] == 'other']
    assert other[0]['total_s'] == pytest.approx(0.0, abs=0)
    assert other[0]['total_s'] >= 0  # the schema minimum; unclamped it would be -0.0005


def test_rows_only_for_known_phases():
    """A root span that is not a phase (a broken file) gets no rows, so the record stays valid."""
    events = [
        _span(2, 1, 'atmos', 0.0, 1.0, component='atmos'),
        _span(1, None, 'warmup', 0.0, 2.0),
        _span(4, 3, 'interior', 2.0, 1.0, component='interior'),
        _span(3, None, 'loop', 2.0, 1.5),
    ]
    assert check_events(events) != []  # the timing_contract check reports the bad root
    rows = collect.component_rows(events)
    assert {r['phase'] for r in rows} == {'loop'}
    assert [r['component'] for r in rows] == ['interior', 'other']


def test_per_iter_attributes_spans_nested_below_an_iteration_child():
    """A component inside a group span inside an iteration counts for that iteration."""
    events = [
        _span(4, 3, 'atmos', 2.0, 2.0, component='atmos', submodule='agni'),
        _span(3, 2, 'equilibrate', 1.0, 4.0),
        _span(2, 1, 'iter', 0.0, 10.0, iter=1),
        _span(5, 1, 'interior', 10.0, 1.0, component='interior'),  # in the loop, no iteration
        _span(1, None, 'loop', 0.0, 11.0),
    ]
    rows = collect.per_iter_rows(events)
    assert rows == [{'iter': 1, 'dur_s': pytest.approx(10.0), 'components': {'atmos': 2.0}}]
    assert 'interior' not in rows[0]['components']


def test_per_iter_terminates_on_a_cyclic_parent_chain():
    """A corrupt file whose parents form a cycle is read, not looped over forever."""
    events = [
        _span(5, 6, 'atmos', 0.0, 1.0, component='atmos'),
        _span(6, 5, 'group', 0.0, 1.0),
        _span(7, None, 'iter', 0.0, 1.0, iter=1),
    ]
    rows = collect.per_iter_rows(events)
    assert rows == [{'iter': 1, 'dur_s': pytest.approx(1.0), 'components': {}}]
    assert collect.per_iter_rows(events[:2]) == []


def test_per_iter_rows(good_events):
    """Iteration 3 carries the structure re-solve; only iteration 1 is an init stage."""
    rows = collect.per_iter_rows(good_events)
    assert [r['iter'] for r in rows] == [1, 2, 3, 4]
    assert rows[2]['dur_s'] == pytest.approx(3.5 + 140 + 1 + 15 + 0.2, abs=1e-6)
    assert rows[2]['components'] == {
        'interior': pytest.approx(3.5),
        'structure': pytest.approx(140.0),
        'outgas': pytest.approx(1.0),
        'atmos': pytest.approx(15.0),
    }
    assert rows[0]['components']['atmos'] == pytest.approx(780.0)
    assert [r['init_stage'] for r in rows] == [True, False, False, False]
    assert collect.per_iter_rows([]) == []


def test_backends_combine_events_and_call_counts(good_events):
    """Aragog reports its solver once and per call; Zalmoxis switches numpy -> jax."""
    assert collect.backends(good_events) == {
        'aragog': {'solver': 'cvode', 'calls': {'cvode': 4}},
        'zalmoxis': {'calls': {'numpy': 3, 'jax': 1}},
    }
    assert collect.backends([]) == {}


def test_outcome_precedence(good_events, run_fake):
    """ok, failed on error or non-zero exit, crashed without run_end, timeout above all."""
    ok = collect.outcome(good_events, 0, False, None)
    assert ok == {
        'exit_code': 0,
        'n_iters': 4,
        'termination': 'iters_maximum',
        'time_yr': pytest.approx(4000.0),
        'status': 'ok',
    }
    assert collect.outcome(good_events, 1, False, None)['status'] == 'failed'
    error_end = [*good_events[:-1], {**good_events[-1], 'status': 'error'}]
    assert (
        collect.outcome(error_end, 0, False, None)['status'] == 'failed'
    )  # exit 0, run_end not ok
    assert collect.outcome(good_events, 0, True, 60.0)['status'] == 'timeout'
    crashed = collect.outcome(good_events[:-1], -9, False, None)
    assert crashed['status'] == 'crashed'
    assert crashed['n_iters'] == 4
    assert collect.outcome([], 0, False, None)['error'] == 'no timing.jsonl events'
    _, _, error_events = run_fake({'fail': 'error', 'fail_at_iter': 2}, iters=4)
    failed = collect.outcome(error_events, 1, False, None)
    assert failed['status'] == 'failed'
    assert failed['n_iters'] == 2
    assert failed['error'] == 'fake atmosphere solver failure'


def test_startup_is_spawn_to_run_start():
    """1.5 s after spawn reads 1.5; a millisecond-rounded early stamp clamps to 0."""
    spawned = dt.datetime(2026, 9, 25, 10, 0, 0, tzinfo=dt.UTC)
    events = [{'ev': 'run_start', 'wall': '2026-09-25T10:00:01.500Z', 'pid': 1, 'v': 1}]
    assert collect.startup_s(events, spawned) == pytest.approx(1.5, abs=1e-9)
    late_spawn = spawned + dt.timedelta(seconds=1.5015)  # rounds to -0.002 s unclamped
    assert collect.startup_s(events, late_spawn) == pytest.approx(0.0, abs=0)
    assert collect.startup_s([], spawned) is None


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


def test_resolved_settings_prefer_init_coupler(run_fake, tmp_path):
    """The resolved file wins; without it the input config is used and a note says so."""
    _, outdir, _ = run_fake()
    flat, notes = collect.resolved_settings(outdir / 'init_coupler.toml', {'x': 1})
    assert flat['params.out.logging'] == 'INFO'  # a default only the resolved file has
    assert notes == []
    flat, notes = collect.resolved_settings(tmp_path / 'absent.toml', {'a': {'b': 1}})
    assert flat == {'a.b': 1}
    assert notes == ['init_coupler.toml missing, settings taken from config.toml']
