"""Tests for series-page helpers (proteus_bench.report.seriespage).

Contract clauses: metrics fall into headline, phase, component and submodule
sections by name; small multiples sort by baseline median, else latest value;
the filter index holds only keys whose values differ, keyed by their JSON.
"""

from __future__ import annotations

import pytest

from proteus_bench.report.seriespage import section_of, settings_index, size_of

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.mark.parametrize(
    ('metric', 'section'),
    [
        ('total', 'headline'),
        ('n_iters', 'headline'),
        ('loop', 'phases'),
        ('shutdown', 'phases'),
        ('init.structure', 'components'),
        ('loop.atmos.per_iter_median', 'components'),
        ('submodule.zalmoxis.total', 'submodules'),
        ('something_new', 'phases'),
    ],
)
def test_section_of(metric, section):
    """Names map to sections; an unknown metric still gets a chart, among the phases."""
    assert section_of(metric) == section
    assert section_of(metric) in {'headline', 'phases', 'components', 'submodules'}


def test_size_prefers_the_baseline_median():
    """With a baseline the median decides; without one the latest value; no points is 0."""
    points = [{'value': 1.0}, {'value': 50.0}]
    assert size_of({'baseline': {'median': 7.0}, 'points': points}) == pytest.approx(7.0)
    assert size_of({'baseline': None, 'points': points}) == pytest.approx(50.0)
    assert size_of({'baseline': None, 'points': []}) == pytest.approx(0.0)


def test_settings_index_keeps_only_differing_keys():
    """A key missing from one run counts as differing (null); equal keys are left out."""
    runs = [
        {'run_id': 'a', 'benchmark': {'settings': {'k': 1, 'same': 'x'}}},
        {'run_id': 'b', 'benchmark': {'settings': {'k': 2, 'same': 'x', 'new': True}}},
    ]
    index = settings_index(runs)
    assert index == {'k': {'1': ['a'], '2': ['b']}, 'new': {'null': ['a'], 'true': ['b']}}
    assert settings_index(runs[:1]) == {}
