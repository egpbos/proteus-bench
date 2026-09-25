"""Resolved PROTEUS settings: flattening, hashing and comparison.

A run's settings are the resolved config PROTEUS writes to ``init_coupler.toml``,
flattened to dotted keys. Two runs belong to the same settings lineage when their
settings hash to the same value once per-run keys (output name, resume and
offline flags) are removed.
"""

from __future__ import annotations

import hashlib
import json

# Keys that differ between runs of identical physics and numerics
PER_RUN_KEYS = frozenset({'params.out.path', 'params.resume', 'params.offline'})


def flatten(nested: dict, prefix: str = '') -> dict:
    """Flatten nested tables to ``{'a.b.c': value}``; lists stay values."""
    flat = {}
    for key, value in nested.items():
        dotted = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, dotted))
        else:
            flat[dotted] = value
    return flat


def comparable(flat: dict) -> dict:
    """The settings with per-run keys removed."""
    return {k: v for k, v in flat.items() if k not in PER_RUN_KEYS}


def settings_hash(flat: dict) -> str:
    """Stable ``sha256:<hex>`` over the comparable settings, independent of key order."""
    return 'sha256:' + hashlib.sha256(_canonical(comparable(flat)).encode()).hexdigest()


def changed_keys(old: dict, new: dict) -> list[str]:
    """Sorted comparable keys that were added, removed or changed between two settings.

    Values compare in the same canonical form the hash uses, so 1, 1.0 and True
    differ here exactly when they change the hash.
    """
    old_c, new_c = comparable(old), comparable(new)
    return sorted(
        k
        for k in old_c.keys() | new_c.keys()
        if k not in old_c or k not in new_c or _canonical(old_c[k]) != _canonical(new_c[k])
    )


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))
