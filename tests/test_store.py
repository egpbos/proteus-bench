"""Tests for proteus_bench.store: run checks, store layout and staging.

Contract clauses: a run directory needs record.json and log.txt, plus
timing.jsonl and init_coupler.toml when it finished ok; the record's settings
hash must match its init_coupler.toml; run_id and settings_hash are checked
even without jsonschema because they become paths; staging writes the layout
from the module docstring, gzips spans and logs, keeps one settings file per
hash, copies profile/, and rewrites artifacts to store paths; an artifact that
names a file the store does not keep is refused.
"""

from __future__ import annotations

import gzip
import json

import pytest

from proteus_bench import schema, store

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

RUN_A = '20260925T031000Z-habrok-default-a1b2'
RUN_B = '20260926T031000Z-habrok-default-c3d4'


def test_good_run_passes_and_missing_files_are_named(make_run_dir):
    """A complete run has no problems; removing required files names each one."""
    pytest.importorskip('jsonschema')
    run_dir = make_run_dir(RUN_A)
    record, problems, shape_checked = store.check_run(run_dir)
    assert problems == [] and shape_checked and record['run_id'] == RUN_A
    (run_dir / 'timing.jsonl').unlink()
    (run_dir / 'log.txt').unlink()
    _, problems, _ = store.check_run(run_dir)
    assert problems[:2] == [f'{run_dir}: missing log.txt', f'{run_dir}: missing timing.jsonl']
    # The record still points at the spans, which is reported as well
    assert len(problems) == 3 and "artifact 'spans' is 'timing.jsonl'" in problems[2]


def test_failed_run_needs_no_spans_or_settings(make_run_dir):
    """A run that failed in setup has no timing.jsonl or init_coupler.toml; it still publishes."""
    run_dir = make_run_dir(RUN_A, status='failed', artifacts={'log': 'log.txt'})
    (run_dir / 'timing.jsonl').unlink()
    (run_dir / 'init_coupler.toml').unlink()
    _, problems, _ = store.check_run(run_dir)
    assert problems == []
    assert store.store_artifacts(
        run_dir, json.loads((run_dir / 'record.json').read_text())
    ) == {'log': f'logs/2026/{RUN_A}.log.gz'}


def test_not_a_run_dir_and_broken_json(tmp_path):
    """No record.json and unparsable JSON are reported, not raised."""
    record, problems, _ = store.check_run(tmp_path)
    assert record is None and 'not a run directory' in problems[0]
    (tmp_path / 'record.json').write_text('{"run_id": ')
    record, problems, _ = store.check_run(tmp_path)
    assert record is None and 'not valid JSON' in problems[0]


def test_settings_hash_must_match_init_coupler(make_run_dir):
    """Editing the stored settings after the run makes the claimed hash wrong."""
    run_dir = make_run_dir(RUN_A)
    path = run_dir / 'init_coupler.toml'
    path.write_text(path.read_text().replace('use_jax = true', 'use_jax = false'))
    _, problems, _ = store.check_run(run_dir)
    assert len(problems) == 1
    assert 'settings hash is sha256:' in problems[0] and 'but record.json says' in problems[0]
    # The per-run output path does not count: changing only it keeps the hash
    run_b = make_run_dir(RUN_B)
    toml = run_b / 'init_coupler.toml'
    toml.write_text(toml.read_text().replace(f'path = "{RUN_B}"', 'path = "elsewhere"'))
    assert store.check_run(run_b)[1] == []


def test_path_fields_checked_without_jsonschema(make_run_dir, monkeypatch):
    """Without jsonschema a run id that would escape the store is still refused."""
    monkeypatch.setattr(schema, 'shape_problems', lambda kind, instance: None)
    run_dir = make_run_dir(RUN_A)
    record = json.loads((run_dir / 'record.json').read_text())
    record['run_id'] = '../../etc'
    record['benchmark']['settings_hash'] = 'md5:abc'
    del record['outcome']  # needed to decide which files are required
    (run_dir / 'record.json').write_text(json.dumps(record))
    _, problems, shape_checked = store.check_run(run_dir)
    assert shape_checked is False and len(problems) == 3
    assert problems[2].endswith('outcome.status is missing')
    assert any("run_id '../../etc' does not match" in p for p in problems)
    assert any("settings_hash 'md5:abc' does not match" in p for p in problems)


def test_stage_writes_layout_and_rewrites_artifacts(make_run_dir, tmp_path):
    """Spans and log are gzipped copies; the stored record points at store paths."""
    run_dir = make_run_dir(RUN_A)
    record = json.loads((run_dir / 'record.json').read_text())
    tree = tmp_path / 'tree'
    stored = store.stage_run(run_dir, record, tree)
    hex_ = record['benchmark']['settings_hash'].removeprefix('sha256:')
    assert stored['artifacts'] == {
        'spans': f'spans/2026/{RUN_A}.timing.jsonl.gz',
        'log': f'logs/2026/{RUN_A}.log.gz',
        'settings': f'settings/{hex_}.toml',
    }
    spans = gzip.decompress((tree / stored['artifacts']['spans']).read_bytes())
    assert spans == (run_dir / 'timing.jsonl').read_bytes()
    log = gzip.decompress((tree / stored['artifacts']['log']).read_bytes())
    assert log == (run_dir / 'log.txt').read_bytes()
    on_disk = json.loads((tree / f'records/2026/{RUN_A}.json').read_text())
    assert on_disk == stored and on_disk['timings'] == record['timings']
    assert store.read_records(tree) == [stored]


def test_settings_file_is_kept_once_per_hash(make_run_dir, tmp_path):
    """Two runs with equal settings share one file (the first); other settings get their own."""
    tree = tmp_path / 'tree'
    changed = {'params': {'out': {'path': 'x'}}, 'interior_struct': {'module': 'spider'}}
    for run_id, settings in ((RUN_A, None), (RUN_B, None), ('20260927T000000Z-x', changed)):
        run_dir = make_run_dir(run_id, settings=settings)
        store.stage_run(run_dir, json.loads((run_dir / 'record.json').read_text()), tree)
    files = sorted((tree / 'settings').iterdir())
    assert len(files) == 2
    texts = [f.read_text() for f in files]
    assert sum(f'path = "{RUN_A}"' in t for t in texts) == 1  # the first run's copy survived
    assert not any(f'path = "{RUN_B}"' in t for t in texts)
    assert sum('module = "spider"' in t for t in texts) == 1


def test_profile_is_copied_and_flame_link_rewritten(make_run_dir, tmp_path):
    """profile/ is copied as is (no double gzip) and artifacts.flame points into it."""
    run_dir = make_run_dir(RUN_A, artifacts={'flame': 'profile/flame.html', 'log': 'log.txt'})
    (run_dir / 'profile' / 'raw').mkdir(parents=True)
    (run_dir / 'profile' / 'flame.html').write_text('<html></html>')
    (run_dir / 'profile' / 'stacks.folded.gz').write_bytes(gzip.compress(b'a;b 3\n'))
    (run_dir / 'profile' / 'raw' / 'scalene-profile.json').write_text('{}')
    record = json.loads((run_dir / 'record.json').read_text())
    stored = store.stage_run(run_dir, record, tmp_path / 'tree')
    base = f'profiles/2026/{RUN_A}/'
    assert stored['artifacts']['flame'] == base + 'flame.html'
    assert stored['artifacts']['profile'] == base
    folded = (tmp_path / 'tree' / base / 'stacks.folded.gz').read_bytes()
    assert gzip.decompress(folded) == b'a;b 3\n'
    assert (tmp_path / 'tree' / base / 'raw' / 'scalene-profile.json').read_text() == '{}'


def test_artifact_outside_the_store_is_refused(make_run_dir):
    """An artifact the store would drop (config.toml, a missing file) is a check problem."""
    run_dir = make_run_dir(RUN_A, artifacts={'config': 'config.toml'})
    record, problems, _ = store.check_run(run_dir)
    assert len(problems) == 1 and "artifact 'config' is 'config.toml'" in problems[0]
    with pytest.raises(ValueError, match='not a file the store keeps'):
        store.store_artifacts(run_dir, record)
    run_b = make_run_dir(RUN_B, artifacts={'flame': 'profile/flame.html'})  # no profile dir
    assert "artifact 'flame'" in store.check_run(run_b)[1][0]
