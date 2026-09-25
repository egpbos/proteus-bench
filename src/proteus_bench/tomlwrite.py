"""Write TOML from the values ``tomllib`` returns, so ``tomllib.loads(dumps(x)) == x``.

The standard library reads TOML but cannot write it. This covers every type
``tomllib`` produces: tables, arrays (written inline, including arrays of
tables as arrays of inline tables), strings, integers, floats (with ``inf``
and ``nan``), booleans, and offset or local dates and times.
"""

from __future__ import annotations

import datetime as dt
import json
import re

_BARE_KEY = re.compile(r'[A-Za-z0-9_-]+')


def dumps(table: dict) -> str:
    """Serialise a nested dict of TOML values; raise ``TypeError`` for other types."""
    return ''.join(_table_lines(table, ()))


def _table_lines(table: dict, path: tuple[str, ...]):
    """Key-value lines of one table, then its sub-tables, as header blocks."""
    pairs = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
    subtables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
    # A header is needed for key-values and for an empty table; a table holding
    # only sub-tables is created implicitly by their headers.
    if path and (pairs or not subtables):
        yield f'\n[{".".join(map(_key, path))}]\n'
    for key, value in pairs:
        yield f'{_key(key)} = {_value(value)}\n'
    for key, value in subtables:
        yield from _table_lines(value, (*path, key))


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _string(key)


def _string(text: str) -> str:
    # JSON string escapes are valid TOML basic-string escapes; TOML also
    # forbids a raw DEL character, which JSON leaves unescaped.
    return json.dumps(text, ensure_ascii=False).replace('\x7f', '\\u007f')


def _value(value) -> str:
    """One TOML value in inline form."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float):
        # repr is the shortest round-tripping form ('1e-07', '1e+16', 'inf', 'nan',
        # '0.1'), and every one is a valid TOML float; integral values keep '.0'
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int | dt.date | dt.time):
        # str() of a datetime separates date and time with a space, which TOML allows
        return str(value)
    if isinstance(value, list):
        return '[' + ', '.join(_value(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(f'{_key(k)} = {_value(v)}' for k, v in value.items()) + '}'
    raise TypeError(f'cannot write {type(value).__name__} value {value!r} as TOML')
