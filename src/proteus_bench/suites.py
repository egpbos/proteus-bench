"""Benchmark suites (``suites.toml`` at the repository root) and the run config they define.

A suite names a PROTEUS config file relative to the PROTEUS checkout, dotted
overrides applied to it, and the backends the run is expected to report.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

SUITES_PATH = Path(__file__).resolve().parents[2] / 'suites.toml'


def load_suite(name: str, path: Path = SUITES_PATH) -> dict:
    """Return ``{name, config, overrides, expected_backends}`` for one suite.

    Raises ``ValueError`` when the suites file is missing or has no such suite.
    """
    if not path.is_file():
        raise ValueError(f'suites file {path} not found (expected at the repository root)')
    suites = tomllib.loads(path.read_text()).get('suite', {})
    if name not in suites:
        raise ValueError(f'unknown suite {name!r} in {path}; known: {sorted(suites)}')
    entry = suites[name]
    return {
        'name': name,
        'config': entry['config'],
        'overrides': entry.get('overrides', {}),
        'expected_backends': entry.get('expected_backends', {}),
    }


def apply_overrides(config: dict, overrides: dict) -> dict:
    """Copy of ``config`` with each dotted key set, creating missing tables.

    Raises ``ValueError`` when a key prefix names a value that is not a table.
    """
    out = copy.deepcopy(config)
    for dotted, value in overrides.items():
        *tables, leaf = dotted.split('.')
        node = out
        for depth, part in enumerate(tables):
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                prefix = '.'.join(tables[: depth + 1])
                raise ValueError(f'override {dotted!r}: {prefix!r} is a value, not a table')
        node[leaf] = value
    return out


def build_run_config(proteus_root: Path, suite: dict, run_id: str) -> dict:
    """The config passed to proteus: the suite config, its overrides, output named run_id."""
    source = proteus_root / suite['config']
    if not source.is_file():
        raise ValueError(f'suite {suite["name"]!r}: config {source} not found')
    config = tomllib.loads(source.read_text())
    return apply_overrides(config, {**suite['overrides'], 'params.out.path': run_id})
