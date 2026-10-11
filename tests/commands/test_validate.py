"""Tests for ``proteus-bench validate`` (proteus_bench.commands.validate).

Contract clauses: exit 0 and 'ok' for a valid record; exit 1 with a problem
count for an invalid one; unreadable JSON is reported instead of raising; a
missing jsonschema is stated, not hidden.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proteus_bench import cli, schema

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

EXAMPLES = Path(__file__).parent.parent.parent / 'examples'


def test_valid_example_passes(capsys):
    """The committed example record validates cleanly through the CLI."""
    pytest.importorskip('jsonschema')
    code = cli.main(['validate', str(EXAMPLES / 'record.json')])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count(': ok') == 1
    assert 'not checked' not in out


def test_invalid_record_fails_with_count(tmp_path, capsys):
    """A record with a wrong field fails and names it."""
    pytest.importorskip('jsonschema')
    record = json.loads((EXAMPLES / 'record.json').read_text())
    record['machine']['n_cpus'] = 0
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps(record))
    assert cli.main(['validate', str(bad)]) == 1
    out = capsys.readouterr().out
    assert '1 problem(s)' in out
    assert 'machine/n_cpus' in out


def test_unreadable_record_is_reported_not_raised(tmp_path, capsys):
    """Broken JSON gives a clear problem line and a failing exit code."""
    broken = tmp_path / 'record.json'
    broken.write_text('{"schema": ')
    code = cli.main(['validate', str(broken)])
    assert code == 1
    assert 'not valid JSON' in capsys.readouterr().out


def test_missing_jsonschema_is_stated(monkeypatch, capsys):
    """Without jsonschema the output says the shape was not checked."""
    monkeypatch.setattr(schema, '_validator', lambda kind: None)
    assert cli.main(['validate', str(EXAMPLES / 'record.json')]) == 0
    assert 'shape not checked' in capsys.readouterr().out
