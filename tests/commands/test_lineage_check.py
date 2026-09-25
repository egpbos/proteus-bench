"""Tests for ``proteus-bench lineage-check`` (proteus_bench.commands.lineage_check).

Contract clauses: exit 0 and ``carry_over=none`` when the settings did not
change; exit 3 with key=value lines naming the old settings file (an existing
absolute path whose content holds the old settings), the lineage, the new
run and its commit when they did; exit 1 when the run record is unreadable or
the store lacks the settings file; without --store the cached checkout of the
configured remote is read.
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


def test_changed_settings_ask_for_carry_over(make_run_dir, tmp_path, capsys):
    """New defaults: exit 3 and the path of the previous settings to replay."""
    tree = tmp_path / 'tree'
    _, old = _stage(make_run_dir, tree, RUN_1)
    _stage(make_run_dir, tree, RUN_2)
    new = make_run_dir(RUN_3, settings=NEW_SETTINGS)
    code = cli.main(['lineage-check', str(new), '--store', str(tree)])
    assert code == CARRY_OVER_NEEDED == 3
    out = _lines(capsys.readouterr().out)
    old_hash = old['benchmark']['settings_hash']
    assert out['carry_over'] == 'needed' and out['carry_over_of'] == RUN_3
    assert out['lineage'] == old_hash and out['commit'] == 'abd4ca53'
    assert out['changed_keys'] == 'interior_struct.zalmoxis.use_jax'
    settings = tree / 'settings' / f'{old_hash.removeprefix("sha256:")}.toml'
    assert out['settings_toml'] == str(settings.resolve())
    assert 'use_jax = true' in settings.read_text()  # the old value, not the new false


def test_carry_over_already_run(make_run_dir, tmp_path, capsys):
    """With the carry-over record in the store, the same check says nothing is due."""
    tree = tmp_path / 'tree'
    _, old = _stage(make_run_dir, tree, RUN_1)
    new_dir, _ = _stage(make_run_dir, tree, RUN_3, settings=NEW_SETTINGS)
    carry = '20260915T091000Z-habrok-carry-0001'
    _stage(
        make_run_dir,
        tree,
        carry,
        lineage=old['benchmark']['settings_hash'],
        carry_over_of=RUN_3,
    )
    assert cli.main(['lineage-check', str(new_dir), '--store', str(tree)]) == 0
    assert capsys.readouterr().out == 'carry_over=none\n'


def test_errors_exit_1(make_run_dir, tmp_path, capsys):
    """Missing record, or a store without the old settings file, is an error, not 'none'."""
    assert cli.main(['lineage-check', str(tmp_path), '--store', str(tmp_path)]) == 1
    assert 'cannot read the run record' in capsys.readouterr().out
    tree = tmp_path / 'tree'
    _stage(make_run_dir, tree, RUN_1)
    for path in (tree / 'settings').iterdir():
        path.unlink()
    new = make_run_dir(RUN_3, settings=NEW_SETTINGS)
    assert cli.main(['lineage-check', str(new), '--store', str(tree)]) == 1
    assert 'the store has no settings file settings/' in capsys.readouterr().out


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
