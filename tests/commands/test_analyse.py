"""Tests for ``proteus-bench analyse`` (proteus_bench.commands.analyse).

Contract clauses: every ``records/**/*.json`` under the store is read, at any
depth including directly in ``records/``; the output file holds the analysis
structure; an empty store, a file that is not UTF-8 JSON and a record with a
missing or non-numeric field exit 1 with a message naming the problem.
"""

from __future__ import annotations

import json

import pytest

from proteus_bench import cli, schema

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

_DIRS = ('', '2026', '2026/01')


def _store(tmp_path, records):
    """Write records into the store, spread over three directory depths."""
    for i, record in enumerate(records):
        folder = tmp_path / 'records' / _DIRS[i % len(_DIRS)]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{record["run_id"]}.json').write_text(json.dumps(record))
    return tmp_path


def test_analyse_writes_series_for_all_stored_records(tmp_path, make_records, capsys):
    """Six records at three depths give time-ordered series and one flag."""
    records = make_records([1.0, 1.0, 1.0, 1.0, 1.0, 1.2])
    pytest.importorskip('jsonschema')
    assert schema.shape_problems('record', records[0]) == []  # generator stays valid
    store = _store(tmp_path, records)
    assert len(list((store / 'records' / '2026' / '01').glob('*.json'))) == 2
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
    """No records, malformed JSON, non-UTF-8 bytes and bad fields each exit 1 with a reason."""
    out = tmp_path / 'a.json'
    assert cli.main(['analyse', str(tmp_path), '--out', str(out)]) == 1
    assert 'no run records found' in capsys.readouterr().err
    store = _store(tmp_path, make_records([1.0]))
    broken = store / 'records' / 'torn.json'
    cases = [
        (b'{"run_id": ', 'torn.json: not valid UTF-8 JSON'),
        (b'{"run_id": "caf\xe9"}', 'torn.json: not valid UTF-8 JSON'),
        (b'{"timings": {"wall_s": NaN}}', 'torn.json: not valid UTF-8 JSON (NaN is not'),
    ]
    record = make_records([1.0])[0]
    record['timings']['wall_s'] = None
    cases.append((json.dumps(record | {'run_id': 'no-wall'}).encode(), "'no-wall': malformed"))
    del record['timings']
    no_timings = json.dumps(record | {'run_id': 'no-timings'}).encode()
    cases.append((no_timings, "'no-timings': missing field"))
    for content, message in cases:
        broken.write_bytes(content)
        assert cli.main(['analyse', str(store), '--out', str(out)]) == 1
        assert message in capsys.readouterr().err
    assert not out.exists()
