"""Tests for ``proteus-bench timing`` (proteus_bench.commands.timing) and its page.

Contract clauses: the command reads a grid folder's cases and writes cases.csv,
spans.csv, summary.csv and timing.html with its assets, an empty cell where a case
lacks a span, and one line naming the cases, the noted ones and the three largest
spread shares; log files without a PROTEUS header or timing line are no case;
columns are ordered by phase, then by summed time, unlogged last and only when a
case has it; no case at all exits 1 with a message; a non-empty output folder is
refused. The page stacks init and first-iteration spans as one block each, draws
one bar per case with iterations by total time, colours init and unlogged with the
phase fills, hatches first iterations and gives components their module colours.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime

import proteus_plotly
import pytest

from proteus_bench import cli
from proteus_bench.commands.timing import ordered_columns
from proteus_bench.report import timingpage

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

TOL = 1e-9


def log(interior: float, atmos: float, extra: str = '') -> str:
    return (
        '[ INFO  ] Current time      2026-11-23 10:00:00 \n'
        '[ INFO  ] [INIT_TIMING] structure=10.000 total=10.000\n'
        '[ INFO  ] [IT_TIMING] iter=1 init_stage=1 atmos=20.000 total=20.000\n'
        f'[ INFO  ] [IT_TIMING] iter=2 init_stage=0 interior={interior:.3f} '
        f'atmos={atmos:.3f}{extra} total={interior + atmos:.3f}\n'
    )


@pytest.fixture
def grid(tmp_path):
    """A slow stopped case in its own folder, a fast killed one as a log copy, a manager log."""
    root = tmp_path / 'grid'
    (root / 'case_000001').mkdir(parents=True)
    (root / 'logs').mkdir()
    # 30 s logged before the second iteration and 2 s unlogged in each case
    stopped = log(500.0, 70.0, ' escape=0.000') + (
        '[ INFO  ] Simulation stopped at: 2026-11-23 10:10:02 \n'
    )
    (root / 'case_000001' / 'proteus_00.log').write_text(stopped)
    (root / 'case_000001' / 'status').write_text('10\nCompleted (solidified)\n')
    killed = root / 'logs' / 'case_000002.log'
    killed.write_text(log(40.0, 30.0))
    end = datetime(2026, 11, 23, 10, 1, 42).timestamp()
    os.utime(killed, (end, end))
    (root / 'manager.log').write_text('[ INFO  ] grid started\n')
    return root


def read(path) -> list[dict]:
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def test_writes_the_tables_and_page(grid, tmp_path, capsys):
    """Two cases, slowest last in no particular file order; interior carries the spread."""
    out = tmp_path / 'out'
    assert cli.main(['timing', str(grid), '--out', str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        'cases.csv',
        'charts.js',
        'spans.csv',
        'style.css',
        'summary.csv',
        'timing.html',
    ]
    cases = {r['case']: r for r in read(out / 'cases.csv')}
    assert list(cases) == ['case_000001', 'case_000002']
    assert cases['case_000001']['status'] == '10'
    assert (cases['case_000001']['loop.escape'], cases['case_000002']['loop.escape']) == (
        '0.0',
        '',
    )
    assert float(cases['case_000001']['total_s']) == pytest.approx(602.0, abs=TOL)
    assert float(cases['case_000002']['unlogged']) == pytest.approx(2.0, abs=TOL)
    # Totals 602 and 102 s: interior differs by 460 s and atmos by 40 s, so 0.92 and 0.08
    summary = {r['column']: r for r in read(out / 'summary.csv')}
    assert float(summary['loop.interior']['spread_share']) == pytest.approx(0.92, abs=1e-4)
    assert float(summary['loop.interior']['corr']) == pytest.approx(1.0, abs=1e-4)
    assert float(summary['init.structure']['spread_share']) == pytest.approx(0.0, abs=TOL)
    assert len(read(out / 'spans.csv')) == 4 + 1 + 4
    assert 'case_000002' in (out / 'timing.html').read_text()
    assert capsys.readouterr().out == (
        f'{out}: 2 cases, 1 with notes; largest spread shares: '
        'loop.interior 0.92, loop.atmos 0.08, init.structure 0.00\n'
    )


def test_column_order():
    """Phases in run order; within one, the largest summed time first; unlogged last."""
    rows = [
        {'case': 'a', 'total_s': 9.0, 'unlogged': 1.0, 'loop.atmos': 1.0, 'loop.interior': 3.0},
        # summed, atmos has 3.5 s against the 3 s of interior; its last value alone is less
        {
            'case': 'b',
            'total_s': 9.0,
            'first.atmos': 2.0,
            'init.structure': 1.0,
            'loop.atmos': 2.5,
        },
    ]
    assert ordered_columns(rows) == [
        'init.structure',
        'first.atmos',
        'loop.atmos',
        'loop.interior',
        'unlogged',
    ]
    assert ordered_columns([{'case': 'a', 'loop.atmos': 1.0}]) == ['loop.atmos']


def test_no_case_exits_1(tmp_path, capsys):
    """A folder with only non-PROTEUS logs is reported, not turned into empty tables."""
    (tmp_path / 'manager.log').write_text('grid started\n')
    assert cli.main(['timing', str(tmp_path), '--out', str(tmp_path / 'out')]) == 1
    assert 'no log with a PROTEUS header or timing line' in capsys.readouterr().out
    assert not (tmp_path / 'out').exists()


def test_refuses_a_non_empty_output(grid, tmp_path):
    """Old tables in the output folder would mix with the new ones."""
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'cases.csv').write_text('old\n')
    with pytest.raises(ValueError, match='is not empty'):
        cli.main(['timing', str(grid), '--out', str(out)])


def test_stacks_merge_init_and_first_iteration():
    """Init steps and first-iteration components each stack as one block."""
    row = {'init.structure': 3.0, 'init.other': 1.0, 'first.atmos': 5.0, 'loop.atmos': 2.0}
    row['unlogged'] = 4.0
    assert timingpage.stacks(row, [*row]) == pytest.approx(
        {'init': 4.0, 'first iteration': 5.0, 'atmos': 2.0, 'unlogged': 4.0}, abs=TOL
    )


def test_figure_has_one_bar_per_timed_case_by_total():
    """Cases without iterations are left out; bars run from the fastest case to the slowest."""
    rows = [
        {
            'case': 'slow',
            'iters': 2,
            'total_s': 12.0,
            'init.structure': 3.0,
            'loop.interior': 9.0,
        },
        {'case': 'none', 'iters': 0, 'total_s': 5.0, 'unlogged': 5.0},
        {'case': 'fast', 'iters': 2, 'total_s': 3.0, 'first.atmos': 2.0, 'unlogged': 1.0},
    ]
    columns = ['init.structure', 'first.atmos', 'loop.interior', 'unlogged']
    fig = timingpage.figure(rows, columns, 'light')
    assert [bar.name for bar in fig.data] == ['init', 'first iteration', 'interior', 'unlogged']
    assert list(fig.data[0].x) == ['fast', 'slow']
    assert list(fig.data[3].y) == [1.0, 0.0]
    assert fig.data[3].hovertext[0] == 'fast, unlogged: 1.0 s of 3.0 s'


def test_figure_colours():
    """Init and unlogged take the phase fills, first iterations are hatched, interior is Aragog's."""
    colors = proteus_plotly.colors('light')
    aragog = proteus_plotly.module_markers(['aragog'], 'light')['aragog']['color']
    spans = {'init.data': 1.0, 'first.atmos': 1.0, 'loop.interior': 1.0, 'unlogged': 1.0}
    row = {'case': 'a', 'iters': 2, 'total_s': 4.0} | spans
    init, first, interior, unlogged = timingpage.figure([row], [*spans], 'light').data
    assert init.marker.color == colors['text-d2']
    assert first.marker.pattern.shape == '/'
    assert interior.marker.color == aragog
    assert unlogged.marker.color == colors['text-d']
