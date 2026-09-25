"""Axis geometry shared by the SVG charts: nice ticks, unit scaling, a plot frame."""

from __future__ import annotations

import math
from dataclasses import dataclass

from proteus_bench.report.fmt import esc

# (smallest axis maximum, divisor, unit shown) for axes in seconds
TIME_SCALES = ((3 * 3600.0, 3600.0, 'h'), (180.0, 60.0, 'min'), (0.0, 1.0, 's'))
# plot margins (left, right, top, bottom) [viewBox units]: tick labels left, dates below
MARGINS = (44.0, 10.0, 26.0, 24.0)


def axis_unit(unit: str, max_value: float) -> tuple[float, str]:
    """Divisor and label for an axis: seconds switch to min or h for long durations."""
    if unit != 's':
        return 1.0, unit
    return next((div, name) for low, div, name in TIME_SCALES if max_value >= low)


def nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    """Ticks at 1, 2 or 5 times a power of ten; the first is <= lo, the last >= hi."""
    if hi <= lo:  # a flat series still gets a readable axis around its value
        pad = abs(lo) * 0.1 or 1.0
        # durations are never negative, so a flat zero series starts at 0
        lo, hi = (0.0 if 0 <= lo < pad else lo - pad), hi + pad
    raw = (hi - lo) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw * (1 - 1e-9))
    start = math.floor(lo / step + 1e-9) * step
    n = math.ceil((hi - start) / step - 1e-9)
    digits = max(0, -math.floor(math.log10(step)))
    return [round(start + i * step, digits) for i in range(n + 1)]


@dataclass(frozen=True)
class Frame:
    """Plot area inside a ``width`` x ``height`` viewBox; ``n`` evenly spaced x slots."""

    width: float
    height: float
    left: float
    right: float
    top: float
    bottom: float
    lo: float
    hi: float
    n: int

    @property
    def slot(self) -> float:
        return (self.width - self.left - self.right) / max(self.n, 1)

    def x(self, i: float) -> float:
        return self.left + (i + 0.5) * self.slot

    def y(self, value: float) -> float:
        span = self.hi - self.lo
        return self.top + (self.hi - value) / span * (self.height - self.top - self.bottom)

    @property
    def base(self) -> float:
        return self.height - self.bottom

    @classmethod
    def plot(cls, width: float, height: float, lo: float, hi: float, n: int) -> Frame:
        """A frame with the dashboard's standard margins."""
        return cls(width, height, *MARGINS, lo, hi, n)


def y_grid(frame: Frame, ticks: list[float], div: float, unit: str) -> list[str]:
    """Hairline gridlines at ``ticks`` (in axis units, ``div`` data units each) and labels."""
    parts = [f'<text x="4" y="{frame.top - 8:.1f}" class="tick">{esc(unit)}</text>']
    for t in ticks:
        y = frame.y(t * div)
        parts.append(
            f'<line x1="{frame.left}" x2="{frame.width - frame.right}" y1="{y:.1f}" '
            f'y2="{y:.1f}" class="grid"/>'
            f'<text x="{frame.left - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{t:g}</text>'
        )
    return parts


def svg_open(width: float, height: float, chart_id: str, title: str) -> str:
    """A static chart: one image to assistive technology, named by its title."""
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="{chart_id}" '
        f'class="chart"><title id="{chart_id}">{esc(title)}</title>'
    )


def svg_open_group(width: float, height: float, title: str, attrs: str = '') -> str:
    """An interactive chart: a labelled group, so its linked marks stay reachable."""
    return (
        f'<svg viewBox="0 0 {width} {height}" role="group" aria-label="{esc(title)}" '
        f'class="chart"{attrs}><title>{esc(title)}</title>'
    )
