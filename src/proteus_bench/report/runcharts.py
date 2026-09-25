"""Inline SVG for one run: the phase bar and the per-iteration component stack.

Components keep one colour everywhere (colour follows the component, never its
rank). The eight slots are the validated categorical order of the dashboard
palette; components beyond them, and unattributed time, are grey ``other``.
"""

from __future__ import annotations

from proteus_bench.report.fmt import esc, fmt_s
from proteus_bench.report.svg import Frame, axis_unit, nice_ticks, svg_open, y_grid

COMPONENT_SLOTS = (
    'structure',
    'atmos',
    'interior',
    'outgas',
    'stellar',
    'escape',
    'orbit',
    'chem',
)
PHASE_COLOURS = {
    'startup': 'other',
    'setup': 'other',
    'init': 's1',
    'loop': 's2',
    'shutdown': 's3',
}
GAP = 2.0  # surface gap between adjacent fills [px]


def component_colour(name: str) -> str:
    if name in COMPONENT_SLOTS:
        return f'var(--s{COMPONENT_SLOTS.index(name) + 1})'
    return 'var(--other)'


def stack_order(names) -> list[str]:
    """Known components in slot order, then the rest alphabetically, ``other`` last."""
    known = [c for c in COMPONENT_SLOTS if c in names]
    rest = sorted(n for n in names if n not in COMPONENT_SLOTS and n != 'other')
    return known + rest + (['other'] if 'other' in names else [])


def legend(items: list[tuple[str, str, str]]) -> str:
    """HTML legend rows: (colour, name, value text)."""
    rows = ''.join(
        f'<li><span class="sw" style="background:{c}"></span><span>{esc(n)}</span>'
        f'<span class="pct">{esc(v)}</span></li>'
        for c, n, v in items
    )
    return f'<ul class="legend">{rows}</ul>'


def phase_segments(record: dict) -> list[tuple[str, float]]:
    """(name, seconds) for start-up and every phase that ran, in order."""
    timings = record['timings']
    phases = [('startup', timings.get('startup_s'))] + list(timings['phases'].items())
    return [(name, dur) for name, dur in phases if dur]


def phase_bar(record: dict) -> str:
    """Horizontal bar of the phases to scale, with a legend giving each share."""
    segments = phase_segments(record)
    total = sum(d for _, d in segments)
    if not total:
        return '<p class="note">No phase timings recorded.</p>'
    width, height = 640, 34
    usable = width - GAP * (len(segments) - 1)
    parts = [svg_open(width, height, 'phase-bar', f'Phases of the run, {fmt_s(total)} timed')]
    x = 0.0
    for name, dur in segments:
        w = usable * dur / total
        colour = f'var(--{PHASE_COLOURS.get(name, "other")})'
        parts.append(
            f'<g class="seg"><title>{esc(name)}: {fmt_s(dur)} ({dur / total:.0%})</title>'
            f'<rect x="{x:.1f}" y="0" width="{max(w, 1):.1f}" height="{height}" rx="4" fill="{colour}"/></g>'
        )
        if w > 90:
            parts.append(
                f'<text x="{x + 8:.1f}" y="{height / 2 + 5}" class="barlab">{esc(name)}</text>'
            )
        x += w + GAP
    parts.append('</svg>')
    items = [
        (f'var(--{PHASE_COLOURS.get(n, "other")})', n, f'{fmt_s(d)} ({d / total:.0%})')
        for n, d in segments
    ]
    return ''.join(parts) + legend(items)


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


def iteration_stack(record: dict) -> str:
    """Stacked bars, one per main-loop iteration, split by component."""
    iters = record['timings']['per_iter']
    if not iters:
        return '<p class="note">No main-loop iterations recorded.</p>'
    rows = iteration_rows(record)
    names = stack_order({n for row in rows for n in row})
    top = max(it['dur_s'] for it in iters)
    div, unit = axis_unit('s', top)
    ticks = nice_ticks(0.0, top / div)
    frame = Frame(440, 220, 44, 10, 26, 24, 0.0, ticks[-1] * div, len(iters))
    title = f'Time per main-loop iteration by component, {len(iters)} iterations'
    parts = [svg_open(440, 220, 'iter-stack', title), *y_grid(frame, ticks, div, unit)]
    for i, (it, row) in enumerate(zip(iters, rows, strict=True)):
        parts += _bar(frame, i, it, row, names)
    parts += _iter_labels(frame, iters)
    parts.append('</svg>')
    totals = {n: sum(r.get(n, 0.0) for r in rows) for n in names}
    items = [
        (component_colour(n), n, f'{fmt_s(totals[n])} in the loop') for n in reversed(names)
    ]
    return ''.join(parts) + legend(items)


def _bar(frame: Frame, i: int, it: dict, row: dict, names: list[str]) -> list[str]:
    width = min(frame.slot * 0.72, 28.0)
    x = frame.x(i) - width / 2
    stage = ' (init stage)' if it.get('init_stage') else ''
    parts, acc = [], 0.0
    for name in (n for n in names if row.get(n)):
        y_top, y_bottom = frame.y(acc + row[name]), frame.y(acc)
        h = max(y_bottom - y_top - GAP, 0.5)
        parts.append(
            f'<g class="seg"><title>iteration {it["iter"]}{stage}, {esc(name)}: {fmt_s(row[name])}'
            f'</title><rect x="{x:.1f}" y="{y_top:.1f}" width="{width:.1f}" height="{h:.1f}" rx="1.5" '
            f'fill="{component_colour(name)}"/></g>'
        )
        acc += row[name]
    return parts


def _iter_labels(frame: Frame, iters: list[dict]) -> list[str]:
    every = max(1, -(-len(iters) // 12))  # at most 12 labels
    return [
        f'<text x="{frame.x(i):.1f}" y="{frame.height - 8}" class="tick" text-anchor="middle">{it["iter"]}</text>'
        for i, it in enumerate(iters)
        if i % every == 0 or i == len(iters) - 1
    ]
