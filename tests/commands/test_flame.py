"""Tests for ``proteus-bench flame`` (proteus_bench.commands.flame).

Contract clauses: a scalene JSON, a .folded and a .folded.gz input each give a
page and exit 0 with the sample count; the profiler is shown only when known from
the input; an empty or unreadable input exits 1 with one message on stderr that
names the input once and the likely cause, and writes no page.
"""

from __future__ import annotations

import gzip
import json

import pytest

from proteus_bench import cli

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_each_input_type_gives_a_page(tmp_path, capsys, profiles):
    """JSON, plain folded and gzipped folded inputs all render, with the right totals."""
    gz = tmp_path / 'slice.folded.gz'
    gz.write_bytes(gzip.compress(profiles.slice.read_bytes()))
    cases = [
        (profiles.scalene, profiles.scalene_total, 'profiler scalene'),
        (profiles.slice, profiles.slice_total, None),
        (gz, profiles.slice_total, None),
    ]
    for i, (source, total, marker) in enumerate(cases):
        out = tmp_path / f'page{i}.html'
        code = cli.main(['flame', str(source), '--out', str(out), '--title', 'Manual run'])
        assert code == 0, source
        assert f'{total:,} samples' in capsys.readouterr().out
        page = out.read_text()
        assert '<h1>Manual run</h1>' in page
        if marker:
            assert marker in page
        else:
            assert 'profiler ' not in page  # a folded file does not say which profiler


def test_default_output_name(tmp_path, monkeypatch, capsys, profiles):
    """Without --out the page is flame.html in the working directory; a bad dir fails."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(['flame', str(profiles.slice)]) == 0
    assert 'PROTEUS CPU flame graph' in (tmp_path / 'flame.html').read_text()
    missing_dir = tmp_path / 'no' / 'flame.html'
    assert cli.main(['flame', str(profiles.slice), '--out', str(missing_dir)]) == 1
    assert f'{missing_dir}: No such file' in capsys.readouterr().err


def test_empty_profiles_fail_clearly(tmp_path, capsys, profiles):
    """An empty scalene profile or folded file exits 1 and explains why; no page appears."""
    profile = json.loads(profiles.scalene.read_text())
    empty_json = tmp_path / 'scalene-profile.json'
    empty_json.write_text(json.dumps(profile | {'combined_stacks': [], 'files': {}}))
    empty_folded = tmp_path / 'empty.folded'
    empty_folded.write_text('\n')
    for source, cause in ((empty_json, '--profile-all'), (empty_folded, 'no samples')):
        out = tmp_path / 'flame.html'
        assert cli.main(['flame', str(source), '--out', str(out)]) == 1
        err = capsys.readouterr().err
        assert err.count(str(source)) == 1
        assert cause in err
        assert not out.exists()


def test_unreadable_inputs_fail_clearly(tmp_path, capsys):
    """Missing files, broken JSON or gzip and malformed lines exit 1, naming the input once."""
    broken = tmp_path / 'broken.json'
    broken.write_text('{"combined_stacks": ')
    malformed = tmp_path / 'bad.folded'
    malformed.write_text('a;b 1\na;b\n')
    corrupt = tmp_path / 'bad.folded.gz'
    corrupt.write_bytes(b'not gzip')
    cases = (
        (tmp_path / 'missing.folded', 'No such file'),
        (broken, 'not valid JSON'),
        (malformed, 'line 2'),
        (corrupt, 'Not a gzipped file'),
    )
    for source, reason in cases:
        assert cli.main(['flame', str(source), '--out', str(tmp_path / 'x.html')]) == 1
        err = capsys.readouterr().err
        assert reason in err, source
        assert err.count(str(source)) == 1, err
