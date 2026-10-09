"""Plotly figures of the dashboard, styled by the proteus-plotly templates.

Every chart is built once per theme (``themed``): trace colours come from the
PROTEUS tokens of that surface, and ``charts.js`` draws the figure matching the
reader's colour scheme with that theme's template from ``TEMPLATES``, which a
page embeds once instead of in every figure. Plotly reads a subset of HTML in its text, so record
text reaches it escaped (``hover`` escapes each line).

The series trend puts runs at evenly spaced x positions in time order: a dot per
comparable run, a ring per other run, and a triangle per flagged run (up for a
regression, down for an improvement; filled once confirmed, outlined until
then, grey when the next run did not confirm it). It draws the baseline band
(median +- 3 sigma, dashed lines at +-5 %), the trend through comparable runs,
broken at settings boundaries, and boundary and step markers. Clicking a point
opens its run page, whose path is the point's ``customdata``.
"""

from __future__ import annotations

import html

import plotly.graph_objects as go
import proteus_plotly

from proteus_bench.report.fmt import confirmation, fmt_rel, fmt_s, fmt_time, fmt_value, run_href

THEMES = ('light', 'dark')
SIGMAS = 3.0
REL_FLOOR = 0.05  # the analysis' smallest flag threshold, max(3 sigma, 5 %)
HEIGHTS = {'large': 260, 'small': 210}
MAX_X_LABELS = 6
LEGEND = {'orientation': 'h', 'x': 0, 'y': 1, 'yanchor': 'bottom'}
# A component's usual module: its colour is the module's domain colour, and the
# second component of one domain (structure, escape) is hatched.
COMPONENT_MODULES = {
    'interior': 'aragog',
    'structure': 'zalmoxis',
    'atmos': 'agni',
    'outgas': 'calliope',
    'stellar': 'mors',
    'chem': 'vulcan',
    'escape': 'zephyrus',
    'orbit': 'obliqua',
}
# phase -> (fill token, label token that reads on it in both themes)
PHASE_FILLS = {
    'startup': ('text-d3', 'text-d'),
    'setup': ('text-d3', 'text-d'),
    'init': ('text-d2', 'void'),
    'loop': ('verdant', 'ink'),
    'shutdown': ('text-d', 'void'),
}


TEMPLATES = {theme: proteus_plotly.template(theme).to_plotly_json() for theme in THEMES}


def themed(build, *args) -> dict[str, dict]:
    """Theme -> Plotly JSON of ``build(*args, theme)``, without its template."""
    figures = {theme: build(*args, theme).to_plotly_json() for theme in THEMES}
    for figure in figures.values():
        del figure['layout']['template']
    return figures


def hover(*lines) -> str:
    return '<br>'.join(html.escape(str(line)) for line in lines)


def _layout(height: int, **extra) -> dict:
    return {
        'template': {},
        'height': height,
        'margin': {'l': 8, 'r': 8, 't': 24, 'b': 8},
        'paper_bgcolor': 'rgba(0,0,0,0)',
        'plot_bgcolor': 'rgba(0,0,0,0)',
        'font': {'size': 12},
        'hovermode': 'closest',
    } | extra


def trend(series: dict, root: str, size: str, theme: str) -> go.Figure:
    """The trend chart of one series; ``root`` prefixes the run-page paths."""
    c = proteus_plotly.colors(theme)
    points = series['points']
    index = {p['run_id']: i for i, p in enumerate(points)}
    flags = {_index_of(index, f['run_id'], series): f for f in series['flags']}
    cuts = [_index_of(index, b['run_id'], series) for b in series['boundaries']]
    fig = go.Figure(layout=_trend_layout(points, series['unit'], size))
    fig.add_traces(_band(series['baseline'], points, c))
    fig.add_trace(_trend_line(points, cuts, c))
    fig.add_trace(_points(points, flags, series['unit'], root, c))
    markers = []
    for boundary, i in zip(series['boundaries'], cuts, strict=True):
        keys = ', '.join(boundary['changed_keys']) or 'none listed'
        text = f'{boundary["reason"]} before run {boundary["run_id"]}; changed keys: {keys}'
        markers.append(_marker(i - 0.5, 'settings', text, 'top', c))
    for step in series['steps']:
        i = _index_of(index, step['after_run_id'], series)
        text = (
            f'step at run {step["after_run_id"]}: {step["before"]:.4g} to {step["after"]:.4g} '
            f'({fmt_rel(step["delta_rel"])})'
        )
        markers.append(_marker(i - 0.5, fmt_rel(step['delta_rel']), text, 'bottom', c))
    for shape, note in markers:
        fig.add_shape(shape)
        fig.add_annotation(note)
    return fig


def _index_of(index: dict, run_id: str, series: dict) -> int:
    if run_id not in index:
        raise ValueError(
            f'series {series["key"]["metric"]!r} refers to run {run_id!r}, '
            'which is not one of its points'
        )
    return index[run_id]


def _trend_layout(points: list[dict], unit: str, size: str) -> dict:
    every = -(-len(points) // MAX_X_LABELS)
    ticks = list(range(0, len(points), every))
    return _layout(
        HEIGHTS[size],
        showlegend=False,
        xaxis={
            'range': [-0.5, len(points) - 0.5],
            'tickvals': ticks,
            'ticktext': [html.escape(points[i]['time'][5:10]) for i in ticks],
            'showgrid': False,
            'zeroline': False,
        },
        yaxis={'title': {'text': html.escape(unit)}, 'zeroline': False},
    )


def _band(baseline: dict | None, points: list[dict], c: dict) -> list[go.Scatter]:
    """Band over the last ``n`` comparable runs, extended to the right edge."""
    if baseline is None:
        return []
    comparable = [i for i, p in enumerate(points) if p['comparable']] or [0]
    x0 = comparable[-min(baseline['n'], len(comparable))] - 0.5
    x1 = len(points) - 0.5
    med, sig = baseline['median'], baseline['sigma']
    lo, hi = med - SIGMAS * sig, med + SIGMAS * sig
    text = hover(
        f'baseline median {med:.4g}, sigma {sig:.3g}, n {baseline["n"]}',
        f'band median +- {SIGMAS:g} sigma, dashed lines +-{REL_FLOOR:.0%}',
    )
    rel_lo, rel_hi = med * (1 - REL_FLOOR), med * (1 + REL_FLOOR)
    line = {'color': c['text-d2'], 'width': 1}
    return [
        go.Scatter(
            x=[x0, x1, x1, x0, x0],
            y=[lo, lo, hi, hi, lo],
            fill='toself',
            fillcolor=c['basalt-2'],
            mode='none',
            hoveron='fills',
            hoverinfo='text',
            text=text,
        ),
        go.Scatter(x=[x0, x1], y=[med, med], mode='lines', line=line, hoverinfo='skip'),
        go.Scatter(
            x=[x0, x1, None, x0, x1],
            y=[rel_lo, rel_lo, None, rel_hi, rel_hi],
            mode='lines',
            line=line | {'dash': 'dash'},
            hoverinfo='skip',
        ),
    ]


def _trend_line(points: list[dict], cuts: list[int], c: dict) -> go.Scatter:
    """Thin line through comparable runs, broken at every settings boundary."""
    xs, ys = [], []
    for i, p in enumerate(points):
        if i in cuts:
            xs.append(None)
            ys.append(None)
        if p['comparable']:
            xs.append(i)
            ys.append(p['value'])
    return go.Scatter(
        x=xs,
        y=ys,
        mode='lines',
        line={'color': c['text-d'], 'width': 1.5},
        opacity=0.45,
        hoverinfo='skip',
    )


def _mark(point: dict, flag: dict | None, c: dict) -> tuple[str, str, int]:
    """(symbol, colour, size) of one point."""
    if flag is None:
        if point['comparable']:
            return 'circle', c['text-d'], 8
        return 'circle-open', c['text-d2'], 8
    shape = 'triangle-up' if flag['kind'] == 'regression' else 'triangle-down'
    colour = c['danger'] if flag['kind'] == 'regression' else c['positive']
    if flag['confirmed'] is True:
        return shape, colour, 13
    if flag['confirmed'] is None:
        return f'{shape}-open', colour, 13
    return f'{shape}-open', c['text-d3'], 13


def _flag_lines(flag: dict) -> list[str]:
    # relative values are null when the baseline median is 0
    threshold = 'n/a' if flag['threshold_rel'] is None else f'{flag["threshold_rel"]:.1%}'
    return [
        f'{flag["kind"]} {fmt_rel(flag["delta_rel"])} ({flag["delta_abs"]:+.4g})',
        f'{confirmation(flag)}, threshold {threshold}',
    ]


def _points(
    points: list[dict], flags: dict[int, dict], unit: str, root: str, c: dict
) -> go.Scatter:
    marks = [_mark(p, flags.get(i), c) for i, p in enumerate(points)]
    texts = []
    for i, p in enumerate(points):
        lines = [
            f'{fmt_time(p["time"])}, {p["commit"][:8]}',
            fmt_value(p['value'], unit),
            'comparable' if p['comparable'] else 'not comparable',
        ]
        if i in flags:
            lines += _flag_lines(flags[i])
        texts.append(hover(*lines))
    return go.Scatter(
        x=list(range(len(points))),
        y=[p['value'] for p in points],
        ids=[p['run_id'] for p in points],
        customdata=[root + run_href(p['run_id']) for p in points],
        mode='markers',
        marker={
            'symbol': [m[0] for m in marks],
            'color': [m[1] for m in marks],
            'size': [m[2] for m in marks],
            'line': {'width': 1.5, 'color': [m[1] for m in marks]},
        },
        hoverinfo='text',
        hovertext=texts,
        hoverlabel={'align': 'left'},
    )


def _marker(x: float, label: str, text: str, where: str, c: dict) -> tuple[dict, dict]:
    """A vertical line at ``x`` and its label at the top or bottom, which hovers ``text``."""
    line = {'color': c['text-d2'], 'width': 1.2, 'dash': 'solid' if where == 'top' else 'dot'}
    shape = {'type': 'line', 'x0': x, 'x1': x, 'yref': 'paper', 'y0': 0, 'y1': 1, 'line': line}
    note = {
        'x': x,
        'xanchor': 'left',
        'yref': 'paper',
        'y': 1 if where == 'top' else 0,
        'yanchor': 'bottom',
        'text': html.escape(label),
        'hovertext': hover(text),
        'showarrow': False,
        'font': {'size': 11, 'color': c['text-d2']},
    }
    return shape, note


def phase_segments(record: dict) -> list[tuple[str, float]]:
    """(name, seconds) for start-up and every phase that ran, in order."""
    timings = record['timings']
    phases = [('startup', timings.get('startup_s'))] + list(timings['phases'].items())
    return [(name, dur) for name, dur in phases if dur]


def phases(record: dict, theme: str) -> go.Figure:
    """One horizontal bar of the run's phases to scale."""
    c = proteus_plotly.colors(theme)
    segments = phase_segments(record)
    total = sum(d for _, d in segments)
    bars = []
    for name, dur in segments:
        fill, label = PHASE_FILLS.get(name, PHASE_FILLS['startup'])
        bars.append(
            go.Bar(
                x=[dur],
                y=[''],
                orientation='h',
                name=html.escape(name),
                marker={'color': c[fill], 'line': {'color': c['basalt'], 'width': 2}},
                text=[html.escape(name)],
                textposition='inside',
                insidetextanchor='start',
                textfont={'color': c[label]},
                hoverinfo='text',
                hovertext=[hover(f'{name}: {fmt_s(dur)} ({dur / total:.0%})')],
            )
        )
    return go.Figure(
        bars,
        _layout(
            150,
            barmode='stack',
            uniformtext={'minsize': 10, 'mode': 'hide'},
            legend=LEGEND | {'traceorder': 'normal'},
            xaxis={'title': {'text': 'time [s]'}},
            yaxis={'showticklabels': False, 'ticks': '', 'showline': False},
        ),
    )


def iteration_rows(record: dict) -> list[dict[str, float]]:
    """Per iteration: component seconds plus the unattributed remainder as ``other``."""
    rows = []
    for it in record['timings']['per_iter']:
        row = dict(it['components'])
        rest = it['dur_s'] - sum(row.values())
        if rest > 1e-6:
            row['other'] = row.get('other', 0.0) + rest
        rows.append(row)
    return rows


def stack_order(names) -> list[str]:
    """Known components in module order, then the rest alphabetically, ``other`` last."""
    known = [n for n in COMPONENT_MODULES if n in names]
    rest = sorted(n for n in names if n not in COMPONENT_MODULES and n != 'other')
    return known + rest + (['other'] if 'other' in names else [])


def component_markers(names: list[str], theme: str) -> dict[str, dict]:
    """Bar marker per component: its module's domain colour, else a neutral grey."""
    c = proteus_plotly.colors(theme)
    by_module = proteus_plotly.module_markers(COMPONENT_MODULES.values(), theme)
    neutral = {'color': c['text-d3'], 'line': {'color': c['void'], 'width': 1}}
    return {
        n: by_module[COMPONENT_MODULES[n]] if n in COMPONENT_MODULES else neutral for n in names
    }


def iterations(record: dict, theme: str) -> go.Figure:
    """Stacked bars, one per main-loop iteration, split by component."""
    iters = record['timings']['per_iter']
    rows = iteration_rows(record)
    names = stack_order({n for row in rows for n in row})
    markers = component_markers(names, theme)
    labels = [
        f'iteration {it["iter"]}' + (' (init stage)' if it.get('init_stage') else '')
        for it in iters
    ]
    x = [html.escape(str(it['iter'])) for it in iters]
    bars = [
        go.Bar(
            x=x,
            y=[row.get(name, 0.0) for row in rows],
            name=html.escape(name),
            marker=markers[name],
            hoverinfo='text',
            hovertext=[
                hover(f'{label}, {name}: {fmt_s(row.get(name, 0.0))}')
                for label, row in zip(labels, rows, strict=True)
            ],
        )
        for name in names
    ]
    return go.Figure(
        bars,
        _layout(
            320,
            barmode='stack',
            legend=LEGEND,
            xaxis={'title': {'text': 'iteration'}, 'type': 'category'},
            yaxis={'title': {'text': 'time [s]'}},
        ),
    )
