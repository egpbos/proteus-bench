"""Stand-in for the ``proteus`` CLI, for developing and testing the harness.

Usage mirrors the real command::

    python -m proteus_bench.testing.fake_proteus start -c cfg.toml [--offline]

It writes what the harness reads from a real run, into
``$PROTEUS_OUTPUT_PATH/<params.out.path>`` (default ``./output``):
``init_coupler.toml``, ``runtime_helpfile.csv`` and ``proteus_00.log``. Like
PROTEUS since #983, it prints ``[INIT_TIMING]`` and ``[IT_TIMING]`` lines when
``PROTEUS_TIMING=1`` (the first iteration in the init stage) and a ``[BACKEND]``
line for Aragog. Times come from a
synthetic clock shaped like the 2026 Hábrók scaling runs (a long first structure
solve, periodic in-loop re-solves), so a run finishes at once unless
``[fake] sleep_scale`` asks for real sleeping; the logged seconds are synthetic.

The ``[fake]`` table of the config selects the scenario; see ``FAKE_DEFAULTS``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from pathlib import Path
from typing import TextIO

FAKE_DEFAULTS = {
    'structure_init_s': 1138.0,  # first numpy structure solve [s]
    'equilibrate_iters': 2,
    'equilibrate_structure_s': 420.0,
    'atmos_first_s': 780.0,  # first AGNI solve from a hot surface [s]
    'atmos_s': 15.0,
    'interior_s': 3.5,
    'outgas_s': 1.0,
    'restructure_every': 3,  # in-loop structure re-solve cadence [iterations]
    'restructure_s': 140.0,
    'aragog_solver': 'cvode',
    'fail': 'none',  # none | error (exception, exit 1) | kill (hard exit mid-line)
    'fail_at_iter': 3,
    'sleep_scale': 0.0,  # real seconds slept per synthetic second
}
FAIL_MODES = ('none', 'error', 'kill')

# Defaults the stub "resolves" into init_coupler.toml, like PROTEUS's attrs defaults
RESOLVED_DEFAULTS = {
    'params': {
        'out': {'path': 'fake_run', 'logging': 'INFO'},
        'stop': {'iters': {'maximum': 4}},
    },
    'interior_struct': {'module': 'zalmoxis'},
    'interior_energetics': {'module': 'aragog', 'aragog': {'solver_method': 'cvode'}},
    'atmos_clim': {'module': 'agni'},
    'outgas': {'module': 'calliope'},
}


class SyntheticClock:
    """Monotonic clock that advances only when told to (optionally sleeping)."""

    def __init__(self, sleep_scale: float = 0.0):
        self.now = 0.0
        self.sleep_scale = sleep_scale

    def advance(self, seconds: float) -> None:
        self.now += seconds
        if self.sleep_scale > 0:
            time.sleep(seconds * self.sleep_scale)


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, val in over.items():
        out[key] = (
            _deep_merge(out[key], val)
            if isinstance(val, dict) and isinstance(out.get(key), dict)
            else val
        )
    return out


def _dump_toml(data: dict, prefix: str = '') -> str:
    """Minimal TOML writer for nested tables of scalars and lists."""
    scalars = [(k, v) for k, v in data.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, dict)]
    lines = [f'[{prefix}]'] if prefix and scalars else []
    lines += [f'{k} = {json.dumps(v)}' for k, v in scalars]
    text = '\n'.join(lines) + ('\n\n' if lines else '')
    for key, val in tables:
        text += _dump_toml(val, f'{prefix}.{key}' if prefix else key)
    return text


def _seconds(fields: dict[str, float]) -> str:
    return ' '.join(f'{k}={v:.3f}' for k, v in fields.items())


def scenario(overrides: dict) -> dict:
    """The ``[fake]`` settings merged over FAKE_DEFAULTS; typos are errors, not no-ops."""
    unknown = sorted(overrides.keys() - FAKE_DEFAULTS.keys())
    if unknown:
        raise ValueError(f'unknown [fake] keys {unknown}; allowed: {sorted(FAKE_DEFAULTS)}')
    merged = {**FAKE_DEFAULTS, **overrides}
    if merged['fail'] not in FAIL_MODES:
        raise ValueError(f'[fake] fail = {merged["fail"]!r}; allowed: {FAIL_MODES}')
    return merged


class _Run:
    """One fake simulation: state shared by the stage functions below."""

    def __init__(self, cfg: dict, outdir: Path, timing: bool, log_fh: TextIO):
        self.fake = scenario(cfg.get('fake', {}))
        self.cfg = _deep_merge(RESOLVED_DEFAULTS, {k: v for k, v in cfg.items() if k != 'fake'})
        self.outdir = outdir
        self.timing = timing
        self.clock = SyntheticClock(self.fake['sleep_scale'])
        self.log = log_fh
        self.rows: list[tuple[float, ...]] = []

    def info(self, msg: str, end: str = '\n') -> None:
        line = f'[ INFO  ] {msg}'
        print(line, end=end, flush=True)
        self.log.write(line + end)
        self.log.flush()

    def init_phase(self) -> None:
        f = self.fake
        self.info('Using Zalmoxis to solve for interior structure')
        self.clock.advance(f['structure_init_s'])
        steps = {'structure': self.clock.now}
        for k in range(1, f['equilibrate_iters'] + 1):
            self.clock.advance(f['outgas_s'] + f['equilibrate_structure_s'] / k)
        steps['equilibrate'] = self.clock.now - steps['structure']
        self.clock.advance(2.0)
        steps['stellar'] = 2.0
        self.info('Wrote init_coupler.toml')
        if self.timing:
            steps |= {'other': self.clock.now - sum(steps.values()), 'total': self.clock.now}
            self.info(f'[INIT_TIMING] {_seconds(steps)}')

    def iteration(self, n: int) -> None:
        f = self.fake
        self.info('Loop counters')
        t_iter = self.clock.now
        times = {}
        for comp, secs in self._stages(n):
            if f['fail'] != 'none' and n == f['fail_at_iter'] and comp == 'atmos':
                self._fail(n, times)
            times[comp] = secs
            self.clock.advance(secs)
        self.clock.advance(0.2)  # unattributed bookkeeping
        times |= {'other': 0.2, 'total': self.clock.now - t_iter}
        if self.timing:
            self.info(f'[IT_TIMING] iter={n} init_stage={int(n <= 1)} {_seconds(times)}')
        self.rows.append((1e3 * n, 3000.0 - 50 * n, 1.0 - 0.01 * n, 1e5 / n, 250.0 + n))

    def _stages(self, n: int):
        f = self.fake
        yield 'interior', f['interior_s']
        if n % f['restructure_every'] == 0:
            yield 'structure', f['restructure_s']
        yield 'outgas', f['outgas_s']
        yield 'atmos', f['atmos_first_s'] if n == 1 else f['atmos_s']

    def _fail(self, n: int, times: dict) -> None:
        if self.fake['fail'] == 'kill':
            self.info(
                f'[IT_TIMING] iter={n} init_stage=0 {_seconds(times)} atm', end=''
            )  # torn
            os._exit(137)
        raise RuntimeError('fake atmosphere solver failure')

    def shutdown_phase(self) -> None:
        self.info('Writing data')
        header = 'Time\tT_magma\tPhi_global\tF_atm\tP_surf\n'
        body = ''.join('\t'.join(f'{x:.10e}' for x in row) + '\n' for row in self.rows)
        (self.outdir / 'runtime_helpfile.csv').write_text(header + body)
        self.clock.advance(5.0)


def run(cfg: dict, outdir: Path, timing: bool) -> int:
    """Run the fake simulation; return the process exit code."""
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / 'proteus_00.log', 'w') as log_fh:
        return _simulate(_Run(cfg, outdir, timing, log_fh))


def _simulate(r: _Run) -> int:
    """Run every stage through ``r``; return the process exit code."""
    r.info(f'[BACKEND] aragog solver={r.fake["aragog_solver"]}')
    (r.outdir / 'init_coupler.toml').write_text(_dump_toml(r.cfg))
    n_max = r.cfg['params']['stop']['iters']['maximum']
    try:
        r.init_phase()
        for n in range(1, n_max + 1):
            r.iteration(n)
        r.shutdown_phase()
    except RuntimeError as err:
        print(f'[ ERROR ] {err}', file=sys.stderr, flush=True)
        return 1
    r.info('===> Maximum number of iterations reached')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='fake-proteus')
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('start')
    start.add_argument('-c', '--config', required=True, type=Path)
    # Accepted so harness command lines match real proteus; they change nothing here.
    start.add_argument('-o', '--offline', action='store_true')
    start.add_argument('-r', '--resume', action='store_true')
    args = parser.parse_args(argv)
    cfg = tomllib.loads(args.config.read_text())
    name = (
        cfg.get('params', {})
        .get('out', {})
        .get('path', RESOLVED_DEFAULTS['params']['out']['path'])
    )
    root = Path(os.environ.get('PROTEUS_OUTPUT_PATH', '').strip() or 'output').expanduser()
    timing = os.environ.get('PROTEUS_TIMING', '').lower() in ('1', 'true', 'yes', 'on')
    return run(cfg, root / name, timing)


if __name__ == '__main__':
    sys.exit(main())
