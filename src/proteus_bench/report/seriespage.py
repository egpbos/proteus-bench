"""A series page: trend charts for one (benchmark, lineage, machine class) group, and its runs.

Headline metrics get large charts; phase, component and submodule metrics get
small multiples sorted by size (baseline median, else latest value). The run
table can be filtered by any settings key whose value differs between the
group's runs, from a JSON index embedded in the page.
"""

from __future__ import annotations

import json

from proteus_bench.report import figures
from proteus_bench.report.fmt import GroupKey
from proteus_bench.report.render import render

HEADLINE = ('total', 'init', 'loop_per_iter_median', 'n_iters')
TABLE_METRICS = ('total', 'init', 'loop_per_iter_median')
SECTIONS = (
    ('phases', 'Other phase metrics'),
    ('components', 'Components'),
    ('submodules', 'Submodules'),
)
ROOT = '../'


def section_of(metric: str) -> str:
    if metric in HEADLINE:
        return 'headline'
    if metric.startswith('submodule.'):
        return 'submodules'
    if metric.startswith(('init.', 'loop.')):
        return 'components'
    return 'phases'


def size_of(series: dict) -> float:
    if series['baseline']:
        return series['baseline']['median']
    return series['points'][-1]['value'] if series['points'] else 0.0


def page(group: GroupKey, by_metric: dict[str, dict], records: list[dict]) -> str:
    """The page for one group; ``records`` are the group's runs, oldest first."""
    headline = [_panel(by_metric[m], 'large') for m in HEADLINE if m in by_metric]
    multiples = []
    for name, heading in SECTIONS:
        chosen = sorted(
            (s for m, s in by_metric.items() if section_of(m) == name),
            key=lambda s: (-size_of(s), s['key']['metric']),
        )
        if chosen:
            multiples.append((heading, [_panel(s, 'small') for s in chosen]))
    shown = [m for m in TABLE_METRICS if m in by_metric]
    values = {m: {p['run_id']: p['value'] for p in by_metric[m]['points']} for m in shown}
    return render(
        'series.html',
        root=ROOT,
        group=group,
        records=records,
        headline=headline,
        multiples=multiples,
        table_metrics=[(m, by_metric[m]['unit'], values[m]) for m in shown],
        settings_index=settings_index(records),
    )


def _panel(series: dict, size: str) -> dict:
    """A chart with its series, or no chart for a series without points."""
    chart = figures.themed(figures.trend, series, ROOT, size) if series['points'] else None
    return {'series': series, 'chart': chart}


def settings_index(records: list[dict]) -> dict[str, dict[str, list[str]]]:
    """For each settings key whose value differs between runs: JSON value -> run ids."""
    index: dict[str, dict[str, list[str]]] = {}
    keys = {k for r in records for k in r['benchmark']['settings']}
    for key in sorted(keys):
        by_value: dict[str, list[str]] = {}
        for r in records:
            value = json.dumps(r['benchmark']['settings'].get(key), sort_keys=True)
            by_value.setdefault(value, []).append(r['run_id'])
        if len(by_value) > 1:
            index[key] = by_value
    return index
