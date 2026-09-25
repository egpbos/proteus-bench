"""Benchmark suites (``proteus_bench/suites.toml``) and the run config they define.

A suite names a PROTEUS config file relative to the PROTEUS checkout, dotted
overrides applied to it, and the backends the run is expected to report. The
file ships inside the package, so it is there however the harness is installed.
"""

from __future__ import annotations

import copy
import re
import tomllib
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

SUITES = resources.files('proteus_bench').joinpath('suites.toml')
SUITE_KEYS = frozenset({'config', 'overrides', 'expected_backends'})
# A dotted key: non-empty parts separated by single dots
_DOTTED = re.compile(r'[^.]+(\.[^.]+)*')


def load_suite(name: str, source: Traversable | Path = SUITES) -> dict:
    """Return ``{name, config, overrides, expected_backends}`` for one suite.

    Raises ``ValueError`` when the file is missing, has no such suite, or the
    suite is malformed, so a bad suite fails before a run rather than after it.
    """
    if not source.is_file():
        raise ValueError(f'suites file {source} not found')
    suites = tomllib.loads(source.read_text()).get('suite', {})
    if name not in suites:
        raise ValueError(f'unknown suite {name!r} in {source}; known: {sorted(suites)}')
    entry = suites[name]
    problems = suite_problems(entry)
    if problems:
        raise ValueError(f'suite {name!r} in {source}: ' + '; '.join(problems))
    return {
        'name': name,
        'config': entry['config'],
        'overrides': entry.get('overrides', {}),
        'expected_backends': entry.get('expected_backends', {}),
    }


def suite_problems(entry: dict) -> list[str]:
    """What is wrong with one ``[suite.NAME]`` table; empty when it is usable."""
    problems = [f'unknown key {k!r}' for k in sorted(set(entry) - SUITE_KEYS)]
    if not isinstance(entry.get('config'), str) or not entry.get('config'):
        problems.append('config must be a path to a PROTEUS config file')
    overrides = entry.get('overrides', {})
    if not isinstance(overrides, dict):
        problems.append('overrides must be a table')
    else:
        problems += [
            f'override key {k!r} has an empty part'
            for k in overrides
            if not _DOTTED.fullmatch(k)
        ]
    expected = entry.get('expected_backends', {})
    if not isinstance(expected, dict):
        return [*problems, 'expected_backends must be a table']
    for key, value in expected.items():
        if not _DOTTED.fullmatch(key) or '.' not in key:
            problems.append(f'expected_backends key {key!r} is not "<submodule>.<key>"')
        if not isinstance(value, str):
            problems.append(f'expected_backends {key!r} must be a string, got {value!r}')
    return problems


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
