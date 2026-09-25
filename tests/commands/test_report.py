"""Tests for ``proteus-bench report`` (proteus_bench.commands.report).

Contract clauses: the command loads every record of the store, passes them to
``proteus_bench.analysis.analyse`` and writes the site; a missing store exits 1
with a message; an analysis of the wrong schema stops the build loudly. The
analysis module is replaced by a stand-in, so these tests do not depend on it.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from proteus_bench import cli

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

EXAMPLE = Path(__file__).parent.parent.parent / 'examples' / 'record.json'


@pytest.fixture
def analysed(monkeypatch):
    """Install a stand-in analysis module; return the list of record lists it was given."""
    calls: list[list[dict]] = []
    result = {'schema': 'proteus-bench-analysis/1', 'series': []}

    def analyse(records):
        calls.append(records)
        return result

    module = types.ModuleType('proteus_bench.analysis')
    module.analyse = analyse
    monkeypatch.setitem(sys.modules, 'proteus_bench.analysis', module)
    return calls, result


def test_report_builds_site_from_store(tmp_path, analysed, capsys):
    """One record in the store: analyse sees it, and overview, compare and run pages exist."""
    record = json.loads(EXAMPLE.read_text())
    path = tmp_path / 'store' / 'records' / '2026' / f'{record["run_id"]}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    code = cli.main(
        ['report', '--store', str(tmp_path / 'store'), '--out', str(tmp_path / 'site')]
    )
    calls, _ = analysed
    assert code == 0
    assert [[r['run_id'] for r in c] for c in calls] == [[record['run_id']]]
    assert (tmp_path / 'site' / 'runs' / f'{record["run_id"]}.html').is_file()
    assert '4 pages from 1 runs' in capsys.readouterr().out  # index, compare, series, run


def test_missing_store_and_wrong_analysis(tmp_path, analysed, capsys):
    """No store: exit 1 and say so. Wrong analysis schema: ValueError, nothing half-built."""
    code = cli.main(
        ['report', '--store', str(tmp_path / 'nope'), '--out', str(tmp_path / 'site')]
    )
    assert code == 1
    assert 'is not a directory' in capsys.readouterr().out
    (tmp_path / 'store' / 'records').mkdir(parents=True)
    analysed[1]['schema'] = 'something-else/1'
    with pytest.raises(ValueError, match='proteus-bench-analysis/1'):
        cli.main(
            ['report', '--store', str(tmp_path / 'store'), '--out', str(tmp_path / 'site')]
        )
    assert not (tmp_path / 'site').exists()


def test_repository_comes_from_the_environment(tmp_path, analysed, monkeypatch, capsys):
    """GITHUB_REPOSITORY is the default; an explicit empty --repo turns links off; junk is refused."""
    record = json.loads(EXAMPLE.read_text())
    path = tmp_path / 'store' / 'records' / '2026' / f'{record["run_id"]}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    (tmp_path / 'store' / record['artifacts']['log']).parent.mkdir(parents=True)
    (tmp_path / 'store' / record['artifacts']['log']).write_text('log')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/n')
    base = ['report', '--store', str(tmp_path / 'store'), '--out', str(tmp_path / 'site')]
    assert cli.main(base) == 0
    assert 'artifact links to o/n' in capsys.readouterr().out
    run_page = (tmp_path / 'site' / 'runs' / f'{record["run_id"]}.html').read_text()
    assert f'https://github.com/o/n/blob/results/{record["artifacts"]["log"]}' in run_page
    assert cli.main([*base, '--repo', '']) == 0
    assert 'no repository, so no artifact links' in capsys.readouterr().out
    with pytest.raises(ValueError, match='owner/name'):
        cli.main([*base, '--repo', 'javascript:x'])
