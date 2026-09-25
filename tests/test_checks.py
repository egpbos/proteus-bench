"""Tests for proteus_bench.checks: environment checks and the comparability rule.

Contract clauses: CVODE is checked only when the config (with PROTEUS
defaults for absent keys) selects Aragog with CVODE; any thread variable other
than unset or 1 fails; a dirty tree fails; timing problems and backend
mismatches fail with the offending values; comparability lists one reason per
failed check, a non-ok outcome, a profiler and each collection note, and is ok
only with no reasons.
"""

from __future__ import annotations

import sys

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


@pytest.mark.smoke
def test_cvode_check_runs_the_import_in_the_target_interpreter(cvode_stub):
    """The check passes with an importable stub and reports the import error otherwise."""
    cvode_stub(False)
    missing = checks.cvode_check(sys.executable, {})
    assert missing['ok'] is False
    assert missing['detail'].endswith('ImportError: stub: SUNDIALS library not found')
    cvode_stub(True)
    assert checks.cvode_check(sys.executable, {})['ok'] is True
    skipped = checks.cvode_check(
        '/nonexistent/python', {'interior_energetics': {'module': 'dummy'}}
    )
    assert skipped == {
        'name': 'cvode_importable',
        'ok': True,
        'detail': 'not required by this config',
    }


def test_threads_check_accepts_only_unset_or_one():
    """OMP=1 and absent pass; JULIA_NUM_THREADS=auto or OMP=4 fail and are named."""
    assert checks.threads_check({'OMP_NUM_THREADS': '1'})['ok'] is True
    assert checks.threads_check({})['ok'] is True
    bad = checks.threads_check({'JULIA_NUM_THREADS': 'auto', 'OMP_NUM_THREADS': '4'})
    assert bad['ok'] is False
    assert 'OMP_NUM_THREADS=4' in bad['detail'] and 'JULIA_NUM_THREADS=auto' in bad['detail']


def test_clean_tree_check():
    """A dirty checkout fails; the describe string (or the sha) is the detail."""
    clean = checks.clean_tree_check(
        {'sha': 'abd4ca53', 'dirty': False, 'describe': 'v1-2-gabd4'}
    )
    assert clean == {'name': 'clean_tree', 'ok': True, 'detail': 'v1-2-gabd4'}
    dirty = checks.clean_tree_check({'sha': 'abd4ca53', 'dirty': True})
    assert dirty['ok'] is False and 'abd4ca53' in dirty['detail']


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
    assert ok['ok'] is True and ok['detail'] == 'aragog.solver=cvode'
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
    failed = [{'name': 'threads', 'ok': False, 'detail': 'x'}]
    result = checks.comparability(failed, 'timeout', 'py-spy', ['n'])
    assert result['ok'] is False
    assert len(result['reasons']) == 4
    assert result['reasons'][0].startswith('check threads') and result['reasons'][-1] == 'n'
