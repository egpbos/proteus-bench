"""Tests for dashboard formatting and link helpers (proteus_bench.report.fmt).

Contract clauses: durations switch format at 100 s and 1 h; missing values
render as n/a; relative changes carry a sign; slugs are file-name safe and the
series-page separator cannot come from a slug; a commit URL keeps the sha in its
last path segment.
"""

from __future__ import annotations

import pytest

from proteus_bench.report.fmt import commit_url, fmt_rel, fmt_s, fmt_value, series_href, slug

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_duration_formats_at_their_boundaries():
    """99.9 s, 100 s = 1:40 min, 3599 s = 59:59 min, 3600 s = 1 h 00 min, 1 h 30.5 min rounds."""
    assert fmt_s(99.94) == '99.9 s'
    assert fmt_s(100.0) == '1:40 min'
    assert fmt_s(3599.0) == '59:59 min'
    assert fmt_s(3600.0) == '1 h 00 min'
    assert fmt_s(5430.0) == '1 h 30 min'  # 90.5 min rounds half to even
    assert fmt_s(None) == 'n/a'


def test_values_and_relative_changes():
    """Non-second units print as given; a count has no unit; signs are explicit."""
    assert fmt_value(6, 'count') == '6'  # n_iters, unit 'count' in the analysis output
    assert fmt_value(6, '') == '6'
    assert fmt_value(2.5, 'GB') == '2.5 GB'
    assert fmt_value(None, 's') == 'n/a'
    assert fmt_rel(0.081) == '+8.1 %'
    assert fmt_rel(-0.07) == '-7.0 %'
    assert fmt_rel(None) == 'n/a'


def test_slugs_and_series_paths_are_unambiguous():
    """A settings-hash lineage becomes a safe name; the ``--`` separator never comes from a slug."""
    assert slug('sha256:AB12') == 'sha256-ab12'
    assert slug('a--b / c') == 'a-b-c'
    assert len(slug('x' * 100)) == 64
    assert series_href(('a-', '-b', 'c')).startswith('series/a--b--c--')
    assert series_href(('A', 'b', 'c')) != series_href(('a', 'b', 'c'))  # same slug, other key
    # Stable across builds and machines: pinned to sha256('["a", "b", "c"]'), computed
    # independently with `shasum -a 256`, so a per-process salt (like hash()) would fail.
    assert series_href(('a', 'b', 'c')) == 'series/a--b--c--d33b202c02.html'


def test_commit_url_cannot_leave_the_commit_page():
    """Slashes and dots in a sha are percent-encoded, so the URL stays on one commit page."""
    assert commit_url('abd4ca53') == 'https://github.com/FormingWorlds/PROTEUS/commit/abd4ca53'
    assert commit_url('../../x?y') == (
        'https://github.com/FormingWorlds/PROTEUS/commit/..%2F..%2Fx%3Fy'
    )


@pytest.mark.parametrize('seconds', [0.0, 0.04])
def test_near_zero_durations(seconds):
    """Zero and sub-0.05 s durations still format, as 0.0 s."""
    assert fmt_s(seconds) == '0.0 s'
    assert fmt_value(seconds, 's') == '0.0 s'
