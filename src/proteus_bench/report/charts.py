"""Inline SVG trend chart for one analysis series.

Runs sit at evenly spaced x positions in time order. The chart shows the
baseline band (median +- 3 sigma, and dashed lines at +-5 %), filled dots for
comparable runs and rings for the others, flag triangles (up for a regression,
down for an improvement, hollow while unconfirmed), dotted step markers and
solid settings-change boundaries. Every mark has a ``<title>`` for hover and
screen readers, and each dot links to its run page.
"""

from __future__ import annotations

from proteus_bench.report.fmt import esc, fmt_rel, fmt_time, fmt_value, run_href, slug
from proteus_bench.report.svg import Frame, axis_unit, nice_ticks, svg_open, y_grid

SIZES = {'large': (400, 200), 'small': (320, 170)}
SIGMAS = 3.0
REL_FLOOR = 0.05  # flag threshold floor, max(3 sigma, 5 %) (PLAN.md D9)


def series_chart(series: dict, root: str, size: str = 'large') -> str:
    """SVG for one series; ``root`` prefixes the run-page links."""
    points = series['points']
    metric = series['key']['metric']
    if not points:
        return f'<p class="note">{esc(metric)}: no runs.</p>'
    width, height = SIZES[size]
    baseline = series['baseline']
    extent = [p['value'] for p in points] + _band_extent(baseline)
    div, unit = axis_unit(series['unit'], max(extent))
    ticks = nice_ticks(min(extent) / div, max(extent) / div)
    frame = Frame(width, height, 44, 10, 26, 24, ticks[0] * div, ticks[-1] * div, len(points))
    index = {p['run_id']: i for i, p in enumerate(points)}
    marks_at = _flags_by_index(series, index)
    title = (
        f'{metric}: {len(points)} runs, latest {fmt_value(points[-1]["value"], series["unit"])}'
    )
    parts = [
        svg_open(width, height, f'c-{slug(metric)}', title, f' data-metric="{esc(metric)}"')
    ]
    parts += y_grid(frame, ticks, div, unit)
    parts += _band(frame, baseline, points)
    parts += [
        _boundary(frame, b, _index_of(index, b['run_id'], metric)) for b in series['boundaries']
    ]
    parts += [
        _step(frame, s, _index_of(index, s['after_run_id'], metric)) for s in series['steps']
    ]
    parts += _trend(
        frame, points, [_index_of(index, b['run_id'], metric) for b in series['boundaries']]
    )
    for i, point in enumerate(points):
        mark = _point(frame, i, point, series['unit'], marks_at.get(i, []))
        parts.append(f'<a href="{root}{run_href(point["run_id"])}">{mark}</a>')
    parts += _x_labels(frame, points)
    parts.append('</svg>')
    return ''.join(parts)


def _index_of(index: dict, run_id: str, metric: str) -> int:
    if run_id not in index:
        raise ValueError(
            f'series {metric!r} refers to run {run_id!r}, which is not one of its points'
        )
    return index[run_id]


def _band_extent(baseline: dict | None) -> list[float]:
    if baseline is None:
        return []
    half = max(SIGMAS * baseline['sigma'], REL_FLOOR * baseline['median'])
    return [baseline['median'] - half, baseline['median'] + half]


def _flags_by_index(series: dict, index: dict) -> dict[int, list[dict]]:
    marks: dict[int, list[dict]] = {}
    for flag in series['flags']:
        marks.setdefault(_index_of(index, flag['run_id'], series['key']['metric']), []).append(
            flag
        )
    return marks


def _band(frame: Frame, baseline: dict | None, points: list[dict]) -> list[str]:
    """Band over the last ``n`` comparable runs, extended to the right edge."""
    if baseline is None:
        return []
    comparable = [i for i, p in enumerate(points) if p['comparable']] or [0]
    x0 = frame.x(comparable[-min(baseline['n'], len(comparable))]) - frame.slot / 2
    x1 = frame.width - frame.right
    med, sig = baseline['median'], baseline['sigma']
    top, bottom = frame.y(med + SIGMAS * sig), frame.y(med - SIGMAS * sig)
    text = (
        f'baseline median {med:.4g}, sigma {sig:.3g}, n {baseline["n"]}; '
        f'band median +- {SIGMAS:g} sigma, dashed lines +-{REL_FLOOR:.0%}'
    )
    parts = [
        f'<g class="band"><title>{esc(text)}</title>',
        f'<rect x="{x0:.1f}" y="{top:.1f}" width="{x1 - x0:.1f}" height="{bottom - top:.1f}"/>',
        f'<line x1="{x0:.1f}" x2="{x1:.1f}" y1="{frame.y(med):.1f}" y2="{frame.y(med):.1f}" class="median"/>',
    ]
    for sign in (1, -1):
        y = frame.y(med * (1 + sign * REL_FLOOR))
        parts.append(
            f'<line x1="{x0:.1f}" x2="{x1:.1f}" y1="{y:.1f}" y2="{y:.1f}" class="rel"/>'
        )
    return [*parts, '</g>']


def _vertical(frame: Frame, x: float, cls: str, label: str, title: str) -> str:
    # boundary labels above the plot, step labels at its foot, so adjacent ones never collide
    label_y = frame.top - 6 if cls == 'bnd' else frame.base - 4
    return (
        f'<g class="{cls}"><title>{esc(title)}</title>'
        f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{frame.top - 4}" y2="{frame.base}" class="hit"/>'
        f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{frame.top - 4}" y2="{frame.base}" class="mark"/>'
        f'<text x="{x + 3:.1f}" y="{label_y:.1f}" class="tick">{esc(label)}</text></g>'
    )


def _boundary(frame: Frame, boundary: dict, i: int) -> str:
    keys = ', '.join(boundary['changed_keys']) or 'none listed'
    title = f'{boundary["reason"]} before run {boundary["run_id"]}; changed keys: {keys}'
    return _vertical(frame, frame.x(i) - frame.slot / 2, 'bnd', 'settings', title)


def _step(frame: Frame, step: dict, i: int) -> str:
    title = (
        f'step after run {step["after_run_id"]}: {step["before"]:.4g} to {step["after"]:.4g} '
        f'({fmt_rel(step["delta_rel"])})'
    )
    return _vertical(
        frame, frame.x(i) + frame.slot / 2, 'step', fmt_rel(step['delta_rel']), title
    )


def _trend(frame: Frame, points: list[dict], cuts: list[int]) -> list[str]:
    """Thin line through comparable runs, broken at every settings boundary."""
    segments: list[list[str]] = [[]]
    for i, p in enumerate(points):
        if i in cuts:
            segments.append([])
        if p['comparable']:
            segments[-1].append(f'{frame.x(i):.1f},{frame.y(p["value"]):.1f}')
    return [f'<polyline points="{" ".join(s)}" class="trend"/>' for s in segments if len(s) > 1]


def _point(frame: Frame, i: int, point: dict, unit: str, flags: list[dict]) -> str:
    x, y = frame.x(i), frame.y(point['value'])
    state = 'comparable' if point['comparable'] else 'not comparable'
    lines = [
        f'{fmt_time(point["time"])}, {point["commit"][:8]}',
        fmt_value(point['value'], unit),
        state,
    ]
    lines += [_flag_text(f) for f in flags]
    cls = 'dot' if point['comparable'] else 'ring'
    marks = ''.join(_triangle(x, y, f) for f in flags)
    return (
        f'<g class="pt" data-run="{esc(point["run_id"])}">'
        f'<title>{esc(chr(10).join(lines))}</title><circle cx="{x:.1f}" cy="{y:.1f}" r="10" class="hit"/>'
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="{cls}"/>{marks}</g>'
    )


def _flag_text(flag: dict) -> str:
    state = 'confirmed' if flag['confirmed'] else 'unconfirmed'
    # relative values are null when the baseline median is 0
    threshold = 'n/a' if flag['threshold_rel'] is None else f'{flag["threshold_rel"]:.1%}'
    return (
        f'{flag["kind"]} {fmt_rel(flag["delta_rel"])} ({flag["delta_abs"]:+.4g}), {state}, '
        f'threshold {threshold}'
    )


def _triangle(x: float, y: float, flag: dict) -> str:
    """Up triangle above the dot for a regression, down below it for an improvement."""
    s, gap = 5.0, 12.0
    if flag['kind'] == 'regression':
        cy, tip, cls = y - gap, -s, 'bad'
    else:
        cy, tip, cls = y + gap, s, 'good'
    fill = '' if flag['confirmed'] else ' hollow'
    path = f'M{x:.1f},{cy + tip:.1f}L{x + s:.1f},{cy - tip:.1f}L{x - s:.1f},{cy - tip:.1f}Z'
    return f'<path d="{path}" class="flag {cls}{fill}"/>'


def _x_labels(frame: Frame, points: list[dict]) -> list[str]:
    y = frame.height - 6
    first = f'<text x="{frame.left}" y="{y}" class="tick">{points[0]["time"][:10]}</text>'
    if len(points) == 1:
        return [first]
    last = (
        f'<text x="{frame.width - frame.right}" y="{y}" class="tick" '
        f'text-anchor="end">{points[-1]["time"][:10]}</text>'
    )
    return [first, last]
