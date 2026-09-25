"""Assemble and write the run record (schema ``proteus-bench/1``) of one run directory.

Artifact paths in the record are relative to the run directory; publishing
rewrites them to results-store paths.
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from proteus_bench import checks, collect, settings
from proteus_bench.runner import ProcessResult
from proteus_bench.timing import read_events

SCHEMA = 'proteus-bench/1'
COPIED_OUTPUTS = ('timing.jsonl', 'init_coupler.toml')
# record.artifacts key -> file in the run directory
ARTIFACTS = {
    'spans': 'timing.jsonl',
    'resolved_config': 'init_coupler.toml',
    'config': 'config.toml',
    'log': 'log.txt',
    'flame': 'profile/flame.html',
}


@dataclass
class RunContext:
    """Everything known about a run before the proteus process starts."""

    suite: dict
    run_id: str
    run_dir: Path
    run_config: dict
    harness: dict
    trigger: dict
    code: dict
    machine: dict
    env: dict
    checks: list[dict]
    timeout_s: float | None
    proteus_root: Path  # working directory of the proteus process
    argv: list[str]
    child_env: dict

    @property
    def output_dir(self) -> Path:
        """Where proteus writes: $PROTEUS_OUTPUT_PATH/<params.out.path>."""
        return self.run_dir / 'output' / self.run_id


def utc_iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def detect_adapter(env: dict) -> str:
    if env.get('GITHUB_ACTIONS') == 'true':
        return 'gha'
    return 'slurm' if env.get('SLURM_JOB_ID') else 'local'


def trigger_section(adapter: str, env: dict) -> dict:
    """Adapter, user and CI or scheduler identifiers; ``started_at`` is set at spawn."""
    trigger = {'adapter': adapter, 'user': env.get('GITHUB_ACTOR') or getpass.getuser()}
    if env.get('GITHUB_RUN_ID'):
        server = env.get('GITHUB_SERVER_URL', 'https://github.com')
        trigger['gha'] = {
            'run_url': f'{server}/{env.get("GITHUB_REPOSITORY")}/actions/runs/{env["GITHUB_RUN_ID"]}',
            'run_id': int(env['GITHUB_RUN_ID']),
            'workflow': env.get('GITHUB_WORKFLOW', ''),
            'event': env.get('GITHUB_EVENT_NAME', ''),
        }
    if env.get('SLURM_JOB_ID'):
        slurm = {'job_id': env['SLURM_JOB_ID']}
        for key, var in (('partition', 'SLURM_JOB_PARTITION'), ('node', 'SLURMD_NODENAME')):
            if env.get(var):
                slurm[key] = env[var]
        trigger['slurm'] = slurm
    return trigger


def copy_outputs(ctx: RunContext) -> None:
    """Copy the raw timing and resolved config next to the record, where present."""
    for name in COPIED_OUTPUTS:
        source = ctx.output_dir / name
        if source.is_file():
            shutil.copy2(source, ctx.run_dir / name)


def benchmark_section(ctx: RunContext, flat: dict) -> dict:
    digest = settings.settings_hash(flat)
    return {
        'name': Path(ctx.suite['config']).stem,
        'lineage': 'default' if ctx.suite['name'] == 'default' else digest,
        'settings_hash': digest,
        'settings': settings.comparable(flat),
        'overrides': ctx.suite['overrides'],
        'config_source': ctx.suite['config'],
        'carry_over_of': None,
    }


def timings_section(events: list[dict], result: ProcessResult) -> dict:
    section = {
        'wall_s': round(result.wall_s, 3),
        'phases': collect.phase_totals(events),
        'components': collect.component_rows(events),
        'per_iter': collect.per_iter_rows(events),
        'rusage': result.rusage,
    }
    startup = collect.startup_s(events, result.started_at)
    if startup is not None:
        section['startup_s'] = startup
    return section


def build_record(ctx: RunContext, result: ProcessResult) -> dict:
    """The run record, from the context and the files in the run and output directories."""
    timing_path = ctx.run_dir / 'timing.jsonl'
    events = read_events(timing_path) if timing_path.is_file() else []
    flat, notes = collect.resolved_settings(ctx.run_dir / 'init_coupler.toml', ctx.run_config)
    fingerprint, fp_notes = collect.fingerprint(ctx.output_dir / 'runtime_helpfile.csv')
    backends = collect.backends(events)
    all_checks = [
        *ctx.checks,
        checks.timing_contract_check(events),
        checks.expected_backends_check(backends, ctx.suite['expected_backends']),
    ]
    outcome = collect.outcome(events, result.exit_code, result.timed_out, ctx.timeout_s)
    profiler = ctx.env['knobs']['profiler']
    return {
        'schema': SCHEMA,
        'run_id': ctx.run_id,
        'harness': ctx.harness,
        'benchmark': benchmark_section(ctx, flat),
        'trigger': {**ctx.trigger, 'started_at': utc_iso(result.started_at)},
        'code': ctx.code,
        'machine': ctx.machine,
        'env': ctx.env,
        'checks': all_checks,
        'backends': backends,
        'outcome': outcome,
        'comparability': checks.comparability(
            all_checks, outcome['status'], profiler, notes + fp_notes
        ),
        'timings': timings_section(events, result),
        'fingerprint': fingerprint,
        'artifacts': {k: p for k, p in ARTIFACTS.items() if (ctx.run_dir / p).is_file()},
    }


def write_record(run_dir: Path, record: dict) -> Path:
    path = run_dir / 'record.json'
    path.write_text(json.dumps(record, indent=2) + '\n')
    return path
