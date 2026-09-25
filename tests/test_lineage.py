"""Tests for proteus_bench.lineage: settings changes, carry-over runs, ended lineages.

Contract clauses: a new default run is compared with the latest earlier
default run of the same benchmark and machine class (by start time, not by
list or string order), ignoring runs of other lineages; equal hashes mean no
change; a change asks for one carry-over replaying the previous settings file
at the new run's commit, unless a carry-over for the new run exists in the
same series; a lineage has ended once the default moved off it and that move's
carry-over exists, and is active again if the default returns to it.
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
H_A, H_B, H_C = (settings_hash(s) for s in (S_A, S_B, S_C))


REC_DEFAULTS = {
    'lineage_': 'default',
    'bench': 'all_options',
    'cls': 'habrok-vink',
    'carry_over_of': None,
    'sha': 'abd4ca53',
    'started_at': None,
}


def rec(run_id, settings, day, **fields):
    """A record with only the fields lineage rules read; ``fields`` override REC_DEFAULTS."""
    f = REC_DEFAULTS | fields
    assert f.keys() == REC_DEFAULTS.keys(), f'unknown fields {fields}'
    return {
        'run_id': run_id,
        'benchmark': {
            'name': f['bench'],
            'lineage': f['lineage_'],
            'settings': settings,
            'settings_hash': settings_hash(settings),
            'carry_over_of': f['carry_over_of'],
        },
        'machine': {'class': f['cls']},
        'trigger': {'started_at': f['started_at'] or f'2026-09-{day:02d}T03:10:00Z'},
        'code': {'proteus': {'sha': f['sha'], 'dirty': False}},
    }


def test_hashes_are_distinct_and_independent_of_the_code():
    """Guard: the fixtures really differ, and H_A is sha256 of the canonical JSON."""
    canonical = json.dumps(S_A, sort_keys=True, separators=(',', ':'))
    assert 'sha256:' + hashlib.sha256(canonical.encode()).hexdigest() == H_A
    assert len({H_A, H_B, H_C}) == 3


def test_no_change_and_first_run_need_nothing():
    """Same settings as before, or no earlier run at all: no change, no carry-over."""
    history = [rec('r1', S_A, 1), rec('r2', S_A, 2)]
    new = rec('r3', S_A, 3, sha='bbbbbbb')  # a new commit alone does not start a lineage
    assert lineage.settings_change(history, new) is None
    assert lineage.carry_over(history, new) is None
    assert lineage.carry_over([], rec('r0', S_B, 1)) is None
    assert lineage.carry_over([new], new) is None  # the new run already in the store


def test_changed_defaults_request_carry_over_of_old_settings():
    """A to B at r3: replay A's settings file once, at r3's commit, as lineage H_A."""
    history = [rec('r1', S_A, 1), rec('r2', S_A, 2)]
    new = rec('r3', S_B, 3, sha='ccccccc')
    due = lineage.carry_over(history + [new], new)
    assert due == lineage.CarryOver(
        carry_over_of='r3',
        lineage=H_A,
        settings_toml=f'settings/{H_A.removeprefix("sha256:")}.toml',
        commit='ccccccc',
        changed_keys=['interior_struct.zalmoxis.use_jax'],
    )
    # Guard against replaying the new settings or pointing at the old run
    assert due.settings_toml != f'settings/{H_B.removeprefix("sha256:")}.toml'
    assert due.carry_over_of != 'r2'
    assert lineage.settings_change(history, new).previous['run_id'] == 'r2'


def test_previous_run_is_the_latest_earlier_one_by_time():
    """Order comes from started_at (parsed), not list order, run id or string order."""
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


def test_existing_carry_over_in_the_same_series_suppresses_request():
    """Once the carry-over for r3 exists (even failed) none is requested; another class's does not count."""
    history = [rec('r1', S_A, 1), rec('r3', S_B, 3)]
    new = history[1]
    elsewhere = rec('c0', S_A, 3, lineage_=H_A, carry_over_of='r3', cls='gha-ubuntu-epyc7763')
    assert lineage.carry_over(history + [elsewhere], new) is not None
    done = rec('c1', S_A, 3, lineage_=H_A, carry_over_of='r3')
    assert lineage.carry_over(history + [done], new) is None
    assert lineage.settings_change(history + [done], new).changed_keys == [
        'interior_struct.zalmoxis.use_jax'
    ]


def test_lineage_ends_after_its_carry_over():
    """A ends when the default moved to B and the carry-over exists; B stays active."""
    runs = [rec('r1', S_A, 1), rec('r2', S_A, 2), rec('r3', S_B, 3)]
    args = ('all_options', 'habrok-vink')
    assert lineage.lineage_ended(runs, *args, H_A) is False  # carry-over pending
    runs.append(rec('c1', S_A, 3, lineage_=H_A, carry_over_of='r3'))
    assert lineage.lineage_ended(runs, *args, H_A) is True
    assert lineage.lineage_ended(runs, *args, H_B) is False  # current default
    assert lineage.lineage_ended(runs, *args, H_C) is False  # never the default
    assert lineage.lineage_ended(runs, 'all_options', 'gha-ubuntu-epyc7763', H_A) is False


def test_return_to_old_settings_reopens_the_lineage():
    """A to B to A: A is active again; after A to C only C's carry-over ends A again."""
    args = ('all_options', 'habrok-vink')
    runs = [
        rec('r1', S_A, 1),
        rec('r2', S_B, 2),
        rec('c2', S_A, 2, lineage_=H_A, carry_over_of='r2'),
        rec('r3', S_A, 3),
    ]
    assert lineage.lineage_ended(runs, *args, H_A) is False
    assert lineage.lineage_ended(runs, *args, H_B) is False  # B moved off at r3, no carry-over
    runs.append(rec('r4', S_C, 4))
    assert (
        lineage.lineage_ended(runs, *args, H_A) is False
    )  # the old carry-over c2 does not count
    runs.append(rec('c4', S_A, 4, lineage_=H_A, carry_over_of='r4'))
    assert lineage.lineage_ended(runs, *args, H_A) is True
