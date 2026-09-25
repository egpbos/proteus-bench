"""Tests for reading the results store (proteus_bench.report.store).

Contract clauses: records load from any year directory, oldest first; broken
JSON, a foreign schema, a missing or mistyped required field, an unsafe run id
(full match only) and a duplicated run id are refused with the file named;
symlinks and paths that leave the store are refused, for records and for
artifacts; artifacts link to the results branch only when a repository is
known and the file exists; a repository name that is not owner/name is refused.
"""

from __future__ import annotations

import json

import pytest

from proteus_bench.report.store import (
    artifact_source,
    artifacts_of,
    field_problems,
    load_records,
    results_url,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def write_record(store, record, name=None):
    path = store / 'records' / '2026' / f'{name or record["run_id"]}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return path


def test_records_load_oldest_first(store, records):
    """Ten records come back sorted by start time, whatever the file order."""
    first = store / 'records' / '2026' / f'{records[0]["run_id"]}.json'
    first.rename(store / 'records' / 'zz.json')
    loaded = load_records(store)
    assert [r['run_id'] for r in loaded] == [r['run_id'] for r in records]
    assert loaded[0]['trigger']['started_at'] < loaded[-1]['trigger']['started_at']


@pytest.mark.parametrize(
    ('content', 'message'),
    [
        ('{"schema": ', 'not valid JSON'),
        ('[1, 2]', "found 'list'"),
        (
            '{"schema": "proteus-bench/2", "run_id": "20260101T000000Z-x"}',
            "found 'proteus-bench/2'",
        ),
    ],
)
def test_unreadable_records_are_refused_with_the_file(tmp_path, content, message):
    """Each problem raises ValueError naming the file, instead of skipping the record."""
    bad = tmp_path / 'records' / '2026' / 'bad.json'
    bad.parent.mkdir(parents=True)
    bad.write_text(content)
    with pytest.raises(ValueError, match=message) as err:
        load_records(tmp_path)
    assert 'bad.json' in str(err.value)


@pytest.mark.parametrize(
    'run_id',
    [
        '../../index',
        '20260101T000000Z-ok\n',  # re.match with '$' would accept the trailing newline
        '20260101T000000Z-ok/../../x',  # re.match without '$' would accept the suffix
        'x20260101T000000Z-ok',
        '20260101T000000Z-UPPER',
    ],
)
def test_run_id_must_match_in_full(tmp_path, records, run_id):
    """Only a full match of the run-id pattern is safe as a page file name."""
    records[0]['run_id'] = run_id
    write_record(tmp_path, records[0], name='r')
    with pytest.raises(ValueError, match='does not match') as err:
        load_records(tmp_path)
    assert 'r.json' in str(err.value)


def test_missing_and_mistyped_fields_are_named(tmp_path, records):
    """A record without timings.wall_s, or with a string for it, is refused with the field."""
    del records[0]['timings']['wall_s']
    records[0]['comparability']['ok'] = 'yes'
    assert field_problems(records[0]) == [
        'comparability.ok has type str',
        'missing timings.wall_s',
    ]
    records[1]['timings']['wall_s'] = True  # a bool is not a number of seconds
    assert field_problems(records[1]) == ['timings.wall_s has type bool']
    write_record(tmp_path, records[0])
    with pytest.raises(ValueError, match='missing timings.wall_s') as err:
        load_records(tmp_path)
    assert records[0]['run_id'] in str(err.value)
    assert field_problems(records[2]) == []


def test_duplicate_run_ids_are_refused(tmp_path, records):
    """Two files with one run id would share a run page; the second file is named."""
    write_record(tmp_path, records[0], name='a')
    write_record(tmp_path, records[0], name='b')
    with pytest.raises(ValueError, match='also in') as err:
        load_records(tmp_path)
    assert 'b.json' in str(err.value)


def test_symlinked_record_is_refused(tmp_path, records):
    """A record symlinked to a file outside the store is never read."""
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps(records[0]))
    link = tmp_path / 'store' / 'records' / '2026' / 'r.json'
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    with pytest.raises(ValueError, match='symlinks are not allowed'):
        load_records(tmp_path / 'store')
    assert outside.is_file()


def test_symlinked_records_directory_is_refused(tmp_path, records):
    """A directory symlink that leads out of the store is caught by the resolved path."""
    write_record(tmp_path / 'elsewhere', records[0])
    (tmp_path / 'store').mkdir()
    (tmp_path / 'store' / 'records').symlink_to(tmp_path / 'elsewhere' / 'records')
    with pytest.raises(ValueError, match='resolves outside the store'):
        load_records(tmp_path / 'store')
    assert len(list((tmp_path / 'store' / 'records').glob('**/*.json'))) == 1


@pytest.mark.parametrize('path', ['../x.log', '/etc/passwd', 'logs/../../x', '', 7])
def test_artifact_paths_stay_in_the_store(tmp_path, path):
    """Absolute paths, parent references, empty paths and non-strings are refused."""
    with pytest.raises(ValueError, match='stay inside the store'):
        artifact_source(tmp_path, path)
    assert artifact_source(tmp_path, 'logs/2026/a.log') == tmp_path / 'logs' / '2026' / 'a.log'


def test_symlinked_artifacts_are_refused(tmp_path):
    """A file symlink, or a directory symlink leading out, is refused even if the path looks safe."""
    store = tmp_path / 'store'
    (store / 'logs').mkdir(parents=True)
    secret = tmp_path / 'secret.txt'
    secret.write_text('secret')
    (store / 'logs' / 'a.log').symlink_to(secret)
    with pytest.raises(ValueError, match='symlinks are not allowed'):
        artifact_source(store, 'logs/a.log')
    (store / 'spans').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='resolves outside the store'):
        artifact_source(store, 'spans/secret.txt')


def test_artifact_links_need_a_repository_and_the_file(store, records):
    """r2: its spans link to the results branch; its log is missing; without a repo, no links."""
    r2 = records[1]
    by_name = {a.name: a for a in artifacts_of(r2, store, 'egpbos/proteus-bench')}
    spans = f'spans/2026/{r2["run_id"]}.timing.jsonl.gz'
    assert (
        by_name['spans'].url == f'https://github.com/egpbos/proteus-bench/blob/results/{spans}'
    )
    assert by_name['log'].present is False
    assert by_name['log'].url is None
    assert all(a.url is None for a in artifacts_of(r2, store, None))


def test_artifact_errors_name_the_run(store, records):
    """A bad artifact path in a record is reported with the record's run id."""
    records[0]['artifacts'] = {'log': '../../etc/passwd'}
    with pytest.raises(ValueError, match=records[0]['run_id']):
        artifacts_of(records[0], store, None)
    records[0]['artifacts'] = ['not', 'an', 'object']
    with pytest.raises(ValueError, match='artifacts must be an object'):
        artifacts_of(records[0], store, None)


@pytest.mark.parametrize('repo', ['egpbos', 'a/b/c', 'javascript:alert(1)//x', 'a b/c'])
def test_repository_must_be_owner_slash_name(repo):
    """Only owner/name forms a results URL; the path part is percent-encoded."""
    with pytest.raises(ValueError, match='owner/name'):
        results_url(repo, 'logs/a.log')
    assert (
        results_url('o/n', 'logs/a b#c.log')
        == 'https://github.com/o/n/blob/results/logs/a%20b%23c.log'
    )
