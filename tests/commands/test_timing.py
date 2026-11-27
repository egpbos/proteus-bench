"""Tests for ``proteus-bench timing`` (proteus_bench.commands.timing) and its page.

Contract clauses: the command reads a grid folder's cases and writes cases.csv,
spans.csv, summary.csv and timing.html with its assets; log files without a
PROTEUS header or timing line are no case; columns are ordered by phase, then by
summed time, unlogged last; no case at all exits 1 with a message; a non-empty
output folder is refused. The page stacks init and first-iteration spans as one
block each and draws one bar per case with iterations, by total time.
"""

from __future__ import annotations

import csv

import pytest

from proteus_bench import cli
from proteus_bench.commands.timing import ordered_columns
from proteus_bench.report import timingpage

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

TOL = 1e-9


def log(start: str, end: str, interior: float, atmos: float) -> str:
    return (
        f'[ INFO  ] Current time      2026-11-23 {start} \n'
        '[ INFO  ] [INIT_TIMING] structure=10.000 total=10.000\n'
        '[ INFO  ] [IT_TIMING] iter=1 init_stage=1 atmos=20.000 total=20.000\n'
        f'[ INFO  ] [IT_TIMING] iter=2 init_stage=0 interior={interior:.3f} '
        f'atmos={atmos:.3f} total={interior + atmos:.3f}\n'
        f'[ INFO  ] Simulation stopped at: 2026-11-23 {end} \n'
    )


@pytest.fixture
def grid(tmp_path):
    """A slow case in its own folder, a fast one as a log copy, and a manager log."""
    root = tmp_path / 'grid'
    (root / 'case_000001').mkdir(parents=True)
    (root / 'logs').mkdir()
    # 30 s logged before the second iteration and 2 s unlogged in each case
    (root / 'case_000001' / 'proteus_00.log').write_text(
        log('10:00:00', '10:10:02', 500.0, 70.0)
    )
    (root / 'case_000001' / 'status').write_text('10\nCompleted (solidified)\n')
    (root / 'logs' / 'case_000002.log').write_text(log('10:00:00', '10:01:42', 40.0, 30.0))
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
    assert float(cases['case_000001']['total_s']) == pytest.approx(602.0, abs=TOL)
    assert float(cases['case_000002']['unlogged']) == pytest.approx(2.0, abs=TOL)
    # Totals 602 and 102 s: interior differs by 460 s and atmos by 40 s, so 0.92 and 0.08
    summary = {r['column']: r for r in read(out / 'summary.csv')}
    assert float(summary['loop.interior']['spread_share']) == pytest.approx(0.92, abs=1e-4)
    assert float(summary['init.structure']['spread_share']) == pytest.approx(0.0, abs=TOL)
    assert len(read(out / 'spans.csv')) == 2 * 4
    assert 'case_000002' in (out / 'timing.html').read_text()
    assert 'loop.interior 0.92, loop.atmos 0.08' in capsys.readouterr().out


def test_column_order():
    """Phases in run order; within one, the largest summed time first; unlogged last."""
    rows = [
        {'case': 'a', 'total_s': 9.0, 'unlogged': 1.0, 'loop.atmos': 1.0, 'loop.interior': 3.0},
        {
            'case': 'b',
            'total_s': 9.0,
            'first.atmos': 2.0,
            'init.structure': 1.0,
            'loop.atmos': 3.0,
        },
    ]
    assert ordered_columns(rows) == [
        'init.structure',
        'first.atmos',
        'loop.atmos',
        'loop.interior',
        'unlogged',
    ]


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
        {'case': 'slow', 'iters': 2, 'total_s': 9.0, 'loop.interior': 9.0},
        {'case': 'none', 'iters': 0, 'total_s': 5.0, 'unlogged': 5.0},
        {'case': 'fast', 'iters': 2, 'total_s': 3.0, 'loop.interior': 2.0, 'unlogged': 1.0},
    ]
    fig = timingpage.figure(rows, ['loop.interior', 'unlogged'], 'light')
    assert [bar.name for bar in fig.data] == ['interior', 'unlogged']
    assert list(fig.data[0].x) == ['fast', 'slow']
    assert list(fig.data[1].y) == [1.0, 0.0]
