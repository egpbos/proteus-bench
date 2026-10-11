"""Run checks, each ``{name, ok, detail}``, and the comparability rule.

The pre-run check (a clean PROTEUS tree) can stop a run before it is timed;
post-run checks only affect comparability.
"""

from __future__ import annotations


def _check(name: str, ok: bool, detail: str) -> dict:
    return {'name': name, 'ok': ok, 'detail': detail}


def clean_tree_check(proteus_git: dict) -> dict:
    """The PROTEUS checkout has no changes to tracked files."""
    describe = proteus_git.get('describe') or proteus_git['sha']
    if proteus_git['dirty']:
        return _check('clean_tree', False, f'uncommitted changes in PROTEUS ({describe})')
    return _check('clean_tree', True, describe)


def expected_backends_check(backends: dict, expected: dict) -> dict:
    """Backend events match the suite's ``"<submodule>.<key>" = value`` expectations."""
    wrong = []
    for dotted, want in expected.items():
        submodule, key = dotted.split('.', 1)
        got = backends.get(submodule, {}).get(key)
        if got != want:
            wrong.append(f'{dotted}: expected {want}, got {got if got is not None else "none"}')
    if wrong:
        return _check('expected_backends', False, '; '.join(wrong))
    return _check(
        'expected_backends', True, ', '.join(f'{k}={v}' for k, v in expected.items()) or 'none'
    )


def comparability(checks: list[dict], status: str, profiler: str, notes: list[str]) -> dict:
    """``{ok, reasons}``: whether the run may enter baselines, and why not."""
    reasons = [f'check {c["name"]} failed: {c["detail"]}' for c in checks if not c['ok']]
    if status != 'ok':
        reasons.append(f'outcome is {status}')
    if profiler != 'none':
        reasons.append(f'profiled with {profiler}, which slows the run')
    reasons += notes
    return {'ok': not reasons, 'reasons': reasons}
