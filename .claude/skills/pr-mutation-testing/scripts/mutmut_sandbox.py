"""pytest plugin for mutation runs: each test runs in its own empty working folder.

A mutant that drops a folder argument (os.path.join(dir, '*') to os.path.join('*'))
then deletes files in that folder, not in mutmut's state folder.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mutation_sandbox_cwd(tmp_path_factory, monkeypatch):
    monkeypatch.chdir(tmp_path_factory.mktemp('cwd'))
