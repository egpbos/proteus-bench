"""Access to the bundled JSON Schemas and optional shape validation.

Shape validation needs the ``jsonschema`` package, which is optional so the
harness stays free of runtime dependencies. Without it, ``available()`` is
False and ``shape_problems`` returns ``None``.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources

SCHEMAS = {
    'timing': 'timing-v1.schema.json',
    'record': 'record-v1.schema.json',
}


@cache
def load(kind: str) -> dict:
    """Return the parsed JSON Schema for ``kind`` ('timing' or 'record')."""
    name = SCHEMAS[kind]
    return json.loads(resources.files('proteus_bench.schemas').joinpath(name).read_text())


def available() -> bool:
    """Whether shape validation can run (jsonschema is installed)."""
    return _validator('record') is not None


@cache
def _validator(kind: str):
    try:
        import jsonschema
    except ImportError:
        return None
    cls = jsonschema.Draft202012Validator
    return cls(load(kind), format_checker=cls.FORMAT_CHECKER)


def shape_problems(kind: str, instance: dict) -> list[str] | None:
    """Schema violations for one instance, or ``None`` if jsonschema is missing."""
    validator = _validator(kind)
    if validator is None:
        return None
    return [
        f'{"/".join(map(str, err.absolute_path)) or "<root>"}: {err.message}'
        for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    ]
