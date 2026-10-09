"""Package-level contract: proteus-bench adds only tomli-w to a PROTEUS environment.

The harness is installed into PROTEUS environments for runs, so it must not constrain
them: tomli-w is its one unconditional requirement, everything else sits in extras, and
the runtime code imports nothing third-party but tomli-w.
"""

from __future__ import annotations

import subprocess
import sys
from importlib import metadata

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_distribution_requires_only_tomli_w_outside_extras():
    """Every Requires-Dist entry but tomli-w is conditional on an extra."""
    requirements = metadata.requires('proteus-bench') or []
    unconditional = [r for r in requirements if 'extra ==' not in r]
    assert unconditional == ['tomli-w>=1']
    # The dev extra really exists, so the check above is not vacuous.
    assert any('jsonschema' in r and 'extra == "dev"' in r for r in requirements)


def test_runtime_uses_only_the_standard_library_and_tomli_w():
    """``proteus-bench run`` and the profilers load nothing third-party but tomli-w.

    AGENTS.md architecture rule 1. The CLI imports every subcommand module to
    build its parser, so the other commands must import their extras lazily.
    """
    script = (
        'import sys\n'
        'ALLOWED = {"proteus_bench", "tomli_w"}\n'
        'class Block:\n'
        '    def find_spec(self, name, path=None, target=None):\n'
        '        top = name.partition(".")[0]\n'
        '        if top not in sys.stdlib_module_names and top not in ALLOWED:\n'
        '            raise ImportError(f"third-party import in runtime code: {name}")\n'
        'sys.meta_path.insert(0, Block())\n'
        'import proteus_bench.commands.run, proteus_bench.profiling\n'
        'from proteus_bench import cli\n'
        "sys.exit(cli.main(['run', '--help']))\n"
    )
    proc = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert 'usage: proteus-bench run' in proc.stdout
