"""Tests for proteus_bench.provenance: git state and module versions of a run.

Contract clauses: git_state reports an 8-character sha, the branch (not when
detached) and dirtiness of tracked files only, and refuses directories that
are not the top of a work tree; module entries are editable (with the
checkout's sha), git (with the VCS commit), pypi (no direct_url) or unknown;
AGNI is read from <proteus-root>/AGNI and SOCRATES from $RAD_DIR; the
introspection script runs in the given interpreter and failures are errors.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

from proteus_bench import provenance

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(60)]


def test_git_state_of_a_checkout(tmp_path, git_repo):
    """Clean after commit; an untracked file keeps it clean, a tracked edit makes it dirty."""
    (tmp_path / 'a.txt').write_text('one\n')
    full_sha = git_repo(tmp_path)
    state = provenance.git_state(tmp_path)
    assert state['sha'] == full_sha[:8]
    assert state['dirty'] is False and state['branch'] == 'main'
    (tmp_path / 'bench-runs').mkdir()
    (tmp_path / 'bench-runs' / 'record.json').write_text('{}')
    assert provenance.git_state(tmp_path)['dirty'] is False
    (tmp_path / 'a.txt').write_text('two\n')
    dirty = provenance.git_state(tmp_path)
    assert dirty['dirty'] is True and dirty['describe'].endswith('-dirty')


def test_git_state_refuses_non_toplevel_and_non_repo(tmp_path, git_repo):
    """A subdirectory must not be credited with the enclosing repository's commit."""
    git_repo(tmp_path / 'repo')
    (tmp_path / 'repo' / 'AGNI').mkdir()
    assert provenance.git_state(tmp_path / 'repo' / 'AGNI') is None
    assert provenance.git_state(tmp_path / 'plain') is None
    assert provenance.checkout_state(tmp_path / 'repo' / 'AGNI') == {'source': 'unknown'}
    assert provenance.checkout_state(None) is None
    assert provenance.checkout_state(tmp_path / 'absent') is None


def test_git_state_on_a_detached_head(tmp_path, git_repo):
    """CI checks out a commit, not a branch: no branch is recorded."""
    sha = git_repo(tmp_path)
    provenance.git(tmp_path, 'checkout', '-q', sha)
    state = provenance.git_state(tmp_path)
    assert 'branch' not in state
    assert state['sha'] == sha[:8]


def test_module_state_sources(tmp_path, git_repo):
    """Editable installs carry their checkout sha; VCS installs their commit; index ones none."""
    sha = git_repo(tmp_path / 'aragog')
    editable = {'url': (tmp_path / 'aragog').as_uri(), 'dir_info': {'editable': True}}
    assert provenance.module_state({'version': '0.0.1', 'direct_url': editable}) == {
        'source': 'editable',
        'version': '0.0.1',
        'sha': sha[:8],
        'dirty': False,
    }
    no_git = {'url': (tmp_path / 'plain').as_uri(), 'dir_info': {'editable': True}}
    assert provenance.module_state({'version': '1', 'direct_url': no_git}) == {
        'source': 'editable',
        'version': '1',
    }
    vcs = {'url': 'https://github.com/x/y', 'vcs_info': {'vcs': 'git', 'commit_id': 'ab' * 20}}
    assert provenance.module_state({'version': '2', 'direct_url': vcs})['sha'] == 'abababab'
    archive = {'url': 'file:///w.whl', 'archive_info': {}}
    assert (
        provenance.module_state({'version': '3', 'direct_url': archive})['source'] == 'unknown'
    )
    assert provenance.module_state({'version': '4', 'direct_url': None}) == {
        'source': 'pypi',
        'version': '4',
    }


def test_code_section_includes_agni_and_socrates(tmp_path, git_repo):
    """fwl- prefixes are dropped; AGNI under the root and SOCRATES at RAD_DIR are git entries."""
    agni_sha = git_repo(tmp_path / 'PROTEUS' / 'AGNI')
    soc_sha = git_repo(tmp_path / 'socrates')
    report = {
        'python': '3.12.0',
        'dists': {
            'fwl-aragog': {'version': '26.9.18', 'direct_url': None},
            'numpy': {'version': '2.4.2', 'direct_url': None},
        },
    }
    code = provenance.code_section(
        {'sha': 'abd4ca53', 'dirty': False},
        report,
        tmp_path / 'PROTEUS',
        {'RAD_DIR': str(tmp_path / 'socrates')},
    )
    assert code['modules']['aragog'] == {'source': 'pypi', 'version': '26.9.18'}
    assert code['modules']['agni']['sha'] == agni_sha[:8]
    assert code['modules']['socrates']['sha'] == soc_sha[:8]
    assert code['packages'] == {'numpy': '2.4.2'}
    assert 'numpy' not in code['modules']
    no_rad = provenance.code_section({'sha': 'abd4ca53', 'dirty': False}, report, tmp_path, {})
    assert 'socrates' not in no_rad['modules'] and 'agni' not in no_rad['modules']


def test_introspect_env_runs_the_target_interpreter():
    """The report comes from the given Python; an unusable interpreter is an error."""
    report = provenance.introspect_env(sys.executable)
    assert report['python'] == platform.python_version()
    assert set(report['dists']) <= set(provenance.MODULE_DISTS + provenance.PACKAGES)
    with pytest.raises(ValueError, match='cannot run --python'):
        provenance.introspect_env('/nonexistent/python')
    with pytest.raises(ValueError, match='failed to report'):
        provenance.introspect_env('false')


def test_harness_state_from_its_checkout():
    """Run from the source tree, the harness records the tree's commit."""
    state = provenance.harness_state()
    repo = Path(provenance.__file__).resolve().parents[2]
    assert state['sha'] == provenance.git(repo, 'rev-parse', '--short=8', 'HEAD')
    assert state['version'] == provenance.__version__
