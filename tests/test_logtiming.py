"""Tests for proteus_bench.logtiming: timings of a run without timing.jsonl.

Contract clauses: an iteration starts at its line's arrival minus its total, so
init, loop and shutdown add up to the wall time; loop components sum over
iterations and 'other' takes the rest of the loop, gaps between iterations
included; per-iteration rows hold the components without 'other' and 'total';
other output lines are ignored, and a line without a total or with a non-numeric
or non-finite value is left out and fails the check; without iterations there are no phases
and the check fails; the status comes from the exit code.
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
    iters, _ = logtiming.iterations(lines)
    return logtiming.timings_section(iters, wall_s, {'max_rss_mb': 1.0})


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
    iters, bad = logtiming.iterations(LINES[:1])
    assert (iters, bad) == ([], 0)
    t = timings(LINES[:1])
    assert set(t['phases'].values()) == {None}
    assert t['components'] == [] and t['per_iter'] == []
    assert logtiming.check(iters, bad)['ok'] is False
    assert logtiming.check(*logtiming.iterations(LINES))['ok'] is True


@pytest.mark.parametrize(
    'torn',
    [
        '[ INFO  ] [IT_TIMING] iter=3 interior=1.000 atm',  # cut before total
        '[ INFO  ] [IT_TIMING] iter=3 interior=1.0x0 total=2.000',
        '[ INFO  ] [IT_TIMING] iter=3 interior=nan total=2.000',  # JSON has no NaN
    ],
)
def test_unreadable_lines_are_left_out_and_fail_the_check(torn):
    """A torn or garbled line does not raise: the others are kept and the check names it."""
    iters, bad = logtiming.iterations([*LINES, (118.0, torn)])
    assert [n for _, n, _ in iters] == [1, 2]
    assert bad == 1
    result = logtiming.check(iters, bad)
    assert result['ok'] is False
    assert '1 unreadable lines left out' in result['detail']


@pytest.mark.parametrize(
    ('exit_code', 'timed_out', 'status'),
    [(0, False, 'ok'), (1, False, 'failed'), (-9, False, 'crashed'), (-9, True, 'timeout')],
)
def test_status_comes_from_the_exit_code(exit_code, timed_out, status):
    """Exit 0 is ok, a positive code failed, a signal crashed, unless the timeout fired."""
    out = logtiming.outcome(logtiming.iterations(LINES)[0], exit_code, timed_out, 60.0)
    assert out['status'] == status
    assert out['n_iters'] == 2
