"""Tests for proteus_bench.publishing against local bare git repositories.

Contract clauses: the first publish creates the store branch as an orphan
holding the README; later publishes add one commit each; a push rejected
because another publisher pushed first is retried on the new tip (counted in
``retries``) and keeps both publishers' runs, up to three attempts; a push
refused for another reason fails with git's message; a push that landed
although git exited non-zero counts as published; runs already in the store
are skipped without a push; every run dir, added or skipped, gets
``.published`` with the commit holding it; an existing directory that is not
a marked store checkout is never touched; the file lock serialises publishers
sharing a cache; ``git`` raises with the exit code and stderr; the dashboard
trigger dispatches pages.yml with gh and reports failures.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading

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


def _records(repo):
    return [f for f in _tree(repo) if f.startswith('records/')]


def _other_publishes(bare_remote, other, name):
    """Another clone adds a file to results and pushes it."""
    if not other.exists():
        _clone(bare_remote, other)
    git_out(other, 'fetch', '-q', 'origin')
    git_out(other, 'checkout', '-q', '-B', 'results', 'origin/results')
    (other / name).write_text('x\n')
    git_out(other, 'add', '-A')
    git_out(other, 'commit', '-q', '-m', f'Add {name}')
    git_out(other, 'push', '-q', 'origin', 'HEAD:results')


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
    assert result == publishing.Published(commit=tip, added=[RUN_A], skipped={}, retries=0)
    assert git_out(bare_remote, 'rev-list', '--count', 'results') == '1'
    no_base = subprocess.run(['git', 'merge-base', 'main', 'results'], cwd=bare_remote)
    assert no_base.returncode == 1  # no common ancestor with main
    files = _tree(bare_remote)
    assert 'harness.py' not in files
    assert 'README.md' in files
    assert f'records/2026/{RUN_A}.json' in files
    assert f'configs/2026/{RUN_A}.toml' in files
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
    assert _records(bare_remote) == [f'records/2026/{r}.json' for r in (RUN_A, RUN_B, RUN_C)]
    # Equal settings share one file, so the second commit adds no settings file
    assert len([f for f in _tree(bare_remote) if f.startswith('settings/')]) == 1
    readme_commits = git_out(bare_remote, 'log', '--format=%H', 'results', '--', 'README.md')
    assert readme_commits == first.commit
    assert second.skipped == {}


def test_concurrent_publisher_triggers_retry(make_run_dir, bare_remote, tmp_path, monkeypatch):
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
    assert len(pushes) == 2
    assert result.retries == 1
    assert _records(bare_remote) == [f'records/2026/{r}.json' for r in (RUN_A, RUN_B, RUN_C)]
    assert result.commit == git_out(bare_remote, 'rev-parse', 'results')
    assert git_out(bare_remote, 'rev-list', '--count', 'results') == '3'  # linear, no merge


def test_rejected_every_time_gives_up(make_run_dir, bare_remote, tmp_path, monkeypatch):
    """When every attempt loses the race, publish raises after three pushes."""
    real_push, pushes = publishing._push, []

    def always_beaten(repo, branch):
        _other_publishes(bare_remote, tmp_path / 'other', f'x{len(pushes)}')
        pushes.append(branch)
        return real_push(repo, branch)

    publishing.publish([_run(make_run_dir, RUN_A)], str(bare_remote), 'results', tmp_path / 'c')
    monkeypatch.setattr(publishing, '_push', always_beaten)
    run_b, record_b = _run(make_run_dir, RUN_B)
    with pytest.raises(publishing.PublishError, match='still rejected after 3 attempts'):
        publishing.publish([(run_b, record_b)], str(bare_remote), 'results', tmp_path / 'c')
    assert len(pushes) == 3
    assert f'records/2026/{RUN_B}.json' not in _tree(bare_remote)
    assert not (run_b / '.published').exists()


def test_push_refused_by_remote_is_not_retried(make_run_dir, bare_remote, tmp_path):
    """A hook declining the push fails at once with git's message; nothing lands."""
    hook = bare_remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "store is read-only" >&2\nexit 1\n')
    hook.chmod(0o755)
    run_dir, record = _run(make_run_dir, RUN_A)
    with pytest.raises(publishing.PublishError, match='store is read-only'):
        publishing.publish([(run_dir, record)], str(bare_remote), 'results', tmp_path / 'c')
    assert not (run_dir / '.published').exists()
    assert git_out(bare_remote, 'for-each-ref') == ''


def test_push_that_landed_despite_an_error_counts(
    make_run_dir, bare_remote, tmp_path, monkeypatch
):
    """The connection drops after the remote took the push: the run is published and marked."""
    real_push = publishing._push

    def lands_then_fails(repo, branch):
        real_push(repo, branch)
        return subprocess.CompletedProcess([], 128, '', 'fatal: the remote end hung up')

    monkeypatch.setattr(publishing, '_push', lands_then_fails)
    run_dir, record = _run(make_run_dir, RUN_A)
    result = publishing.publish(
        [(run_dir, record)], str(bare_remote), 'results', tmp_path / 'c'
    )
    tip = git_out(bare_remote, 'rev-parse', 'results')
    assert result.commit == tip
    assert (run_dir / '.published').read_text() == tip + '\n'


def test_duplicate_run_is_skipped_without_push(
    make_run_dir, bare_remote, tmp_path, monkeypatch
):
    """Publishing a run again pushes nothing and marks it with the commit that has it."""
    checkout = tmp_path / 'c'
    run_dir, record = _run(make_run_dir, RUN_A)
    first = publishing.publish([(run_dir, record)], str(bare_remote), 'results', checkout)
    (run_dir / '.published').unlink()  # e.g. lost when the first push reported an error
    real_push, pushes = publishing._push, []
    monkeypatch.setattr(publishing, '_push', lambda *a: pushes.append(a) or real_push(*a))
    again = publishing.publish([(run_dir, record)], str(bare_remote), 'results', checkout)
    assert again == publishing.Published(commit=None, added=[], skipped={RUN_A: first.commit})
    assert pushes == []
    assert (run_dir / '.published').read_text() == first.commit + '\n'
    assert git_out(bare_remote, 'rev-parse', 'results') == first.commit
    fresh = publishing.publish([(run_dir, record)], str(bare_remote), 'results', tmp_path / 'f')
    assert fresh.skipped == {RUN_A: first.commit}  # a fresh cache finds it on the remote too


def test_new_store_from_a_used_cache_starts_empty(make_run_dir, bare_remote, tmp_path):
    """Reusing the cache for a remote without the branch does not carry old files over."""
    checkout = tmp_path / 'c'
    publishing.publish([_run(make_run_dir, RUN_A)], str(bare_remote), 'results', checkout)
    second = tmp_path / 'second.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(second)], check=True)
    publishing.publish([_run(make_run_dir, RUN_B)], str(second), 'results', checkout)
    assert _records(second) == [f'records/2026/{RUN_B}.json']
    assert git_out(second, 'rev-list', '--count', 'results') == '1'
    assert (
        git_out(bare_remote, 'rev-list', '--count', 'results') == '1'
    )  # first store unchanged


def test_foreign_repository_as_cache_is_left_alone(make_run_dir, bare_remote, tmp_path):
    """A work repo given as cache_dir is refused; commits, edits and untracked files survive."""
    victim = tmp_path / 'work'
    victim.mkdir()
    git_out(victim, 'init', '-q')
    (victim / 'model.py').write_text('x = 1\n')
    git_out(victim, 'add', 'model.py')
    git_out(victim, 'commit', '-q', '-m', 'work')
    (victim / 'model.py').write_text('x = 2  # not committed\n')
    (victim / 'scratch.txt').write_text('untracked\n')
    head, status = git_out(victim, 'rev-parse', 'HEAD'), git_out(victim, 'status', '--short')
    runs = [_run(make_run_dir, RUN_A)]
    with pytest.raises(publishing.PublishError, match='is not a proteus-bench store checkout'):
        publishing.publish(runs, str(bare_remote), 'results', victim)
    with pytest.raises(publishing.PublishError, match='is not a proteus-bench store checkout'):
        publishing.update_checkout(victim, str(bare_remote), 'results')
    assert (victim / 'model.py').read_text() == 'x = 2  # not committed\n'
    assert (victim / 'scratch.txt').read_text() == 'untracked\n'
    assert git_out(victim, 'rev-parse', 'HEAD') == head
    assert git_out(victim, 'status', '--short') == status
    assert git_out(victim, 'remote') == ''  # no origin was added
    assert not (tmp_path / 'work.lock').exists()
    assert git_out(bare_remote, 'for-each-ref') == ''


def test_own_checkout_is_marked_and_reused(make_run_dir, bare_remote, tmp_path):
    """The checkout this module creates carries the marker, so later publishes reuse it."""
    checkout = tmp_path / 'c'
    publishing.publish([_run(make_run_dir, RUN_A)], str(bare_remote), 'results', checkout)
    assert git_out(checkout, 'config', '--get', publishing.MARKER) == 'true'
    assert publishing.check_checkout(checkout) is None
    publishing.publish([_run(make_run_dir, RUN_B)], str(bare_remote), 'results', checkout)
    assert len(_records(bare_remote)) == 2


def test_lock_serialises_publishers(make_run_dir, bare_remote, tmp_path):
    """While another holder has the cache lock, a publish waits and pushes nothing."""
    checkout = tmp_path / 'c'
    runs = [_run(make_run_dir, RUN_A)]
    outcome = []

    def publish():
        outcome.append(publishing.publish(runs, str(bare_remote), 'results', checkout))

    worker = threading.Thread(target=publish)
    with publishing.locked(checkout):
        worker.start()
        worker.join(1.5)  # an unlocked publish of one run takes well under a second here
        assert worker.is_alive()
        assert git_out(bare_remote, 'for-each-ref') == ''
    worker.join(30)
    assert len(outcome) == 1
    assert outcome[0].added == [RUN_A]


def test_unreachable_remote_and_failing_git(tmp_path, git_env):
    """A missing remote is an error, not a new store; git failures carry exit code and stderr."""
    with pytest.raises(publishing.PublishError, match='cannot read'):
        publishing.update_checkout(tmp_path / 'c', str(tmp_path / 'missing.git'), 'results')
    with pytest.raises(publishing.PublishError) as err:
        publishing.git(tmp_path / 'c', 'rev-parse', '--verify', 'no-such-ref')
    assert 'git rev-parse --verify no-such-ref failed in' in str(err.value)
    assert '(exit 128): fatal: Needed a single revision' in str(err.value)


def test_trigger_dashboard_with_and_without_gh(fake_gh, monkeypatch):
    """With gh the workflow is dispatched; without it the command is printed; failures are reported."""
    remote = 'git@github.com:egpbos/proteus-bench.git'
    message = publishing.trigger_dashboard(remote)
    assert fake_gh.calls() == [['workflow', 'run', 'pages.yml', '-R', 'egpbos/proteus-bench']]
    assert message.startswith('dashboard rebuild started')
    monkeypatch.setenv('FAKE_GH_EXIT', '1')
    monkeypatch.setenv('FAKE_GH_STDERR', 'HTTP 404: workflow not found')
    failed = publishing.trigger_dashboard(remote)
    assert 'failed (exit 1): HTTP 404' in failed
    assert 'retry with: gh workflow run' in failed
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
