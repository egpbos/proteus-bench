"""Tests for proteus_bench.profiling.package_dirs, which starts a Python subprocess.

Contract clauses: installed regular packages are located by path without being
imported; single-file modules and missing names are left out; the current
directory is not searched; an interpreter that cannot run or that fails raises
RuntimeError with the reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from proteus_bench.profiling import package_dirs

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(30)]


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A PYTHONPATH directory with two packages that exit if imported, and a module."""
    site = tmp_path / 'site'
    for pkg in ('pkg_a', 'pkg_a_extra'):
        (site / pkg).mkdir(parents=True)
        (site / pkg / '__init__.py').write_text('raise SystemExit("imported")\n')
    (site / 'mod_c.py').write_text('')
    (tmp_path / 'cwd' / 'pkg_b').mkdir(parents=True)
    (tmp_path / 'cwd' / 'pkg_b' / '__init__.py').write_text('')
    monkeypatch.chdir(tmp_path / 'cwd')
    monkeypatch.setenv('PYTHONPATH', str(site))
    return site


def test_locates_packages_only_and_ignores_the_current_directory(site):
    """Packages are found by path; a module, a cwd package and a missing name are skipped."""
    names = ('pkg_a', 'pkg_b', 'mod_c', 'missing')
    found = package_dirs(Path(sys.executable), names)
    assert found == {'pkg_a': str(site / 'pkg_a')}  # located, not imported (it would exit)
    assert 'pkg_a_extra' not in found  # only the names asked for


def test_interpreter_failures_raise(site, tmp_path):
    """A missing interpreter and a failing lookup both raise with the reason."""
    with pytest.raises(RuntimeError, match='cannot run'):
        package_dirs(tmp_path / 'no-python', ('pkg_a',))
    # find_spec of a submodule imports its parent first, which fails here.
    with pytest.raises(
        RuntimeError, match='(?s)failed to locate packages: .*ModuleNotFoundError'
    ):
        package_dirs(Path(sys.executable), ('no_parent.child',))
