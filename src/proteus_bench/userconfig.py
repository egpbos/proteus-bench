"""Per-user settings: ``$XDG_CONFIG_HOME/proteus-bench/config.toml``.

The file is optional. ``load`` overlays it on the defaults and rejects unknown
tables, unknown keys and values of the wrong type, so a typo never falls back
silently to a default. ``render`` produces the file ``proteus-bench init`` writes:
every key with a comment, chosen values active and the rest commented out.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path


def _xdg_dir(var: str, fallback: str) -> Path:
    # The XDG spec says relative values are invalid and must be ignored
    value = os.environ.get(var, '').strip()
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def config_path() -> Path:
    """Where the user config lives (it need not exist)."""
    return _xdg_dir('XDG_CONFIG_HOME', '.config') / 'proteus-bench' / 'config.toml'


def _fields() -> dict[str, dict[str, tuple[object, str]]]:
    """Table -> key -> (default, comment). Defaults depend on the environment."""
    cache = _xdg_dir('XDG_CACHE_HOME', '.cache') / 'proteus-bench' / 'store'
    return {
        'machine': {
            'label': ('', 'Name of this machine in run records, e.g. "habrok-vink15"'),
            'class': ('', 'Comparability group; runs are compared only within one class'),
        },
        'store': {
            'remote': ('', 'Git URL of the proteus-bench repository that holds the results'),
            'branch': ('results', 'Branch of that repository used as the results store'),
            'cache_dir': (str(cache), 'Local checkout of the store branch (a cache)'),
        },
        'runs': {
            'dir': ('bench-runs', 'Where run directories are written and ingested'),
        },
        'slurm': {
            'env_activation': ('', 'Shell lines that activate the PROTEUS environment'),
            'sbatch_options': ([], 'Default sbatch options, e.g. ["--partition=regular"]'),
        },
    }


def defaults() -> dict:
    """The settings used when the config file sets nothing."""
    return {t: {k: d for k, (d, _) in keys.items()} for t, keys in _fields().items()}


def load(path: Path | None = None) -> dict:
    """Defaults overlaid with the config file at ``path`` (default ``config_path()``).

    Raises ValueError naming the file for invalid TOML, an unknown table or key,
    or a value whose type differs from the default's.
    """
    path = path or config_path()
    merged = defaults()
    if not path.exists():
        return merged
    try:
        user = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as err:
        raise ValueError(f'{path}: not valid TOML ({err})') from err
    for table, values in user.items():
        if table not in merged:
            raise ValueError(f'{path}: unknown table [{table}]; known: {", ".join(merged)}')
        if not isinstance(values, dict):
            raise ValueError(f'{path}: {table} must be a table, found {values!r}')
        for key, value in values.items():
            _check_value(path, table, key, value, merged[table])
            merged[table][key] = value
    return merged


def _check_value(path: Path, table: str, key: str, value: object, known: dict) -> None:
    if key not in known:
        raise ValueError(f'{path}: unknown key {table}.{key}; known: {", ".join(known)}')
    expected = type(known[key])
    if not isinstance(value, expected) or (
        isinstance(value, list) and not all(isinstance(v, str) for v in value)
    ):
        what = 'a list of strings' if expected is list else 'a string'
        raise ValueError(f'{path}: {table}.{key} must be {what}, found {value!r}')


def render(values: dict[str, dict]) -> str:
    """Config file text: keys in ``values`` active, all others as commented defaults."""
    lines = ['# proteus-bench user configuration. Uncomment a line to change a default.']
    for table, keys in _fields().items():
        lines += ['', f'[{table}]']
        for key, (default, comment) in keys.items():
            lines.append(f'# {comment}')
            if key in values.get(table, {}):
                lines.append(f'{key} = {_toml_value(values[table][key])}')
            else:
                lines.append(f'# {key} = {_toml_value(default)}')
    return '\n'.join(lines) + '\n'


def _toml_value(value: str | list[str]) -> str:
    # JSON's escapes are TOML basic-string escapes; ensure_ascii=False avoids \u
    # surrogate pairs, which TOML rejects
    if isinstance(value, list):
        return '[' + ', '.join(_toml_value(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)
