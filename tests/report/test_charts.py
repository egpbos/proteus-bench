"""Tests for the series trend chart (proteus_bench.report.charts).

Contract clauses: one linked point per series point, filled for comparable and
a ring otherwise; the chart is a labelled group so its links stay reachable;
the band spans median +- 3 sigma (not 1 sigma) and the axis always covers the
+-5 % lines; no band without a baseline; regression triangles sit above and
point up, improvements below and point down; filled when confirmed, outlined
when not yet, dashed when the next run did not confirm; the trend line joins
comparable runs only and breaks at a boundary; boundary labels sit above the
plot and step labels at its foot; one date label for a single run, two
otherwise; null relative values render as n/a; unknown run ids raise.
"""

from __future__ import annotations

import re

import pytest

from proteus_bench.report.charts import series_chart
from proteus_bench.report.svg import MARGINS

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

TOP = MARGINS[2]


def make_series(values, comparable=None, **fields) -> dict:
    """A series of runs r0, r1, ...; ``fields`` override baseline, flags, steps, boundaries."""
    comparable = comparable or [True] * len(values)
    points = [
        {
            'run_id': f'r{i}',
            'commit': f'abc{i:05d}',
            'time': f'2026-09-{10 + i}T00:00:00Z',
            'value': v,
            'comparable': c,
        }
        for i, (v, c) in enumerate(zip(values, comparable, strict=True))
    ]
    key = {'benchmark': 'b', 'lineage': 'default', 'machine_class': 'm', 'metric': 'total'}
    marks = {'baseline': None, 'flags': [], 'steps': [], 'boundaries': []}
    return {'key': key, 'unit': 's', 'points': points} | marks | fields


def flag(run_id, kind='regression', confirmed=None, delta_rel=0.08, threshold_rel=0.05) -> dict:
    return {
        'run_id': run_id,
        'kind': kind,
        'delta_rel': delta_rel,
        'delta_abs': 1.2,
        'threshold_rel': threshold_rel,
        'confirmed': confirmed,
    }


def circles(svg: str, cls: str) -> list[float]:
    return [float(y) for y in re.findall(rf'cy="([\d.]+)" r="4" class="{cls}"', svg)]


def tick_values(svg: str) -> list[float]:
    return [float(v) for v in re.findall(r'text-anchor="end">(-?[\d.]+)</text>', svg)]


def test_points_links_and_comparability():
    """Three points, one a ring; each links to its run page; higher values sit higher."""
    svg = series_chart(make_series([10.0, 12.0, 11.0], comparable=[True, False, True]), '../')
    assert svg.count('class="pt"') == 3
    assert re.findall(r'href="\.\./runs/(r\d)\.html"', svg) == ['r0', 'r1', 'r2']
    dots, rings = circles(svg, 'dot'), circles(svg, 'ring')
    assert len(dots) == 2
    assert len(rings) == 1
    assert dots[1] < dots[0]  # 11 s plots above 10 s
    assert 'role="group" aria-label="total: 3 runs' in svg
    assert 'role="img"' not in svg


def test_band_is_three_sigma_and_absent_without_baseline():
    """median 10, sigma 1: the band spans 7..13, three times the 1-sigma height."""
    baseline = {'median': 10.0, 'sigma': 1.0, 'n': 3}
    svg = series_chart(make_series([7.0, 10.0, 13.0], baseline=baseline), '')
    height = float(re.search(r'<g class="band">.*?<rect [^>]*height="([\d.]+)"', svg).group(1))
    ys = circles(svg, 'dot')  # the 7 s and 13 s points lie exactly on the band edges
    assert height == pytest.approx(ys[0] - ys[2], abs=0.2)
    assert height > 2.5 * (ys[0] - ys[2]) / 3  # a 1-sigma band would be a third of this
    assert 'class="band"' not in series_chart(make_series([7.0, 10.0]), '')


def test_axis_covers_the_five_percent_lines():
    """median 100 s, sigma 0.1 s: 3 sigma is 0.3 s but the 5 % lines at 95 and 105 s must fit."""
    baseline = {'median': 100.0, 'sigma': 0.1, 'n': 2}
    ticks = tick_values(series_chart(make_series([100.0, 101.0], baseline=baseline), ''))
    assert min(ticks) <= 95.0  # without the 5 % floor the axis would start near 99.5
    assert max(ticks) >= 105.0


def test_flag_triangles_direction():
    """A regression points up above its dot, an improvement down below it."""
    series = make_series([10.0, 11.0, 9.0], flags=[flag('r1'), flag('r2', 'improvement', True)])
    svg = series_chart(series, '')
    up = re.search(r'd="M[\d.]+,([\d.]+)L[\d.]+,([\d.]+)L[^"]*" class="flag bad hollow"', svg)
    down = re.search(r'd="M[\d.]+,([\d.]+)L[\d.]+,([\d.]+)L[^"]*" class="flag good"', svg)
    y11, y9 = circles(svg, 'dot')[1:]
    assert float(up.group(1)) < float(up.group(2)) < y11  # tip above base, both above the dot
    assert float(down.group(1)) > float(down.group(2)) > y9
    assert svg.count('class="flag') == 2


@pytest.mark.parametrize(
    ('confirmed', 'cls', 'text'),
    [
        (True, 'flag bad"', 'confirmed, threshold'),
        (None, 'flag bad hollow"', 'not yet confirmed'),
        (False, 'flag bad hollow rejected"', 'not confirmed by the next run'),
    ],
)
def test_three_confirmation_states(confirmed, cls, text):
    """true, null and false each get their own fill and hover text."""
    svg = series_chart(make_series([10.0, 11.0], flags=[flag('r1', confirmed=confirmed)]), '')
    assert f'class="{cls}' in svg
    assert text in svg
    assert svg.count('class="flag') == 1


def test_trend_line_joins_comparable_runs_and_breaks_at_boundary():
    """A boundary before the third run splits the line; a ring is never on it."""
    boundary = {'run_id': 'r2', 'reason': 'settings_changed', 'changed_keys': ['a.b']}
    svg = series_chart(make_series([1.0, 2.0, 3.0, 4.0], boundaries=[boundary]), '')
    assert svg.count('class="trend"') == 2
    assert svg.count('class="bnd"') == 1
    assert 'changed keys: a.b' in svg
    ring = make_series([1.0, 9.0, 2.0], comparable=[True, False, True])
    line = re.search(r'<polyline points="([^"]+)" class="trend"', series_chart(ring, '')).group(
        1
    )
    assert len(line.split()) == 2  # r0 and r2 only, the ring r1 is skipped


def test_boundary_label_above_and_step_label_below():
    """Boundary text sits above the plot top, step text inside the plot near its foot."""
    boundary = {'run_id': 'r2', 'reason': 'settings_changed', 'changed_keys': []}
    step = {'after_run_id': 'r2', 'before': 1.0, 'after': 2.0, 'delta_rel': 1.0}
    svg = series_chart(
        make_series([1.0, 1.0, 2.0, 2.0], boundaries=[boundary], steps=[step]), ''
    )
    bnd_y = float(re.search(r'y="([\d.]+)" class="tick">settings<', svg).group(1))
    step_y = float(re.search(r'y="([\d.]+)" class="tick">\+100\.0 %<', svg).group(1))
    assert bnd_y < TOP
    assert step_y > TOP + 100  # 200-high chart: the foot, far below the top labels
    assert 'changed keys: none listed' in svg


def test_date_labels():
    """One run: one date label. Several: first and last date."""
    one = series_chart(make_series([5.0]), '')
    assert re.findall(r'>(2026-09-\d\d)</text>', one) == ['2026-09-10']
    three = series_chart(make_series([5.0, 6.0, 7.0]), '')
    assert re.findall(r'>(2026-09-\d\d)</text>', three) == ['2026-09-10', '2026-09-12']


def test_null_relative_values_and_unknown_runs():
    """Null delta_rel and threshold_rel (baseline median 0) show n/a; an unknown run id raises."""
    nulls = make_series([0.0, 1.0], flags=[flag('r1', delta_rel=None, threshold_rel=None)])
    svg = series_chart(nulls, '')
    assert 'regression n/a' in svg
    assert 'threshold n/a' in svg
    unknown = make_series([1.0, 2.0], flags=[flag('r9')])
    with pytest.raises(ValueError, match="'r9'"):
        series_chart(unknown, '')
    assert 'no runs' in series_chart(make_series([]), '')
