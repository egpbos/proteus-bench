"""Tests for proteus_bench.publishing against local bare git repositories.

Contract clauses: the first publish creates the store branch as an orphan (no
history shared with other branches) holding the README; later publishes add
one commit each on top; a push rejected because another publisher pushed
first is retried on the new tip and keeps both publishers' runs; a push
failing for another reason is not retried; runs already in the store are
skipped and nothing is pushed; ``.published`` holds the pushed commit; the
dashboard trigger dispatches pages.yml with gh for github.com remotes and
prints the command otherwise.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from proteus_bench import publishing, store

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(60)]

RUN_A = '20260925T031000Z-habrok-default-a1b2'
RUN_B = '20260926T031000Z-habrok-default-c3d4'
RUN_C = '20260927T031000Z-gha-default-e5f6'


def _run(make_run_dir, run_id, **fields):
    run_dir = make_run_dir(run_id, **fields)
    return run_dir, json.loads((run_dir / 'record.json').read_text())


def git_out(repo, *args):
    proc = subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def _clone(remote, dest):
    subprocess.run(['git', 'clone', '-q', str(remote), str(dest)], check=True)
    return dest


def _tree(repo, ref='results'):
    return sorted(git_out(repo, 'ls-tree', '-r', '--name-only', ref).splitlines())


def test_first_publish_creates_orphan_branch_with_readme(make_run_dir, bare_remote, tmp_path):
    """The results branch starts from nothing even when the remote already has main."""
    code = _clone(bare_remote, tmp_path / 'code')
    (code / 'harness.py').write_text('print(1)\n')
    git_out(code, 'add', 'harness.py')
    git_out(code, 'commit', '-q', '-m', 'code')
    git_out(code, 'push', '-q', 'origin', 'HEAD:main')
    run_dir, record = _run(make_run_dir, RUN_A)
    result = publishing.publish(
        [(run_dir, record)], str(bare_remote), 'results', tmp_path / 'c'
    )
    tip = git_out(bare_remote, 'rev-parse', 'results')
    assert result.commit == tip and result.added == [RUN_A] and result.skipped == {}
    assert git_out(bare_remote, 'rev-list', '--count', 'results') == '1'  # orphan: one commit
    no_base = subprocess.run(['git', 'merge-base', 'main', 'results'], cwd=bare_remote)
    assert no_base.returncode == 1  # no common ancestor with main
    files = _tree(bare_remote)
    assert 'harness.py' not in files and 'README.md' in files
    assert f'records/2026/{RUN_A}.json' in files
    assert git_out(bare_remote, 'log', '-1', '--format=%s', 'results') == f'Add runs {RUN_A}'
    assert (run_dir / '.published').read_text() == tip + '\n'


def test_second_publish_appends_one_commit(make_run_dir, bare_remote, tmp_path):
    """A later publish adds its files on top; earlier files and the README stay as they were."""
    checkout = tmp_path / 'c'
    first = publishing.publish(
        [_run(make_run_dir, RUN_A)], str(bare_remote), 'results', checkout
    )
    runs = [_run(make_run_dir, RUN_B), _run(make_run_dir, RUN_C)]
    second = publishing.publish(runs, str(bare_remote), 'results', checkout)
    assert git_out(bare_remote, 'rev-parse', 'results^') == first.commit
    assert git_out(bare_remote, 'log', '-1', '--format=%s', 'results') == (
        f'Add runs {RUN_B} {RUN_C}'
    )
    files = _tree(bare_remote)
    assert [f for f in files if f.startswith('records/')] == [
        f'records/2026/{r}.json' for r in (RUN_A, RUN_B, RUN_C)
    ]
    # Equal settings share one file, so the second commit adds no settings file
    assert len([f for f in files if f.startswith('settings/')]) == 1
    readme_commits = git_out(bare_remote, 'log', '--format=%H', 'results', '--', 'README.md')
    assert readme_commits == first.commit and second.skipped == {}


def test_concurrent_publisher_triggers_retry(
    make_run_dir, bare_remote, tmp_path, monkeypatch, capsys
):
    """Another clone pushes between our commit and push; the retry keeps both runs."""
    checkout = tmp_path / 'c'
    publishing.publish([_run(make_run_dir, RUN_A)], str(bare_remote), 'results', checkout)
    other = _clone(bare_remote, tmp_path / 'other')
    git_out(other, 'checkout', '-q', 'results')
    other_run, other_record = _run(make_run_dir, RUN_C, root=tmp_path / 'other-runs')
    real_push, pushes = publishing._push, []

    def push_after_other(repo, branch):
        if not pushes:
            store.stage_run(other_run, other_record, other)
            git_out(other, 'add', '-A')
            git_out(other, 'commit', '-q', '-m', f'Add runs {RUN_C}')
            git_out(other, 'push', '-q', 'origin', 'HEAD:results')
        pushes.append(branch)
        return real_push(repo, branch)

    monkeypatch.setattr(publishing, '_push', push_after_other)
    result = publishing.publish(
        [_run(make_run_dir, RUN_B)], str(bare_remote), 'results', checkout
    )
    assert len(pushes) == 2 and 'retrying (1/3)' in capsys.readouterr().out
    records = [f for f in _tree(bare_remote) if f.startswith('records/')]
    assert records == [f'records/2026/{r}.json' for r in (RUN_A, RUN_B, RUN_C)]
    assert result.commit == git_out(bare_remote, 'rev-parse', 'results')
    assert git_out(bare_remote, 'rev-list', '--count', 'results') == '3'  # linear, no merge


def test_rejected_every_time_gives_up(make_run_dir, bare_remote, tmp_path, monkeypatch):
    """When every attempt loses the race, publish raises after three pushes."""
    other = tmp_path / 'other'
    counter = []

    def always_beaten(repo, branch):
        if not other.exists():
            _clone(bare_remote, other)
        git_out(other, 'fetch', '-q', 'origin')
        git_out(other, 'checkout', '-q', '-B', 'results', 'origin/results')
        (other / f'x{len(counter)}').write_text('x')
        git_out(other, 'add', '-A')
        git_out(other, 'commit', '-q', '-m', 'other')
        git_out(other, 'push', '-q', 'origin', 'HEAD:results')
        counter.append(1)
        return subprocess.run(
            ['git', 'push', '-q', 'origin', f'HEAD:refs/heads/{branch}'],
            cwd=repo,
            capture_output=True,
            text=True,
        )

    publishing.publish([_run(make_run_dir, RUN_A)], str(bare_remote), 'results', tmp_path / 'c')
    monkeypatch.setattr(publishing, '_push', always_beaten)
    with pytest.raises(publishing.PublishError, match='still rejected after 3 attempts'):
        publishing.publish(
            [_run(make_run_dir, RUN_B)], str(bare_remote), 'results', tmp_path / 'c'
        )
    assert len(counter) == 3
    assert f'records/2026/{RUN_B}.json' not in _tree(bare_remote)


def test_push_refused_by_remote_is_not_retried(make_run_dir, bare_remote, tmp_path, capsys):
    """A hook declining the push fails at once with git's message; nothing lands."""
    hook = bare_remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "store is read-only" >&2\nexit 1\n')
    hook.chmod(0o755)
    run_dir, record = _run(make_run_dir, RUN_A)
    with pytest.raises(publishing.PublishError, match='store is read-only'):
        publishing.publish([(run_dir, record)], str(bare_remote), 'results', tmp_path / 'c')
    assert 'retrying' not in capsys.readouterr().out
    assert not (run_dir / '.published').exists()
    assert git_out(bare_remote, 'for-each-ref') == ''


def test_duplicate_run_is_skipped(make_run_dir, bare_remote, tmp_path):
    """Publishing a run again pushes nothing and reports the commit that has it."""
    checkout = tmp_path / 'c'
    runs = [_run(make_run_dir, RUN_A)]
    first = publishing.publish(runs, str(bare_remote), 'results', checkout)
    again = publishing.publish(runs, str(bare_remote), 'results', checkout)
    assert again.commit is None and again.added == []
    assert again.skipped == {RUN_A: first.commit}
    assert git_out(bare_remote, 'rev-parse', 'results') == first.commit
    # A fresh cache dir finds the run in the remote as well
    fresh = publishing.publish(runs, str(bare_remote), 'results', tmp_path / 'fresh')
    assert fresh.skipped == {RUN_A: first.commit}


def test_new_store_from_a_used_cache_starts_empty(make_run_dir, bare_remote, tmp_path):
    """Reusing the cache for a remote without the branch does not carry old files over."""
    checkout = tmp_path / 'c'
    publishing.publish([_run(make_run_dir, RUN_A)], str(bare_remote), 'results', checkout)
    second = tmp_path / 'second.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(second)], check=True)
    publishing.publish([_run(make_run_dir, RUN_B)], str(second), 'results', checkout)
    records = [f for f in _tree(second) if f.startswith('records/')]
    assert records == [f'records/2026/{RUN_B}.json']
    assert git_out(second, 'rev-list', '--count', 'results') == '1'
    assert (
        git_out(bare_remote, 'rev-list', '--count', 'results') == '1'
    )  # first store unchanged


def test_unreachable_remote_is_an_error(tmp_path, git_env):
    """A remote that does not exist gives a PublishError, not a new empty store."""
    with pytest.raises(publishing.PublishError, match='cannot read'):
        publishing.update_checkout(tmp_path / 'c', str(tmp_path / 'missing.git'), 'results')


@pytest.mark.parametrize(
    ('remote', 'repo'),
    [
        ('https://github.com/egpbos/proteus-bench.git', 'egpbos/proteus-bench'),
        ('https://github.com/FormingWorlds/proteus-bench', 'FormingWorlds/proteus-bench'),
        ('git@github.com:egpbos/proteus-bench.git', 'egpbos/proteus-bench'),
        ('ssh://git@github.com/egpbos/proteus.bench.git', 'egpbos/proteus.bench'),
        ('https://gitlab.com/egpbos/proteus-bench.git', None),
        ('https://github.com.evil.org/egpbos/proteus-bench.git', None),
        ('/srv/git/proteus-bench.git', None),
    ],
)
def test_github_repo_parsing(remote, repo):
    """Only github.com remotes map to owner/name; look-alike hosts do not."""
    assert publishing.github_repo(remote) == repo
    if repo is None:  # returns before looking for gh, so nothing is dispatched
        assert publishing.trigger_dashboard(remote) == (
            f'dashboard not triggered: {remote} is not a github.com repository'
        )


def test_trigger_dashboard_with_and_without_gh(fake_gh, monkeypatch):
    """With gh the workflow is dispatched; without it the command is printed; failures are reported."""
    remote = 'git@github.com:egpbos/proteus-bench.git'
    message = publishing.trigger_dashboard(remote)
    assert fake_gh.calls() == [['workflow', 'run', 'pages.yml', '-R', 'egpbos/proteus-bench']]
    assert message.startswith('dashboard rebuild started')
    monkeypatch.setenv('FAKE_GH_EXIT', '1')
    monkeypatch.setenv('FAKE_GH_STDERR', 'HTTP 404: workflow not found')
    failed = publishing.trigger_dashboard(remote)
    assert 'failed (exit 1): HTTP 404' in failed and 'retry with: gh workflow run' in failed
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, 'which', lambda name: None if name == 'gh' else real_which(name)
    )
    hint = publishing.trigger_dashboard(remote)
    assert hint == (
        'gh not found; to rebuild the dashboard now, run: '
        'gh workflow run pages.yml -R egpbos/proteus-bench'
    )
    assert len(fake_gh.calls()) == 2  # the no-gh path ran nothing
