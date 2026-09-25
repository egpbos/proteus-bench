"""Results store: artifact contract, run-directory checks and staging runs.

The store layout and the artifact contract are specified in ``docs/interface.md``,
section "Results store". ``ARTIFACTS`` and ``store_path`` implement it. Run
directories are treated as untrusted input, since the store feeds a public
site. Nothing here runs git; see ``proteus_bench.publishing``.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import tomllib
from pathlib import Path

from proteus_bench import schema
from proteus_bench.settings import flatten, settings_hash

# Artifact key -> file in the run directory. The runner and the profiler write
# these names; the stored record's ``artifacts`` maps the same keys to store paths.
ARTIFACTS = {
    'spans': 'timing.jsonl',
    'settings': 'init_coupler.toml',
    'config': 'config.toml',
    'log': 'log.txt',
    'profile': 'profile/stacks.folded.gz',
    'flame': 'profile/flame.html',
}
# Top-level run-dir file -> store path template; files under profile/ go to PROFILE_DIR
_TOP_LEVEL = {
    'timing.jsonl': 'spans/{year}/{run_id}.timing.jsonl.gz',
    'init_coupler.toml': 'settings/{hex}.toml',
    'config.toml': 'configs/{year}/{run_id}.toml',
    'log.txt': 'logs/{year}/{run_id}.log.gz',
}
PROFILE_DIR = 'profiles/{year}/{run_id}/'
REQUIRED = ('log', 'config')  # every run directory has these
REQUIRED_IF_OK = ('spans', 'settings')  # a run that failed in setup may lack these

_RECORD_SCHEMA = schema.load('record')['properties']
RUN_ID = re.compile(_RECORD_SCHEMA['run_id']['pattern'])
SETTINGS_HASH = re.compile(
    _RECORD_SCHEMA['benchmark']['properties']['settings_hash']['pattern']
)

README = """\
# proteus-bench results store

Raw results of proteus-bench runs: one record per run plus its spans, log,
configs and profile. Files are only ever added, never changed. The layout is
specified in `docs/interface.md` ("Results store") on the main branch of this
repository. Add runs with `proteus-bench publish RUN_DIR`.
"""


def store_path(run_dir_path: str, record: dict) -> str:
    """Store path of a run-dir file (``ARTIFACTS`` values or files under ``profile/``)."""
    run_id = record['run_id']
    fields = {
        'year': run_id[:4],
        'run_id': run_id,
        'hex': record['benchmark']['settings_hash'].removeprefix('sha256:'),
    }
    if run_dir_path.startswith('profile/'):
        return PROFILE_DIR.format(**fields) + run_dir_path.removeprefix('profile/')
    return _TOP_LEVEL[run_dir_path].format(**fields)


def record_path(run_id: str) -> str:
    """Store path of a run's record."""
    return f'records/{run_id[:4]}/{run_id}.json'


def check_run(run_dir: Path) -> tuple[dict | None, list[str], bool]:
    """Load and check a run directory before publishing.

    Returns (record or None, problems, whether the record shape was checked).
    The shape needs jsonschema; the fields that become paths or decide which
    files are required are checked either way. Symbolic links are refused.
    """
    record_file = run_dir / 'record.json'
    if not record_file.is_file():
        return None, [f'{run_dir}: no record.json, so not a run directory'], True
    try:
        record = json.loads(record_file.read_text())
    except json.JSONDecodeError as err:
        return None, [f'{record_file}: not valid JSON ({err.msg}, line {err.lineno})'], True
    if not isinstance(record, dict):
        return None, [f'{record_file}: a run record must be a JSON object'], True
    shape = schema.shape_problems('record', record)
    found = (shape or []) + _key_field_problems(record)
    problems = [f'{record_file}: {p}' for p in found]
    if problems:
        return record, problems, shape is not None
    problems += [f'{link}: symbolic links are not published' for link in _symlinks(run_dir)]
    ok = record['outcome']['status'] == 'ok'
    required = REQUIRED + (REQUIRED_IF_OK if ok else ())
    problems += [
        f'{run_dir}: missing {ARTIFACTS[key]}'
        for key in required
        if not (run_dir / ARTIFACTS[key]).is_file()
    ]
    problems += _settings_problems(run_dir, record)
    problems += [f'{record_file}: {p}' for p in _artifact_problems(run_dir, record)]
    return record, problems, shape is not None


def _symlinks(run_dir: Path) -> list[Path]:
    # os.walk does not descend into linked directories, so a link to / stays cheap
    found = [run_dir] if run_dir.is_symlink() else []
    for root, dirs, files in os.walk(run_dir):
        found += [Path(root, n) for n in dirs + files if Path(root, n).is_symlink()]
    return found


def _key_field_problems(record: dict) -> list[str]:
    """Checks that must hold with or without jsonschema: these values become paths."""
    containers = {k: record.get(k) for k in ('benchmark', 'outcome')}
    containers['artifacts'] = record.get('artifacts', {})  # optional
    wrong = [f'{k} must be an object' for k, v in containers.items() if not isinstance(v, dict)]
    if wrong:
        return wrong
    found = []
    # fullmatch: the schema's '$' (and jsonschema) accept a trailing newline
    run_id = record.get('run_id')
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        found.append(f'run_id {run_id!r} does not match {RUN_ID.pattern}')
    hash_ = record['benchmark'].get('settings_hash')
    if not isinstance(hash_, str) or not SETTINGS_HASH.fullmatch(hash_):
        found.append(f'settings_hash {hash_!r} does not match {SETTINGS_HASH.pattern}')
    if not isinstance(record['outcome'].get('status'), str):
        found.append('outcome.status is missing')
    return found


def _settings_problems(run_dir: Path, record: dict) -> list[str]:
    # The settings file is stored under the record's hash, so they must agree
    path = run_dir / ARTIFACTS['settings']
    if not path.is_file():
        return []
    try:
        actual = settings_hash(flatten(tomllib.loads(path.read_text())))
    except tomllib.TOMLDecodeError as err:
        return [f'{path}: not valid TOML ({err})']
    claimed = record['benchmark']['settings_hash']
    if actual != claimed:
        return [f'{path}: settings hash is {actual}, but record.json says {claimed}']
    return []


def _artifact_problems(run_dir: Path, record: dict) -> list[str]:
    """The record's artifacts must be entries of ARTIFACTS whose file exists."""
    found = []
    for key, src in record.get('artifacts', {}).items():
        if ARTIFACTS.get(key) != src:
            expected = ARTIFACTS.get(key, 'no such key')
            found.append(f'artifact {key!r} is {src!r}; ARTIFACTS gives {expected!r}')
        elif not (run_dir / src).is_file():
            found.append(f'artifact {key!r} names {src}, which is missing')
    return found


def _files(run_dir: Path) -> list[str]:
    """Run-dir relative paths of every file the store keeps for this run."""
    top = [name for name in _TOP_LEVEL if (run_dir / name).is_file()]
    profile = sorted(p for p in (run_dir / 'profile').rglob('*') if p.is_file())
    return top + [p.relative_to(run_dir).as_posix() for p in profile]


def stored_artifacts(run_dir: Path, record: dict) -> dict[str, str]:
    """Artifact key -> store path, for every ``ARTIFACTS`` file the run has."""
    return {
        key: store_path(src, record)
        for key, src in ARTIFACTS.items()
        if (run_dir / src).is_file()
    }


def stage_run(run_dir: Path, record: dict, tree: Path) -> dict:
    """Copy one checked run into the store tree at ``tree``; return the stored record.

    The settings file is written only if none exists for that hash yet: runs
    with equal hashes differ only in per-run keys such as the output path.
    """
    for src in _files(run_dir):
        target = tree / store_path(src, record)
        if src == ARTIFACTS['settings'] and target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == '.gz' and not src.endswith('.gz'):
            # mtime=0 keeps the compressed bytes a function of the content only
            target.write_bytes(gzip.compress((run_dir / src).read_bytes(), mtime=0))
        else:
            shutil.copyfile(run_dir / src, target)
    stored = {**record, 'artifacts': stored_artifacts(run_dir, record)}
    target = tree / record_path(record['run_id'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + '\n')
    return stored


def read_records(tree: Path) -> list[dict]:
    """All run records in a store tree, in path order."""
    return [json.loads(p.read_text()) for p in sorted(tree.glob('records/*/*.json'))]
