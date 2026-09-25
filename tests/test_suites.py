"""Tests for proteus_bench.suites.

Contract clauses: the packaged suites.toml defines the default suite as
all_options.toml capped at 16 iterations expecting Aragog CVODE; unknown
suites, missing files and malformed suites (unknown keys, no config, bad
override or backend keys) fail at load time with the reason; overrides replace
only the named keys, create missing tables and refuse to descend into a value;
the run config names its output after the run id.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proteus_bench.suites import (
    SUITES,
    apply_overrides,
    build_run_config,
    load_suite,
    suite_problems,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

DATA = Path(__file__).parent / 'data'


def test_default_suite_from_the_packaged_file():
    """The default suite matches the agreed benchmark definition and ships in the package."""
    suite = load_suite('default')
    assert suite == {
        'name': 'default',
        'config': 'input/all_options.toml',
        'overrides': {'params.stop.iters.maximum': 16},
        'expected_backends': {'aragog.solver': 'cvode'},
    }
    assert SUITES.name == 'suites.toml'
    assert SUITES.is_file()


def test_unknown_suite_and_missing_file_are_errors(tmp_path):
    """Asking for a suite that does not exist names the known ones or the missing path."""
    with pytest.raises(ValueError, match=r"unknown suite 'nope'.*\['default'\]"):
        load_suite('nope')
    missing = tmp_path / 'suites.toml'
    with pytest.raises(ValueError, match='not found'):
        load_suite('default', missing)
    missing.write_text('[suite.small]\nconfig = "input/dummy.toml"\n')
    assert load_suite('small', missing)['overrides'] == {}  # optional tables default empty


def test_malformed_suites_fail_at_load_time(tmp_path):
    """A suite that would break after a finished run is refused before the run."""
    path = tmp_path / 'suites.toml'
    path.write_text(
        '[suite.bad]\noverrides = { "a..b" = 1 }\nexpected_backends = { solver = "cvode" }\n'
        'expected = 1\n'
    )
    with pytest.raises(ValueError, match="suite 'bad'") as err:
        load_suite('bad', path)
    message = str(err.value)
    assert "unknown key 'expected'" in message
    assert 'config must be a path' in message
    assert "override key 'a..b' has an empty part" in message
    assert "expected_backends key 'solver' is not" in message


def test_suite_problems_per_rule():
    """Each rule reports on its own; a valid entry has no problems."""
    good = {'config': 'input/x.toml', 'overrides': {'a': 1}, 'expected_backends': {'b.c': 'd'}}
    assert suite_problems(good) == []
    assert suite_problems({'config': ''}) == ['config must be a path to a PROTEUS config file']
    assert suite_problems({'config': 'x', 'overrides': 3}) == ['overrides must be a table']
    assert suite_problems({'config': 'x', 'expected_backends': []}) == [
        'expected_backends must be a table'
    ]
    assert suite_problems({'config': 'x', 'expected_backends': {'b.c': 1}}) == [
        "expected_backends 'b.c' must be a string, got 1"
    ]
    assert suite_problems({'config': 'x', 'expected_backends': {'.c': 'v'}}) == [
        'expected_backends key \'.c\' is not "<submodule>.<key>"'
    ]


def test_overrides_replace_only_named_keys_and_create_tables():
    """Siblings survive, new tables appear, and the input config is not modified."""
    config = {'params': {'stop': {'iters': {'maximum': 9000, 'minimum': 5}}, 'x': 1}}
    out = apply_overrides(config, {'params.stop.iters.maximum': 16, 'new.table.key': 'v'})
    assert out['params']['stop']['iters'] == {'maximum': 16, 'minimum': 5}
    assert out['new'] == {'table': {'key': 'v'}}
    assert config['params']['stop']['iters']['maximum'] == 9000  # deep copy
    assert apply_overrides(config, {}) == config


def test_override_through_a_value_is_refused():
    """Setting 'a.b.c' when 'a.b' is a number would silently drop the number: refuse."""
    with pytest.raises(ValueError, match=r"'a\.b' is a value, not a table"):
        apply_overrides({'a': {'b': 3}}, {'a.b.c': 1})
    assert apply_overrides({'a': {'b': 3}}, {'a.b': {'c': 1}}) == {'a': {'b': {'c': 1}}}


def test_run_config_from_a_proteus_checkout(tmp_path):
    """The real all_options.toml gets the suite cap and the run id as output name."""
    (tmp_path / 'input').mkdir()
    (tmp_path / 'input' / 'all_options.toml').write_text(
        (DATA / 'all_options.toml').read_text()
    )
    suite = load_suite('default')
    cfg = build_run_config(tmp_path, suite, '20260925T031000Z-x-default-a1b2')
    assert cfg['params']['stop']['iters']['maximum'] == 16  # was 9000 in the file
    assert cfg['params']['out']['path'] == '20260925T031000Z-x-default-a1b2'  # was 'auto'
    assert cfg['params']['out']['logging'] == 'INFO'
    with pytest.raises(ValueError, match='all_options.toml not found'):
        build_run_config(tmp_path / 'elsewhere', suite, 'r')
