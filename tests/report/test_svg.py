"""Tests for chart axis geometry (proteus_bench.report.svg).

Contract clauses: ticks are 1, 2 or 5 times a power of ten and cover the data
range; a flat range still gets a non-degenerate axis; seconds switch to min at
180 s and to h at 3 h; other units pass through; larger values plot higher.
"""

from __future__ import annotations

import pytest

from proteus_bench.report.svg import Frame, axis_unit, nice_ticks

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_ticks_cover_the_range_with_round_steps():
    """0..13.3 gives 0, 5, 10, 15 (step 5, not 2 or 10); 29.3..50.7 starts at or below 29.3."""
    assert nice_ticks(0.0, 13.3) == pytest.approx([0, 5, 10, 15])
    ticks = nice_ticks(29.3, 50.7)
    assert ticks[0] <= 29.3
    assert ticks[-1] >= 50.7
    steps = {round(b - a, 9) for a, b in zip(ticks, ticks[1:], strict=False)}
    assert steps == {10.0}  # (50.7 - 29.3) / 4 = 5.35 rounds up to 10, never to 5.35


def test_small_and_flat_ranges():
    """Sub-unit ranges keep decimals; a flat range still spans, and never below 0 from 0."""
    # range 0.1 / 4 ticks = 0.025 per tick; 0.02 is too small, so the step is 0.05
    assert nice_ticks(0.95, 1.05) == pytest.approx([0.95, 1.0, 1.05])
    flat = nice_ticks(6.0, 6.0)
    assert flat[0] < 6.0 < flat[-1]
    assert nice_ticks(0.0, 0.0) == pytest.approx([0.0, 0.5, 1.0])  # no negative seconds
    negative = nice_ticks(-3.0, -3.0)
    assert negative[0] < -3.0 < negative[-1]


def test_axis_unit_thresholds():
    """179 s stays in s, 180 s becomes min, 3 h becomes h; counts keep their unit."""
    assert axis_unit('s', 179.0) == (1.0, 's')
    assert axis_unit('s', 180.0) == (60.0, 'min')
    assert axis_unit('s', 3 * 3600.0) == (3600.0, 'h')
    assert axis_unit('', 6.0) == (1.0, '')


def test_frame_maps_larger_values_higher():
    """y decreases as the value grows, and the domain ends map to the plot edges."""
    frame = Frame(400, 200, 44, 10, 26, 24, 0.0, 10.0, 4)
    assert frame.y(10.0) == pytest.approx(26.0)
    assert frame.y(0.0) == pytest.approx(frame.base) == pytest.approx(176.0)
    assert frame.y(7.0) < frame.y(3.0)
    assert frame.x(0) == pytest.approx(44 + 0.5 * (400 - 54) / 4)
