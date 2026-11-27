"""The page of ``proteus-bench timing``: cases as stacked bars by time, and the spread table.

Each bar is one case, sorted by total time, so the stack that grows from left to
right is the one that follows the spread. The ``init`` and ``first`` spans stack as
one block each, ``first`` hatched; the loop components keep their module colours.
"""

from __future__ import annotations

import html
from pathlib import Path

import plotly.graph_objects as go
import proteus_plotly

from proteus_bench.logsplit import UNLOGGED
from proteus_bench.report.figures import (
    LEGEND,
    PHASE_FILLS,
    component_markers,
    hover,
    layout,
    stack_order,
    themed,
)
from proteus_bench.report.fmt import fmt_s
from proteus_bench.report.render import render
from proteus_bench.report.site import TEMPLATES, prepare_dir

BLOCKS = {'init': 'init', 'first': 'first iteration'}


def stacks(row: dict, columns: list[str]) -> dict[str, float]:
    """Seconds per stack of one case: init and first summed, loop components, unlogged."""
    out: dict[str, float] = {}
    for name in columns:
        phase, _, module = name.partition('.')
        key = BLOCKS.get(phase, module or name)
        out[key] = out.get(key, 0.0) + row.get(name, 0.0)
    return out


def figure(rows: list[dict], columns: list[str], theme: str) -> go.Figure:
    """Stacked bars, one per case with iterations, ordered by total time."""
    c = proteus_plotly.colors(theme)
    cases = sorted((r for r in rows if r['iters']), key=lambda r: r['total_s'])
    per_case = [stacks(r, columns) for r in cases]
    present = {n for s in per_case for n in s}
    loop = stack_order(present - {*BLOCKS.values(), UNLOGGED})
    markers = component_markers(loop, theme) | {
        'init': {'color': c[PHASE_FILLS['init'][0]]},
        'first iteration': {'color': c['text-d3'], 'pattern': {'shape': '/'}},
        UNLOGGED: {'color': c[PHASE_FILLS['shutdown'][0]]},
    }
    names = [n for n in ('init', 'first iteration', *loop, UNLOGGED) if n in present]
    x = [html.escape(r['case']) for r in cases]
    bars = [
        go.Bar(
            x=x,
            y=[s[name] for s in per_case],
            name=html.escape(name),
            marker=markers[name],
            hoverinfo='text',
            hovertext=[
                hover(f'{r["case"]}, {name}: {fmt_s(s[name])} of {fmt_s(r["total_s"])}')
                for r, s in zip(cases, per_case, strict=True)
            ],
        )
        for name in names
        if any(s[name] for s in per_case)
    ]
    return go.Figure(
        bars,
        layout(
            380,
            barmode='stack',
            bargap=0.1,
            legend=LEGEND,
            xaxis={
                'title': {'text': 'case, by total time'},
                'type': 'category',
                'showticklabels': len(cases) <= 40,
            },
            yaxis={'title': {'text': 'time [s]'}},
        ),
    )


def write(out_dir: Path, rows: list[dict], spread: list[dict], columns: list[str]) -> None:
    """Write ``timing.html`` and its assets to ``out_dir``, which must be new or empty."""
    timed = [r for r in rows if r['iters']]
    html_text = render(
        'timing.html',
        root='',
        n_cases=len(rows),
        n_timed=len(timed),
        n_noted=sum(bool(r['notes']) for r in rows),
        cases_chart=themed(figure, rows, columns),
        spread=spread,
    )
    prepare_dir(out_dir)
    (out_dir / 'timing.html').write_text(html_text, encoding='utf-8')
    for asset in ('style.css', 'charts.js'):
        (out_dir / asset).write_bytes((TEMPLATES / asset).read_bytes())
