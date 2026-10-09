"""Fixture results store for the dashboard tests.

Ten records derived from ``examples/record.json``:

- group all_options / default / habrok-vink, runs r1..r8 on 2026-09-18..25:
  from r2 on ``interior_struct.zalmoxis.use_jax`` is true (a settings boundary
  before r2, and init 66 % faster); r4 is not comparable (Aragog fell back to
  Radau, so its expected_backends check failed); from r6 on the atmosphere is
  8 % slower (a step, flagged on r6 and r7, both confirmed, and on r8, not yet
  confirmed); r6 also has a one-off slow interior (flagged, not confirmed by
  r7); r8 names a flame page (publisher HTML, which the site must not link);
  r2's log file is missing from the store.
- group all_options / default / gha-ubuntu-epyc7763, runs g1 (ok) and g2
  (failed), too few for a baseline.

The ``analysis`` fixture is ``proteus_bench.analysis.analyse`` of these records.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from proteus_bench.analysis import analyse
from proteus_bench.report.site import build_site
from proteus_bench.report.store import load_records
from proteus_bench.settings import settings_hash

EXAMPLE = Path(__file__).parent.parent.parent / 'examples' / 'record.json'
JITTER = (1.0, 1.006, 0.994, 1.01, 0.998, 1.003, 0.997, 1.004)
SUBMODULE = {
    'atmos': 'agni',
    'structure': 'zalmoxis',
    'interior': 'aragog',
    'outgas': 'calliope',
}
FLAME_RUN = '20260925T031000Z-habrok-default-r8'
MISSING_LOG_RUN = '20260919T031000Z-habrok-default-r2'


def fixture_timings(j: float, atmos: float, interior: float, jax: bool) -> dict:
    """Six iterations shaped like the example run, every duration scaled by ``j``."""
    per_iter = []
    for n in range(1, 7):
        comps = {'interior': interior, 'outgas': 1.0, 'atmos': 780.0 if n == 1 else atmos}
        if n % 3 == 0:
            comps['structure'] = 140.0
        comps = {k: round(v * j, 3) for k, v in comps.items()}
        dur = round(sum(comps.values()) + 0.2, 3)
        per_iter.append({'iter': n, 'dur_s': dur, 'init_stage': n == 1, 'components': comps})
    structure = (600.0, 'jax', 3) if jax else (1768.0, 'numpy', 3)
    init_rows = [
        ('structure', 'zalmoxis', structure[1], round(structure[0] * j, 3), structure[2]),
        ('outgas', 'calliope', None, 2.0, 2),
        ('stellar', 'mors', None, 2.0, 1),
        ('other', None, None, 0.0, 0),
    ]
    rows = [_row('init', *r) for r in init_rows]
    for comp, sub in SUBMODULE.items():
        total = round(sum(it['components'].get(comp, 0.0) for it in per_iter), 3)
        calls = sum(comp in it['components'] for it in per_iter)
        backend = {'structure': 'jax', 'interior': 'cvode'}.get(comp)
        rows.append(_row('loop', comp, sub, backend, total, calls))
    rows += [
        _row('loop', 'other', None, None, 1.2, 0),
        _row('shutdown', 'other', None, None, 5.0, 0),
    ]
    init = round(sum(r['total_s'] for r in rows if r['phase'] == 'init'), 3)
    loop = round(sum(it['dur_s'] for it in per_iter), 3)
    phases = {'setup': None, 'init': init, 'loop': loop, 'shutdown': 5.0}
    return {
        'wall_s': round(21.0 + init + loop + 5.0, 3),
        'startup_s': 21.0,
        'phases': phases,
        'components': rows,
        'per_iter': per_iter,
        'rusage': {'max_rss_mb': 2450.0, 'user_s': 2950.0, 'sys_s': 12.5},
    }


ROW_KEYS = ('phase', 'component', 'submodule', 'backend', 'total_s', 'n_calls')


def _row(*values) -> dict:
    return dict(zip(ROW_KEYS, values, strict=True))


def fixture_record(run_id: str, started_at: str, sha: str, machine: str, **scenario) -> dict:
    """One record: the example with this run's identity and a scenario applied."""
    record = copy.deepcopy(json.loads(EXAMPLE.read_text()))
    record['run_id'], record['trigger']['started_at'] = run_id, started_at
    record['code']['proteus']['sha'] = sha
    record['machine']['class'] = record['machine']['label'] = machine
    jax = scenario.get('jax', False)
    record['benchmark']['settings']['interior_struct.zalmoxis.use_jax'] = jax
    record['benchmark']['settings_hash'] = settings_hash(record['benchmark']['settings'])
    interior = scenario.get('interior', 12.0 if scenario.get('radau') else 3.5)
    record['timings'] = fixture_timings(
        scenario.get('jitter', 1.0), scenario.get('atmos', 15.0), interior, jax
    )
    record['artifacts'] = {
        'spans': f'spans/2026/{run_id}.timing.jsonl.gz',
        'log': f'logs/2026/{run_id}.log.gz',
    }
    if scenario.get('radau'):
        _radau(record)
    if scenario.get('failed'):
        record['outcome'] |= {
            'status': 'failed',
            'exit_code': 1,
            'error': 'RuntimeError: AGNI did not converge',
        }
        record['comparability'] = {'ok': False, 'reasons': ['run failed']}
    return record


def _radau(record: dict) -> None:
    record['backends']['aragog'] = {'solver': 'radau', 'calls': {'radau': 6}}
    detail = 'aragog.solver: expected cvode, got radau'
    record['checks'][2] = {'name': 'expected_backends', 'ok': False, 'detail': detail}
    reasons = [f'check expected_backends failed: {detail}']
    record['comparability'] = {'ok': False, 'reasons': reasons}


def fixture_records() -> list[dict]:
    """The ten fixture records, oldest first."""
    records = []
    for i in range(8):
        day = 18 + i
        scenario = {
            'jitter': JITTER[i],
            'jax': i >= 1,
            'radau': i == 3,
            'atmos': 16.2 if i >= 5 else 15.0,
        }
        if i == 5:
            scenario['interior'] = 5.5
        run_id = f'202609{day}T031000Z-habrok-default-r{i + 1}'
        records.append(
            fixture_record(
                run_id, f'2026-09-{day}T03:10:00Z', f'abcd000{i + 1}', 'habrok-vink', **scenario
            )
        )
    records[-1]['artifacts'] |= {
        'flame': f'profiles/2026/{FLAME_RUN}.flame.html',
        'profile': f'profiles/2026/{FLAME_RUN}.folded.gz',
    }
    for n, (day, failed) in enumerate(((21, False), (24, True)), start=1):
        run_id = f'202609{day}T120000Z-gha-default-g{n}'
        records.append(
            fixture_record(
                run_id,
                f'2026-09-{day}T12:00:00Z',
                f'beef000{n}',
                'gha-ubuntu-epyc7763',
                failed=failed,
            )
        )
    return sorted(records, key=lambda r: r['trigger']['started_at'])


def write_store(store: Path, records: list[dict]) -> None:
    """Records under records/2026/ and a small file for every artifact except r2's log."""
    for record in records:
        path = store / 'records' / '2026' / f'{record["run_id"]}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1))
        for name, relpath in record['artifacts'].items():
            if record['run_id'] == MISSING_LOG_RUN and name == 'log':
                continue
            target = store / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f'<!doctype html><title>{name}</title>'
                if name == 'flame'
                else f'{name} of {record["run_id"]}\n'
            )


@pytest.fixture
def records() -> list[dict]:
    return fixture_records()


@pytest.fixture
def analysis(records) -> dict:
    return analyse(records)


@pytest.fixture
def store(tmp_path, records) -> Path:
    path = tmp_path / 'store'
    write_store(path, records)
    return path


@pytest.fixture(scope='session')
def site(tmp_path_factory) -> Path:
    """The fixture store's site with artifact links, built once for the tests that only read it."""
    tmp = tmp_path_factory.mktemp('site')
    write_store(tmp / 'store', fixture_records())
    records = load_records(tmp / 'store')
    build_site(records, analyse(records), tmp / 'site', tmp / 'store', 'egpbos/proteus-bench')
    return tmp / 'site'
