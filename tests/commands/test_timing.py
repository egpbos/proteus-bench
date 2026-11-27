"""Tests for ``proteus-bench timing`` (proteus_bench.commands.timing).

Contract clauses: the command reads a grid folder's cases and writes cases.csv,
spans.csv and summary.csv, an empty cell where a case lacks a span, and one line
naming the cases, the noted ones and the three largest spread shares; a fake
PROTEUS run is read with its clock and simulated time; log files without a PROTEUS
header or timing line are no case; columns are ordered by phase, then by summed
time, unlogged last and only when a case has it; no case at all, or a non-empty
output folder, exits 1 with a message and leaves the folder as it was.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime

import pytest

from proteus_bench import cli
from proteus_bench.commands.timing import ordered_columns

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
    (root / 'case_000001' / 'runtime_helpfile.csv').write_text('Time\n0.0\n1.2e5\n')
    killed = root / 'logs' / 'case_000002.log'
    killed.write_text(log(40.0, 30.0))
    end = datetime(2026, 11, 23, 10, 1, 42).timestamp()
    os.utime(killed, (end, end))
    (root / 'manager.log').write_text('[ INFO  ] grid started\n')
    return root


def read(path) -> list[dict]:
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def test_writes_the_tables(grid, tmp_path, capsys):
    """Two cases, slowest last in no particular file order; interior carries the spread."""
    out = tmp_path / 'out'
    assert cli.main(['timing', str(grid), '--out', str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ['cases.csv', 'spans.csv', 'summary.csv']
    cases = {r['case']: r for r in read(out / 'cases.csv')}
    assert list(cases) == ['case_000001', 'case_000002']
    assert cases['case_000001']['status'] == '10'
    assert [r['loop.escape'] for r in cases.values()] == ['0.0', '']
    assert float(cases['case_000001']['total_s']) == pytest.approx(602.0, abs=TOL)
    assert float(cases['case_000002']['unlogged']) == pytest.approx(2.0, abs=TOL)
    # 1.2e5 yr in 602 s of clock
    rate = float(cases['case_000001']['sim_yr_per_hour'])
    assert rate == pytest.approx(1.2e5 * 3600 / 602, abs=1e-3)
    assert [cases['case_000002'][k] for k in ('sim_time_yr', 'sim_yr_per_hour')] == ['', '']
    # Totals 602 and 102 s: interior differs by 460 s and atmos by 40 s, so 0.92 and 0.08
    summary = {r['column']: r for r in read(out / 'summary.csv')}
    assert float(summary['loop.interior']['spread_share']) == pytest.approx(0.92, abs=1e-4)
    assert float(summary['loop.interior']['corr']) == pytest.approx(1.0, abs=1e-4)
    assert float(summary['init.structure']['spread_share']) == pytest.approx(0.0, abs=TOL)
    assert len(read(out / 'spans.csv')) == 4 + 1 + 4
    assert capsys.readouterr().out == (
        f'{out}: 2 cases, 1 with notes; largest spread shares: '
        'loop.interior 0.92, loop.atmos 0.08, init.structure 0.00\n'
    )


def test_reads_a_fake_run(run_fake, tmp_path):
    """The stub's start and stop lines give the clock around its synthetic seconds."""
    code, outdir, _ = run_fake(iters=4)
    assert code == 0
    out = tmp_path / 'out'
    assert cli.main(['timing', str(outdir), '--out', str(out)]) == 0
    (case,) = read(out / 'cases.csv')
    # init 1138 + 632 + 2 = 1772 s; iterations 784.7 + 19.7 + 159.7 + 19.7 = 983.8 s;
    # 5 s of shutdown, so the stop line reads 2760 whole seconds after the start
    assert float(case['total_s']) == pytest.approx(2760.0, abs=TOL)
    assert float(case['unlogged']) == pytest.approx(2760 - 1772 - 983.8, abs=1e-6)
    assert float(case['first.atmos']) == pytest.approx(780.0, abs=TOL)
    assert float(case['sim_yr_per_hour']) == pytest.approx(4000 * 3600 / 2760, abs=1e-3)
    assert case['notes'] == ''


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


def test_out_is_required(grid):
    """Without --out the command stops before reading anything."""
    with pytest.raises(SystemExit):
        cli.main(['timing', str(grid)])


def test_no_case_exits_1(tmp_path, capsys):
    """A folder with only non-PROTEUS logs is reported, not turned into empty tables."""
    (tmp_path / 'manager.log').write_text('grid started\n')
    assert cli.main(['timing', str(tmp_path), '--out', str(tmp_path / 'out')]) == 1
    assert 'no log with a PROTEUS header or timing line' in capsys.readouterr().out
    assert not (tmp_path / 'out').exists()


def test_refuses_a_non_empty_output(grid, tmp_path, capsys):
    """Old tables in the output folder would mix with the new ones, so they stay as they are."""
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'cases.csv').write_text('old\n')
    assert cli.main(['timing', str(grid), '--out', str(out)]) == 1
    assert 'is not empty' in capsys.readouterr().out
    assert (out / 'cases.csv').read_text() == 'old\n'
