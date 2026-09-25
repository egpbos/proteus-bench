"""Tests for the series trend chart (proteus_bench.report.charts).

Contract clauses: one linked point per series point, filled for comparable and
a ring otherwise; the band spans median +- 3 sigma (not 1 sigma); no band
without a baseline; regression triangles sit above and point up, improvements
below and point down, hollow while unconfirmed; the trend line breaks at a
boundary; null relative values render as n/a; unknown run ids raise.
"""

from __future__ import annotations

import re

import pytest

from proteus_bench.report.charts import series_chart

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def make_series(values, comparable=None, baseline=None, flags=(), boundaries=()) -> dict:
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
    return {
        'key': key,
        'unit': 's',
        'points': points,
        'baseline': baseline,
        'flags': list(flags),
        'steps': [],
        'boundaries': list(boundaries),
    }


def flag(
    run_id, kind='regression', confirmed=False, delta_rel=0.08, threshold_rel=0.05
) -> dict:
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


def test_points_links_and_comparability():
    """Three points, one a ring; each links to its run page; higher values sit higher."""
    svg = series_chart(make_series([10.0, 12.0, 11.0], comparable=[True, False, True]), '../')
    assert svg.count('class="pt"') == 3
    assert re.findall(r'href="\.\./runs/(r\d)\.html"', svg) == ['r0', 'r1', 'r2']
    dots, rings = circles(svg, 'dot'), circles(svg, 'ring')
    assert len(dots) == 2 and len(rings) == 1
    assert dots[1] < dots[0]  # 11 s plots above 10 s


def test_band_is_three_sigma_and_absent_without_baseline():
    """median 10, sigma 1: the band spans 7..13, three times the 1-sigma height."""
    baseline = {'median': 10.0, 'sigma': 1.0, 'n': 3}
    svg = series_chart(make_series([7.0, 10.0, 13.0], baseline=baseline), '')
    height = float(re.search(r'<g class="band">.*?<rect [^>]*height="([\d.]+)"', svg).group(1))
    ys = circles(svg, 'dot')  # the 7 s and 13 s points lie exactly on the band edges
    assert height == pytest.approx(ys[0] - ys[2], abs=0.2)
    assert height > 2.5 * (ys[0] - ys[2]) / 3  # a 1-sigma band would be a third of this
    assert 'class="band"' not in series_chart(make_series([7.0, 10.0]), '')


def test_flag_triangles_direction_and_confirmation():
    """A regression points up above its dot, an improvement down below; unconfirmed is hollow."""
    series = make_series(
        [10.0, 11.0, 9.0], flags=[flag('r1'), flag('r2', 'improvement', confirmed=True)]
    )
    svg = series_chart(series, '')
    up = re.search(r'd="M[\d.]+,([\d.]+)L[\d.]+,([\d.]+)L[^"]*" class="flag bad hollow"', svg)
    down = re.search(r'd="M[\d.]+,([\d.]+)L[\d.]+,([\d.]+)L[^"]*" class="flag good"', svg)
    y11, y9 = circles(svg, 'dot')[1:]
    assert float(up.group(1)) < float(up.group(2)) < y11  # tip above base, both above the dot
    assert float(down.group(1)) > float(down.group(2)) > y9
    assert svg.count('class="flag') == 2


def test_trend_line_breaks_at_boundary():
    """Four comparable points with a boundary before the third: two line pieces, not one."""
    boundary = {'run_id': 'r2', 'reason': 'settings_changed', 'changed_keys': ['a.b']}
    svg = series_chart(make_series([1.0, 2.0, 3.0, 4.0], boundaries=[boundary]), '')
    assert svg.count('class="trend"') == 2
    assert svg.count('class="bnd"') == 1 and 'changed keys: a.b' in svg
    assert series_chart(make_series([1.0, 2.0, 3.0, 4.0]), '').count('class="trend"') == 1


def test_null_relative_values_and_unknown_runs():
    """Null delta_rel and threshold_rel (baseline median 0) show n/a; an unknown run id raises."""
    svg = series_chart(
        make_series([0.0, 1.0], flags=[flag('r1', delta_rel=None, threshold_rel=None)]), ''
    )
    assert 'regression n/a' in svg and 'threshold n/a' in svg
    with pytest.raises(ValueError, match="'r9'"):
        series_chart(make_series([1.0, 2.0], flags=[flag('r9')]), '')
    assert 'no runs' in series_chart(make_series([]), '')
