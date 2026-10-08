"""Run checks, each ``{name, ok, detail}``, and the comparability rule.

The pre-run check (a clean PROTEUS tree) can stop a run before it is timed;
post-run checks only affect comparability.
"""

from __future__ import annotations

from pathlib import Path

from proteus_bench.timing import check_events, read_events


def _check(name: str, ok: bool, detail: str) -> dict:
    return {'name': name, 'ok': ok, 'detail': detail}


def proteus_import_check(origin: str | None, proteus_root: Path) -> dict:
    """``proteus`` imports from the checkout whose git state the record names."""
    if origin and Path(origin).resolve().is_relative_to(proteus_root.resolve()):
        return _check('proteus_import', True, origin)
    found = origin or 'nowhere'
    return _check('proteus_import', False, f'proteus imports from {found}, not {proteus_root}')


def clean_tree_check(proteus_git: dict) -> dict:
    """The PROTEUS checkout has no changes to tracked files."""
    describe = proteus_git.get('describe') or proteus_git['sha']
    if proteus_git['dirty']:
        return _check('clean_tree', False, f'uncommitted changes in PROTEUS ({describe})')
    return _check('clean_tree', True, describe)


def timing_contract_check(path: Path) -> tuple[list[dict], dict]:
    """Events of timing.jsonl and whether they follow the interface rules.

    A file that cannot be parsed fails the check and gives no events, so the run
    still gets a record.
    """
    try:
        events = read_events(path) if path.is_file() else []
    except ValueError as err:
        return [], _check('timing_contract', False, str(err))
    problems = check_events(events)
    if problems:
        more = f' (+{len(problems) - 3} more)' if len(problems) > 3 else ''
        return events, _check('timing_contract', False, '; '.join(problems[:3]) + more)
    return events, _check('timing_contract', True, f'{len(events)} events')


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
