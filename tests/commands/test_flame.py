"""Tests for ``proteus-bench flame`` (proteus_bench.commands.flame).

Contract clauses: a scalene JSON, a .folded and a .folded.gz input each give a
page and exit 0 with the sample count; an empty or unreadable profile exits 1
with a message on stderr naming the input and the likely cause, and writes no page.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from proteus_bench import cli

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

FIXTURES = Path(__file__).parent.parent / 'fixtures'
FIXTURE_TOTAL = 90  # 40 + 25 + 12 + 8 + 3 + 2 hits in scalene-profile.json
SLICE_TOTAL = 22562  # awk '{s+=$NF} END {print s}' real-slice.folded


def test_each_input_type_gives_a_page(tmp_path, capsys):
    """JSON, plain folded and gzipped folded inputs all render, with the right totals."""
    gz = tmp_path / 'slice.folded.gz'
    gz.write_bytes(gzip.compress((FIXTURES / 'real-slice.folded').read_bytes()))
    cases = [
        (FIXTURES / 'scalene-profile.json', FIXTURE_TOTAL, 'profiler scalene'),
        (FIXTURES / 'real-slice.folded', SLICE_TOTAL, None),
        (gz, SLICE_TOTAL, None),
    ]
    for i, (source, total, marker) in enumerate(cases):
        out = tmp_path / f'page{i}.html'
        code = cli.main(['flame', str(source), '--out', str(out), '--title', 'Manual run'])
        assert code == 0, source
        assert f'{total:,} samples' in capsys.readouterr().out
        page = out.read_text()
        assert '<h1>Manual run</h1>' in page
        assert (marker in page) if marker else ('profiler ' not in page)


def test_default_output_name(tmp_path, monkeypatch, capsys):
    """Without --out the page is flame.html in the working directory."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(['flame', str(FIXTURES / 'real-slice.folded')]) == 0
    assert (tmp_path / 'flame.html').exists()
    assert 'PROTEUS CPU flame graph' in (tmp_path / 'flame.html').read_text()


def test_empty_profiles_fail_clearly(tmp_path, capsys):
    """An empty scalene profile or folded file exits 1 and explains why; no page appears."""
    profile = json.loads((FIXTURES / 'scalene-profile.json').read_text())
    empty_json = tmp_path / 'scalene-profile.json'
    empty_json.write_text(json.dumps(profile | {'combined_stacks': [], 'files': {}}))
    empty_folded = tmp_path / 'empty.folded'
    empty_folded.write_text('\n')
    for source, cause in ((empty_json, '--profile-all'), (empty_folded, 'no samples')):
        out = tmp_path / 'flame.html'
        assert cli.main(['flame', str(source), '--out', str(out)]) == 1
        err = capsys.readouterr().err
        assert str(source) in err and cause in err
        assert not out.exists()


def test_unreadable_inputs_fail_clearly(tmp_path, capsys):
    """Missing files, broken JSON and malformed folded lines exit 1 with a reason."""
    broken = tmp_path / 'broken.json'
    broken.write_text('{"combined_stacks": ')
    malformed = tmp_path / 'bad.folded'
    malformed.write_text('a;b 1\na;b\n')
    cases = ((tmp_path / 'missing.folded', 'No such file'), (broken, 'not valid JSON'))
    for source, reason in (*cases, (malformed, 'line 2')):
        assert cli.main(['flame', str(source), '--out', str(tmp_path / 'x.html')]) == 1
        assert reason in capsys.readouterr().err
