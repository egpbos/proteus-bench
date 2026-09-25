"""Tests for ``proteus-bench validate`` (proteus_bench.commands.validate).

Contract clauses: exit 0 and 'ok' for valid files; exit 1 with a problem count
for invalid ones; timing files get both shape and tree checks; unreadable JSON
is reported instead of raising; a missing jsonschema is stated, not hidden.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proteus_bench import cli, schema

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

EXAMPLES = Path(__file__).parent.parent.parent / 'examples'


def test_valid_examples_pass(capsys):
    """Both committed examples validate cleanly through the CLI."""
    pytest.importorskip('jsonschema')
    code = cli.main(['validate', str(EXAMPLES / 'timing.jsonl'), str(EXAMPLES / 'record.json')])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count(': ok') == 2
    assert 'not checked' not in out


def test_tree_violation_fails_with_count(tmp_path, capsys):
    """A timing file that is well-formed per line but breaks the tree rules fails."""
    events = [json.loads(line) for line in (EXAMPLES / 'timing.jsonl').read_text().splitlines()]
    group = next(e for e in events if e['ev'] == 'span' and e['name'] == 'equilibrate')
    group['component'] = 'structure'  # now nested attribution
    bad = tmp_path / 'bad.jsonl'
    bad.write_text(''.join(json.dumps(e) + '\n' for e in events))
    code = cli.main(['validate', str(bad)])
    out = capsys.readouterr().out
    assert code == 1
    assert 'problem(s)' in out
    assert 'ancestor' in out


def test_unreadable_record_is_reported_not_raised(tmp_path, capsys):
    """Broken JSON gives a clear problem line and a failing exit code."""
    broken = tmp_path / 'record.json'
    broken.write_text('{"schema": ')
    code = cli.main(['validate', str(broken)])
    assert code == 1
    assert 'not valid JSON' in capsys.readouterr().out


def test_missing_jsonschema_is_stated_and_tree_rules_still_run(monkeypatch, tmp_path, capsys):
    """Without jsonschema the output says shape was skipped, yet tree violations still fail."""
    monkeypatch.setattr(schema, '_validator', lambda kind: None)
    assert cli.main(['validate', str(EXAMPLES / 'timing.jsonl')]) == 0
    assert 'shape not checked' in capsys.readouterr().out
    events = [json.loads(line) for line in (EXAMPLES / 'timing.jsonl').read_text().splitlines()]
    events.insert(1, events[-1])  # run_end in the middle: a tree rule, not a shape rule
    bad = tmp_path / 'bad.jsonl'
    bad.write_text(''.join(json.dumps(e) + '\n' for e in events))
    assert cli.main(['validate', str(bad)]) == 1
    assert 'run_end must be the last' in capsys.readouterr().out


def test_corrupt_timing_line_is_reported_with_its_location(tmp_path, capsys):
    """A torn line in the middle of timing.jsonl fails with file and line number."""
    lines = (EXAMPLES / 'timing.jsonl').read_text().splitlines()
    corrupt = tmp_path / 'corrupt.jsonl'
    corrupt.write_text('\n'.join([lines[0], '{"v": 1,', *lines[1:]]) + '\n')
    assert cli.main(['validate', str(corrupt)]) == 1
    out = capsys.readouterr().out
    assert 'corrupt.jsonl:2: not valid JSON' in out
    assert '1 problem(s)' in out
