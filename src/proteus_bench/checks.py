"""Environment checks and the comparability rule for a run.

Each check returns ``{name, ok, detail}``. Pre-run checks (CVODE, data and
library directories, clean tree) can stop a run before it is timed; post-run
checks (timing contract, expected backends) only affect comparability. A run may enter
baselines only when every check passed, the run finished ok, and nothing
else (a profiler, a missing resolved config, a non-finite fingerprint) marks
its timings as unlike the series.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from proteus_bench.timing import check_events

# The import PROTEUS requires before an Aragog CVODE run (aragog.py require_cvode)
CVODE_IMPORT = 'from scikits_odes_sundials.cvode import CVODE, CV_RootFunction, StatusEnum'
# Required directories and the file each must hold, as PROTEUS doctor checks them
# (src/proteus/doctor.py ENVIRONMENT_VARS)
REQUIRED_DIRS = (('FWL_DATA', None), ('RAD_DIR', 'bin/radlib.a'), ('FC_DIR', None))


def _check(name: str, ok: bool, detail: str) -> dict:
    return {'name': name, 'ok': ok, 'detail': detail}


def uses_cvode(config: dict) -> bool:
    """Whether PROTEUS will require CVODE for this config.

    Absent keys take PROTEUS's defaults: interior_energetics.module 'aragog'
    and aragog.solver_method 'cvode' (src/proteus/config/_interior.py).
    """
    energetics = config.get('interior_energetics', {})
    solver = energetics.get('aragog', {}).get('solver_method', 'cvode')
    return energetics.get('module', 'aragog') == 'aragog' and solver == 'cvode'


def cvode_check(python: str, config: dict) -> dict:
    """CVODE imports in the target interpreter, when the config needs it."""
    if not uses_cvode(config):
        return _check('cvode_importable', True, 'not required by this config')
    proc = subprocess.run([python, '-c', CVODE_IMPORT], capture_output=True, text=True)
    if proc.returncode == 0:
        return _check('cvode_importable', True, 'scikits_odes_sundials.cvode imports')
    last = (proc.stderr.strip().splitlines() or ['no error output'])[-1]
    return _check('cvode_importable', False, f'{python}: {last}')


def env_dirs_check(env: dict) -> dict:
    """The data and library directories PROTEUS needs are set and exist in the child env.

    ``pixi run`` does not source shell rc files, so exports made there are missing.
    """
    problems = []
    for var, marker in REQUIRED_DIRS:
        value = env.get(var, '')
        if not value:
            problems.append(f'{var} is not set')
        elif not Path(value).is_dir():
            problems.append(f'{var}={value} is not a directory')
        elif marker and not (Path(value) / marker).is_file():
            problems.append(f'{var}={value} has no {marker}')
    if problems:
        hint = 'pass them explicitly, e.g. RAD_DIR=/path/to/socrates proteus-bench run'
        return _check('env_dirs', False, '; '.join(problems) + f'; {hint}')
    return _check('env_dirs', True, ', '.join(f'{v}={env[v]}' for v, _ in REQUIRED_DIRS))


def clean_tree_check(proteus_git: dict) -> dict:
    """The PROTEUS checkout has no changes to tracked files."""
    describe = proteus_git.get('describe') or proteus_git['sha']
    if proteus_git['dirty']:
        return _check('clean_tree', False, f'uncommitted changes in PROTEUS ({describe})')
    return _check('clean_tree', True, describe)


def timing_contract_check(events: list[dict]) -> dict:
    """timing.jsonl follows the interface rules, so its totals are meaningful."""
    problems = check_events(events)
    if problems:
        more = f' (+{len(problems) - 3} more)' if len(problems) > 3 else ''
        return _check('timing_contract', False, '; '.join(problems[:3]) + more)
    return _check('timing_contract', True, f'{len(events)} events')


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
    """``{ok, reasons}``: whether the run may enter baselines, and why not.

    ``notes`` are reasons found while collecting results.
    """
    reasons = [f'check {c["name"]} failed: {c["detail"]}' for c in checks if not c['ok']]
    if status != 'ok':
        reasons.append(f'outcome is {status}')
    if profiler != 'none':
        reasons.append(f'profiled with {profiler}, which slows the run')
    reasons += notes
    return {'ok': not reasons, 'reasons': reasons}
