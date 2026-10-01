"""Tests for proteus_bench.record: trigger detection and record sections.

Contract clauses: GHA is detected before Slurm, and local otherwise; the GHA
trigger carries a run URL and integer run id, the Slurm one its job, partition
and node when set; timestamps are UTC with a Z suffix; the default suite's
lineage is 'default', others use the settings hash; settings drop per-run keys.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pytest

from proteus_bench import record

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

GHA_ENV = {
    'GITHUB_ACTIONS': 'true',
    'GITHUB_ACTOR': 'egpbos',
    'GITHUB_REPOSITORY': 'FormingWorlds/PROTEUS',
    'GITHUB_RUN_ID': '1234567890',
    'GITHUB_WORKFLOW': 'bench',
    'GITHUB_EVENT_NAME': 'schedule',
}


def test_adapter_detection():
    """GITHUB_ACTIONS=true wins over a Slurm job id; neither means local."""
    assert record.detect_adapter(GHA_ENV | {'SLURM_JOB_ID': '1'}) == 'gha'
    assert record.detect_adapter({'SLURM_JOB_ID': '31948135'}) == 'slurm'
    assert record.detect_adapter({'GITHUB_ACTIONS': 'false'}) == 'local'
    assert record.detect_adapter({}) == 'local'


def test_trigger_sections():
    """GHA gets a clickable run URL; Slurm records only the variables that are set."""
    gha = record.trigger_section('gha', GHA_ENV)
    assert gha['user'] == 'egpbos'
    assert gha['gha'] == {
        'run_url': 'https://github.com/FormingWorlds/PROTEUS/actions/runs/1234567890',
        'run_id': 1234567890,
        'workflow': 'bench',
        'event': 'schedule',
    }
    slurm = record.trigger_section('slurm', {'SLURM_JOB_ID': '7', 'SLURMD_NODENAME': 'vink15'})
    assert slurm['slurm'] == {'job_id': '7', 'node': 'vink15'}
    assert 'gha' not in slurm
    assert slurm['user']


def test_utc_iso():
    """Offsets are converted to UTC and written with Z, to the second."""
    cet = dt.timezone(dt.timedelta(hours=2))
    assert record.utc_iso(dt.datetime(2026, 9, 25, 5, 10, 0, 999, tzinfo=cet)) == (
        '2026-09-25T03:10:00Z'
    )
    assert record.utc_iso(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)) == '2026-01-01T00:00:00Z'


def _ctx(suite_name: str) -> record.RunContext:
    suite = {'name': suite_name, 'config': 'input/all_options.toml', 'overrides': {'a': 1},
             'expected_backends': {}}  # fmt: skip
    return record.RunContext(
        suite, 'r', Path('.'), {}, {}, {}, {}, {}, {}, [], None, Path('.'), [], {}, None
    )


def test_benchmark_lineage_and_settings():
    """Default suite -> lineage 'default'; another suite -> its settings hash."""
    flat = {'params.out.path': 'run', 'interior_struct.module': 'zalmoxis'}
    default = record.benchmark_section(_ctx('default'), flat)
    assert default['lineage'] == 'default'
    assert default['name'] == 'all_options'
    assert default['settings'] == {'interior_struct.module': 'zalmoxis'}  # per-run key dropped
    other = record.benchmark_section(_ctx('small'), flat)
    assert other['lineage'] == other['settings_hash'] == default['settings_hash']
    assert other['lineage'].startswith('sha256:')


def test_non_finite_settings_become_null_but_keep_their_hash():
    """TOML allows inf and nan; the record holds null, the hash still tells them apart."""
    flat = {
        'a.inf': math.inf,
        'a.list': [1.0, -math.inf],
        'a.rows': [{'x': math.nan}],
        'b': 2.5,
    }
    section = record.benchmark_section(_ctx('default'), flat)
    assert section['settings'] == {
        'a.inf': None,
        'a.list': [1.0, None],
        'a.rows': [{'x': None}],
        'b': 2.5,
    }
    assert json.loads(json.dumps(section, allow_nan=False))['settings']['b'] == 2.5
    nan_instead = record.benchmark_section(_ctx('default'), {**flat, 'a.inf': math.nan})
    assert nan_instead['settings_hash'] != section['settings_hash']


def test_record_with_a_non_finite_value_is_refused(tmp_path):
    """NaN is not JSON: writing it fails instead of leaving an unreadable record."""
    with pytest.raises(ValueError, match='not JSON compliant'):
        record.write_record(tmp_path, {'timings': {'wall_s': math.nan}})
    assert not (tmp_path / 'record.json').exists()
    path = record.write_record(tmp_path, {'timings': {'wall_s': 1.5}})
    assert json.loads(path.read_text()) == {'timings': {'wall_s': 1.5}}
