"""Tests for proteus_bench.logsplit: the time split of finished runs from their logs alone.

Contract clauses: a case is a folder's proteus_NN.log files in order, or another log
file by its stem, and the folder's logs win over a copy of the same name; two
folders or copies of one name are an error. Each PROTEUS header starts a segment;
its clock runs to the stop line, or to the file time for the file's last segment,
and a segment ended by the next header has no clock. Spans are the [INIT_TIMING]
steps (init), the first [IT_TIMING] line of a segment (first) and the later lines
(loop), never the total; unlogged is the clock minus the spans, floored at 0, and
None when a segment has no clock. Notes name a missing header, stop line or
[INIT_TIMING] line, unreadable lines, and a log without [IT_TIMING] lines. The
status code comes from the case folder's status file, also for a grid's log copy;
a malformed one is an error. The summary takes cases with iterations, their
fastest and slowest third, the correlation and the spread share
cov(column, total) / var(total), which adds up to 1 over the columns.
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from proteus_bench import logsplit

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

TOL = 1e-9


def header(time: str) -> str:
    return f'[ INFO  ] Current time      {time} \n'


def stop(time: str) -> str:
    return f'[ INFO  ] Simulation stopped at: {time} \n'


def init(**steps) -> str:
    fields = ' '.join(f'{k}={v:.3f}' for k, v in steps.items())
    return f'[ INFO  ] [INIT_TIMING] {fields} total={sum(steps.values()):.3f}\n'


def iteration(n: int, **components) -> str:
    fields = ' '.join(f'{k}={v:.3f}' for k, v in components.items())
    return (
        f'[ INFO  ] [IT_TIMING] iter={n} init_stage=0 {fields} '
        f'total={sum(components.values()):.3f}\n'
    )


# Init 52 s, then iterations of 46, 33 and 35 s: 166 s logged in a 180 s clock
RUN = [
    header('2026-11-23 10:00:00'),
    '[ INFO  ] Some other line\n',
    init(structure=50.0, other=2.0),
    iteration(1, interior=5.0, atmos=40.0, other=1.0),
    iteration(2, interior=20.0, structure=8.0, atmos=4.0, other=1.0),
    iteration(3, interior=30.0, atmos=4.0, other=1.0),
    stop('2026-11-23 10:03:00'),
]


def write(path, lines, mtime: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(lines))
    if mtime:
        stamp = datetime.strptime(mtime, '%Y-%m-%d %H:%M:%S').timestamp()
        os.utime(path, (stamp, stamp))
    return path


def test_find_cases_groups_folder_logs_and_prefers_them_over_copies(tmp_path):
    """A folder's proteus_NN.log files form one case in order; a copy by that name is dropped."""
    grid = tmp_path / 'grid'
    first = write(grid / 'case_000001' / 'proteus_00.log', RUN)
    second = write(grid / 'case_000001' / 'proteus_01.log', RUN)
    write(grid / 'logs' / 'case_000001.log', RUN)
    copy = write(grid / 'logs' / 'case_000002.log', RUN)
    assert logsplit.find_cases([grid]) == {
        'case_000001': [first, second],
        'case_000002': [copy],
    }


def test_find_cases_takes_a_file_argument_as_one_case(tmp_path):
    """A log file given directly is a case named by its stem, whatever its name."""
    log = write(tmp_path / 'run.log', RUN)
    assert logsplit.find_cases([log]) == {'run': [log]}


def test_find_cases_refuses_two_folders_of_one_name(tmp_path):
    """Two grids with a case of the same name cannot both be read in one call."""
    write(tmp_path / 'a' / 'case_000001' / 'proteus_00.log', RUN)
    write(tmp_path / 'b' / 'case_000001' / 'proteus_00.log', RUN)
    with pytest.raises(ValueError, match="case 'case_000001' found twice"):
        logsplit.find_cases([tmp_path / 'a', tmp_path / 'b'])


def test_split_of_a_stopped_run(tmp_path):
    """Init steps, the first iteration and the later ones are separate spans; 'total' is not one."""
    log = write(tmp_path / 'proteus_00.log', RUN)
    spans, counts, notes = logsplit.split(logsplit.read_segments([log]))
    seconds = {key: span[0] for key, span in spans.items()}
    assert seconds == pytest.approx(
        {
            ('init', 'structure'): 50.0,
            ('init', 'other'): 2.0,
            ('first', 'interior'): 5.0,
            ('first', 'atmos'): 40.0,
            ('first', 'other'): 1.0,
            ('loop', 'interior'): 50.0,
            ('loop', 'structure'): 8.0,
            ('loop', 'atmos'): 8.0,
            ('loop', 'other'): 2.0,
        },
        abs=TOL,
    )
    assert spans[('loop', 'interior')][1:] == [2, pytest.approx(30.0, abs=TOL)]
    # 180 s clock minus 166 s of spans; counting the totals as spans would leave none
    assert counts == {'segments': 1, 'iters': 3, 'unlogged_s': pytest.approx(14.0, abs=TOL)}
    assert notes == []


def test_a_killed_run_ends_its_clock_at_the_file_time(tmp_path):
    """Without a stop line, the time from the last timing line to the file time is unlogged."""
    log = write(tmp_path / 'proteus_00.log', RUN[:-1], mtime='2026-11-23 11:00:00')
    _, counts, notes = logsplit.split(logsplit.read_segments([log]))
    assert counts['unlogged_s'] == pytest.approx(3600.0 - 166.0, abs=TOL)
    assert notes == ['segment 1: no stop line; the clock ends at the log file time']


def test_a_resumed_copy_has_a_segment_without_clock(tmp_path):
    """A header after a killed segment starts a new one; the killed segment gets no clock."""
    lines = [
        *RUN[:-1],
        header('2026-11-23 12:00:00'),
        init(structure=10.0),
        iteration(4, interior=5.0),
        stop('2026-11-23 12:01:00'),
    ]
    log = write(tmp_path / 'case.log', lines)
    segments = logsplit.read_segments([log])
    spans, counts, notes = logsplit.split(segments)
    assert [s['start'].hour for s in segments] == [10, 12]
    assert spans[('first', 'interior')][:2] == [pytest.approx(10.0, abs=TOL), 2]
    assert counts == {'segments': 2, 'iters': 4, 'unlogged_s': None}
    assert notes == ['segment 1: no stop line before the next segment, so no clock']


def test_short_segment_floors_unlogged_at_zero(tmp_path):
    """Spans above a whole-second clock that started after init's timer leave 0 unlogged."""
    lines = [header('2026-11-23 10:00:00'), init(other=2.0), iteration(1, atmos=0.5)]
    lines.append(stop('2026-11-23 10:00:01'))
    log = write(tmp_path / 'proteus_00.log', lines)
    _, counts, _ = logsplit.split(logsplit.read_segments([log]))
    assert counts['unlogged_s'] == 0.0


def test_notes_on_incomplete_logs(tmp_path):
    """Timing lines before any header, no [INIT_TIMING] line and a torn line are noted."""
    lines = [
        iteration(1, interior=1.0),
        '[ INFO  ] [IT_TIMING] iter=2 init_stage=0 interior=1.0\n',
    ]
    log = write(tmp_path / 'proteus_00.log', lines)
    spans, counts, notes = logsplit.split(logsplit.read_segments([log]))
    assert counts == {'segments': 1, 'iters': 1, 'unlogged_s': None}
    assert notes == [
        'segment 1: no PROTEUS header, so no clock',
        'segment 1: no [INIT_TIMING] line, so init is unlogged',
        'segment 1: 1 unreadable timing lines left out',
    ]


def test_a_log_without_timing_lines(tmp_path):
    """A run without PROTEUS_TIMING has a segment, no spans and a note that says why."""
    log = write(tmp_path / 'proteus_00.log', [RUN[0], RUN[-1]])
    spans, counts, notes = logsplit.split(logsplit.read_segments([log]))
    assert spans == {}
    assert counts['iters'] == 0
    assert notes == ['no [IT_TIMING] line; was PROTEUS_TIMING=1 set?']


def test_a_file_without_header_or_timing_lines_has_no_segment(tmp_path):
    """Other log files (a grid's manager.log) give no segment, so they are no case."""
    log = write(
        tmp_path / 'manager.log', ['[ INFO  ] Simulation stopped at: 2026-11-23 10:00:00\n']
    )
    assert logsplit.read_segments([log]) == []


def test_status_code_of_a_folder_and_of_a_grid_copy(tmp_path):
    """The folder's status file gives the code; a grid's log copy finds it at <grid>/<case>."""
    own = write(tmp_path / 'case_1' / 'proteus_00.log', RUN)
    write(tmp_path / 'case_1' / 'status', ['11\n', 'Completed (maximum clock runtime)\n'])
    copy = write(tmp_path / 'logs' / 'case_2.log', RUN)
    write(tmp_path / 'case_2' / 'status', ['25\n'])
    lone = write(tmp_path / 'logs' / 'case_3.log', RUN)
    assert [logsplit.status_code([p]) for p in (own, copy, lone)] == [11, 25, None]


def test_malformed_status_file_is_an_error(tmp_path):
    """A status file without a code on its first line stops the command and names the file."""
    log = write(tmp_path / 'case_1' / 'proteus_00.log', RUN)
    write(tmp_path / 'case_1' / 'status', ['Running\n'])
    with pytest.raises(ValueError, match=r"case_1/status: expected a status code.*'Running'"):
        logsplit.status_code([log])


def test_case_row_columns_add_up_to_the_clock(tmp_path):
    """The row's span columns and unlogged sum to its total, and span rows keep calls and maxima."""
    log = write(tmp_path / 'proteus_00.log', RUN)
    row, span_rows = logsplit.case_row('run', [log])
    assert row['total_s'] == pytest.approx(180.0, abs=TOL)
    assert row['unlogged'] == pytest.approx(14.0, abs=TOL)
    assert row['loop.interior'] == pytest.approx(50.0, abs=TOL)
    assert {
        'case': 'run',
        'phase': 'loop',
        'module': 'interior',
        'total_s': 50.0,
        'calls': 2,
        'max_s': 30.0,
    } in span_rows


def case(total: float, **columns) -> dict:
    return {'case': f'c{total}', 'iters': 1, 'total_s': total} | columns


def test_summary_of_three_cases():
    """Spread shares 67/70 and 3/70 (worked by hand), thirds of one case, correlation 0.327."""
    rows = [case(10.0, a=2.0, b=8.0), case(20.0, a=8.0, b=12.0), case(40.0, a=30.0, b=10.0)]
    rows.append({'case': 'empty', 'iters': 0, 'total_s': 1000.0, 'a': 1000.0})
    by_column = {s['column']: s for s in logsplit.summary(rows, ['b', 'a'])}
    assert list(by_column) == ['a', 'b']
    assert by_column['a']['spread_share'] == pytest.approx(67 / 70, abs=1e-4)
    assert by_column['b']['spread_share'] == pytest.approx(3 / 70, abs=1e-4)
    assert by_column['a']['fast_share'] == pytest.approx(0.2, abs=1e-4)
    assert by_column['a']['slow_share'] == pytest.approx(0.75, abs=1e-4)
    assert by_column['b']['corr'] == pytest.approx(10 / (2 * (700 / 3) ** 0.5), abs=1e-4)
    assert by_column['a']['median_s'] == pytest.approx(8.0, abs=TOL)


def test_summary_without_spread():
    """Equal totals give no correlation and no spread share; a constant column no correlation."""
    rows = [case(10.0, a=2.0, b=8.0), case(10.0, a=2.0, b=8.0)]
    (a,) = logsplit.summary(rows, ['a'])
    assert (a['corr'], a['spread_share']) == (None, None)
    rows = [case(10.0, a=2.0), case(20.0, a=2.0)]
    (a,) = logsplit.summary(rows, ['a'])
    assert a['corr'] is None
    assert a['spread_share'] == pytest.approx(0.0, abs=TOL)
