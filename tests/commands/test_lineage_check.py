"""Tests for ``proteus-bench lineage-check`` (proteus_bench.commands.lineage_check).

Contract clauses: exit 0 and ``carry_over=none`` when the settings did not
change, including across a failed default run; exit 3 with key=value lines
naming the previous run's stored settings and config (existing absolute
paths holding the old values), the lineage, this run and its commit when they
did; exit 1 when the run directory fails the publish checks, the store cannot
be read, or it lacks a file the old record points to; without --store the
cached checkout of the configured remote is read.
"""

from __future__ import annotations

import json

import pytest

from proteus_bench import cli, publishing, store
from proteus_bench.commands.lineage_check import CARRY_OVER_NEEDED

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(60)]

RUN_1 = '20260901T031000Z-habrok-default-a1b2'
RUN_2 = '20260908T031000Z-habrok-default-c3d4'
RUN_3 = '20260915T031000Z-habrok-default-e5f6'
NEW_SETTINGS = {
    'params': {'out': {'path': 'x'}, 'stop': {'iters': {'maximum': 6}}},
    'interior_struct': {'module': 'zalmoxis', 'zalmoxis': {'use_jax': False}},
}


def _stage(make_run_dir, tree, run_id, **fields):
    run_dir = make_run_dir(run_id, **fields)
    record = json.loads((run_dir / 'record.json').read_text())
    store.stage_run(run_dir, record, tree)
    return run_dir, record


def _failed_run(make_run_dir, run_id):
    """A default run that failed before PROTEUS wrote init_coupler.toml."""
    run_dir = make_run_dir(run_id, status='failed', settings=NEW_SETTINGS)
    (run_dir / 'init_coupler.toml').unlink()
    (run_dir / 'timing.jsonl').unlink()
    record = json.loads((run_dir / 'record.json').read_text())
    record['artifacts'] = {'config': 'config.toml', 'log': 'log.txt'}
    (run_dir / 'record.json').write_text(json.dumps(record))
    return run_dir


def _lines(out: str) -> dict:
    return dict(line.split('=', 1) for line in out.splitlines())


def test_unchanged_settings_need_nothing(make_run_dir, tmp_path, capsys):
    """Same settings as the previous default run: exit 0, one line."""
    tree = tmp_path / 'tree'
    _stage(make_run_dir, tree, RUN_1)
    new = make_run_dir(RUN_2)
    assert cli.main(['lineage-check', str(new), '--store', str(tree)]) == 0
    assert capsys.readouterr().out == 'carry_over=none\n'
    # An empty store (first run ever) needs nothing either
    assert cli.main(['lineage-check', str(new), '--store', str(tmp_path / 'empty')]) == 0


def test_failed_run_between_equal_settings(make_run_dir, tmp_path, capsys):
    """A, failed (another hash, no settings file), A: nothing is due, and no error."""
    tree = tmp_path / 'tree'
    _stage(make_run_dir, tree, RUN_1)
    failed = _failed_run(make_run_dir, RUN_2)
    record = json.loads((failed / 'record.json').read_text())
    store.stage_run(failed, record, tree)
    new = make_run_dir(RUN_3)
    assert cli.main(['lineage-check', str(new), '--store', str(tree)]) == 0
    assert capsys.readouterr().out == 'carry_over=none\n'
    # The failed run itself asks for nothing either
    assert cli.main(['lineage-check', str(failed), '--store', str(tree)]) == 0


def test_changed_settings_ask_for_carry_over(make_run_dir, tmp_path, capsys):
    """New defaults: exit 3 and the paths of the previous settings and config to replay."""
    tree = tmp_path / 'tree'
    _stage(make_run_dir, tree, RUN_1)
    _, old = _stage(make_run_dir, tree, RUN_2)
    # The record lists no artifacts: the files present decide whether settings are resolved
    new = make_run_dir(RUN_3, settings=NEW_SETTINGS, artifacts={})
    code = cli.main(['lineage-check', str(new), '--store', str(tree)])
    assert code == CARRY_OVER_NEEDED == 3
    out = _lines(capsys.readouterr().out)
    old_hash = old['benchmark']['settings_hash']
    assert out['carry_over'] == 'needed'
    assert out['carry_over_of'] == RUN_3
    assert out['lineage'] == old_hash
    assert out['commit'] == 'abd4ca53'
    assert out['changed_keys'] == 'interior_struct.zalmoxis.use_jax'
    settings = tree / 'settings' / f'{old_hash.removeprefix("sha256:")}.toml'
    assert out['settings_toml'] == str(settings.resolve())
    assert 'use_jax = true' in settings.read_text()  # the old value, not the new false
    assert out['config_toml'] == str((tree / f'configs/2026/{RUN_2}.toml').resolve())


def test_carry_over_already_run(make_run_dir, tmp_path, capsys):
    """With the carry-over record in the store, the same check says nothing is due."""
    tree = tmp_path / 'tree'
    _, old = _stage(make_run_dir, tree, RUN_1)
    new_dir, _ = _stage(make_run_dir, tree, RUN_3, settings=NEW_SETTINGS)
    carry = '20260915T091000Z-habrok-carry-0001'
    lineage = old['benchmark']['settings_hash']
    _stage(make_run_dir, tree, carry, lineage=lineage, carry_over_of=RUN_3)
    assert cli.main(['lineage-check', str(new_dir), '--store', str(tree)]) == 0
    assert capsys.readouterr().out == 'carry_over=none\n'


def test_bad_run_dir_and_incomplete_store_exit_1(make_run_dir, tmp_path, capsys):
    """No record, a malformed record, or a store missing the old settings file: exit 1."""
    assert cli.main(['lineage-check', str(tmp_path), '--store', str(tmp_path)]) == 1
    assert 'not a run directory' in capsys.readouterr().out
    broken = make_run_dir(RUN_2)
    record = json.loads((broken / 'record.json').read_text())
    record['benchmark'] = 'all_options'
    (broken / 'record.json').write_text(json.dumps(record))
    assert cli.main(['lineage-check', str(broken), '--store', str(tmp_path)]) == 1
    assert 'benchmark' in capsys.readouterr().out
    tree = tmp_path / 'tree'
    _stage(make_run_dir, tree, RUN_1)
    for path in (tree / 'settings').iterdir():
        path.unlink()
    new = make_run_dir(RUN_3, settings=NEW_SETTINGS)
    assert cli.main(['lineage-check', str(new), '--store', str(tree)]) == 1
    assert 'the store has no file settings/' in capsys.readouterr().out


def test_store_errors_exit_1(make_run_dir, git_env, tmp_path, capsys):
    """No remote configured, or one that cannot be read: exit 1 with the reason."""
    new = make_run_dir(RUN_3)
    assert cli.main(['lineage-check', str(new)]) == 1
    assert 'no store remote' in capsys.readouterr().out
    missing = str(tmp_path / 'missing.git')
    assert cli.main(['lineage-check', str(new), '--remote', missing]) == 1
    assert f'cannot read {missing}' in capsys.readouterr().out


def test_reads_cached_checkout_of_remote(make_run_dir, bare_remote, tmp_path, capsys):
    """Without --store the configured remote's branch is fetched and read."""
    first = make_run_dir(RUN_1)
    record = json.loads((first / 'record.json').read_text())
    publishing.publish([(first, record)], str(bare_remote), 'results', tmp_path / 'pub')
    new = make_run_dir(RUN_3, settings=NEW_SETTINGS)
    assert cli.main(['lineage-check', str(new), '--remote', str(bare_remote)]) == 3
    out = _lines(capsys.readouterr().out)
    assert out['lineage'] == record['benchmark']['settings_hash']
    assert out['settings_toml'].startswith(str(tmp_path / 'xdg-cache'))
