"""Tests for proteus_bench.settings: flattening, hashing and key comparison.

Contract clauses: flattening keeps lists as values and nests arbitrarily deep; the
hash ignores key order and per-run keys but changes with any other value or type;
changed_keys reports additions, removals and edits, and never per-run keys.
"""

from __future__ import annotations

import pytest

from proteus_bench.settings import PER_RUN_KEYS, changed_keys, flatten, settings_hash

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

RESOLVED = {
    'version': '2.0',
    'params': {'resume': False, 'offline': False, 'out': {'path': 'run_a', 'write_mod': 1}},
    'interior_struct': {'module': 'zalmoxis', 'zalmoxis': {'use_jax': True}},
    'planet': {'gas_list': ['H2O', 'CO2']},
}


def test_flatten_nests_deeply_and_keeps_lists():
    """Nested tables become dotted keys; arrays and empty tables are handled."""
    flat = flatten(RESOLVED)
    assert flat['interior_struct.zalmoxis.use_jax'] is True
    assert flat['planet.gas_list'] == ['H2O', 'CO2']
    assert flat['params.out.path'] == 'run_a'
    assert flatten({}) == {}
    assert flatten({'empty': {}}) == {}  # an empty table has no leaves


def test_hash_ignores_order_and_per_run_keys_only():
    """Renaming the output or resuming keeps the hash; any physics change alters it."""
    flat = flatten(RESOLVED)
    base = settings_hash(flat)
    reordered = dict(reversed(list(flat.items())))
    assert settings_hash(reordered) == base
    per_run = {
        **flat,
        'params.out.path': 'run_b',
        'params.resume': True,
        'params.offline': True,
    }
    assert settings_hash(per_run) == base
    assert settings_hash({**flat, 'interior_struct.zalmoxis.use_jax': False}) != base
    # Type changes count: 1 and 1.0 or '1' are different settings in a config file.
    assert settings_hash({**flat, 'params.out.write_mod': 1.0}) != base
    assert settings_hash({**flat, 'params.out.write_mod': '1'}) != base
    assert base.startswith('sha256:')
    assert len(base) == len('sha256:') + 64


def test_changed_keys_reports_edits_additions_and_removals():
    """All three kinds of difference appear, sorted, and per-run keys never do."""
    old = flatten(RESOLVED)
    new = {**old, 'interior_struct.module': 'spider', 'params.out.path': 'other'}
    del new['planet.gas_list']
    new['atmos_clim.module'] = 'agni'
    assert changed_keys(old, new) == [
        'atmos_clim.module',
        'interior_struct.module',
        'planet.gas_list',
    ]
    assert changed_keys(old, old) == []
    assert not PER_RUN_KEYS & set(changed_keys(old, new))


def test_a_key_set_to_none_differs_from_a_missing_key():
    """None is a real value (PROTEUS writes 'none' strings, but records may hold null)."""
    assert changed_keys({'a': None}, {}) == ['a']
    assert settings_hash({'a': None}) != settings_hash({})


def test_type_only_changes_are_reported_consistently_with_the_hash():
    """1 -> 1.0 and 1 -> True change the hash, so changed_keys must name them too."""
    for old, new in ((1, 1.0), (1, True), (0, False)):
        assert settings_hash({'k': old}) != settings_hash({'k': new})
        assert changed_keys({'k': old}, {'k': new}) == ['k']
    assert changed_keys({'k': [1, 2]}, {'k': [1, 2]}) == []
