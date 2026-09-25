"""``proteus-bench run``: run one benchmark suite through the proteus CLI and record it.

Stages: load the suite, build the run config, check the environment, spawn
proteus with ``PROTEUS_TIMING=1``, collect its outputs and write
``<runs-dir>/<run_id>/record.json``. Failing checks stop the run before it is
timed unless ``--allow-failed-checks`` is given; the record then says why the
run is not comparable. Exit code: 0 when the run finished ok, 1 when it
failed, crashed or timed out, 2 when it could not start.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib
import os
import platform
import secrets
import shlex
import shutil
import sys
from pathlib import Path
from types import ModuleType

from proteus_bench import checks, machine, provenance, record, runner, suites, tomlwrite

PROFILERS = ('none', 'scalene', 'py-spy')
PROFILE_DIR = 'profile'


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--suite', default='default', help='suite name in suites.toml')
    parser.add_argument(
        '--proteus-root', type=Path, help='PROTEUS checkout (default: $PROTEUS_DIR or cwd)'
    )
    parser.add_argument(
        '--proteus-cmd', default='proteus', help='command that runs PROTEUS, split like a shell'
    )
    parser.add_argument(
        '--python', default=sys.executable, help='interpreter of the PROTEUS environment'
    )
    parser.add_argument('--runs-dir', type=Path, default=Path('bench-runs'))
    parser.add_argument('--machine-label', help='default: short host name, or gha on CI')
    parser.add_argument('--machine-class', help='default: <os>-<arch>-<cpu model>')
    parser.add_argument('--timeout', type=float, help='kill the run after this many seconds')
    parser.add_argument(
        '--allow-failed-checks', action='store_true', help='run anyway, as not comparable'
    )
    parser.add_argument('--profiler', choices=PROFILERS, default='none')


def main(args: argparse.Namespace) -> int:
    try:
        ctx = prepare(args)
    except ValueError as err:
        print(f'proteus-bench run: {err}', file=sys.stderr)
        return 2
    for check in ctx.checks:
        print(f'{"ok  " if check["ok"] else "FAIL"} {check["name"]}: {check["detail"]}')
    if not all(c['ok'] for c in ctx.checks) and not args.allow_failed_checks:
        _discard_empty(ctx.run_dir)
        print('proteus-bench run: checks failed; fix them or pass --allow-failed-checks')
        return 2
    rec = execute(ctx)
    print(ctx.run_dir)
    return 0 if rec['outcome']['status'] == 'ok' else 1


def _discard_empty(run_dir: Path) -> None:
    """Remove the directories a profiler hook created for a run that did not start."""
    for path in (run_dir / PROFILE_DIR, run_dir):
        with contextlib.suppress(OSError):  # absent, or not empty: keep it
            path.rmdir()


def execute(ctx: record.RunContext) -> dict:
    """Write the config, run proteus, collect its outputs and write the record."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)  # the profiler hook may have made it
    (ctx.run_dir / record.ARTIFACTS['config']).write_text(tomlwrite.dumps(ctx.run_config))
    result = runner.spawn(
        ctx.argv,
        ctx.proteus_root,
        ctx.child_env,
        ctx.run_dir / record.ARTIFACTS['log'],
        ctx.timeout_s,
    )
    record.copy_outputs(ctx)
    rec = record.build_record(ctx, result, collect_profile(ctx))
    record.write_record(ctx.run_dir, rec)
    return rec


def collect_profile(ctx: record.RunContext) -> tuple[dict, list[str]]:
    """Profile artifacts (paths relative to the run dir) and notes; empty if not profiled."""
    if ctx.profiling is None:
        return {}, []
    meta = {'run_id': ctx.run_id, 'commit': ctx.code['proteus']['sha']}
    try:
        return ctx.profiling.collect(ctx.run_dir / PROFILE_DIR, meta), []
    except (FileNotFoundError, ValueError) as err:
        return {}, [f'profile not collected: {err}']


def prepare(args: argparse.Namespace) -> record.RunContext:
    """Everything up to the spawn; raises ``ValueError`` when the run cannot start.

    The profiler hook, which may create the profile directory, runs last, so
    no earlier failure leaves a directory behind.
    """
    root = (args.proteus_root or Path(os.environ.get('PROTEUS_DIR') or '.')).resolve()
    suite = suites.load_suite(args.suite)
    adapter = record.detect_adapter(os.environ)
    label = args.machine_label or ('gha' if adapter == 'gha' else platform.node().split('.')[0])
    run_id = make_run_id(dt.datetime.now(dt.UTC), label, suite['name'])
    run_dir = args.runs_dir.resolve() / run_id
    run_config = suites.build_run_config(root, suite, run_id)
    proteus_git = provenance.git_state(root)
    if proteus_git is None:
        raise ValueError(f'--proteus-root {root} is not the top of a git checkout')
    env_report = provenance.introspect_env(args.python)
    machine_section = machine.machine_section(label, args.machine_class)
    argv, child_env, profiler_env, profiling = command(args, run_dir)
    return record.RunContext(
        suite=suite,
        run_id=run_id,
        run_dir=run_dir,
        run_config=run_config,
        harness=provenance.harness_state(),
        trigger=record.trigger_section(adapter, os.environ),
        code=provenance.code_section(proteus_git, env_report, root, child_env),
        machine=machine_section,
        env=machine.env_section(child_env, env_report, args.profiler, profiler_env),
        checks=[
            checks.cvode_check(args.python, run_config),
            checks.env_dirs_check(child_env),
            checks.clean_tree_check(proteus_git),
        ],
        timeout_s=args.timeout,
        proteus_root=root,
        argv=argv,
        child_env=child_env,
        profiling=profiling,
    )


def make_run_id(now: dt.datetime, label: str, suite: str) -> str:
    """``<UTC time>-<label>-<suite>-<4 hex>``, unique across concurrent runs."""
    parts = [machine.slug(label), machine.slug(suite), secrets.token_hex(2)]
    return f'{now:%Y%m%dT%H%M%SZ}-' + '-'.join(p for p in parts if p)


def child_environment(run_dir: Path) -> dict:
    """Our environment plus the timing switch, the output root and single threading."""
    return {
        **os.environ,
        **dict.fromkeys(machine.THREAD_VARS, '1'),
        'PROTEUS_TIMING': '1',
        'PROTEUS_OUTPUT_PATH': str(run_dir / 'output'),
    }


def command(args: argparse.Namespace, run_dir: Path):
    """``(argv, env, profiler_env, profiling module or None)`` for the proteus process.

    ``argv[0]`` is resolved on the child's PATH, so a missing command fails here,
    before anything is written. Raises ``ValueError`` for a missing command and
    for profiler support that cannot be imported or used.
    """
    env = child_environment(run_dir)
    words = shlex.split(args.proteus_cmd)
    exe = shutil.which(words[0], path=env.get('PATH')) if words else None
    if exe is None:
        raise ValueError(f'--proteus-cmd {args.proteus_cmd!r}: command not found on PATH')
    config = str(run_dir / record.ARTIFACTS['config'])
    argv = [os.path.abspath(exe), *words[1:], 'start', '-c', config]
    if args.profiler == 'none':
        return argv, env, {}, None
    profiling = _profiling_module(args.profiler)
    try:
        argv, profiler_env = profiling.wrap_command(args.profiler, argv, run_dir / PROFILE_DIR)
    except RuntimeError as err:
        raise ValueError(f'--profiler {args.profiler}: {err}') from err
    return argv, {**env, **profiler_env}, profiler_env, profiling


def _profiling_module(profiler: str) -> ModuleType:
    try:
        return importlib.import_module('proteus_bench.profiling')
    except ImportError as err:
        raise ValueError(f'--profiler {profiler} needs proteus_bench.profiling: {err}') from err
