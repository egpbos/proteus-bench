"""Formatting and link helpers shared by the dashboard pages.

Every string that comes from a record or the analysis passes through ``esc``
before it reaches HTML, and through ``script_json`` before it reaches a
``<script>`` block, because records are published by many machines.
"""

from __future__ import annotations

import html
import json
import re

PROTEUS_COMMIT_URL = 'https://github.com/FormingWorlds/PROTEUS/commit/{sha}'

# (benchmark, lineage, machine_class): one series page per group
GroupKey = tuple[str, str, str]


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def fmt_s(seconds: float | None) -> str:
    """A duration for people: ``12.3 s`` below 100 s, ``m:ss min`` below 1 h, else ``h h mm min``."""
    if seconds is None:
        return 'n/a'
    if seconds < 100:
        return f'{seconds:.1f} s'
    if seconds < 3600:
        minutes, secs = divmod(round(seconds), 60)
        return f'{minutes}:{secs:02d} min'
    hours, minutes = divmod(round(seconds / 60), 60)
    return f'{hours} h {minutes:02d} min'


def fmt_value(value: float | None, unit: str) -> str:
    """A metric value in its unit; seconds use ``fmt_s``, counts print bare."""
    if value is None:
        return 'n/a'
    if unit == 's':
        return fmt_s(value)
    if unit == 'count':
        return f'{value:g}'
    return f'{value:g} {unit}'.rstrip()


def fmt_rel(ratio: float | None) -> str:
    """A signed relative change: 0.081 -> ``+8.1 %``."""
    return 'n/a' if ratio is None else f'{ratio * 100:+.1f} %'


def fmt_time(iso: str) -> str:
    """``2026-09-25T03:10:00Z`` -> ``2026-09-25 03:10 UTC``."""
    return iso[:16].replace('T', ' ') + ' UTC'


def slug(text: str) -> str:
    """Lower-case file-name part: runs of other characters become one ``-``."""
    return re.sub(r'[^a-z0-9._]+', '-', text.lower()).strip('-')[:64]


def group_of(record: dict) -> GroupKey:
    return (
        record['benchmark']['name'],
        record['benchmark']['lineage'],
        record['machine']['class'],
    )


def group_label(group: GroupKey) -> str:
    return ' / '.join(group)


def series_href(group: GroupKey) -> str:
    """Site-relative path of a group's series page; ``--`` cannot occur inside a slug."""
    return 'series/' + '--'.join(slug(part) for part in group) + '.html'


def run_href(run_id: str) -> str:
    return f'runs/{run_id}.html'


def commit_link(sha: str) -> str:
    return (
        f'<a class="mono" href="{esc(PROTEUS_COMMIT_URL.format(sha=sha))}">{esc(sha[:8])}</a>'
    )


def script_json(value) -> str:
    """JSON safe to embed in ``<script type="application/json">``: no ``</script>`` break-out."""
    return json.dumps(value, separators=(',', ':'), sort_keys=True).replace('<', '\\u003c')
