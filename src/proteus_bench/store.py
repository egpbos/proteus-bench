"""Results store: artifact contract, run-directory checks and staging runs.

Implements ``docs/interface.md``, section "Results store". Run directories are
untrusted input, since the store feeds a public site.
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

# Artifact key -> run-directory file; a stored record maps the same keys to store paths
ARTIFACTS = {
    'spans': 'timing.jsonl',
    'settings': 'init_coupler.toml',
    'config': 'config.toml',
    'log': 'log.txt',
    'profile': 'profile/stacks.folded.gz',
    'flame': 'profile/flame.html',
}
_TOP_LEVEL = {
    'timing.jsonl': 'spans/{year}/{run_id}.timing.jsonl.gz',
    'init_coupler.toml': 'settings/{hex}.toml',
    'config.toml': 'configs/{year}/{run_id}.toml',
    'log.txt': 'logs/{year}/{run_id}.log.gz',
}
PROFILE_DIR = 'profiles/{year}/{run_id}/'
REQUIRED = ('log', 'config')
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
    return f'records/{run_id[:4]}/{run_id}.json'


def check_run(run_dir: Path) -> tuple[dict | None, list[str]]:
    """(record or None, problems); a run with any problem is not stored.

    The record must pass its JSON Schema, so jsonschema is required.
    """
    record_file = run_dir / 'record.json'
    if not record_file.is_file():
        return None, [f'{run_dir}: no record.json, so not a run directory']
    try:
        record = json.loads(record_file.read_text(encoding='utf-8'))
    except json.JSONDecodeError as err:
        return None, [f'{record_file}: not valid JSON ({err.msg}, line {err.lineno})']
    if not schema.available():
        return record, [
            f'{record_file}: cannot check the record against its schema; '
            'install proteus-bench[publish]'
        ]
    problems = schema.shape_problems('record', record) or _file_name_problems(record)
    if problems:
        return record, [f'{record_file}: {p}' for p in problems]
    problems += [f'{link}: symbolic links are not published' for link in _symlinks(run_dir)]
    required = REQUIRED
    if record['outcome']['status'] == 'ok':
        required += REQUIRED_IF_OK
        if record['timings'].get('source') == 'log':
            required = tuple(key for key in required if key != 'spans')
    problems += [
        f'{run_dir}: missing {ARTIFACTS[key]}'
        for key in required
        if not (run_dir / ARTIFACTS[key]).is_file()
    ]
    problems += _settings_problems(run_dir, record)
    problems += [f'{record_file}: {p}' for p in _artifact_problems(run_dir, record)]
    return record, problems


def _symlinks(run_dir: Path) -> list[Path]:
    # os.walk does not descend into linked directories, so a link to / stays cheap
    return [
        Path(root, name)
        for root, dirs, files in os.walk(run_dir)
        for name in dirs + files
        if Path(root, name).is_symlink()
    ]


def _file_name_problems(record: dict) -> list[str]:
    """Values that become file names must match in full: the schema's '$' accepts a final newline."""
    fields = (
        ('run_id', record['run_id'], RUN_ID),
        ('settings_hash', record['benchmark']['settings_hash'], SETTINGS_HASH),
    )
    return [
        f'{name} {value!r} does not match {pattern.pattern}'
        for name, value, pattern in fields
        if not pattern.fullmatch(value)
    ]


def _settings_problems(run_dir: Path, record: dict) -> list[str]:
    # The settings file is stored under the record's hash, so they must agree
    path = run_dir / ARTIFACTS['settings']
    if not path.is_file():
        return []
    try:
        actual = settings_hash(flatten(tomllib.loads(path.read_text(encoding='utf-8'))))
    except tomllib.TOMLDecodeError as err:
        return [f'{path}: not valid TOML ({err})']
    claimed = record['benchmark']['settings_hash']
    if actual != claimed:
        return [f'{path}: settings hash is {actual}, but record.json says {claimed}']
    return []


def _artifact_problems(run_dir: Path, record: dict) -> list[str]:
    found = []
    for key, src in record.get('artifacts', {}).items():
        if ARTIFACTS.get(key) != src:
            expected = ARTIFACTS.get(key, 'no such key')
            found.append(f'artifact {key!r} is {src!r}; ARTIFACTS gives {expected!r}')
        elif not (run_dir / src).is_file():
            found.append(f'artifact {key!r} names {src}, which is missing')
    return found


def stored_artifacts(run_dir: Path, record: dict) -> dict[str, str]:
    """Artifact key -> store path for every ``ARTIFACTS`` file the run has."""
    return {
        key: store_path(src, record)
        for key, src in ARTIFACTS.items()
        if (run_dir / src).is_file()
    }


def stage_run(run_dir: Path, record: dict, tree: Path) -> dict:
    """Copy one checked run into the store tree at ``tree``; return the stored record."""
    for src in (p for p in ARTIFACTS.values() if (run_dir / p).is_file()):
        target = tree / store_path(src, record)
        # Runs with equal hashes differ only in per-run keys: keep the first file
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
    target.write_text(json.dumps(stored, indent=2) + '\n')
    return stored


def read_records(tree: Path) -> list[dict]:
    """All run records in a store tree, in path order."""
    if not tree.is_dir():
        raise ValueError(f'{tree} is not a directory, so not a store checkout')
    paths = sorted(tree.glob('records/*/*.json'))
    return [json.loads(p.read_text(encoding='utf-8')) for p in paths]
