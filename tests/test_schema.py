"""Tests for the bundled JSON Schemas (timing v1, run record v1) and the examples.

Contract clauses: both schemas are valid Draft 2020-12; the committed examples
satisfy them and each other (record timings are derived from the timing
example); typical mistakes (unknown keys, bad names, negative durations, bad
enums, malformed hashes) are rejected at the field that is wrong.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip('jsonschema')

from proteus_bench import schema  # noqa: E402
from proteus_bench.settings import PER_RUN_KEYS, settings_hash  # noqa: E402
from proteus_bench.timing import attributed_totals, read_events  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

EXAMPLES = Path(__file__).parent.parent / 'examples'


@pytest.fixture(scope='module')
def example_events():
    return read_events(EXAMPLES / 'timing.jsonl')


@pytest.fixture(scope='module')
def example_record():
    return json.loads((EXAMPLES / 'record.json').read_text())


def test_schemas_are_valid_draft_2020_12():
    """Both bundled schemas are themselves valid, so validation results mean something."""
    for kind in schema.SCHEMAS:
        jsonschema.Draft202012Validator.check_schema(schema.load(kind))
    broken = {'type': 'object', 'properties': {'x': {'type': 'no-such-type'}}}
    with pytest.raises(jsonschema.SchemaError):
        jsonschema.Draft202012Validator.check_schema(broken)


def test_example_timing_events_match_the_schema(example_events):
    """Every example event passes, and all four event kinds are represented."""
    assert all(schema.shape_problems('timing', ev) == [] for ev in example_events)
    assert {ev['ev'] for ev in example_events} == {'run_start', 'backend', 'span', 'run_end'}


@pytest.mark.parametrize(
    ('field', 'value', 'where'),
    [
        ('dur', -1.0, 'dur'),
        ('name', 'Structure', 'name'),
        ('unexpected', 1, '<root>'),
        ('id', 0, 'id'),
    ],
    ids=['negative_duration', 'capitalised_name', 'unknown_key', 'zero_id'],
)
def test_bad_span_fields_are_rejected_at_the_field(example_events, field, value, where):
    """A wrong span field is reported against that field, not somewhere vague."""
    span = copy.deepcopy(next(ev for ev in example_events if ev['ev'] == 'span'))
    assert schema.shape_problems('timing', span) == []
    span[field] = value
    problems = schema.shape_problems('timing', span)
    assert problems and all(p.startswith(f'{where}:') for p in problems)


def test_run_end_status_is_an_enum(example_events):
    """Only ok, error and interrupted are valid run statuses."""
    end = copy.deepcopy(example_events[-1])
    end['status'] = 'finished'
    assert any(p.startswith('status:') for p in schema.shape_problems('timing', end))
    end['status'] = 'interrupted'
    assert schema.shape_problems('timing', end) == []


def test_example_record_settings_hash_is_reproducible(example_record):
    """The stored hash is what settings_hash gives, and no per-run key is stored."""
    bench = example_record['benchmark']
    assert settings_hash(bench['settings']) == bench['settings_hash']
    assert not PER_RUN_KEYS & bench['settings'].keys()
    # A per-run key sneaking back in must not change the hash, or lineages would split.
    renamed = {**bench['settings'], 'params.out.path': 'another_run'}
    assert settings_hash(renamed) == bench['settings_hash']


def test_example_record_matches_schema_and_timing_example(example_record, example_events):
    """The record example is valid and its phase totals come from the timing example."""
    assert schema.shape_problems('record', example_record) == []
    totals = attributed_totals(example_events)
    phases = example_record['timings']['phases']
    assert {p: phases[p] for p in totals} == pytest.approx(
        {p: t['total'] for p, t in totals.items()}
    )
    # Per-phase component rows (including 'other') add back up to the phase totals.
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
    assert problems and all(p.startswith(f'{where}:') for p in problems)


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
