"""Tests for shared page blocks (proteus_bench.report.layout).

Contract clauses: flag badges carry direction in the icon (up for a
regression, down for an improvement), the confirmation state in the icon fill
and in words, and a bad/good class for colour, so colour is never the only
cue; table rows carry run ids when given; the page loads no external fonts.
"""

from __future__ import annotations

import pytest

from proteus_bench.report.layout import flag_badge, page, table

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def badge(kind: str, confirmed) -> str:
    flag = {'kind': kind, 'delta_rel': 0.08, 'confirmed': confirmed}
    return flag_badge(flag, 'loop.atmos.per_iter_median')


@pytest.mark.parametrize(
    ('kind', 'confirmed', 'icon', 'cls', 'words'),
    [
        ('regression', True, '▲', 'bad', 'confirmed'),
        ('regression', None, '△', 'bad', 'not yet confirmed'),
        ('regression', False, '△', 'bad', 'not confirmed by the next run'),
        ('improvement', True, '▼', 'good', 'confirmed'),
        ('improvement', None, '▽', 'good', 'not yet confirmed'),
    ],
)
def test_badge_icon_class_and_words(kind, confirmed, icon, cls, words):
    """Each kind and confirmation state has its own icon, class and wording."""
    html = badge(kind, confirmed)
    assert f'<span class="badge {cls}">' in html
    assert f'aria-hidden="true">{icon}</span>' in html
    assert html.endswith(f'{kind} +8.0 %, {words}</span>')
    assert 'loop.atmos.per_iter_median' in html


def test_table_row_ids_and_escaping():
    """Row ids become data-run attributes, escaped; without ids rows have none."""
    html = table(['Run'], [['a'], ['b']], row_ids=['r1', 'x"y'])
    assert '<tr data-run="r1">' in html
    assert '<tr data-run="x&quot;y">' in html
    assert 'data-run' not in table(['Run'], [['a']])
    with pytest.raises(ValueError):
        table(['Run'], [['a'], ['b']], row_ids=['only-one'])


def test_page_uses_system_fonts_only():
    """No request to a font service; the stylesheet link is relative to the root."""
    html = page('T <x>', '<p>body</p>', '../')
    assert 'fonts.googleapis' not in html
    assert '<link rel="stylesheet" href="../style.css">' in html
    assert '<title>T &lt;x&gt;</title>' in html
