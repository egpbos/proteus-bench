"""Settings lineages and carry-over runs (decision D4), as pure functions of records.

A series is one benchmark on one machine class. Its ``default`` lineage follows
whatever settings the benchmark resolves to at each commit. When those settings
change, the previous settings are run once more at the new commit (a carry-over
run, with ``benchmark.carry_over_of`` set to the default run that moved on), so
the old settings get one point measured with the new code. After that the old
settings' lineage has ended.

Records are run-record dicts (schema ``proteus-bench/1``). Runs are ordered by
``trigger.started_at``, ties broken by run id.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from proteus_bench.settings import changed_keys
from proteus_bench.store import settings_path


@dataclass(frozen=True)
class SettingsChange:
    """The default settings of a series differ from the preceding default run."""

    previous: dict  # the preceding default-lineage record
    changed_keys: list[str]


@dataclass(frozen=True)
class CarryOver:
    """The carry-over run to perform after a default run changed the settings."""

    carry_over_of: str  # run id of the default run whose predecessor's settings to replay
    lineage: str  # settings hash of the replayed settings; the carry-over's lineage
    settings_toml: str  # store path of those settings
    commit: str  # PROTEUS commit to run them at: the one the new default run measured
    changed_keys: list[str]


def _order(record: dict) -> tuple[dt.datetime, str]:
    return dt.datetime.fromisoformat(record['trigger']['started_at']), record['run_id']


def _same_series(record: dict, benchmark: str, machine_class: str) -> bool:
    return (
        record['benchmark']['name'] == benchmark and record['machine']['class'] == machine_class
    )


def default_runs(records: list[dict], benchmark: str, machine_class: str) -> list[dict]:
    """The default-lineage runs of one series, oldest first."""
    runs = [
        r
        for r in records
        if _same_series(r, benchmark, machine_class) and r['benchmark']['lineage'] == 'default'
    ]
    return sorted(runs, key=_order)


def settings_change(records: list[dict], new: dict) -> SettingsChange | None:
    """How ``new``'s settings differ from the default run before it, if they do.

    None when ``new`` is not a default-lineage run, when no earlier default run
    exists in its series, or when the settings hash is unchanged. ``records`` may
    contain ``new`` itself.
    """
    if new['benchmark']['lineage'] != 'default':
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


def _has_carry_over(records: list[dict], mover: dict) -> bool:
    bench, cls = mover['benchmark']['name'], mover['machine']['class']
    return any(
        r['benchmark'].get('carry_over_of') == mover['run_id'] and _same_series(r, bench, cls)
        for r in records
    )


def carry_over(records: list[dict], new: dict) -> CarryOver | None:
    """The one carry-over run D4 asks for after ``new``, or None if none is due.

    Due when ``settings_change`` reports a change and no record in the series
    already carries ``carry_over_of == new['run_id']``. A failed carry-over run
    counts as done: D4 allows one attempt, and its record holds the error.
    """
    change = settings_change(records, new)
    if change is None or _has_carry_over(records, new):
        return None
    old_hash = change.previous['benchmark']['settings_hash']
    return CarryOver(
        carry_over_of=new['run_id'],
        lineage=old_hash,
        settings_toml=settings_path(old_hash),
        commit=new['code']['proteus']['sha'],
        changed_keys=change.changed_keys,
    )


def lineage_ended(
    records: list[dict], benchmark: str, machine_class: str, lineage: str
) -> bool:
    """Whether the settings lineage ``lineage`` (a settings hash) has ended in a series.

    Ended means the default runs moved off these settings and the carry-over run
    for that move exists. False while the latest default run still uses them
    (also after a return to them), while the carry-over is pending, and for
    settings the default never used.
    """
    series = default_runs(records, benchmark, machine_class)
    on = [i for i, r in enumerate(series) if r['benchmark']['settings_hash'] == lineage]
    if not on or on[-1] == len(series) - 1:
        return False
    return _has_carry_over(records, series[on[-1] + 1])
