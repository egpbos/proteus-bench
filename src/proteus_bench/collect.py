"""Run-record sections from the files a proteus run leaves behind."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

from proteus_bench import settings

FINGERPRINT_KEYS = ('T_magma', 'Phi_global', 'F_atm', 'P_surf')
ROUND = 6  # PROTEUS logs three decimals; rounding sums keeps float noise out of records


def component_row(phase: str, key: tuple, total_s: float, n_calls: int) -> dict:
    component, submodule, backend = key
    return {
        'phase': phase,
        'component': component,
        'submodule': submodule,
        'backend': backend,
        'total_s': round(total_s, ROUND),
        'n_calls': n_calls,
    }


def last_complete_row(helpfile: Path) -> dict[str, str] | None:
    """Column -> value of the last row of runtime_helpfile.csv that has every column.

    None without such a row. A shorter row, as a run killed while writing leaves, is skipped.
    """
    rows = [line.split() for line in helpfile.read_text(errors='replace').splitlines()]
    complete = [row for row in rows[1:] if len(row) == len(rows[0])]
    return dict(zip(rows[0], complete[-1], strict=True)) if complete else None


def fingerprint(helpfile: Path) -> tuple[dict, list[str]]:
    """Physics values of the last complete row of runtime_helpfile.csv, and notes on
    unusable ones. Non-finite values are left out, since JSON has no NaN.
    """
    if not helpfile.is_file():
        return {}, ['runtime_helpfile.csv missing, no physics fingerprint']
    last = last_complete_row(helpfile)
    if last is None:
        return {}, ['runtime_helpfile.csv has no data rows, no physics fingerprint']
    values, notes = {}, []
    for key in FINGERPRINT_KEYS:
        if key in last:
            value = float(last[key])
            if math.isfinite(value):
                values[key] = value
            else:
                notes.append(f'fingerprint {key} is {value}')
    return values, notes


def resolved_settings(init_coupler: Path, run_config: dict) -> tuple[dict, list[str]]:
    """Flattened resolved config, falling back to the input config with a note."""
    if init_coupler.is_file():
        return settings.flatten(tomllib.loads(init_coupler.read_text())), []
    note = 'init_coupler.toml missing, settings taken from config.toml'
    return settings.flatten(run_config), [note]
