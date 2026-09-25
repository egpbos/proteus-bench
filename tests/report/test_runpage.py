"""Tests for the run page (proteus_bench.report.runpage).

Contract clauses: a carry-over run names the run it replays; a record without
artifacts says so; artifacts without a repository show their store paths as
text, never as links; the publisher's flame page is named but not linked.
"""

from __future__ import annotations

import pytest

from proteus_bench.report.runpage import render
from proteus_bench.report.store import Artifact

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_carry_over_and_no_artifacts(records):
    """The replayed run id is shown; an empty artifact list gives a note, not an empty table."""
    record = records[5]
    record['benchmark']['carry_over_of'] = records[4]['run_id']
    html = render(record, [], [], records[4]['run_id'])
    assert f'Carry-over of</th><td><span class="mono">{records[4]["run_id"]}</span>' in html
    assert 'The record lists no artifacts.' in html
    assert 'Carry-over of' not in render(records[4], [], [], None)


def test_artifacts_without_repository_and_flame(records):
    """Present files without a URL are text; the flame page stays unlinked even with a URL."""
    artifacts = [
        Artifact(
            'flame', 'profiles/2026/x.flame.html', True, 'https://github.com/o/n/blob/results/x'
        ),
        Artifact('log', 'logs/2026/x.log.gz', True, None),
    ]
    html = render(records[0], [], artifacts, None)
    assert '<code>logs/2026/x.log.gz</code>' in html
    assert (
        '<code>profiles/2026/x.flame.html</code> <span class="note">not linked</span>' in html
    )
    assert 'blob/results/x' not in html
