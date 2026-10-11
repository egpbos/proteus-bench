"""Tests for proteus_bench.logtiming: timings of a run without timing.jsonl.

Contract clauses: an iteration starts at its line's arrival minus its total, so
init, loop and shutdown add up to the wall time; loop components sum over
iterations and 'other' takes the rest of the loop, gaps between iterations
included; per-iteration rows hold the components without 'other' and 'total';
other output lines are ignored, and a line without a total or with a non-numeric
or non-finite value is left out and fails the check; without iterations there are no phases
and the check fails; the status comes from the exit code. An [INIT_TIMING] line
breaks init into its steps, with start-up as the time before PROTEUS's init and
'other' the rest of the init phase; [BACKEND] lines give the backends.
"""

from __future__ import annotations

import pytest

from proteus_bench import logtiming

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

LINES = [
    (3.0, '[ INFO  ] Starting the main loop\n'),
    (
        100.0,
        '[ INFO  ] [IT_TIMING] iter=1 interior=1.000 atmos=8.000 other=1.000 total=10.000\n',
    ),
    (101.0, '[ INFO  ] Writing data\n'),
    (
        115.0,
        '[ INFO  ] [IT_TIMING] iter=2 interior=2.000 atmos=10.000 other=1.000 total=13.000\n',
    ),
]


TOL = 1e-9


def timings(lines=LINES, wall_s=120.0) -> dict:
    return logtiming.timings_section(logtiming.parse(lines), wall_s, {'max_rss_mb': 1.0})


def test_phases_split_the_wall_time_at_the_first_and_last_iteration():
    """Loop starts at 100 - 10 = 90 and ends at 115; the three phases add up to the wall time."""
    t = timings()
    assert t['source'] == 'log'
    phases = t['phases']
    assert phases['setup'] is None
    expected = {'init': 90.0, 'loop': 25.0, 'shutdown': 5.0}
    assert {k: phases[k] for k in expected} == pytest.approx(expected, abs=TOL)
    assert sum(v for v in phases.values() if v) == pytest.approx(t['wall_s'], abs=TOL)


def test_component_rows_add_up_to_their_phase():
    """atmos 18 s over 2 calls, interior 3 s; 'other' is the 2 s logged plus the 2 s gap."""
    rows = {(r['phase'], r['component']): r for r in timings()['components']}
    assert rows['loop', 'atmos']['total_s'] == pytest.approx(18.0, abs=TOL)
    assert rows['loop', 'atmos']['n_calls'] == 2
    assert rows['loop', 'interior']['total_s'] == pytest.approx(3.0, abs=TOL)
    assert rows['loop', 'other']['total_s'] == pytest.approx(4.0, abs=TOL)
    assert rows['init', 'other']['total_s'] == pytest.approx(90.0, abs=TOL)
    assert rows['shutdown', 'other']['total_s'] == pytest.approx(5.0, abs=TOL)
    for phase, total in timings()['phases'].items():
        if total is not None:
            summed = sum(r['total_s'] for (p, _), r in rows.items() if p == phase)
            assert summed == pytest.approx(total, abs=TOL), phase


def test_per_iteration_rows_leave_out_other_and_total():
    """Each row has its iteration, its total as dur_s and only the named components."""
    rows = timings()['per_iter']
    assert [r['iter'] for r in rows] == [1, 2]
    assert [r['dur_s'] for r in rows] == pytest.approx([10.0, 13.0], abs=TOL)
    assert rows[0]['components'] == pytest.approx({'interior': 1.0, 'atmos': 8.0}, abs=TOL)
    assert rows[1]['components'] == pytest.approx({'interior': 2.0, 'atmos': 10.0}, abs=TOL)


def test_without_iterations_there_are_no_phases_and_the_check_fails():
    """Output with no [IT_TIMING] line gives null phases, no rows and a failed check."""
    parsed = logtiming.parse(LINES[:1])
    assert (parsed['iters'], parsed['bad']) == ([], 0)
    t = timings(LINES[:1])
    assert set(t['phases'].values()) == {None}
    assert t['components'] == [] and t['per_iter'] == []
    assert logtiming.check(parsed)['ok'] is False
    assert logtiming.check(logtiming.parse(LINES))['ok'] is True


@pytest.mark.parametrize(
    'torn',
    [
        '[ INFO  ] [IT_TIMING] iter=3 interior=1.000 atm',  # cut before total
        '[ INFO  ] [IT_TIMING] iter=3 interior=1.0x0 total=2.000',
        '[ INFO  ] [IT_TIMING] iter=3 interior=nan total=2.000',  # JSON has no NaN
        '[ INFO  ] [INIT_TIMING] structure=1.000 oth',  # an init line counts as well
    ],
)
def test_unreadable_lines_are_left_out_and_fail_the_check(torn):
    """A torn or garbled line does not raise: the others are kept and the check names it."""
    parsed = logtiming.parse([*LINES, (118.0, torn)])
    assert [n for _, n, _ in parsed['iters']] == [1, 2]
    assert parsed['bad'] == 1
    result = logtiming.check(parsed)
    assert result['ok'] is False
    assert '1 unreadable lines left out' in result['detail']


@pytest.mark.parametrize(
    ('exit_code', 'timed_out', 'status'),
    [(0, False, 'ok'), (1, False, 'failed'), (-9, False, 'crashed'), (-9, True, 'timeout')],
)
def test_status_comes_from_the_exit_code(exit_code, timed_out, status):
    """Exit 0 is ok, a positive code failed, a signal crashed, unless the timeout fired."""
    out = logtiming.outcome(logtiming.parse(LINES), exit_code, timed_out, 60.0)
    assert out['status'] == status
    assert out['n_iters'] == 2


INIT_LINES = [
    (0.5, '[ INFO  ] [BACKEND] aragog solver=cvode\n'),
    (90.0, '[ INFO  ] [INIT_TIMING] structure=70.000 stellar=5.000 other=1.000 total=80.000\n'),
    *LINES,
]


def test_init_line_breaks_init_into_its_steps_and_gives_start_up():
    """Init (90 s) is structure 70 + stellar 5 + other 15; start-up is 90 - 80 = 10 s."""
    t = timings(INIT_LINES)
    assert t['phases']['init'] == pytest.approx(90.0, abs=TOL)
    assert t['startup_s'] == pytest.approx(10.0, abs=1e-3)
    rows = {r['component']: r for r in t['components'] if r['phase'] == 'init'}
    assert rows['structure']['total_s'] == pytest.approx(70.0, abs=TOL)
    assert rows['stellar']['total_s'] == pytest.approx(5.0, abs=TOL)
    assert rows['other']['total_s'] == pytest.approx(
        15.0, abs=TOL
    )  # PROTEUS's other plus start-up
    assert sum(r['total_s'] for r in rows.values()) == pytest.approx(90.0, abs=TOL)
    assert 'startup_s' not in timings()  # no init line, no start-up


def test_backend_lines_name_each_submodule_choice():
    """A [BACKEND] line gives submodule -> key -> value; the last line for a key wins."""
    later = (100.5, '[ INFO  ] [BACKEND] aragog solver=radau\n')
    assert logtiming.parse(INIT_LINES)['backends'] == {'aragog': {'solver': 'cvode'}}
    assert logtiming.parse([*INIT_LINES, later])['backends'] == {'aragog': {'solver': 'radau'}}
    assert logtiming.parse(LINES)['backends'] == {}
