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


def fingerprint(helpfile: Path) -> tuple[dict, list[str]]:
    """Final-row physics values from runtime_helpfile.csv, and notes on unusable ones.

    Non-finite values are left out, since JSON has no NaN.
    """
    if not helpfile.is_file():
        return {}, ['runtime_helpfile.csv missing, no physics fingerprint']
    lines = helpfile.read_text().split('\n')
    rows = [line.split() for line in lines if line.strip()]
    if len(rows) < 2:
        return {}, ['runtime_helpfile.csv has no data rows, no physics fingerprint']
    last = dict(zip(rows[0], rows[-1], strict=False))
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
