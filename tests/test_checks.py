"""Tests for proteus_bench.checks: run checks and the comparability rule.

Contract clauses: a dirty tree fails; backend mismatches fail with the offending
values; comparability lists one reason per failed
check, a non-ok outcome, a profiler and each collection note, and is ok only
with no reasons.
"""

from __future__ import annotations

import pytest

from proteus_bench import checks

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_clean_tree_check():
    """A dirty checkout fails; the describe string (or the sha) is the detail."""
    clean = checks.clean_tree_check(
        {'sha': 'abd4ca53', 'dirty': False, 'describe': 'v1-2-gabd4'}
    )
    assert clean == {'name': 'clean_tree', 'ok': True, 'detail': 'v1-2-gabd4'}
    dirty = checks.clean_tree_check({'sha': 'abd4ca53', 'dirty': True})
    assert dirty['ok'] is False
    assert 'abd4ca53' in dirty['detail']


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
