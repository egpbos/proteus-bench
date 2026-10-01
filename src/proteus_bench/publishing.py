"""Publishing checked runs to the results store, a branch on a git remote.

All git access goes through the ``git`` CLI with the user's own configuration
and credentials. The local checkout is a cache that every attempt resets to the
remote tip, so only a directory carrying ``MARKER`` is reused. After a rejected
push the next attempt stages the runs again on the new tip, which for add-only
commits equals a rebase and also notices runs another publisher added.
"""

from __future__ import annotations

import fcntl
import re
import shlex
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from proteus_bench import store

ATTEMPTS = 3
MARKER = 'proteus-bench.store'  # git config key set in checkouts this module created
PAGES_WORKFLOW = 'pages.yml'
GITHUB_REMOTE = re.compile(
    r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
    r'([\w.-]+/[\w.-]+?)(?:\.git)?/?'
)


class PublishError(RuntimeError):
    pass


@dataclass
class Published:
    commit: str | None = None  # None when nothing was new
    added: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # run_id -> commit that has it
    retries: int = 0


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode:
        raise PublishError(
            f'git {shlex.join(args)} failed in {repo} (exit {proc.returncode}): '
            f'{proc.stderr.strip()}'
        )
    return proc


def _is_own_checkout(checkout: Path) -> bool:
    config = checkout / '.git' / 'config'
    if not config.is_file():
        return False
    proc = git(checkout, 'config', '--file', str(config), '--get', MARKER, check=False)
    return proc.stdout.strip() == 'true'


def check_checkout(checkout: Path) -> None:
    """Raise PublishError unless ``checkout`` is absolute and missing, empty or marked."""
    if not checkout.is_absolute():
        raise PublishError(f'store checkout must be an absolute path, not {str(checkout)!r}')
    if not checkout.exists() or (checkout.is_dir() and not any(checkout.iterdir())):
        return
    if not _is_own_checkout(checkout):
        raise PublishError(
            f'{checkout} exists and is not a proteus-bench store checkout (git config '
            f'{MARKER} is not set), so it is left alone. Set store.cache_dir to a new '
            'directory, or remove this one if it is a stale cache.'
        )


def update_checkout(checkout: Path, remote: str, branch: str) -> bool:
    """Make ``checkout`` a clean copy of ``branch`` on ``remote``.

    Returns False when the remote has no such branch; the checkout is then an
    empty orphan branch of that name.
    """
    check_checkout(checkout)
    if not _is_own_checkout(checkout):
        checkout.mkdir(parents=True, exist_ok=True)
        git(checkout, 'init', '-q')
        git(checkout, 'config', MARKER, 'true')
    git(checkout, 'config', 'remote.origin.url', remote)
    probe = git(checkout, 'ls-remote', '--exit-code', '--heads', 'origin', branch, check=False)
    if probe.returncode not in (0, 2):  # 2: reachable, but no such branch
        raise PublishError(f'cannot read {remote}: {probe.stderr.strip()}')
    tracking = f'refs/remotes/origin/{branch}'
    if probe.returncode == 0:
        git(checkout, 'fetch', '-q', 'origin', f'+refs/heads/{branch}:{tracking}')
        git(checkout, 'checkout', '-q', '-f', '-B', branch, tracking)
    else:
        git(checkout, 'symbolic-ref', 'HEAD', f'refs/heads/{branch}')
        git(checkout, 'update-ref', '-d', f'refs/heads/{branch}')
        git(checkout, 'read-tree', '--empty')
    git(checkout, 'clean', '-q', '-f', '-d', '-x')
    return probe.returncode == 0


@contextmanager
def locked(checkout: Path):
    """Hold the lock of a store checkout, shared by publishers on one machine."""
    check_checkout(checkout)
    checkout.parent.mkdir(parents=True, exist_ok=True)
    with open(checkout.parent / f'{checkout.name}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def publish(
    runs: list[tuple[Path, dict]], remote: str, branch: str, checkout: Path
) -> Published:
    """Add checked runs with distinct ids to the store in one commit and push it.

    Runs already in the store are skipped. Every run dir gets ``.published``
    holding the store commit that has the run.
    """
    retries = 0
    with locked(checkout):
        for _ in range(ATTEMPTS):
            result = _commit_runs(runs, remote, branch, checkout)
            result.retries = retries
            if result.commit is None or _pushed(checkout, branch, result.commit):
                for run_dir, record in runs:
                    commit = result.skipped.get(record['run_id'], result.commit)
                    (run_dir / '.published').write_text(f'{commit}\n')
                return result
            retries += 1
    raise PublishError(f'push to {remote} still rejected after {ATTEMPTS} attempts')


def _commit_runs(runs, remote: str, branch: str, checkout: Path) -> Published:
    result = Published()
    if not update_checkout(checkout, remote, branch):
        (checkout / 'README.md').write_text(store.README)
    for run_dir, record in runs:
        path = store.record_path(record['run_id'])
        if (checkout / path).exists():
            log = git(checkout, 'log', '-1', '--format=%H', '--', path)
            result.skipped[record['run_id']] = log.stdout.strip()
        else:
            store.stage_run(run_dir, record, checkout)
            result.added.append(record['run_id'])
    if result.added:
        git(checkout, 'add', '-A')
        git(checkout, 'commit', '-q', '-m', f'Add runs {" ".join(result.added)}')
        result.commit = git(checkout, 'rev-parse', 'HEAD').stdout.strip()
    return result


def _push(checkout: Path, branch: str) -> subprocess.CompletedProcess:
    return git(
        checkout, 'push', '--porcelain', 'origin', f'HEAD:refs/heads/{branch}', check=False
    )


def _pushed(checkout: Path, branch: str, commit: str) -> bool:
    """Push ``commit``; True when it is on the remote, False when rejected as behind.

    ``--porcelain`` prints ``<flag>\\t<from>:<to>\\t<summary>`` per ref (git-push(1),
    OUTPUT). Other failures are checked against the remote tip, because a push
    can land while git exits non-zero (the connection dropped afterwards).
    """
    proc = _push(checkout, branch)
    if proc.returncode == 0:
        return True
    ref = f'refs/heads/{branch}'
    for line in proc.stdout.splitlines():
        flag, _, rest = line.partition('\t')
        refs, _, summary = rest.partition('\t')
        if flag == '!' and refs.endswith(f':{ref}') and summary.startswith('[rejected]'):
            return False
    tip = git(checkout, 'ls-remote', 'origin', ref, check=False).stdout.split('\t')[0]
    if tip == commit:
        return True
    raise PublishError(f'push failed (exit {proc.returncode}): {proc.stderr.strip()}')


def github_repo(remote: str) -> str | None:
    match = GITHUB_REMOTE.fullmatch(remote)
    return match.group(1) if match else None


def trigger_dashboard(remote: str) -> str:
    """Dispatch the store repository's Pages workflow with gh; return what happened.

    A push to the store branch cannot start that workflow, which lives on main.
    Never raises: the runs are already published and the workflow also runs daily.
    """
    repo = github_repo(remote)
    if repo is None:
        return f'dashboard not triggered: {remote} is not a github.com repository'
    cmd = ['gh', 'workflow', 'run', PAGES_WORKFLOW, '-R', repo]
    if shutil.which('gh') is None:
        return f'gh not found; to rebuild the dashboard now, run: {shlex.join(cmd)}'
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        return (
            f'dashboard trigger failed (exit {proc.returncode}): {proc.stderr.strip()}; '
            f'retry with: {shlex.join(cmd)}'
        )
    return f'dashboard rebuild started: {shlex.join(cmd)}'
