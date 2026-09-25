"""Tests for proteus_bench.lineage: settings changes and carry-over runs (D4).

Contract clauses: a new default run is compared with the latest earlier
resolved default run of the same benchmark and machine class (ordered by
parsed start time, then run id), ignoring runs of other lineages and runs
without resolved settings (failed, or no settings artifact); equal hashes
mean no change; a change asks for one carry-over replaying the previous run's
stored settings and config at the new run's commit, unless a carry-over for
the same move (series, old hash, new hash) is already recorded, which also
covers a late-published default run and a failed carry-over.
"""

from __future__ import annotations

import hashlib
import json
import random

import pytest

from proteus_bench import lineage
from proteus_bench.settings import settings_hash

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

S_A = {'interior_struct.zalmoxis.use_jax': True, 'params.stop.iters.maximum': 16}
S_B = {**S_A, 'interior_struct.zalmoxis.use_jax': False}
S_C = {**S_A, 'params.stop.iters.maximum': 32}
S_RAW = {'params.stop.iters.maximum': 16}  # unresolved input of a run that failed in setup
H_A, H_B, H_C = (settings_hash(s) for s in (S_A, S_B, S_C))
SERIES = {'bench': 'all_options', 'cls': 'habrok-vink'}

REC_DEFAULTS = {
    'lineage_': 'default',
    'bench': 'all_options',
    'cls': 'habrok-vink',
    'carry_over_of': None,
    'sha': 'abd4ca53',
    'started_at': None,
    'status': 'ok',
    'stored_settings': True,
}


def rec(run_id, settings, day, **fields):
    """A stored record with the fields lineage reads; ``fields`` override REC_DEFAULTS."""
    f = REC_DEFAULTS | fields
    assert f.keys() == REC_DEFAULTS.keys(), f'unknown fields {fields}'
    hash_ = settings_hash(settings)
    artifacts = {'config': f'configs/2026/{run_id}.toml'}
    if f['stored_settings']:
        artifacts['settings'] = f'settings/{hash_.removeprefix("sha256:")}.toml'
    return {
        'run_id': run_id,
        'benchmark': {
            'name': f['bench'],
            'lineage': f['lineage_'],
            'settings': settings,
            'settings_hash': hash_,
            'carry_over_of': f['carry_over_of'],
        },
        'machine': {'class': f['cls']},
        'trigger': {'started_at': f['started_at'] or f'2026-09-{day:02d}T03:10:00Z'},
        'code': {'proteus': {'sha': f['sha'], 'dirty': False}},
        'outcome': {'status': f['status']},
        'artifacts': artifacts,
    }


def failed(run_id, day):
    """A default run that failed before PROTEUS resolved its config."""
    return rec(run_id, S_RAW, day, status='failed', stored_settings=False)


def test_hashes_are_distinct_and_independent_of_the_code():
    """Guard: the fixtures really differ, and H_A is sha256 of the canonical JSON."""
    canonical = json.dumps(S_A, sort_keys=True, separators=(',', ':'))
    assert 'sha256:' + hashlib.sha256(canonical.encode()).hexdigest() == H_A
    assert len({H_A, H_B, H_C, settings_hash(S_RAW)}) == 4


def test_no_change_and_first_run_need_nothing():
    """Same settings as before, or no earlier run at all: no change, no carry-over."""
    history = [rec('r1', S_A, 1), rec('r2', S_A, 2)]
    new = rec('r3', S_A, 3, sha='bbbbbbb')  # a new commit alone does not start a lineage
    assert lineage.settings_change(history, new) is None
    assert lineage.carry_over(history, new) is None
    assert lineage.carry_over([], rec('r0', S_B, 1)) is None
    assert lineage.carry_over([new], new) is None  # the new run already in the store


def test_changed_defaults_request_carry_over_of_old_settings():
    """A to B at r3: replay r2's stored settings and config once, at r3's commit."""
    history = [rec('r1', S_A, 1), rec('r2', S_A, 2)]
    new = rec('r3', S_B, 3, sha='ccccccc')
    due = lineage.carry_over(history + [new], new)
    assert due == lineage.CarryOver(
        carry_over_of='r3',
        lineage=H_A,
        settings_toml=f'settings/{H_A.removeprefix("sha256:")}.toml',
        config_toml='configs/2026/r2.toml',
        commit='ccccccc',
        changed_keys=['interior_struct.zalmoxis.use_jax'],
    )
    # Guards against replaying the new settings or naming the old run as the mover
    assert due.settings_toml != f'settings/{H_B.removeprefix("sha256:")}.toml'
    assert due.carry_over_of != 'r2'


def test_failed_runs_do_not_move_the_lineage():
    """A, failed, A needs nothing; A, failed, B replays A; a failed new run needs nothing."""
    a_failed_a = [rec('r1', S_A, 1), failed('r2', 2)]
    assert lineage.settings_change(a_failed_a, rec('r3', S_A, 3)) is None
    assert lineage.carry_over(a_failed_a, rec('r3', S_A, 3)) is None
    due = lineage.carry_over(a_failed_a, rec('r3', S_B, 3))
    assert due.lineage == H_A
    assert due.config_toml == 'configs/2026/r1.toml'  # the failed r2 is skipped over
    assert lineage.carry_over([rec('r1', S_A, 1)], failed('r2', 2)) is None
    # Finished ok but without a stored settings file: not resolved either
    no_file = rec('r2', S_B, 2, stored_settings=False)
    assert lineage.carry_over([rec('r1', S_A, 1), no_file], rec('r3', S_A, 3)) is None
    # Failed later with its settings stored: still not a point of the lineage
    crashed = rec('r2', S_B, 2, status='crashed')
    assert lineage.carry_over([rec('r1', S_A, 1), crashed], rec('r3', S_A, 3)) is None
    no_config = rec('r2', S_B, 2)
    del no_config['artifacts']['config']  # nothing to replay the input from
    assert lineage.carry_over([rec('r1', S_A, 1), no_config], rec('r3', S_A, 3)) is None


def test_previous_run_is_the_latest_earlier_one_by_time():
    """Order comes from started_at (parsed), then run id; not list or string order."""
    history = [rec('r1', S_B, 1), rec('r2', S_A, 2)]
    random.Random(42).shuffle(history)
    new = rec('r3', S_A, 3)
    assert lineage.carry_over(history, new) is None  # r2 (A) is the one before, not r1 (B)
    later = rec('r4', S_C, 4)  # after the new run: irrelevant to it
    assert lineage.carry_over(history + [later], new) is None
    # Fractional seconds: as strings '03:10:00Z' sorts after '03:10:00.300Z' ('Z' > '.'),
    # which would hide r5 (B, the true predecessor) and fall back to r6 (A)
    r5 = rec('r5', S_B, 5, started_at='2026-09-05T03:10:00Z')
    r6 = rec('r6', S_A, 5, started_at='2026-09-05T03:09:59.900Z')
    new7 = rec('r7', S_A, 5, started_at='2026-09-05T03:10:00.300Z')
    assert lineage.carry_over([r6, r5], new7).lineage == H_B


def test_equal_start_times_are_ordered_by_run_id():
    """Two runs starting in the same second: the larger run id counts as the later one."""
    same = '2026-09-09T03:10:00Z'
    r9a = rec('r9a', S_A, 9, started_at=same)
    r9b = rec('r9b', S_B, 9, started_at=same)
    new = rec('r10', S_B, 10)
    # Listed b first: an order by time alone would keep r9a last and see A to B
    assert lineage.carry_over([r9b, r9a], new) is None
    assert lineage.carry_over([r9b, r9a], rec('r10', S_A, 10)).lineage == H_B


def test_other_series_and_lineages_are_ignored():
    """Runs of another machine class, benchmark or lineage never count as the previous run."""
    history = [
        rec('r1', S_A, 1),
        rec('r2', S_B, 2, cls='gha-ubuntu-epyc7763'),
        rec('r3', S_B, 3, bench='dummy'),
        rec('r4', S_B, 4, lineage_=H_B),  # a manual run with other settings
    ]
    assert lineage.carry_over(history, rec('r5', S_A, 5)) is None
    manual = rec('r6', S_B, 6, lineage_=H_B)
    assert lineage.carry_over(history, manual) is None  # only default runs trigger D4
    assert lineage.carry_over(history, rec('r7', S_B, 7)).carry_over_of == 'r7'


def test_existing_carry_over_for_the_move_suppresses_request():
    """A recorded carry-over (even a failed one) for A to B in this series means none is due."""
    history = [rec('r1', S_A, 1), rec('r3', S_B, 3)]
    new = history[1]
    elsewhere = rec('c0', S_A, 3, lineage_=H_A, carry_over_of='r3', cls='gha-ubuntu-epyc7763')
    assert lineage.carry_over(history + [elsewhere], new) is not None
    # Failed in setup: its own hash is of the raw config, but its lineage names A
    done = rec('c1', S_RAW, 3, lineage_=H_A, carry_over_of='r3', status='failed')
    assert lineage.carry_over(history + [done], new) is None
    assert lineage.settings_change(history + [done], new).changed_keys == [
        'interior_struct.zalmoxis.use_jax'
    ]


def test_late_default_run_does_not_ask_twice():
    """A B run published after the carry-over, but started before r3, finds it via the move."""
    records = [
        rec('r1', S_A, 1),
        rec('r3', S_B, 3),
        rec('c3', S_A, 3, lineage_=H_A, carry_over_of='r3'),
    ]
    late = rec('r2', S_B, 2)  # started between r1 and r3, so its predecessor is r1 (A)
    assert lineage.settings_change(records, late).previous['run_id'] == 'r1'
    assert lineage.carry_over(records + [late], late) is None


def test_carry_over_found_before_its_mover_is_published():
    """The carry-over record is in the store but r3 is not yet: checking r3 finds it."""
    records = [rec('r1', S_A, 1), rec('c3', S_A, 3, lineage_=H_A, carry_over_of='r3')]
    assert lineage.carry_over(records, rec('r3', S_B, 3)) is None
    # A manual run of the old settings (lineage A, no carry_over_of) is not a carry-over
    manual = [rec('r1', S_A, 1), rec('m2', S_A, 2, lineage_=H_A)]
    assert lineage.carry_over(manual, rec('r3', S_B, 3)).config_toml == 'configs/2026/r1.toml'


def test_carry_over_must_belong_to_the_same_move():
    """A carry-over of A for a move to C does not cover a move to B, nor one of C for B."""
    records = [
        rec('r1', S_A, 1),
        rec('x', S_C, 2, lineage_=H_C),  # a manual C run, the mover of c_c below
        rec('c_c', S_A, 2, lineage_=H_A, carry_over_of='x'),  # A replayed for a move to C
        rec('c_b', S_C, 2, lineage_=H_C, carry_over_of='r3'),  # replays C, not A
    ]
    new = rec('r3', S_B, 3)
    due = lineage.carry_over(records, new)
    assert due is not None
    assert due.lineage == H_A
