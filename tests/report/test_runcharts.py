"""Tests for the run-page charts (proteus_bench.report.runcharts).

Contract clauses: iteration time not attributed to a component appears as
``other``; components stack in the fixed slot order, unknown ones after them
and ``other`` last; a component keeps its colour whatever else is present;
phase-bar shares add up to the timed total; empty timings give a note.
"""

from __future__ import annotations

import copy
import re

import pytest

from proteus_bench.report.runcharts import (
    component_colour,
    iteration_rows,
    iteration_stack,
    phase_bar,
    stack_order,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_unattributed_iteration_time_becomes_other(records):
    """Iteration 1 of r1: 3.5 + 1.0 + 780.0 attributed of 784.7 s, so other = 0.2 s."""
    rows = iteration_rows(records[0])
    assert rows[0]['other'] == pytest.approx(0.2, abs=1e-6)
    assert sum(rows[0].values()) == pytest.approx(records[0]['timings']['per_iter'][0]['dur_s'])
    exact = copy.deepcopy(records[0])
    exact['timings']['per_iter'][0]['dur_s'] = 784.5  # fully attributed: no other row
    assert 'other' not in iteration_rows(exact)[0]


def test_stack_order_and_stable_colours():
    """Slots first in slot order, unknown names alphabetically, other last; colours follow names."""
    assert stack_order({'other', 'zeta', 'atmos', 'alpha', 'structure'}) == [
        'structure',
        'atmos',
        'alpha',
        'zeta',
        'other',
    ]
    assert component_colour('atmos') == 'var(--s2)'
    assert component_colour('structure') == 'var(--s1)'
    assert component_colour('zeta') == component_colour('other') == 'var(--other)'


def test_phase_bar_shares_and_empty_timings(records):
    """r1: startup 21, init 1772, loop 1163.2, shutdown 5 s; the legend shares follow from those."""
    html = phase_bar(records[0])
    total = 21.0 + 1772.0 + 1163.2 + 5.0
    assert f'({1772.0 / total:.0%})' in html  # 60 %
    assert f'({1163.2 / total:.0%})' in html  # 39 %
    widths = [float(w) for w in re.findall(r'<rect x="[\d.]+" y="0" width="([\d.]+)"', html)]
    assert widths[1] / widths[2] == pytest.approx(1772.0 / 1163.2, rel=1e-3)
    empty = copy.deepcopy(records[0])
    empty['timings'] |= {
        'startup_s': None,
        'phases': {'init': None, 'loop': None},
        'per_iter': [],
    }
    assert 'No phase timings' in phase_bar(empty)
    assert 'No main-loop iterations' in iteration_stack(empty)


def test_iteration_stack_has_one_bar_per_iteration(records):
    """Six iterations, with structure only in iterations 3 and 6."""
    svg = iteration_stack(records[0])
    assert svg.count('iteration 1 (init stage), atmos') == 1
    assert len(re.findall(r'iteration \d+[^<]*, structure', svg)) == 2
    assert len(set(re.findall(r'iteration (\d+)', svg))) == 6
