"""Tests for the Plotly figures of the dashboard (proteus_bench.report.figures).

Contract clauses: every chart comes in a light and a dark variant without its
template; a trend has one point per series point, linked to its run page, a dot
when comparable and a ring otherwise; flags are triangles, up for a regression
and down for an improvement, filled when confirmed, outlined when not yet and
grey when the next run did not confirm; the band spans median +- 3 sigma (not 1
sigma) with dashed lines at +-5 %, and is absent without a baseline; the trend
line joins comparable runs only and breaks at a boundary; boundaries and steps
are vertical lines before their run with hover text; null relative values read
n/a; an unknown run id raises; iteration time not attributed to a component is
``other``; components stack in module order, unknown ones after them and
``other`` last, each in its module's domain colour, the second module of a
domain hatched; phases use no domain or status colour and their labels read on
their fill in both themes; empty timings give no bars; the history chart orders
runs by the commit time of their base, leaves out runs without one, labels
commits by pull request, and joins each commit's median comparable run with a
line that breaks where the settings change.
"""

from __future__ import annotations

import copy

import proteus_plotly
import pytest

from proteus_bench.report import figures

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

LIGHT = proteus_plotly.colors('light')
RESERVED = ('dom-', 'positive', 'warning', 'danger', 'info')


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


def trend(series) -> dict:
    return figures.trend(series, '../', 'large', 'light').to_plotly_json()


def points_trace(figure: dict) -> dict:
    (trace,) = [t for t in figure['data'] if 'ids' in t]
    return trace


def test_both_themes_without_templates():
    """themed gives light and dark figures; the templates are embedded once per page instead."""
    both = figures.themed(figures.trend, make_series([1.0, 2.0]), '', 'small')
    assert set(both) == {'light', 'dark'}
    assert all('template' not in f['layout'] for f in both.values())
    dot = points_trace(both['dark'])['marker']['color'][0]
    assert dot == proteus_plotly.colors('dark')['text-d'] != LIGHT['text-d']
    assert figures.TEMPLATES['dark']['layout']['paper_bgcolor'] == '#05070B'  # Void


def test_points_links_and_comparability():
    """Three points, one a ring; each links to its run page under the root."""
    points = points_trace(
        trend(make_series([10.0, 12.0, 11.0], comparable=[True, False, True]))
    )
    assert points['ids'] == ['r0', 'r1', 'r2']
    assert points['customdata'] == ['../runs/r0.html', '../runs/r1.html', '../runs/r2.html']
    assert points['marker']['symbol'] == ['circle', 'circle-open', 'circle']
    assert points['y'] == [10.0, 12.0, 11.0]
    assert 'not comparable' in points['hovertext'][1]


def test_band_is_three_sigma_and_absent_without_baseline():
    """median 10, sigma 1: the band spans 7..13, not 9..11; the 5 % lines sit at 9.5 and 10.5."""
    baseline = {'median': 10.0, 'sigma': 1.0, 'n': 3}
    band, median, rel = trend(make_series([7.0, 10.0, 13.0], baseline=baseline))['data'][:3]
    assert min(band['y']) == pytest.approx(7.0)
    assert max(band['y']) == pytest.approx(13.0)
    assert band['fill'] == 'toself'
    assert median['y'] == [10.0, 10.0]
    assert [y for y in rel['y'] if y is not None] == pytest.approx([9.5, 9.5, 10.5, 10.5])
    assert rel['line']['dash'] == 'dash'
    assert len(trend(make_series([7.0, 10.0]))['data']) == 2  # trend line and points only


def test_band_covers_the_last_n_comparable_runs():
    """n 2 over five runs whose fourth is not comparable: the band starts before the third run."""
    baseline = {'median': 10.0, 'sigma': 0.1, 'n': 2}
    series = make_series(
        [10.0] * 5, comparable=[True, True, True, False, True], baseline=baseline
    )
    band = trend(series)['data'][0]
    assert min(band['x']) == pytest.approx(
        1.5
    )  # comparable runs 2 and 4; a slice of all runs gives 2.5
    assert max(band['x']) == pytest.approx(4.5)


@pytest.mark.parametrize(
    ('kind', 'confirmed', 'symbol', 'colour'),
    [
        ('regression', True, 'triangle-up', LIGHT['danger']),
        ('regression', None, 'triangle-up-open', LIGHT['danger']),
        ('regression', False, 'triangle-up-open', LIGHT['text-d3']),
        ('improvement', True, 'triangle-down', LIGHT['positive']),
        ('improvement', None, 'triangle-down-open', LIGHT['positive']),
    ],
)
def test_flag_marks(kind, confirmed, symbol, colour):
    """Direction in the triangle, confirmation in its fill and colour, and in the hover text."""
    series = make_series([10.0, 11.0], flags=[flag('r1', kind, confirmed)])
    points = points_trace(trend(series))
    assert points['marker']['symbol'] == ['circle', symbol]
    assert points['marker']['color'][1] == colour
    words = {True: 'confirmed, threshold', None: 'not yet confirmed', False: 'by the next run'}
    assert words[confirmed] in points['hovertext'][1]
    assert kind in points['hovertext'][1]


def test_trend_line_joins_comparable_runs_and_breaks_at_boundary():
    """A boundary before the third run splits the line; a ring is never on it."""
    boundary = {'run_id': 'r2', 'reason': 'settings_changed', 'changed_keys': ['a.b']}
    line = trend(make_series([1.0, 2.0, 3.0, 4.0], boundaries=[boundary]))['data'][0]
    assert line['x'] == [0, 1, None, 2, 3]
    ring = make_series([1.0, 9.0, 2.0], comparable=[True, False, True])
    assert trend(ring)['data'][0]['x'] == [0, 2]


def test_boundary_and_step_markers():
    """Both sit before their run; the boundary names its keys, the step its change."""
    boundary = {'run_id': 'r2', 'reason': 'settings_changed', 'changed_keys': []}
    step = {'after_run_id': 'r3', 'before': 1.0, 'after': 2.0, 'delta_rel': 1.0}
    layout = trend(make_series([1.0, 1.0, 1.0, 2.0], boundaries=[boundary], steps=[step]))[
        'layout'
    ]
    labels = {a['text']: a for a in layout['annotations']}
    assert labels['settings']['x'] == pytest.approx(1.5)
    assert labels['settings']['y'] == 1
    assert 'changed keys: none listed' in labels['settings']['hovertext']
    assert labels['+100.0 %']['x'] == pytest.approx(2.5)
    assert labels['+100.0 %']['y'] == 0
    assert [s['line']['dash'] for s in layout['shapes']] == ['solid', 'dot']


def test_x_labels_stay_few():
    """Twenty runs get at most six date labels, starting at the first run."""
    xaxis = trend(make_series([1.0] * 20))['layout']['xaxis']
    assert xaxis['tickvals'] == [0, 4, 8, 12, 16]
    assert xaxis['ticktext'][0] == '09-10'


def test_null_relative_values_and_unknown_runs():
    """Null delta_rel and threshold_rel (baseline median 0) show n/a; an unknown run id raises."""
    nulls = make_series([0.0, 1.0], flags=[flag('r1', delta_rel=None, threshold_rel=None)])
    text = points_trace(trend(nulls))['hovertext'][1]
    assert 'regression n/a' in text
    assert 'threshold n/a' in text
    unknown_flag = make_series([1.0, 2.0], flags=[flag('r9')])
    unknown_step = make_series([1.0], steps=[{'after_run_id': 'r7'}])
    with pytest.raises(ValueError, match="'r9'"):
        trend(unknown_flag)
    with pytest.raises(ValueError, match="'r7'"):
        trend(unknown_step)


def test_hover_text_is_escaped():
    """Plotly reads tags in text: a commit with markup arrives as entities."""
    series = make_series([1.0])
    series['points'][0]['commit'] = '<b>x</b>'
    assert '&lt;b&gt;x' in points_trace(trend(series))['hovertext'][0]


def test_unattributed_iteration_time_becomes_other(records):
    """Iteration 1 of r1: 3.5 + 1.0 + 780.0 attributed of 784.7 s, so other = 0.2 s."""
    rows = figures.iteration_rows(records[0])
    assert rows[0]['other'] == pytest.approx(0.2, abs=1e-6)
    exact = copy.deepcopy(records[0])
    exact['timings']['per_iter'][0]['dur_s'] = 784.5  # fully attributed: no other row
    assert 'other' not in figures.iteration_rows(exact)[0]


def test_stack_order_and_domain_colours():
    """Modules first in their order, unknown names alphabetically, other last; colours by domain."""
    names = figures.stack_order({'other', 'zeta', 'atmos', 'alpha', 'structure', 'interior'})
    assert names == ['interior', 'structure', 'atmos', 'alpha', 'zeta', 'other']
    markers = figures.component_markers(names, 'light')
    assert (
        markers['interior']['color'] == markers['structure']['color'] == LIGHT['dom-interior']
    )
    assert markers['interior']['pattern']['shape'] == ''
    assert (
        markers['structure']['pattern']['shape'] == '/'
    )  # Zalmoxis, the second interior module
    assert markers['atmos']['color'] == LIGHT['dom-atmos']
    assert markers['zeta'] == markers['other']
    assert markers['zeta']['color'] == LIGHT['text-d3']
    escape = figures.component_markers(['chem', 'escape'], 'light')
    assert escape['escape']['color'] == escape['chem']['color']
    assert escape['escape']['pattern']['shape'] == '/'


def test_iteration_bars(records):
    """Six iterations per component; structure only in iterations 3 and 6."""
    bars = {
        t['name']: t for t in figures.iterations(records[0], 'light').to_plotly_json()['data']
    }
    assert list(bars) == ['interior', 'structure', 'atmos', 'outgas', 'other']
    assert bars['structure']['y'] == pytest.approx([0, 0, 140.0, 0, 0, 140.0])
    assert bars['atmos']['hovertext'][0] == 'iteration 1 (init stage), atmos: 13:00 min'


def test_phase_bar_segments(records):
    """r1: startup 21, init 1772, loop 1163.2, shutdown 5 s; setup (null) is left out."""
    bars = figures.phases(records[0], 'light').to_plotly_json()['data']
    assert [(b['name'], b['x'][0]) for b in bars] == [
        ('startup', 21.0),
        ('init', 1772.0),
        ('loop', pytest.approx(1163.2)),
        ('shutdown', 5.0),
    ]
    assert bars[1]['hovertext'] == ['init: 29:32 min (60%)']  # 1772 of 2961.2 s


def luminance(hex_colour: str) -> float:
    """WCAG 2.1 relative luminance of ``#RRGGBB``."""
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_phases_use_no_module_or_status_colour_and_labels_read():
    """Distinct non-domain fills; each label has WCAG contrast >= 4.5 on its fill, both themes."""
    assert len({fill for fill, _ in figures.PHASE_FILLS.values()}) == 4  # startup = setup
    for fill, label in figures.PHASE_FILLS.values():
        assert not fill.startswith(RESERVED)
        for theme in figures.THEMES:
            c = proteus_plotly.colors(theme)
            assert contrast(c[fill], c[label]) >= 4.5, (fill, theme)
    # a fixed white label fails on the verdant loop fill
    assert contrast(LIGHT['verdant'], '#FFFFFF') < 4.5


def base(sha: str, day: int, subject: str = 'Change things') -> dict:
    return {'sha': sha, 'committed_at': f'2026-08-{day:02d}T12:00:00Z', 'subject': subject}


def placed(*runs) -> dict:
    """A series of (base or None, value, comparable, settings hash) runs, in run order."""
    series = make_series([v for _, v, _, _ in runs], comparable=[c for _, _, c, _ in runs])
    for point, (b, _, _, settings) in zip(series['points'], runs, strict=True):
        point |= {'base': b, 'settings_hash': settings}
    return series


def history(series) -> dict:
    return figures.history(series, '../', 'large', 'light').to_plotly_json()


def test_history_orders_runs_by_commit_time_not_run_time():
    """A run made later of an older commit lands left of it; runs without a base are left out."""
    old, new = (
        base('aaaa0000', 1, 'Old (#678)'),
        base('bbbb1111', 20, 'Merge pull request #900 from x/y'),
    )
    fig = history(placed((new, 2.0, True, 's'), (None, 9.0, True, 's'), (old, 1.0, True, 's')))
    assert fig['layout']['xaxis']['ticktext'] == ['#678', '#900']
    trace = points_trace(fig)
    assert trace['ids'] == ['r2', 'r0']
    assert trace['x'] == [0, 1]
    assert trace['customdata'] == ['../runs/r2.html', '../runs/r0.html']


def test_history_line_joins_commit_medians_and_breaks_at_settings_change():
    """Three runs of one commit give their median; a ring is not counted; new settings break the line."""
    a, b, c = base('a0000000', 1), base('b0000000', 2), base('c0000000', 3)
    runs = [
        (a, 1.0, True, 's1'),
        (a, 5.0, True, 's1'),
        (a, 2.0, True, 's1'),
        (a, 99.0, False, 's1'),
    ]
    runs += [(b, 3.0, True, 's1'), (c, 7.0, True, 's2'), (c, 8.0, True, 's3')]
    fig = history(placed(*runs))
    line = fig['data'][0]
    assert line['x'] == [0, 1, None, 2, None, 2]
    assert line['y'] == [2.0, 3.0, None, 7.0, None, 8.0]  # s2 and s3 never share a median
    assert [a['x'] for a in fig['layout']['annotations']] == pytest.approx([1.5, 1.5])
    xs = points_trace(fig)['x'][:4]
    assert xs == sorted(xs) and max(xs) - min(xs) <= 0.6  # side by side around position 0
    assert sum(xs) == pytest.approx(0)


def test_commit_label_falls_back_to_the_sha():
    assert figures.commit_label(base('abcd1234', 1, 'Fix a bug (#916)')) == '#916'
    assert figures.commit_label(base('abcd1234', 1, 'Merge pull request #7 from a/b')) == '#7'
    assert figures.commit_label(base('abcd1234', 1, 'Refer to #916 in the docs')) == 'abcd1234'
    assert figures.commit_label({'sha': 'abcd1234', 'committed_at': 'x'}) == 'abcd1234'
