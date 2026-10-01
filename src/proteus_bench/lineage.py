"""Settings lineages and carry-over runs (decision D4), as pure functions of records.

A series is one benchmark on one machine class. When the settings of its
``default`` lineage change, the previous settings are run once more at the new
commit. That carry-over run's record has ``benchmark.lineage`` set to the
previous settings hash and ``benchmark.carry_over_of`` to the default run that
moved on.

Only resolved runs take part (see ``resolved``): a run that failed before
PROTEUS resolved its config hashes the unresolved input, which says nothing
about the default settings. Records are stored records; runs are ordered by
``trigger.started_at``, then run id.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from proteus_bench.settings import changed_keys


@dataclass(frozen=True)
class SettingsChange:
    previous: dict  # the preceding resolved default-lineage record
    changed_keys: list[str]


@dataclass(frozen=True)
class CarryOver:
    carry_over_of: str  # the default run that moved to new settings
    lineage: str  # hash of the previous settings, which the carry-over replays
    settings_toml: str  # store paths of the previous run's settings and config
    config_toml: str
    commit: str  # the new default run's PROTEUS commit
    changed_keys: list[str]


def resolved(record: dict) -> bool:
    """Finished ok with settings and config stored, the two files a carry-over replays."""
    stored = record.get('artifacts', {})
    return record['outcome']['status'] == 'ok' and {'settings', 'config'} <= stored.keys()


def _order(record: dict) -> tuple[dt.datetime, str]:
    return dt.datetime.fromisoformat(record['trigger']['started_at']), record['run_id']


def _same_series(record: dict, benchmark: str, machine_class: str) -> bool:
    return (
        record['benchmark']['name'] == benchmark and record['machine']['class'] == machine_class
    )


def default_runs(records: list[dict], benchmark: str, machine_class: str) -> list[dict]:
    """The resolved default-lineage runs of one series, oldest first."""
    runs = [
        r
        for r in records
        if _same_series(r, benchmark, machine_class)
        and r['benchmark']['lineage'] == 'default'
        and resolved(r)
    ]
    return sorted(runs, key=_order)


def settings_change(records: list[dict], new: dict) -> SettingsChange | None:
    """How ``new``'s settings differ from the default run before it, if they do.

    None when ``new`` is not a resolved default-lineage run, when no earlier
    resolved default run exists in its series, or when the settings hash is
    unchanged. ``records`` may contain ``new`` itself.
    """
    if new['benchmark']['lineage'] != 'default' or not resolved(new):
        return None
    series = default_runs(records, new['benchmark']['name'], new['machine']['class'])
    earlier = [r for r in series if _order(r) < _order(new)]  # excludes new itself
    if not earlier:
        return None
    previous = earlier[-1]
    if previous['benchmark']['settings_hash'] == new['benchmark']['settings_hash']:
        return None
    keys = changed_keys(previous['benchmark']['settings'], new['benchmark']['settings'])
    return SettingsChange(previous, keys)


def _carry_over_exists(records: list[dict], new: dict, old_hash: str) -> bool:
    """A carry-over for this move (series, old hash, new hash) is already recorded.

    Keyed by the move, not the mover, so a late-published default run with the
    same new settings does not ask for a second one.
    """
    bench, cls = new['benchmark']['name'], new['machine']['class']
    new_hash = new['benchmark']['settings_hash']
    hashes = {r['run_id']: r['benchmark']['settings_hash'] for r in records}
    hashes[new['run_id']] = new_hash
    return any(
        _same_series(r, bench, cls)
        and r['benchmark']['lineage'] == old_hash
        and hashes.get(r['benchmark'].get('carry_over_of')) == new_hash
        for r in records
    )


def carry_over(records: list[dict], new: dict) -> CarryOver | None:
    """The one carry-over run D4 asks for after ``new``, or None if none is due.

    Due when ``settings_change`` reports a change and no carry-over for the
    same move exists in the series. A failed carry-over run counts as done: D4
    allows one attempt, and its record holds the error.
    """
    change = settings_change(records, new)
    if change is None:
        return None
    previous = change.previous
    old_hash = previous['benchmark']['settings_hash']
    if _carry_over_exists(records, new, old_hash):
        return None
    return CarryOver(
        carry_over_of=new['run_id'],
        lineage=old_hash,
        settings_toml=previous['artifacts']['settings'],
        config_toml=previous['artifacts']['config'],
        commit=new['code']['proteus']['sha'],
        changed_keys=change.changed_keys,
    )
