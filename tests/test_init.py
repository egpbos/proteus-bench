"""Package-level contract: proteus-bench has no runtime dependencies.

The harness is installed into PROTEUS environments, so it must not constrain them:
the distribution declares requirements only inside extras, and every module imports
and the validator runs with the optional ``jsonschema`` unavailable.
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

import proteus_bench

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

EXAMPLES = Path(__file__).parent.parent / 'examples'


def test_distribution_declares_requirements_only_in_extras():
    """Every Requires-Dist entry is conditional on an extra, so a plain install adds nothing."""
    requirements = metadata.requires('proteus-bench') or []
    unconditional = [r for r in requirements if 'extra ==' not in r]
    assert unconditional == []
    # The dev extra really exists, so the check above is not vacuous.
    assert any('jsonschema' in r and 'extra == "dev"' in r for r in requirements)


def test_every_module_imports_and_validate_runs_without_jsonschema():
    """With jsonschema blocked, all modules import and validate still checks the tree rules."""
    modules = [m.name for m in pkgutil.walk_packages(proteus_bench.__path__, 'proteus_bench.')]
    assert 'proteus_bench.commands.validate' in modules
    script = (
        'import sys, importlib\n'
        "sys.modules['jsonschema'] = None  # makes 'import jsonschema' raise ImportError\n"
        f'for name in {modules!r}: importlib.import_module(name)\n'
        'from proteus_bench import cli\n'
        f"sys.exit(cli.main(['validate', {str(EXAMPLES / 'timing.jsonl')!r}]))\n"
    )
    proc = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert 'shape not checked' in proc.stdout
    assert importlib.import_module('proteus_bench.schema').available()  # here it is installed
