"""Results-store layout, run-directory checks and staging runs into a store tree.

The store is a git branch (``results`` by default) with one file per run, so
concurrent publishers never write the same path::

    records/<YYYY>/<run_id>.json            run record, artifacts rewritten to store paths
    spans/<YYYY>/<run_id>.timing.jsonl.gz   raw timing.jsonl
    logs/<YYYY>/<run_id>.log.gz             combined proteus output
    settings/<hex>.toml                     init_coupler.toml, one per settings hash
    profiles/<YYYY>/<run_id>/               the run's profile/ directory, if any

``<YYYY>`` is the year in the run id (the UTC start time). Nothing here runs git;
see ``proteus_bench.publishing``.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import tomllib
from pathlib import Path

from proteus_bench import schema
from proteus_bench.settings import flatten, settings_hash

# Same patterns as record-v1.schema.json; checked here too because they become paths
RUN_ID = re.compile(r'^[0-9]{8}T[0-9]{6}Z-[a-z0-9-]+$')
SETTINGS_HASH = re.compile(r'^sha256:[0-9a-f]{64}$')

# Artifact key -> run-dir path, added to every stored record whose run has the file
DEFAULT_ARTIFACTS = {
    'spans': 'timing.jsonl',
    'log': 'log.txt',
    'settings': 'init_coupler.toml',
    'profile': 'profile',
}

README = """\
# proteus-bench results store

This branch holds the raw results of proteus-bench runs. Every run adds new files
and never changes existing ones, so publishers on different machines do not
conflict. The dashboard and all statistics are computed from these files when the
site is built; no index or summary is committed here.

| Path | Content |
|---|---|
| `records/<YYYY>/<run_id>.json` | Run record (schema `proteus-bench/1`). Its `artifacts` paths are relative to this directory. |
| `spans/<YYYY>/<run_id>.timing.jsonl.gz` | Raw `timing.jsonl` written by PROTEUS. |
| `logs/<YYYY>/<run_id>.log.gz` | Combined output of the `proteus` process. |
| `settings/<hex>.toml` | The resolved PROTEUS config (`init_coupler.toml`) for settings hash `sha256:<hex>`, kept once per hash so old settings can be replayed. |
| `profiles/<YYYY>/<run_id>/` | Profile of a profiling run: `flame.html`, `stacks.folded.gz`, raw profiler output. |

`<YYYY>` is the year of the run's UTC start time, which is also the start of its
run id. Add runs with `proteus-bench publish RUN_DIR`.
"""


def record_path(run_id: str) -> str:
    """Store path of a run's record."""
    return f'records/{run_id[:4]}/{run_id}.json'


def settings_path(hash_: str) -> str:
    """Store path of the settings file for a ``sha256:<hex>`` settings hash."""
    return f'settings/{hash_.removeprefix("sha256:")}.toml'


def check_run(run_dir: Path) -> tuple[dict | None, list[str], bool]:
    """Load and check a run directory before publishing.

    Returns (record or None, problems, whether the record shape was checked). The
    shape needs jsonschema; without it the fields used in store paths are still
    checked. ``timing.jsonl`` and ``init_coupler.toml`` are required only when
    the run finished ``ok``: a run that failed during setup has neither.
    """
    record_file = run_dir / 'record.json'
    if not record_file.is_file():
        return None, [f'{run_dir}: no record.json, so not a run directory'], True
    try:
        record = json.loads(record_file.read_text())
    except json.JSONDecodeError as err:
        return None, [f'{record_file}: not valid JSON ({err.msg}, line {err.lineno})'], True
    shape = schema.shape_problems('record', record)
    problems = [f'{record_file}: {p}' for p in shape or []]
    if shape is None:
        problems += [f'{record_file}: {p}' for p in _path_field_problems(record)]
    if problems:
        return record, problems, shape is not None
    required = ['log.txt']
    if record['outcome']['status'] == 'ok':
        required += ['timing.jsonl', 'init_coupler.toml']
    problems += [
        f'{run_dir}: missing {name}' for name in required if not (run_dir / name).is_file()
    ]
    problems += _settings_problems(run_dir, record)
    try:
        store_artifacts(run_dir, record)
    except ValueError as err:
        problems.append(str(err))
    return record, problems, shape is not None


def _path_field_problems(record: dict) -> list[str]:
    run_id = record.get('run_id')
    hash_ = record.get('benchmark', {}).get('settings_hash')
    found = []
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        found.append(f'run_id {run_id!r} does not match {RUN_ID.pattern}')
    if not isinstance(hash_, str) or not SETTINGS_HASH.match(hash_):
        found.append(
            f'benchmark.settings_hash {hash_!r} does not match {SETTINGS_HASH.pattern}'
        )
    if not isinstance(record.get('outcome', {}).get('status'), str):
        found.append('outcome.status is missing')
    return found


def _settings_problems(run_dir: Path, record: dict) -> list[str]:
    # The settings file is stored under the record's hash, so they must agree
    path = run_dir / 'init_coupler.toml'
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


def _sources(run_dir: Path, record: dict) -> dict[str, str]:
    """Run-dir relative path -> store path, for every file of the run the store keeps."""
    run_id = record['run_id']
    year = run_id[:4]
    sources = {
        'timing.jsonl': f'spans/{year}/{run_id}.timing.jsonl.gz',
        'log.txt': f'logs/{year}/{run_id}.log.gz',
        'init_coupler.toml': settings_path(record['benchmark']['settings_hash']),
    }
    sources = {src: dst for src, dst in sources.items() if (run_dir / src).is_file()}
    profile = run_dir / 'profile'
    if profile.is_dir():
        sources['profile'] = f'profiles/{year}/{run_id}/'
        for path in sorted(p for p in profile.rglob('*') if p.is_file()):
            rel = path.relative_to(profile).as_posix()
            sources[f'profile/{rel}'] = f'profiles/{year}/{run_id}/{rel}'
    return sources


def store_artifacts(run_dir: Path, record: dict) -> dict[str, str]:
    """The record's ``artifacts`` rewritten to store paths, plus spans, log and settings.

    Raises ValueError when an artifact names a file the store does not keep, so
    nothing a record points to is dropped silently.
    """
    sources = _sources(run_dir, record)
    artifacts = {}
    for key, src in record.get('artifacts', {}).items():
        norm = src.rstrip('/')
        if norm not in sources:
            raise ValueError(
                f'{run_dir}: artifact {key!r} is {src!r}, which is not a file the store '
                f'keeps (one of: {", ".join(sorted(sources))})'
            )
        artifacts[key] = sources[norm]
    for key, src in DEFAULT_ARTIFACTS.items():
        if src in sources:
            artifacts.setdefault(key, sources[src])
    return artifacts


def stage_run(run_dir: Path, record: dict, tree: Path) -> dict:
    """Copy one checked run into the store tree at ``tree``; return the stored record.

    The settings file is written only if no file for that hash exists yet: runs
    with equal hashes differ only in per-run keys such as the output path.
    """
    artifacts = store_artifacts(run_dir, record)
    for src, dst in _sources(run_dir, record).items():
        target = tree / dst
        if src == 'profile' or (src == 'init_coupler.toml' and target.exists()):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if dst.endswith('.gz') and not src.endswith('.gz'):
            # mtime=0 keeps the compressed bytes a function of the content only
            target.write_bytes(gzip.compress((run_dir / src).read_bytes(), mtime=0))
        else:
            shutil.copyfile(run_dir / src, target)
    stored = {**record, 'artifacts': artifacts}
    target = tree / record_path(record['run_id'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + '\n')
    return stored


def read_records(tree: Path) -> list[dict]:
    """All run records in a store tree, in path order."""
    return [json.loads(p.read_text()) for p in sorted(tree.glob('records/*/*.json'))]
