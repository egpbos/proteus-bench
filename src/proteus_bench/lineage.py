"""Settings lineages and carry-over runs (decision D4), as pure functions of records.

A series is one benchmark on one machine class. Its ``default`` lineage follows
whatever settings the benchmark resolves to at each commit. When those settings
change, the previous settings are run once more at the new commit: a carry-over
run, whose record has ``benchmark.lineage`` set to the previous settings hash
and ``benchmark.carry_over_of`` to the default run that moved on. After that
the old settings' lineage has ended.

Only runs with resolved settings take part: runs that finished ``ok`` and have
``settings`` (PROTEUS's ``init_coupler.toml``) and ``config`` artifacts, the
two files a carry-over replays. A run that failed
before PROTEUS resolved its config has a hash of the unresolved input instead,
which says nothing about the default settings.

Records are run-record dicts (schema ``proteus-bench/1``) as stored, with
artifact paths relative to the store. Runs are ordered by
``trigger.started_at``, ties broken by run id.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from proteus_bench.settings import changed_keys


@dataclass(frozen=True)
class SettingsChange:
    """The default settings of a series differ from the preceding default run."""

    previous: dict  # the preceding resolved default-lineage record
    changed_keys: list[str]


@dataclass(frozen=True)
class CarryOver:
    """The carry-over run to perform after a default run changed the settings."""

    carry_over_of: str  # run id of the default run that moved to new settings
    lineage: str  # settings hash of the replayed (previous) settings
    settings_toml: str  # store path of the previous run's resolved settings
    config_toml: str  # store path of the config passed to that run
    commit: str  # PROTEUS commit to run at: the one the new default run measured
    changed_keys: list[str]


def resolved(record: dict) -> bool:
    """Whether the run finished ok with its resolved settings and its config stored."""
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

    Keyed by the move rather than by the run that made it, so a late-published
    default run with the same new settings does not ask for a second one.
    """
    bench, cls = new['benchmark']['name'], new['machine']['class']
    new_hash = new['benchmark']['settings_hash']
    # Settings hash of each possible mover; a missing or null carry_over_of maps to None
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
