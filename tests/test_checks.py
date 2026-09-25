"""Tests for proteus_bench.checks: environment checks and the comparability rule.

Contract clauses: CVODE is checked only when the config (with PROTEUS
defaults for absent keys) selects Aragog with CVODE; FWL_DATA, RAD_DIR (with
bin/radlib.a) and FC_DIR must be existing directories; a dirty tree fails; timing problems and backend
mismatches fail with the offending values; comparability lists one reason per
failed check, a non-ok outcome, a profiler and each collection note, and is ok
only with no reasons.
"""

from __future__ import annotations

import subprocess

import pytest

from proteus_bench import checks

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_cvode_needed_follows_proteus_defaults():
    """Absent keys mean Aragog with CVODE, like PROTEUS; other choices do not need it."""
    assert checks.uses_cvode({}) is True
    assert checks.uses_cvode({'interior_energetics': {'module': 'aragog'}}) is True
    assert checks.uses_cvode({'interior_energetics': {'module': 'spider'}}) is False
    radau = {'interior_energetics': {'aragog': {'solver_method': 'radau'}}}
    assert checks.uses_cvode(radau) is False


def test_cvode_check_runs_the_import_in_the_target_interpreter(monkeypatch):
    """The import runs under the given Python; its last error line becomes the detail.

    The real subprocess path is exercised end to end in tests/commands/test_run.py.
    """
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        failed = 'Traceback\nImportError: stub: SUNDIALS library not found\n'
        return subprocess.CompletedProcess(argv, 1, '', failed)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    missing = checks.cvode_check('/envs/proteus/bin/python', {})
    assert calls == [['/envs/proteus/bin/python', '-c', checks.CVODE_IMPORT]]
    assert missing == {
        'name': 'cvode_importable',
        'ok': False,
        'detail': '/envs/proteus/bin/python: ImportError: stub: SUNDIALS library not found',
    }
    monkeypatch.setattr(
        subprocess, 'run', lambda argv, **kw: subprocess.CompletedProcess(argv, 0)
    )
    assert checks.cvode_check('python', {})['ok'] is True
    skipped = checks.cvode_check('python', {'interior_energetics': {'module': 'dummy'}})
    assert skipped == {
        'name': 'cvode_importable',
        'ok': True,
        'detail': 'not required by this config',
    }
    assert len(calls) == 1  # the dummy interior needs no import at all


def _dirs(tmp_path, radlib: bool = True) -> dict:
    for name in ('fwl_data', 'socrates/bin', 'fastchem'):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    if radlib:
        (tmp_path / 'socrates' / 'bin' / 'radlib.a').write_bytes(b'!<arch>\n')
    return {
        'FWL_DATA': str(tmp_path / 'fwl_data'),
        'RAD_DIR': str(tmp_path / 'socrates'),
        'FC_DIR': str(tmp_path / 'fastchem'),
    }


def test_env_dirs_check_passes_with_all_directories(tmp_path):
    """FWL_DATA, RAD_DIR (with bin/radlib.a) and FC_DIR set to real directories pass."""
    env = _dirs(tmp_path)
    result = checks.env_dirs_check(env)
    assert result['ok'] is True
    assert f'RAD_DIR={env["RAD_DIR"]}' in result['detail']


def test_env_dirs_check_names_each_problem(tmp_path):
    """Unset, empty, not a directory and a SOCRATES tree without radlib.a all fail."""
    env = _dirs(tmp_path, radlib=False)
    env['FC_DIR'] = str(tmp_path / 'missing')
    del env['FWL_DATA']
    result = checks.env_dirs_check(env)
    assert result['ok'] is False
    assert result['detail'].startswith('FWL_DATA is not set; ')
    assert f'RAD_DIR={env["RAD_DIR"]} has no bin/radlib.a' in result['detail']
    assert f'FC_DIR={env["FC_DIR"]} is not a directory' in result['detail']
    assert result['detail'].endswith('proteus-bench run')  # says how to fix it
    empty = checks.env_dirs_check({**_dirs(tmp_path), 'RAD_DIR': ''})
    assert 'RAD_DIR is not set' in empty['detail']


def test_clean_tree_check():
    """A dirty checkout fails; the describe string (or the sha) is the detail."""
    clean = checks.clean_tree_check(
        {'sha': 'abd4ca53', 'dirty': False, 'describe': 'v1-2-gabd4'}
    )
    assert clean == {'name': 'clean_tree', 'ok': True, 'detail': 'v1-2-gabd4'}
    dirty = checks.clean_tree_check({'sha': 'abd4ca53', 'dirty': True})
    assert dirty['ok'] is False
    assert 'abd4ca53' in dirty['detail']


def test_timing_contract_check(good_events):
    """Valid events pass; an empty file and a broken tree fail with the problem text."""
    assert checks.timing_contract_check(good_events)['ok'] is True
    assert checks.timing_contract_check([]) == {
        'name': 'timing_contract',
        'ok': False,
        'detail': 'no events',
    }
    broken = [dict(ev) for ev in good_events]
    broken[0]['v'] = 99
    assert 'unsupported version' in checks.timing_contract_check(broken)['detail']


def test_expected_backends_check():
    """A matching event passes; a fallback or a missing event fails with both values."""
    expected = {'aragog.solver': 'cvode'}
    ok = checks.expected_backends_check({'aragog': {'solver': 'cvode', 'calls': {}}}, expected)
    assert ok['ok'] is True
    assert ok['detail'] == 'aragog.solver=cvode'
    radau = checks.expected_backends_check({'aragog': {'solver': 'radau'}}, expected)
    assert radau['ok'] is False
    assert radau['detail'] == 'aragog.solver: expected cvode, got radau'
    missing = checks.expected_backends_check({}, expected)
    assert missing['detail'] == 'aragog.solver: expected cvode, got none'
    assert checks.expected_backends_check({}, {})['ok'] is True  # nothing expected


PASS = [{'name': 'a', 'ok': True, 'detail': ''}]


def test_comparable_only_without_any_reason():
    """All clear gives ok; each rule alone adds exactly its own reason."""
    assert checks.comparability(PASS, 'ok', 'none', []) == {'ok': True, 'reasons': []}
    failed = [*PASS, {'name': 'clean_tree', 'ok': False, 'detail': 'dirty'}]
    cases = {
        'check': (failed, 'ok', 'none', []),
        'outcome': (PASS, 'crashed', 'none', []),
        'profiler': (PASS, 'ok', 'scalene', []),
        'note': (PASS, 'ok', 'none', ['fingerprint F_atm is nan']),
    }
    reasons = {name: checks.comparability(*args)['reasons'] for name, args in cases.items()}
    assert reasons == {
        'check': ['check clean_tree failed: dirty'],
        'outcome': ['outcome is crashed'],
        'profiler': ['profiled with scalene, which slows the run'],
        'note': ['fingerprint F_atm is nan'],
    }
    assert not any(checks.comparability(*args)['ok'] for args in cases.values())


def test_reasons_accumulate():
    """Several problems at once are all reported, in rule order."""
    failed = [{'name': 'clean_tree', 'ok': False, 'detail': 'x'}]
    result = checks.comparability(failed, 'timeout', 'py-spy', ['n'])
    assert result['ok'] is False
    assert len(result['reasons']) == 4
    assert result['reasons'][0].startswith('check clean_tree')
    assert result['reasons'][-1] == 'n'
