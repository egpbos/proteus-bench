"""A series page: trend charts for one (benchmark, lineage, machine class) group, and its runs.

Headline metrics get large charts; phase, component and submodule metrics get
small multiples sorted by size (baseline median, else latest value). The run
table can be filtered by any settings key whose value differs between the
group's runs, from a JSON index embedded in the page.
"""

from __future__ import annotations

import json

from proteus_bench.report.charts import series_chart
from proteus_bench.report.fmt import (
    GroupKey,
    commit_link,
    esc,
    fmt_time,
    fmt_value,
    group_label,
    run_href,
    script_json,
)
from proteus_bench.report.layout import comparable_mark, page, section, table

HEADLINE = ('total', 'init', 'loop_per_iter_median', 'n_iters')
TABLE_METRICS = ('total', 'init', 'loop_per_iter_median')
ROOT = '../'

LEGEND = """<ul class="marks">
<li><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="4" class="dot"/></svg>comparable run</li>
<li><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="4" class="ring"/></svg>not comparable (not in baselines)</li>
<li><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8,3L13,12L3,12Z" class="flag bad"/></svg>regression, confirmed</li>
<li><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8,3L13,12L3,12Z" class="flag bad hollow"/></svg>regression, unconfirmed</li>
<li><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8,13L13,4L3,4Z" class="flag good"/></svg>improvement (outline: unconfirmed)</li>
<li><svg viewBox="0 0 16 16" aria-hidden="true"><rect x="1" y="4" width="14" height="8" class="swatch-band"/></svg>baseline median &#177; 3 sigma; dashed: &#177;5 %</li>
<li><svg viewBox="0 0 16 16" aria-hidden="true"><line x1="8" x2="8" y1="1" y2="15" class="swatch-bnd"/></svg>settings changed (hover for keys)</li>
<li><svg viewBox="0 0 16 16" aria-hidden="true"><line x1="8" x2="8" y1="1" y2="15" class="swatch-step"/></svg>detected step</li>
</ul>"""


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


def render(group: GroupKey, by_metric: dict[str, dict], records: list[dict]) -> str:
    """The page for one group; ``records`` are the group's runs, oldest first."""
    charts = _headline(by_metric) + ''.join(
        _multiples(by_metric, name, heading)
        for name, heading in (
            ('phases', 'Other phase metrics'),
            ('components', 'Components'),
            ('submodules', 'Submodules'),
        )
    )
    if not by_metric:
        charts = '<p class="note">No analysed series for this group yet.</p>'
    body = section(
        'Trends', f'<div class="panel">{LEGEND}</div>{charts}', _trend_note()
    ) + section('Runs', _filters(records) + _run_table(by_metric, records))
    meta = [esc(part) for part in group] + [f'{len(records)} runs']
    return page(f'Series {group_label(group)}', body, ROOT, meta, 'series.js')


def _trend_note() -> str:
    return (
        'One dot per run, oldest on the left, evenly spaced. Hover any mark for details; '
        'click a dot to open the run. Detection never compares across a settings change.'
    )


def _chart_panel(series: dict, size: str) -> str:
    metric = series['key']['metric']
    latest = series['points'][-1]['value'] if series['points'] else None
    return (
        f'<figure class="panel fig"><figcaption><b class="mono">{esc(metric)}</b>'
        f'<span class="note">latest {esc(fmt_value(latest, series["unit"]))}</span></figcaption>'
        f'{series_chart(series, ROOT, size)}</figure>'
    )


def _headline(by_metric: dict[str, dict]) -> str:
    panels = [_chart_panel(by_metric[m], 'large') for m in HEADLINE if m in by_metric]
    return f'<div class="grid2">{"".join(panels)}</div>' if panels else ''


def _multiples(by_metric: dict[str, dict], name: str, heading: str) -> str:
    chosen = sorted(
        (s for m, s in by_metric.items() if section_of(m) == name),
        key=lambda s: (-size_of(s), s['key']['metric']),
    )
    if not chosen:
        return ''
    panels = ''.join(_chart_panel(s, 'small') for s in chosen)
    return f'<h3>{esc(heading)}</h3><div class="multiples">{panels}</div>'


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


def _filters(records: list[dict]) -> str:
    index = settings_index(records)
    if not index:
        return '<p class="note">All runs in this group have the same settings.</p>'
    options = ''.join(f'<option value="{esc(k)}">{esc(k)}</option>' for k in index)
    return f"""<div class="panel filters" id="filters">
<label>Setting <select id="f-key"><option value="">choose a setting</option>{options}</select></label>
<label>Value <select id="f-value" disabled></select></label>
<button id="f-add" type="button" disabled>Add filter</button>
<ul id="f-active" class="chips"></ul>
<p id="f-status" class="note" aria-live="polite">{len(index)} settings differ between runs.</p>
</div>
<script type="application/json" id="settings-index">{script_json(index)}</script>"""


def _run_table(by_metric: dict[str, dict], records: list[dict]) -> str:
    shown = [m for m in TABLE_METRICS if m in by_metric]
    values = {m: {p['run_id']: p['value'] for p in by_metric[m]['points']} for m in shown}
    newest_first = list(reversed(records))
    rows = []
    for r in newest_first:
        cells = [
            f'<a href="{ROOT}{run_href(r["run_id"])}">{esc(fmt_time(r["trigger"]["started_at"]))}</a>',
            commit_link(r['code']['proteus']['sha']),
        ]
        cells += [fmt_value(values[m].get(r['run_id']), by_metric[m]['unit']) for m in shown]
        cells += [esc(r['outcome']['status']), comparable_mark(r['comparability']['ok'])]
        rows.append(cells)
    headers = ['Run', 'Commit', *shown, 'Status', 'Comparable']
    num = frozenset(range(2, 2 + len(shown)))
    return table(headers, rows, num, row_ids=[r['run_id'] for r in newest_first])
