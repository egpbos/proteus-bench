"""The overview page (``index.html``): one card per series group, then recent runs."""

from __future__ import annotations

from proteus_bench.report.fmt import (
    GroupKey,
    commit_link,
    esc,
    fmt_rel,
    fmt_s,
    fmt_time,
    fmt_value,
    group_label,
    group_of,
    run_href,
    series_href,
)
from proteus_bench.report.layout import comparable_mark, flag_badge, page, section, table

RECENT_RUNS = 20
STATS = (
    ('total', 'Total'),
    ('init', 'Init'),
    ('loop_per_iter_median', 'Loop / iter (median)'),
)

HOW_TO_READ = (
    'Each card is one series group: a benchmark, its settings lineage and a machine class. '
    'Runs only count towards a baseline when they are comparable (checks passed, expected '
    'backends, same settings). The baseline is the median of recent comparable runs; a run is '
    'flagged when it differs from it by more than max(3 sigma, 5 %). A flag is '
    '<b>confirmed</b> (filled triangle) when the next comparable run shows the same change, '
    '<b>not yet confirmed</b> (outline) until there is one, and <b>not confirmed by the next '
    'run</b> (dashed outline) when that run did not repeat it. Up triangles are regressions '
    '(slower), down triangles improvements. Open a series for its trend charts.'
)


def render(groups: dict[GroupKey, dict[str, dict]], records: list[dict]) -> str:
    cards = ''.join(_card(group, by_metric) for group, by_metric in sorted(groups.items()))
    series = (
        f'<div class="cards">{cards}</div>'
        if cards
        else '<p class="note">No runs in the store yet.</p>'
    )
    body = (
        section('How to read this', f'<p class="note panel">{HOW_TO_READ}</p>')
        + section('Series', series, 'Latest run of each group; times are wall clock.')
        + section(
            'Recent runs',
            _recent(records),
            f'The {RECENT_RUNS} most recent runs, newest first.',
        )
    )
    meta = [f'{len(records)} runs', f'{len(groups)} series groups']
    return page('Benchmark overview', body, '', meta)


def latest_point(by_metric: dict[str, dict]) -> dict:
    """Latest point of the group's ``total`` series (else of any series)."""
    series = by_metric.get('total') or next(iter(by_metric.values()))
    return series['points'][-1]


def _stat(by_metric: dict[str, dict], metric: str, label: str, run_id: str) -> str:
    series = by_metric.get(metric)
    point = (
        next((p for p in series['points'] if p['run_id'] == run_id), None) if series else None
    )
    value = fmt_value(point['value'], series['unit']) if point else 'n/a'
    median = series['baseline']['median'] if point and series['baseline'] else None
    if median is None:
        change = 'no baseline yet'
    elif median == 0:
        change = 'baseline median is 0'
    else:
        change = f'{fmt_rel(point["value"] / median - 1)} vs baseline'
    return f'<div><dt>{esc(label)}</dt><dd class="value">{esc(value)}</dd><dd class="note">{change}</dd></div>'


def _card(group: GroupKey, by_metric: dict[str, dict]) -> str:
    head = (
        f'<h3><a href="{esc(series_href(group))}">{esc(group[0])}</a></h3>'
        f'<p class="note">lineage <span class="mono">{esc(group[1])}</span> on {esc(group[2])}</p>'
    )
    if not by_metric:
        return (
            f'<article class="panel card">{head}<p class="note">Not analysed yet.</p></article>'
        )
    last = latest_point(by_metric)
    run_id = last['run_id']
    stats = ''.join(_stat(by_metric, m, label, run_id) for m, label in STATS)
    flags = [
        flag_badge(f, m)
        for m, s in sorted(by_metric.items())
        for f in s['flags']
        if f['run_id'] == run_id
    ]
    flags_html = ''.join(flags) or '<span class="note">No flags on the latest run.</span>'
    comparable = '' if last['comparable'] else f' {comparable_mark(False)}'
    return (
        f'<article class="panel card">{head}<dl class="stats">{stats}</dl>'
        f'<div class="badges">{flags_html}</div>'
        f'<p class="note">Latest run <a href="{esc(run_href(run_id))}">{esc(fmt_time(last["time"]))}</a>, '
        f'PROTEUS {commit_link(last["commit"])}{comparable}</p></article>'
    )


def _recent(records: list[dict]) -> str:
    if not records:
        return '<p class="note">No runs in the store yet.</p>'
    rows = [
        [
            f'<a href="{esc(run_href(r["run_id"]))}">{esc(fmt_time(r["trigger"]["started_at"]))}</a>',
            f'<a href="{esc(series_href(group_of(r)))}">{esc(group_label(group_of(r)))}</a>',
            commit_link(r['code']['proteus']['sha']),
            esc(r['outcome']['status']),
            comparable_mark(r['comparability']['ok']),
            fmt_s(r['timings']['wall_s']),
        ]
        for r in reversed(records[-RECENT_RUNS:])
    ]
    return table(
        ['Run', 'Series', 'Commit', 'Status', 'Comparable', 'Wall'], rows, frozenset({5})
    )
