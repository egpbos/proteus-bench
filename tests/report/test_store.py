"""Tests for reading the results store (proteus_bench.report.store).

Contract clauses: records load from any year directory, oldest first; broken
JSON, a foreign schema and an unsafe run id are refused with the file named;
artifact paths may not leave the store; artifacts the store lacks are reported
as missing, the others are copied under the site's files directory.
"""

from __future__ import annotations

import json

import pytest

from proteus_bench.report.store import artifact_source, copy_artifacts, load_records

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_records_load_oldest_first(store, records):
    """Ten records come back sorted by start time, whatever the file order."""
    (store / 'records' / '2026' / f'{records[0]["run_id"]}.json').rename(
        store / 'records' / 'zz.json'
    )
    loaded = load_records(store)
    assert [r['run_id'] for r in loaded] == [r['run_id'] for r in records]
    assert loaded[0]['trigger']['started_at'] < loaded[-1]['trigger']['started_at']


@pytest.mark.parametrize(
    ('content', 'message'),
    [
        ('{"schema": ', 'not valid JSON'),
        (
            '{"schema": "proteus-bench/2", "run_id": "20260101T000000Z-x"}',
            "found 'proteus-bench/2'",
        ),
        ('{"schema": "proteus-bench/1", "run_id": "../../index"}', 'does not match'),
    ],
)
def test_bad_records_are_refused_with_the_file(tmp_path, content, message):
    """Each problem raises ValueError naming the file, instead of skipping the record."""
    bad = tmp_path / 'records' / '2026' / 'bad.json'
    bad.parent.mkdir(parents=True)
    bad.write_text(content)
    with pytest.raises(ValueError, match=message) as err:
        load_records(tmp_path)
    assert 'bad.json' in str(err.value)


@pytest.mark.parametrize('path', ['../x.log', '/etc/passwd', 'logs/../../x', ''])
def test_artifact_paths_stay_in_the_store(tmp_path, path):
    """Absolute paths, parent references and empty paths are refused."""
    with pytest.raises(ValueError, match='stay inside the store'):
        artifact_source(tmp_path, path)
    assert artifact_source(tmp_path, 'logs/2026/a.log') == tmp_path / 'logs' / '2026' / 'a.log'


def test_copy_reports_missing_artifacts(tmp_path, store, records):
    """r2's log is not in the store: None for it, a copied file for its spans."""
    r2 = records[1]
    copied = copy_artifacts(r2, store, tmp_path / 'site' / 'files')
    assert copied['log'] is None
    assert copied['spans'] == f'files/spans/2026/{r2["run_id"]}.timing.jsonl.gz'
    assert (tmp_path / 'site' / copied['spans']).read_text() == f'spans of {r2["run_id"]}\n'
    assert json.loads(json.dumps(copied))  # plain data, usable in pages
