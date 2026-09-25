"""Publishing checked runs to the results store, a branch on a git remote.

Everything goes through the ``git`` CLI, so the user's own git configuration
and credentials apply; this module never sees a token. The local checkout is a
cache: each attempt resets it to the remote tip, stages the runs on top, commits
and pushes. A rejected push means another publisher got there first; the next
attempt fetches the new tip and stages again, which for add-only commits is the
same as a rebase and also notices runs the other publisher already added.
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
PAGES_WORKFLOW = 'pages.yml'
GITHUB_REMOTE = re.compile(
    r'^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
    r'([\w.-]+/[\w.-]+?)(?:\.git)?/?$'
)


class PublishError(RuntimeError):
    """A git step failed; the message holds the command and git's error output."""


@dataclass
class Published:
    """Outcome of one ``publish`` call."""

    commit: str | None = None  # store commit that added the runs; None if nothing new
    added: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # run_id -> commit that has it


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in ``repo``; raise PublishError on failure when ``check``."""
    proc = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode:
        raise PublishError(
            f'git {shlex.join(args)} failed in {repo} (exit {proc.returncode}): '
            f'{proc.stderr.strip()}'
        )
    return proc


def update_checkout(checkout: Path, remote: str, branch: str) -> bool:
    """Make ``checkout`` a clean copy of ``branch`` on ``remote``.

    Returns False when the remote has no such branch; the checkout is then an
    empty orphan branch of that name, ready for the first commit.
    """
    checkout.mkdir(parents=True, exist_ok=True)
    if not (checkout / '.git').exists():
        git(checkout, 'init', '-q')
    if git(checkout, 'remote', 'get-url', 'origin', check=False).returncode:
        git(checkout, 'remote', 'add', 'origin', remote)
    else:
        git(checkout, 'remote', 'set-url', 'origin', remote)
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
    """Hold the machine-wide lock of a store checkout."""
    # Two publishers on one machine (e.g. Slurm jobs ending together) share the cache
    checkout.parent.mkdir(parents=True, exist_ok=True)
    with open(checkout.parent / f'{checkout.name}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def publish(
    runs: list[tuple[Path, dict]], remote: str, branch: str, checkout: Path
) -> Published:
    """Add checked runs (run dir, record) to the store in one commit and push it.

    Run ids must be distinct (``store.check_run`` does not check that across
    runs; the publish command does). Runs already in the store are skipped. Each added run dir gets a
    ``.published`` file holding the store commit. Raises PublishError when git
    fails or the push is still rejected after ``ATTEMPTS`` tries.
    """
    with locked(checkout):
        for attempt in range(1, ATTEMPTS + 1):
            result = _commit_runs(runs, remote, branch, checkout)
            if result.commit is None:
                return result
            push = _push(checkout, branch)
            if push.returncode == 0:
                for run_dir, record in runs:
                    if record['run_id'] in result.added:
                        (run_dir / '.published').write_text(result.commit + '\n')
                return result
            if '[rejected]' not in push.stderr:
                raise PublishError(f'push to {remote} failed: {push.stderr.strip()}')
            print(f'push rejected: the store moved on; retrying ({attempt}/{ATTEMPTS})')
    raise PublishError(f'push to {remote} still rejected after {ATTEMPTS} attempts')


def _commit_runs(runs, remote: str, branch: str, checkout: Path) -> Published:
    """Stage the runs not yet in the store on the remote tip and commit them."""
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
    return git(checkout, 'push', '-q', 'origin', f'HEAD:refs/heads/{branch}', check=False)


def github_repo(remote: str) -> str | None:
    """``owner/name`` for a github.com remote URL, else None."""
    match = GITHUB_REMOTE.match(remote)
    return match.group(1) if match else None


def trigger_dashboard(remote: str) -> str:
    """Start the Pages workflow of the store's repository; return what happened.

    A push to the store branch cannot start the workflow itself (it lives on
    main), so it is dispatched with ``gh`` when available. Never raises: the
    runs are already published, and the workflow also runs daily.
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
