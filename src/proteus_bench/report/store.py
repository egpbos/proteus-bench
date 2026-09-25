"""Reading run records and their artifact files from a results store.

Store layout (PLAN.md section 9): ``records/<YYYY>/<run_id>.json`` plus the
sidecar files the records name in ``artifacts``, with paths relative to the
store root.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath

RECORD_SCHEMA = 'proteus-bench/1'
RUN_ID = re.compile(r'^[0-9]{8}T[0-9]{6}Z-[a-z0-9-]+$')  # as in record-v1.schema.json


def load_records(store: Path) -> list[dict]:
    """All records under ``store/records``, oldest first.

    Raises ``ValueError`` naming the file for unreadable JSON, an unknown record
    schema or a run id that is not safe as a file name.
    """
    records = []
    for path in sorted((store / 'records').glob('**/*.json')):
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError as err:
            raise ValueError(f'{path}: not valid JSON ({err.msg})') from err
        if record.get('schema') != RECORD_SCHEMA:
            raise ValueError(
                f'{path}: expected schema {RECORD_SCHEMA!r}, found {record.get("schema")!r}'
            )
        if not RUN_ID.match(str(record.get('run_id'))):
            raise ValueError(
                f'{path}: run_id {record.get("run_id")!r} does not match {RUN_ID.pattern}'
            )
        records.append(record)
    return sorted(records, key=lambda r: (r['trigger']['started_at'], r['run_id']))


def artifact_source(store: Path, relpath: str) -> Path:
    """The store file an artifact path names; refuses paths that leave the store."""
    parts = PurePosixPath(relpath).parts
    if not parts or PurePosixPath(relpath).is_absolute() or '..' in parts:
        raise ValueError(
            f'artifact path {relpath!r} must be relative and stay inside the store'
        )
    return store.joinpath(*parts)


def copy_artifacts(record: dict, store: Path, files_dir: Path) -> dict[str, str | None]:
    """Copy a record's artifacts to ``files_dir/<path>``.

    Returns name -> site-relative path, or ``None`` for a file the record names
    but the store does not have, so the page can say so instead of linking to
    nothing.
    """
    copied: dict[str, str | None] = {}
    for name, relpath in record.get('artifacts', {}).items():
        source = artifact_source(store, relpath)
        if not source.is_file():
            copied[name] = None
            continue
        target = files_dir.joinpath(*PurePosixPath(relpath).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied[name] = f'{files_dir.name}/{relpath}'
    return copied
