"""Tests for the bundled run record schema (v1) and the example record.

Contract clauses: the schema is valid Draft 2020-12; the committed example
satisfies it and its component rows add up to its phases; typical mistakes
(unknown keys, bad enums, malformed hashes) are rejected at the field that is
wrong.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip('jsonschema')

from proteus_bench import schema  # noqa: E402
from proteus_bench.settings import PER_RUN_KEYS, settings_hash  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

EXAMPLES = Path(__file__).parent.parent / 'examples'


@pytest.fixture(scope='module')
def example_record():
    return json.loads((EXAMPLES / 'record.json').read_text())


def test_schemas_are_valid_draft_2020_12():
    """The bundled schema is itself valid, so validation results mean something."""
    for kind in schema.SCHEMAS:
        jsonschema.Draft202012Validator.check_schema(schema.load(kind))
    broken = {'type': 'object', 'properties': {'x': {'type': 'no-such-type'}}}
    with pytest.raises(jsonschema.SchemaError):
        jsonschema.Draft202012Validator.check_schema(broken)


def test_example_record_settings_hash_is_reproducible(example_record):
    """The stored hash is what settings_hash gives, and no per-run key is stored."""
    bench = example_record['benchmark']
    assert settings_hash(bench['settings']) == bench['settings_hash']
    assert not PER_RUN_KEYS & bench['settings'].keys()
    # A per-run key sneaking back in must not change the hash, or lineages would split.
    renamed = {**bench['settings'], 'params.out.path': 'another_run'}
    assert settings_hash(renamed) == bench['settings_hash']


def test_example_record_matches_schema_and_its_rows_add_up(example_record):
    """The record example is valid, and per-phase component rows add up to the phase totals."""
    assert schema.shape_problems('record', example_record) == []
    phases = example_record['timings']['phases']
    for phase, expected in phases.items():
        rows = [
            c['total_s'] for c in example_record['timings']['components'] if c['phase'] == phase
        ]
        if expected is None:
            assert rows == []
        else:
            assert sum(rows) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize(
    ('path', 'value', 'where'),
    [
        (('benchmark', 'settings_hash'), 'md5:abc', 'benchmark/settings_hash'),
        (('env', 'socrates_build'), 'fast', 'env/socrates_build'),
        (('machine', 'n_cpus'), 0, 'machine/n_cpus'),
        (('run_id',), 'run 1', 'run_id'),
        (('code', 'proteus', 'sha'), 'HEAD', 'code/proteus/sha'),
    ],
    ids=['hash_prefix', 'socrates_mode', 'zero_cpus', 'run_id_format', 'symbolic_sha'],
)
def test_bad_record_fields_are_rejected(example_record, path, value, where):
    """Typical record mistakes fail at the offending field."""
    record = copy.deepcopy(example_record)
    node = record
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    problems = schema.shape_problems('record', record)
    assert problems
    assert all(p.startswith(f'{where}:') for p in problems)


def test_record_requires_comparability_and_rejects_unknown_top_level_keys(example_record):
    """Top-level typos and a missing comparability verdict are both errors."""
    record = copy.deepcopy(example_record)
    del record['comparability']
    assert any(
        "'comparability' is a required property" in p
        for p in schema.shape_problems('record', record)
    )
    record = copy.deepcopy(example_record)
    record['timing'] = {}
    assert any('Additional properties' in p for p in schema.shape_problems('record', record))
