"""Stand-in for the ``proteus`` CLI, for developing and testing the harness.

Usage mirrors the real command::

    python -m proteus_bench.testing.fake_proteus start -c cfg.toml [--offline]

It writes what the harness reads from a real run, into
``$PROTEUS_OUTPUT_PATH/<params.out.path>`` (default ``./output``):
``timing.jsonl`` (only when ``PROTEUS_TIMING=1``, like PROTEUS),
``init_coupler.toml``, ``runtime_helpfile.csv`` and ``proteus_00.log``. Times
come from a synthetic clock shaped like the 2026 Hábrók scaling runs (a long
first structure solve, periodic in-loop re-solves), so a run finishes at once
unless ``[fake] sleep_scale`` asks for real sleeping.

The ``[fake]`` table of the config selects the scenario; see ``FAKE_DEFAULTS``.
``TimingWriter`` doubles as a reference for the emitter PROTEUS implements.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path

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
    'zalmoxis_backend_init': 'numpy',
    'zalmoxis_backend_loop': 'jax',
    'fail': 'none',  # none | error (exception, clean shutdown) | kill (hard exit mid-span)
    'fail_at_iter': 3,
    'sleep_scale': 0.0,  # real seconds slept per synthetic second
}

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


class TimingWriter:
    """Writes timing.jsonl events; spans nest through a stack and close in order."""

    def __init__(self, path: Path | None, clock: SyntheticClock):
        self.fh = open(path, 'w') if path is not None else None
        self.clock = clock
        self.stack: list[int] = []
        self.next_id = 1

    def emit(self, **event) -> None:
        if self.fh is not None:
            self.fh.write(json.dumps({'v': 1, **event}) + '\n')
            self.fh.flush()

    @contextmanager
    def span(self, name: str, **fields):
        sid, self.next_id = self.next_id, self.next_id + 1
        parent = self.stack[-1] if self.stack else None
        t0 = round(self.clock.now, 6)
        self.stack.append(sid)
        ok = True
        try:
            yield
        except BaseException:
            ok = False
            raise
        finally:
            self.stack.pop()
            dur = round(self.clock.now - t0, 6)
            extra = {} if ok else {'ok': False}
            self.emit(
                ev='span', id=sid, parent=parent, name=name, t0=t0, dur=dur, **fields, **extra
            )


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


class _Run:
    """One fake simulation: state shared by the stage functions below."""

    def __init__(self, cfg: dict, outdir: Path, timing: bool):
        self.fake = {**FAKE_DEFAULTS, **cfg.pop('fake', {})}
        self.cfg = _deep_merge(RESOLVED_DEFAULTS, cfg)
        self.outdir = outdir
        self.clock = SyntheticClock(self.fake['sleep_scale'])
        self.tw = TimingWriter(outdir / 'timing.jsonl' if timing else None, self.clock)
        self.log = open(outdir / 'proteus_00.log', 'w')
        self.rows: list[tuple[float, ...]] = []

    def info(self, msg: str) -> None:
        line = f'[ INFO  ] {msg}'
        print(line, flush=True)
        self.log.write(line + '\n')

    def init_phase(self) -> None:
        f = self.fake
        with self.tw.span('init'):
            back = f['zalmoxis_backend_init']
            with self.tw.span(
                'structure', component='structure', submodule='zalmoxis', backend=back
            ):
                self.info('Using Zalmoxis to solve for interior structure')
                self.clock.advance(f['structure_init_s'])
            with self.tw.span('equilibrate'):
                for k in range(1, f['equilibrate_iters'] + 1):
                    with self.tw.span('outgas', component='outgas', submodule='calliope'):
                        self.clock.advance(f['outgas_s'])
                    with self.tw.span(
                        'structure', component='structure', submodule='zalmoxis', backend=back
                    ):
                        self.clock.advance(f['equilibrate_structure_s'] / k)
            with self.tw.span('star', component='stellar', submodule='mors'):
                self.clock.advance(2.0)
        self.info('Wrote init_coupler.toml')

    def iteration(self, n: int) -> None:
        f = self.fake
        self.info('Loop counters')
        t_iter = self.clock.now
        times = {}
        with self.tw.span('iter', iter=n, extra={'init_stage': n <= 1, 'time_yr': 1e3 * n}):
            for comp, sub, secs, back in self._stages(n):
                t0 = self.clock.now
                fields = {'component': comp, 'submodule': sub} | (
                    {'backend': back} if back else {}
                )
                with self.tw.span(comp, **fields):
                    if f['fail'] != 'none' and n == f['fail_at_iter'] and comp == 'atmos':
                        self._fail()
                    self.clock.advance(secs)
                times[comp] = self.clock.now - t0
            self.clock.advance(0.2)  # unattributed bookkeeping
        times['total'] = self.clock.now - t_iter
        self.info(
            '[IT_TIMING] iter=%d %s' % (n, ' '.join(f'{k}={v:.3f}' for k, v in times.items()))
        )
        self.rows.append((1e3 * n, 3000.0 - 50 * n, 1.0 - 0.01 * n, 1e5 / n, 250.0 + n))

    def _stages(self, n: int):
        f = self.fake
        yield 'interior', 'aragog', f['interior_s'], f['aragog_solver']
        if n % f['restructure_every'] == 0:
            yield 'structure', 'zalmoxis', f['restructure_s'], f['zalmoxis_backend_loop']
        yield 'outgas', 'calliope', f['outgas_s'], None
        yield 'atmos', 'agni', f['atmos_first_s'] if n == 1 else f['atmos_s'], None

    def _fail(self) -> None:
        if self.fake['fail'] == 'kill':
            if self.tw.fh is not None:
                self.tw.fh.write('{"v": 1, "ev": "span", "id": 9')  # torn final line
                self.tw.fh.flush()
            os._exit(137)
        raise RuntimeError('fake atmosphere solver failure')

    def shutdown_phase(self) -> None:
        with self.tw.span('shutdown'):
            self.info('Writing data')
            header = 'Time\tT_magma\tPhi_global\tF_atm\tP_surf\n'
            body = ''.join('\t'.join(f'{x:.10e}' for x in row) + '\n' for row in self.rows)
            (self.outdir / 'runtime_helpfile.csv').write_text(header + body)
            self.clock.advance(5.0)


def run(cfg: dict, outdir: Path, timing: bool) -> int:
    """Run the fake simulation; return the process exit code."""
    outdir.mkdir(parents=True, exist_ok=True)
    r = _Run(cfg, outdir, timing)
    wall = dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')
    r.tw.emit(ev='run_start', wall=wall.replace('+00:00', 'Z'), pid=os.getpid())
    r.tw.emit(
        ev='backend', t0=0.0, submodule='aragog', key='solver', value=r.fake['aragog_solver']
    )
    (outdir / 'init_coupler.toml').write_text(_dump_toml(r.cfg))
    n_max = r.cfg['params']['stop']['iters']['maximum']
    try:
        r.init_phase()
        with r.tw.span('loop'):
            for n in range(1, n_max + 1):
                r.iteration(n)
        r.shutdown_phase()
    except RuntimeError as err:
        r.tw.emit(ev='run_end', t0=round(r.clock.now, 6), status='error', error=str(err))
        print(f'[ ERROR ] {err}', file=sys.stderr, flush=True)
        return 1
    r.info('===> Maximum number of iterations reached')
    r.tw.emit(
        ev='run_end',
        t0=round(r.clock.now, 6),
        status='ok',
        termination='iters_maximum',
        n_iters=n_max,
        time_yr=1e3 * n_max,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='fake-proteus')
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('start')
    start.add_argument('-c', '--config', required=True, type=Path)
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
