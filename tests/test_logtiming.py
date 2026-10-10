"""Tests for proteus_bench.logtiming: timings of a run without timing.jsonl.

Contract clauses: an iteration starts at its line's arrival minus its total, so
init, loop and shutdown add up to the wall time; loop components sum over
iterations and 'other' takes the rest of the loop, gaps between iterations
included; per-iteration rows hold the components without 'other' and 'total';
other output lines are ignored; without iterations there are no phases and the
check fails; the status comes from the exit code.
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


def timings(lines=LINES, wall_s=120.0) -> dict:
    iters = logtiming.iterations(lines)
    return logtiming.timings_section(iters, wall_s, {'max_rss_mb': 1.0})


def test_phases_split_the_wall_time_at_the_first_and_last_iteration():
    """Loop starts at 100 - 10 = 90 and ends at 115; the three phases add up to the wall time."""
    t = timings()
    assert t['source'] == 'log'
    assert t['phases'] == {'setup': None, 'init': 90.0, 'loop': 25.0, 'shutdown': 5.0}
    assert sum(v for v in t['phases'].values() if v) == pytest.approx(t['wall_s'])


def test_component_rows_add_up_to_their_phase():
    """atmos 18 s over 2 calls, interior 3 s; 'other' is the 2 s logged plus the 2 s gap."""
    rows = {(r['phase'], r['component']): r for r in timings()['components']}
    assert rows['loop', 'atmos']['total_s'] == pytest.approx(18.0)
    assert rows['loop', 'atmos']['n_calls'] == 2
    assert rows['loop', 'interior']['total_s'] == pytest.approx(3.0)
    assert rows['loop', 'other']['total_s'] == pytest.approx(4.0)
    assert rows['init', 'other']['total_s'] == pytest.approx(90.0)
    assert rows['shutdown', 'other']['total_s'] == pytest.approx(5.0)
    for phase, total in timings()['phases'].items():
        if total is not None:
            summed = sum(r['total_s'] for (p, _), r in rows.items() if p == phase)
            assert summed == pytest.approx(total), phase


def test_per_iteration_rows_leave_out_other_and_total():
    assert timings()['per_iter'] == [
        {'iter': 1, 'dur_s': 10.0, 'components': {'interior': 1.0, 'atmos': 8.0}},
        {'iter': 2, 'dur_s': 13.0, 'components': {'interior': 2.0, 'atmos': 10.0}},
    ]


def test_without_iterations_there_are_no_phases_and_the_check_fails():
    iters = logtiming.iterations(LINES[:1])
    assert iters == []
    t = timings(LINES[:1])
    assert set(t['phases'].values()) == {None}
    assert t['components'] == [] and t['per_iter'] == []
    assert logtiming.check(iters)['ok'] is False
    assert logtiming.check(logtiming.iterations(LINES))['ok'] is True


@pytest.mark.parametrize(
    ('exit_code', 'timed_out', 'status'),
    [(0, False, 'ok'), (1, False, 'failed'), (-9, False, 'crashed'), (-9, True, 'timeout')],
)
def test_status_comes_from_the_exit_code(exit_code, timed_out, status):
    out = logtiming.outcome(logtiming.iterations(LINES), exit_code, timed_out, 60.0)
    assert out['status'] == status
    assert out['n_iters'] == 2
