"""Tests for ``proteus-bench publish`` (proteus_bench.commands.publish).

Contract clauses: an invalid run anywhere in the list stops the whole publish
with the problem printed and nothing pushed; the same run id given twice is
refused; a non-GitHub remote is published to without triggering the dashboard;
after a push to a github.com remote the Pages workflow is dispatched with gh, and without gh
the command to run is printed.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from proteus_bench import cli, publishing, schema
from proteus_bench.commands.publish import cache_checkout

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(60)]

RUN_A = '20260925T031000Z-habrok-default-a1b2'
RUN_B = '20260926T031000Z-habrok-default-c3d4'
GITHUB = 'https://github.com/egpbos/proteus-bench.git'


def _refs(remote) -> str:
    proc = subprocess.run(['git', 'for-each-ref'], cwd=remote, capture_output=True, text=True)
    return proc.stdout.strip()


def test_invalid_record_refuses_everything(make_run_dir, bare_remote, capsys):
    """One bad run among good ones: problem printed, exit 1, remote untouched."""
    good = make_run_dir(RUN_A)
    bad = make_run_dir(RUN_B)
    record = json.loads((bad / 'record.json').read_text())
    del record['machine']
    (bad / 'record.json').write_text(json.dumps(record))
    code = cli.main(['publish', str(good), str(bad), '--remote', str(bare_remote)])
    out = capsys.readouterr().out
    assert code == 1
    assert 'nothing published' in out
    assert f"{bad / 'record.json'}: <root>: 'machine' is a required property" in out
    assert _refs(bare_remote) == ''
    assert not (good / '.published').exists()


def test_same_run_twice(make_run_dir, bare_remote, tmp_path, capsys):
    """One run id in two directories is refused before git runs."""
    run_dir = make_run_dir(RUN_A)
    copy = shutil.copytree(run_dir, tmp_path / 'copy' / RUN_A)
    assert cli.main(['publish', str(run_dir), str(copy), '--remote', str(bare_remote)]) == 1
    out = capsys.readouterr().out
    assert f'run ids given more than once: {RUN_A}\nnothing published\n' in out
    assert not cache_checkout().exists()  # git never ran
    assert _refs(bare_remote) == ''


def test_local_remote_message_and_republish(make_run_dir, bare_remote, capsys):
    """A non-GitHub remote says the dashboard is not triggered; publishing again skips."""
    run_dir = make_run_dir(RUN_A)
    assert cli.main(['publish', str(run_dir), '--remote', str(bare_remote)]) == 0
    out = capsys.readouterr().out
    assert f'published {RUN_A} to {bare_remote} results' in out
    assert 'dashboard not triggered' in out
    assert 'refs/heads/results' in _refs(bare_remote)
    assert cli.main(['publish', str(run_dir), '--remote', str(bare_remote)]) == 0  # skipped
    assert 'already in the store' in capsys.readouterr().out


def test_github_remote_dispatches_pages(make_run_dir, bare_remote, git_env, fake_gh, capsys):
    """A github.com remote (rewritten to the local repo by git) triggers gh once."""
    with git_env.open('a') as fh:
        fh.write(f'[url "{bare_remote}"]\n\tinsteadOf = {GITHUB}\n')
    run_dir = make_run_dir(RUN_A)
    assert cli.main(['publish', str(run_dir), '--remote', GITHUB]) == 0
    assert 'dashboard rebuild started' in capsys.readouterr().out
    assert fake_gh.calls() == [['workflow', 'run', 'pages.yml', '-R', 'egpbos/proteus-bench']]
    assert 'refs/heads/results' in _refs(bare_remote)


def test_github_remote_without_gh_prints_command(
    make_run_dir, bare_remote, git_env, monkeypatch, capsys
):
    """Without gh the publish still succeeds and prints the dispatch command."""
    with git_env.open('a') as fh:
        fh.write(f'[url "{bare_remote}"]\n\tinsteadOf = {GITHUB}\n')
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, 'which', lambda name: None if name == 'gh' else real_which(name)
    )
    run_dir = make_run_dir(RUN_A)
    assert cli.main(['publish', str(run_dir), '--remote', GITHUB]) == 0
    out = capsys.readouterr().out
    assert 'run: gh workflow run pages.yml -R egpbos/proteus-bench' in out
    assert (run_dir / '.published').exists()


def test_without_jsonschema_nothing_is_published(
    make_run_dir, bare_remote, monkeypatch, capsys
):
    """Without the publish extra the run is refused and the store is left untouched."""
    monkeypatch.setattr(schema, 'available', lambda: False)
    run_dir = make_run_dir(RUN_A)
    assert cli.main(['publish', str(run_dir), '--remote', str(bare_remote)]) == 1
    out = capsys.readouterr().out
    assert 'install proteus-bench[publish]' in out
    assert 'nothing published' in out
    assert _refs(bare_remote) == ''


def test_git_failure_and_retries_are_reported(make_run_dir, bare_remote, monkeypatch, capsys):
    """A refused push gives exit 1 and git's reason; a won retry is mentioned on success."""
    hook = bare_remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "store is read-only" >&2\nexit 1\n')
    hook.chmod(0o755)
    run_dir = make_run_dir(RUN_A)
    assert cli.main(['publish', str(run_dir), '--remote', str(bare_remote)]) == 1
    out = capsys.readouterr().out
    assert 'store is read-only' in out
    assert out.endswith('nothing published\n')
    hook.unlink()
    real = publishing.publish

    def one_retry(*args):
        result = real(*args)
        result.retries = 1
        return result

    monkeypatch.setattr(publishing, 'publish', one_retry)
    assert cli.main(['publish', str(run_dir), '--remote', str(bare_remote)]) == 0
    assert 'succeeded after 1 retries' in capsys.readouterr().out
