"""Tests for proteus_bench.logtiming: a run's timings from PROTEUS's timing log lines.

Contract clauses: an iteration starts at its line's arrival minus its total, so
init, loop and shutdown add up to the wall time; [INIT_TIMING] breaks init into
its steps, with start-up as the time before PROTEUS's init and 'other' the rest
of the init phase; loop components sum over iterations and 'other' takes the rest
of the loop, gaps between iterations included; per-iteration rows hold the
components without 'other' and 'total', and the init_stage flag; components get
the module the config selects; [BACKEND] lines give the backends; other output
lines are ignored; a line without a total, with a non-numeric or non-finite
value, with trailing text, without its init_stage flag, or a marked line not in
its form, is left out and fails the check, as does a missing [INIT_TIMING] line
or no iteration; the status comes from the exit code.
"""

from __future__ import annotations

import pytest

from proteus_bench import logtiming

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

TOL = 1e-9
LINES = [
    (0.5, '[ INFO  ] [BACKEND] aragog solver=cvode\n'),
    (3.0, '[ INFO  ] Starting the main loop\n'),
    (90.0, '[ INFO  ] [INIT_TIMING] structure=70.000 stellar=5.000 other=1.000 total=80.000\n'),
    (
        100.0,
        '[ INFO  ] [IT_TIMING] iter=1 init_stage=1 interior=1.000 atmos=8.000 other=1.000 '
        'total=10.000\n',
    ),
    (101.0, '[ INFO  ] Writing data\n'),
    (
        115.0,
        '[ INFO  ] [IT_TIMING] iter=2 init_stage=0 interior=2.000 atmos=10.000 other=1.000 '
        'total=13.000\n',
    ),
]
NAMES = {'atmos': 'agni', 'interior': 'aragog', 'structure': 'zalmoxis'}


def timings(lines=LINES, wall_s=120.0) -> dict:
    return logtiming.timings_section(logtiming.parse(lines), wall_s, {'max_rss_mb': 1.0}, NAMES)


def test_phases_split_the_wall_time_at_the_first_and_last_iteration():
    """Loop starts at 100 - 10 = 90 and ends at 115; the three phases add up to the wall time."""
    t = timings()
    phases = t['phases']
    assert phases['setup'] is None
    expected = {'init': 90.0, 'loop': 25.0, 'shutdown': 5.0}
    assert {k: phases[k] for k in expected} == pytest.approx(expected, abs=TOL)
    assert sum(v for v in phases.values() if v) == pytest.approx(t['wall_s'], abs=TOL)


def test_init_line_breaks_init_into_its_steps_and_gives_start_up():
    """Init (90 s) is structure 70 + stellar 5 + other 15; start-up is 90 - 80 = 10 s."""
    t = timings()
    assert t['startup_s'] == pytest.approx(10.0, abs=1e-3)
    rows = {r['component']: r for r in t['components'] if r['phase'] == 'init'}
    assert rows['structure']['total_s'] == pytest.approx(70.0, abs=TOL)
    assert rows['stellar']['total_s'] == pytest.approx(5.0, abs=TOL)
    assert rows['other']['total_s'] == pytest.approx(
        15.0, abs=TOL
    )  # PROTEUS's other + start-up
    assert sum(r['total_s'] for r in rows.values()) == pytest.approx(90.0, abs=TOL)


def test_component_rows_add_up_and_name_their_module():
    """atmos 18 s over 2 calls, interior 3 s; 'other' is the 2 s logged plus the 2 s gap."""
    t = timings()
    rows = {(r['phase'], r['component']): r for r in t['components']}
    assert rows['loop', 'atmos']['total_s'] == pytest.approx(18.0, abs=TOL)
    assert rows['loop', 'atmos']['n_calls'] == 2
    assert rows['loop', 'atmos']['submodule'] == 'agni'
    assert rows['init', 'structure']['submodule'] == 'zalmoxis'
    assert rows['init', 'stellar']['submodule'] is None  # not in NAMES
    assert rows['loop', 'interior']['total_s'] == pytest.approx(3.0, abs=TOL)
    assert rows['loop', 'other']['total_s'] == pytest.approx(4.0, abs=TOL)
    assert rows['shutdown', 'other']['total_s'] == pytest.approx(5.0, abs=TOL)
    for phase, total in t['phases'].items():
        if total is not None:
            summed = sum(r['total_s'] for (p, _), r in rows.items() if p == phase)
            assert summed == pytest.approx(total, abs=TOL), phase


def test_per_iteration_rows_carry_the_init_stage_flag():
    """Each row has its iteration, its total as dur_s, the flag and only the named components."""
    rows = timings()['per_iter']
    assert [(r['iter'], r['init_stage']) for r in rows] == [(1, True), (2, False)]
    assert [r['dur_s'] for r in rows] == pytest.approx([10.0, 13.0], abs=TOL)
    assert rows[0]['components'] == pytest.approx({'interior': 1.0, 'atmos': 8.0}, abs=TOL)


def test_modules_come_from_the_config_sections():
    """Each component takes its section's module; absent sections give no entry."""
    settings = {'atmos_clim.module': 'agni', 'star.module': 'mors', 'orbit.module': 'none'}
    assert logtiming.modules(settings) == {'atmos': 'agni', 'stellar': 'mors', 'orbit': 'none'}


def test_backend_lines_name_each_submodule_choice():
    """A [BACKEND] line gives submodule -> key -> value; the last line for a key wins."""
    later = (100.5, '[ INFO  ] [BACKEND] aragog solver=radau\n')
    assert logtiming.parse(LINES)['backends'] == {'aragog': {'solver': 'cvode'}}
    assert logtiming.parse([*LINES, later])['backends'] == {'aragog': {'solver': 'radau'}}


def test_without_init_or_iterations_the_check_fails():
    """No [INIT_TIMING] line (PROTEUS before #983, unpatched) or no iteration fails timing_log."""
    assert logtiming.check(logtiming.parse(LINES))['ok'] is True
    no_init = logtiming.check(logtiming.parse([LINES[0], *LINES[3:]]))
    assert no_init['ok'] is False
    assert 'run proteus-bench prepare' in no_init['detail']
    no_iters = logtiming.parse(LINES[:3])
    assert logtiming.check(no_iters)['ok'] is False
    t = timings(LINES[:3])
    assert set(t['phases'].values()) == {None}
    assert t['components'] == [] and t['per_iter'] == []


@pytest.mark.parametrize(
    'torn',
    [
        '[ INFO  ] [IT_TIMING] iter=3 init_stage=0 interior=1.000 atm',  # cut before total
        '[ INFO  ] [IT_TIMING] iter=3 init_stage=0 interior=1.0x0 total=2.000',
        '[ INFO  ] [IT_TIMING] iter=3 init_stage=0 interior=nan total=2.000',  # JSON has no NaN
        '[ INFO  ] [IT_TIMING] iter=3 interior=1.000 total=2.000',  # unpatched: no flag
        '[ INFO  ] [IT_TIMING] iter=x init_stage=0 total=1.000',
        '[ INFO  ] [INIT_TIMING] structure=1.000 other=1.000 total=2.000 oth',  # trailing junk
        '[ INFO  ] [BACKEND] aragog solver=',  # marked, but not a backend line
    ],
)
def test_unreadable_lines_are_left_out_and_fail_the_check(torn):
    """A torn or garbled line does not raise: the others are kept and the check names it."""
    parsed = logtiming.parse([*LINES, (118.0, torn)])
    assert [n for _, n, _, _ in parsed['iters']] == [1, 2]
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
