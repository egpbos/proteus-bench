"""Reading run records and locating their artifact files in a results store.

Store layout (PLAN.md section 9): ``records/<YYYY>/<run_id>.json`` plus the
sidecar files the records name in ``artifacts``, with paths relative to the
store root. Anyone who can push to the results branch is a publisher and the
site is public, so record contents and store files are untrusted: every
problem stops the build with the file named, and no store file is copied into
the site.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import quote

RECORD_SCHEMA = 'proteus-bench/1'
# As in record-v1.schema.json. [0-9], not \d: \d also matches non-ASCII digits, which
# would let unexpected characters into file names.
RUN_ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]+')  # NOSONAR
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')  # GitHub owner/name
NUMBER = (int, float)

# Fields the pages read without a fallback, with their JSON types
REQUIRED = {
    'run_id': str,
    'harness.version': str,
    'benchmark.name': str,
    'benchmark.lineage': str,
    'benchmark.settings_hash': str,
    'benchmark.settings': dict,
    'benchmark.overrides': dict,
    'trigger.adapter': str,
    'trigger.started_at': str,
    'code.proteus.sha': str,
    'code.proteus.dirty': bool,
    'code.modules': dict,
    'machine.label': str,
    'machine.class': str,
    'env.threads': dict,
    'env.knobs': dict,
    'checks': list,
    'backends': dict,
    'outcome.status': str,
    'comparability.ok': bool,
    'comparability.reasons': list,
    'timings.wall_s': NUMBER,
    'timings.phases': dict,
    'timings.components': list,
    'timings.per_iter': list,
}


class Artifact(NamedTuple):
    """One artifact a record names: store path, whether the store has it, its link."""

    name: str
    path: str
    present: bool
    url: str | None


def field_problems(record: dict) -> list[str]:
    """Required fields that are missing or have the wrong JSON type."""
    problems = []
    for dotted, kind in REQUIRED.items():
        value = record
        for part in dotted.split('.'):
            value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            problems.append(f'missing {dotted}')
        elif not isinstance(value, kind) or (kind is NUMBER and isinstance(value, bool)):
            problems.append(f'{dotted} has type {type(value).__name__}')
    return problems


def _read_record(path: Path, store: Path) -> dict:
    inside_store(path, store)
    try:
        record = json.loads(path.read_text())
    except json.JSONDecodeError as err:
        raise ValueError(f'{path}: not valid JSON ({err.msg})') from err
    if not isinstance(record, dict) or record.get('schema') != RECORD_SCHEMA:
        found = record.get('schema') if isinstance(record, dict) else type(record).__name__
        raise ValueError(f'{path}: expected schema {RECORD_SCHEMA!r}, found {found!r}')
    problems = field_problems(record)
    if problems:
        raise ValueError(f'{path}: {"; ".join(problems)}')
    if not RUN_ID.fullmatch(record['run_id']):
        raise ValueError(f'{path}: run_id {record["run_id"]!r} does not match {RUN_ID.pattern}')
    return record


def load_records(store: Path) -> list[dict]:
    """All records under ``store/records``, oldest first.

    Raises ``ValueError`` naming the file for a symlink or a file outside the
    store, unreadable JSON, an unknown record schema, a missing or mistyped
    required field, a run id that is not safe as a file name, or a run id that
    two files share.
    """
    records, seen = [], {}
    for path in sorted((store / 'records').glob('**/*.json')):
        record = _read_record(path, store)
        if record['run_id'] in seen:
            raise ValueError(
                f'{path}: run_id {record["run_id"]!r} also in {seen[record["run_id"]]}'
            )
        seen[record['run_id']] = path
        records.append(record)
    return sorted(records, key=lambda r: (r['trigger']['started_at'], r['run_id']))


def inside_store(path: Path, store: Path) -> Path:
    """``path`` itself, after refusing a symlink or anything resolving outside the store."""
    if path.is_symlink():
        raise ValueError(f'{path}: symlinks are not allowed in the store')
    if not path.resolve().is_relative_to(store.resolve()):
        raise ValueError(f'{path}: resolves outside the store {store}')
    return path


def artifact_source(store: Path, relpath: str) -> Path:
    """The store file an artifact path names; refuses paths that leave the store."""
    parts = PurePosixPath(relpath).parts if isinstance(relpath, str) else ()
    if not parts or PurePosixPath(relpath).is_absolute() or '..' in parts:
        raise ValueError(
            f'artifact path {relpath!r} must be relative and stay inside the store'
        )
    return inside_store(store.joinpath(*parts), store)


def results_url(repo: str, relpath: str) -> str:
    """Link to a store file on the ``results`` branch of ``owner/name`` on GitHub."""
    if not REPO.fullmatch(repo):
        raise ValueError(f'repository {repo!r} is not of the form owner/name')
    return f'https://github.com/{repo}/blob/results/{quote(relpath)}'


def artifacts_of(record: dict, store: Path, repo: str | None) -> list[Artifact]:
    """The record's artifacts, sorted by name; ``url`` is ``None`` without a repository."""
    artifacts = record.get('artifacts', {})
    if not isinstance(artifacts, dict):
        raise ValueError(f'run {record["run_id"]}: artifacts must be an object')
    found = []
    for name, relpath in sorted(artifacts.items()):
        try:
            present = artifact_source(store, relpath).is_file()
        except ValueError as err:
            raise ValueError(f'run {record["run_id"]}: {err}') from err
        url = results_url(repo, relpath) if repo and present else None
        found.append(Artifact(name, relpath, present, url))
    return found
