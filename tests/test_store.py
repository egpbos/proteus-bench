"""Tests for proteus_bench.store against docs/interface.md, section "Results store".

Contract clauses: required files per the Artifacts table (spans and settings
only for ok runs); the record's settings hash must match its
init_coupler.toml; run_id and settings_hash must fully match the schema
patterns, and the fields that become paths are type-checked even without
jsonschema; artifacts may only name ARTIFACTS entries whose file exists;
symbolic links are refused; staging writes the documented store paths,
gzips spans and logs reproducibly, keeps one settings file per hash, copies
all of profile/, and rewrites artifacts to store paths.
"""

from __future__ import annotations

import gzip
import json
import os

import pytest

from proteus_bench import schema, store

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

RUN_A = '20260925T031000Z-habrok-default-a1b2'
RUN_B = '20260926T031000Z-habrok-default-c3d4'


def _record(run_dir):
    return json.loads((run_dir / 'record.json').read_text())


def _rewrite(run_dir, edit):
    record = _record(run_dir)
    edit(record)
    (run_dir / 'record.json').write_text(json.dumps(record))


def test_good_run_passes_and_missing_files_are_named(make_run_dir):
    """A complete run has no problems; removing required files names each one."""
    pytest.importorskip('jsonschema')
    run_dir = make_run_dir(RUN_A, artifacts={})
    record, problems, shape_checked = store.check_run(run_dir)
    assert problems == []
    assert shape_checked is True
    assert record['run_id'] == RUN_A
    for name in ('timing.jsonl', 'log.txt', 'config.toml', 'init_coupler.toml'):
        (run_dir / name).unlink()
    _, problems, _ = store.check_run(run_dir)
    assert problems == [
        f'{run_dir}: missing {name}'
        for name in ('log.txt', 'config.toml', 'timing.jsonl', 'init_coupler.toml')
    ]


def test_failed_run_needs_no_spans_or_settings(make_run_dir):
    """A run that failed in setup has no timing.jsonl or init_coupler.toml; it still publishes."""
    run_dir = make_run_dir(
        RUN_A, status='failed', artifacts={'log': 'log.txt', 'config': 'config.toml'}
    )
    (run_dir / 'timing.jsonl').unlink()
    (run_dir / 'init_coupler.toml').unlink()
    record, problems, _ = store.check_run(run_dir)
    assert problems == []
    assert store.stored_artifacts(run_dir, record) == {
        'config': f'configs/2026/{RUN_A}.toml',
        'log': f'logs/2026/{RUN_A}.log.gz',
    }
    (run_dir / 'config.toml').unlink()  # the runner always writes it
    assert f'{run_dir}: missing config.toml' in store.check_run(run_dir)[1]


def test_not_a_run_dir_broken_json_and_non_object(tmp_path):
    """No record.json, unparsable JSON and a JSON array are reported, not raised."""
    record, problems, _ = store.check_run(tmp_path)
    assert record is None
    assert 'not a run directory' in problems[0]
    (tmp_path / 'record.json').write_text('{"run_id": ')
    record, problems, _ = store.check_run(tmp_path)
    assert record is None
    assert 'not valid JSON' in problems[0]
    (tmp_path / 'record.json').write_text('[1, 2]')
    assert store.check_run(tmp_path)[1] == [
        f'{tmp_path / "record.json"}: a run record must be a JSON object'
    ]


def test_settings_hash_must_match_init_coupler(make_run_dir):
    """Edited or unparsable settings are refused; the per-run output path does not count."""
    run_dir = make_run_dir(RUN_A)
    path = run_dir / 'init_coupler.toml'
    path.write_text(path.read_text().replace('use_jax = true', 'use_jax = false'))
    _, problems, _ = store.check_run(run_dir)
    assert len(problems) == 1
    assert 'settings hash is sha256:' in problems[0]
    assert 'but record.json says' in problems[0]
    path.write_text('[params\n')
    problems = store.check_run(run_dir)[1]
    assert len(problems) == 1
    assert problems[0].startswith(f'{path}: not valid TOML (')
    run_b = make_run_dir(RUN_B)
    toml = run_b / 'init_coupler.toml'
    toml.write_text(toml.read_text().replace(f'path = "{RUN_B}"', 'path = "elsewhere"'))
    assert store.check_run(run_b)[1] == []


def test_key_fields_checked_without_jsonschema(make_run_dir, monkeypatch):
    """Without jsonschema, path-forming fields and their containers are still checked."""
    monkeypatch.setattr(schema, 'shape_problems', lambda kind, instance: None)
    run_dir = make_run_dir(RUN_A)

    def escape(record):
        record['run_id'] = '../../etc'
        record['benchmark']['settings_hash'] = 'md5:abc'
        del record['outcome']['status']  # decides which files are required

    _rewrite(run_dir, escape)
    _, problems, shape_checked = store.check_run(run_dir)
    assert shape_checked is False
    assert [p.split(': ', 1)[1].split(' ')[0] for p in problems] == [
        'run_id',
        'settings_hash',
        'outcome.status',
    ]
    for key, value in (('benchmark', 'all_options'), ('artifacts', ['timing.jsonl'])):
        run = make_run_dir(f'20260927T000000Z-{key}')
        _rewrite(run, lambda r, k=key, v=value: r.update({k: v}))
        assert store.check_run(run)[1][0].endswith(f'{key} must be an object'), key


def test_trailing_newline_in_run_id_is_refused(make_run_dir):
    """The schema's '$' (used by jsonschema) accepts a final newline; a file name must not."""
    run_dir = make_run_dir(RUN_A)
    _rewrite(run_dir, lambda r: r.update(run_id=RUN_A + '\n'))
    _, problems, _ = store.check_run(run_dir)
    assert len(problems) == 1
    assert "run_id '20260925T031000Z-habrok-default-a1b2\\n' does not match" in problems[0]
    assert store.RUN_ID.pattern == schema.load('record')['properties']['run_id']['pattern']


def test_stage_writes_documented_paths(make_run_dir, tmp_path):
    """Each artifact lands at its documented store path; spans and log are gzipped reproducibly."""
    run_dir = make_run_dir(RUN_A)
    record = _record(run_dir)
    tree = tmp_path / 'tree'
    stored = store.stage_run(run_dir, record, tree)
    hex_ = record['benchmark']['settings_hash'].removeprefix('sha256:')
    assert stored['artifacts'] == {
        'spans': f'spans/2026/{RUN_A}.timing.jsonl.gz',
        'settings': f'settings/{hex_}.toml',
        'config': f'configs/2026/{RUN_A}.toml',
        'log': f'logs/2026/{RUN_A}.log.gz',
    }
    spans = (tree / stored['artifacts']['spans']).read_bytes()
    assert gzip.decompress(spans) == (run_dir / 'timing.jsonl').read_bytes()
    assert spans[4:8] == b'\0\0\0\0'  # gzip header mtime: bytes depend on content only
    log = gzip.decompress((tree / stored['artifacts']['log']).read_bytes())
    assert log == (run_dir / 'log.txt').read_bytes()
    config = (tree / stored['artifacts']['config']).read_text()
    assert config == (run_dir / 'config.toml').read_text()
    on_disk = json.loads((tree / f'records/2026/{RUN_A}.json').read_text())
    assert on_disk == stored
    assert on_disk['timings'] == record['timings']
    assert store.read_records(tree) == [stored]


def test_settings_file_is_kept_once_per_hash(make_run_dir, tmp_path):
    """Two runs with equal settings share one file (the first); other settings get their own."""
    tree = tmp_path / 'tree'
    changed = {'params': {'out': {'path': 'x'}}, 'interior_struct': {'module': 'spider'}}
    for run_id, settings in ((RUN_A, None), (RUN_B, None), ('20260927T000000Z-x', changed)):
        run_dir = make_run_dir(run_id, settings=settings)
        store.stage_run(run_dir, _record(run_dir), tree)
    files = sorted((tree / 'settings').iterdir())
    assert len(files) == 2
    texts = [f.read_text() for f in files]
    assert sum(f'path = "{RUN_A}"' in t for t in texts) == 1  # the first run's copy survived
    assert not any(f'path = "{RUN_B}"' in t for t in texts)
    assert sum('module = "spider"' in t for t in texts) == 1


def test_profile_is_copied_whole(make_run_dir, tmp_path):
    """All of profile/ is copied unchanged; profile and flame point into it."""
    artifacts = {'flame': 'profile/flame.html', 'profile': 'profile/stacks.folded.gz'}
    run_dir = make_run_dir(RUN_A, artifacts=artifacts)
    (run_dir / 'profile' / 'raw').mkdir(parents=True)
    (run_dir / 'profile' / 'flame.html').write_text('<html></html>')
    (run_dir / 'profile' / 'stacks.folded.gz').write_bytes(gzip.compress(b'a;b 3\n'))
    (run_dir / 'profile' / 'raw' / 'scalene-profile.json').write_text('{}')
    record, problems, _ = store.check_run(run_dir)
    assert problems == []
    stored = store.stage_run(run_dir, record, tmp_path / 'tree')
    base = f'profiles/2026/{RUN_A}/'
    assert stored['artifacts']['flame'] == base + 'flame.html'
    assert stored['artifacts']['profile'] == base + 'stacks.folded.gz'
    folded = (tmp_path / 'tree' / base / 'stacks.folded.gz').read_bytes()
    assert gzip.decompress(folded) == b'a;b 3\n'  # not gzipped a second time
    assert (tmp_path / 'tree' / base / 'raw' / 'scalene-profile.json').read_text() == '{}'


@pytest.mark.parametrize(
    ('artifacts', 'expected'),
    [
        ({'resolved_config': 'init_coupler.toml'}, "ARTIFACTS gives 'no such key'"),
        ({'spans': 'spans.jsonl'}, "ARTIFACTS gives 'timing.jsonl'"),
        ({'profile': 'profile/'}, "ARTIFACTS gives 'profile/stacks.folded.gz'"),
        ({'flame': 'profile/flame.html'}, 'names profile/flame.html, which is missing'),
    ],
)
def test_artifacts_outside_the_contract_are_refused(make_run_dir, artifacts, expected):
    """Unknown keys, other paths for a known key, and missing files are check problems."""
    run_dir = make_run_dir(RUN_A, artifacts=artifacts)
    _, problems, _ = store.check_run(run_dir)
    assert len(problems) == 1
    assert expected in problems[0]


def test_symbolic_links_are_refused(make_run_dir, tmp_path):
    """A linked file or directory anywhere in the run directory is refused, without following it."""
    run_dir = make_run_dir(RUN_A)
    (run_dir / 'log.txt').unlink()
    (run_dir / 'log.txt').symlink_to(tmp_path / 'secret.txt')
    (tmp_path / 'secret.txt').write_text('token\n')
    (run_dir / 'profile').mkdir()
    os.symlink('/', run_dir / 'profile' / 'root')
    _, problems, _ = store.check_run(run_dir)
    links = [p for p in problems if p.endswith('symbolic links are not published')]
    assert sorted(links) == sorted(
        f'{path}: symbolic links are not published'
        for path in (run_dir / 'log.txt', run_dir / 'profile' / 'root')
    )
