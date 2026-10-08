"""Tests for proteus_bench.publishing that need no git: remote parsing and checkout safety.

Contract clauses: only github.com remotes map to owner/name, look-alike hosts
and trailing text do not; a non-GitHub remote is reported without looking for
gh; ``check_checkout`` accepts a missing path or an empty directory, and
refuses relative paths and existing directories without the store marker.
The git-driven behaviour is in test_publishing_git.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proteus_bench import publishing

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.mark.parametrize(
    ('remote', 'repo'),
    [
        ('https://github.com/egpbos/proteus-bench.git', 'egpbos/proteus-bench'),
        ('https://github.com/FormingWorlds/proteus-bench', 'FormingWorlds/proteus-bench'),
        ('git@github.com:egpbos/proteus-bench.git', 'egpbos/proteus-bench'),
        ('ssh://git@github.com/egpbos/proteus.bench.git', 'egpbos/proteus.bench'),
        ('https://gitlab.com/egpbos/proteus-bench.git', None),
        ('https://github.com.evil.org/egpbos/proteus-bench.git', None),
        ('https://github.com/egpbos/proteus-bench.git\n', None),
        ('/srv/git/proteus-bench.git', None),
    ],
)
def test_github_repo_parsing(remote, repo):
    """Only whole github.com remote URLs map to owner/name."""
    assert publishing.github_repo(remote) == repo
    if repo is None:  # returns before looking for gh, so nothing is dispatched
        assert publishing.trigger_dashboard(remote) == (
            f'dashboard not triggered: {remote} is not a github.com repository'
        )


def test_check_checkout_accepts_new_and_empty(tmp_path):
    """A path that does not exist yet, or an empty directory, may become the checkout."""
    assert publishing.check_checkout(tmp_path / 'new' / 'store') is None
    (tmp_path / 'empty').mkdir()
    assert publishing.check_checkout(tmp_path / 'empty') is None


def test_check_checkout_refuses_relative_and_foreign(tmp_path):
    """A relative path (e.g. '' from an empty setting) and a used directory are refused."""
    for relative in (Path(''), Path('store')):
        with pytest.raises(publishing.PublishError, match='must be an absolute path'):
            publishing.check_checkout(relative)
    used = tmp_path / 'used'
    used.mkdir()
    (used / 'notes.txt').write_text('keep me\n')
    with pytest.raises(publishing.PublishError, match='is not a proteus-bench store checkout'):
        publishing.check_checkout(used)
    assert (used / 'notes.txt').read_text() == 'keep me\n'
