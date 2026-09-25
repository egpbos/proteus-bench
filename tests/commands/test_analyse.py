"""Tests for ``proteus-bench analyse`` (proteus_bench.commands.analyse).

Contract clauses: every ``records/**/*.json`` under the store is read, at any
depth; the output file holds the ``proteus-bench-analysis/1`` structure; an empty
store, unreadable JSON or a record without a required field exit 1 with a
message naming the problem.
"""

from __future__ import annotations

import json

import pytest

from proteus_bench import cli, schema

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def _store(tmp_path, records):
    """Write records the way the results store lays them out, one year deeper for some."""
    for i, record in enumerate(records):
        year = tmp_path / 'records' / ('2025' if i % 2 else '2026')
        year.mkdir(parents=True, exist_ok=True)
        (year / f'{record["run_id"]}.json').write_text(json.dumps(record))
    return tmp_path


def test_analyse_writes_series_for_all_stored_records(tmp_path, make_records, capsys):
    """Six stored records across two directories give time-ordered series and one flag."""
    records = make_records([1.0, 1.0, 1.0, 1.0, 1.0, 1.2])
    assert schema.shape_problems('record', records[0]) in ([], None)  # generator stays valid
    store = _store(tmp_path, records)
    out = tmp_path / 'analysis.json'
    code = cli.main(['analyse', str(store), '--out', str(out)])
    stdout = capsys.readouterr().out
    assert code == 0
    result = json.loads(out.read_text())
    assert result['schema'] == 'proteus-bench-analysis/1'
    total = next(s for s in result['series'] if s['key']['metric'] == 'total')
    assert [p['run_id'][-6:] for p in total['points']] == [f'run00{i}' for i in range(6)]
    assert [f['run_id'] for f in total['flags']] == ['20260106T031000Z-run005']
    assert total['key'] == {
        'benchmark': 'all_options',
        'lineage': 'default',
        'machine_class': 'habrok-vink',
        'metric': 'total',
    }
    assert stdout.startswith(f'6 records, {len(result["series"])} series, ')


def test_analyse_reports_missing_and_broken_input(tmp_path, make_records, capsys):
    """No records, a malformed file and a record without timings each exit 1 with a reason."""
    assert cli.main(['analyse', str(tmp_path), '--out', str(tmp_path / 'a.json')]) == 1
    assert 'no run records found' in capsys.readouterr().err
    store = _store(tmp_path, make_records([1.0]))
    broken = store / 'records' / '2026' / 'torn.json'
    broken.write_text('{"run_id": ')
    assert cli.main(['analyse', str(store), '--out', str(tmp_path / 'a.json')]) == 1
    assert 'torn.json: not valid JSON' in capsys.readouterr().err
    record = make_records([1.0])[0]
    del record['timings']
    broken.write_text(json.dumps(record | {'run_id': 'no-timings'}))
    assert cli.main(['analyse', str(store), '--out', str(tmp_path / 'a.json')]) == 1
    assert "record 'no-timings': missing field 'timings'" in capsys.readouterr().err
    assert not (tmp_path / 'a.json').exists()
