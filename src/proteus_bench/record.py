"""Assemble and write the run record (schema ``proteus-bench/1``) of one run directory.

Artifact paths in the record are relative to the run directory; publishing
rewrites them to results-store paths.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from proteus_bench import checks, collect, logtiming, settings
from proteus_bench.runner import ProcessResult, home_pattern

SCHEMA = 'proteus-bench/1'
# record.artifacts key -> file in the run directory; one key per file
ARTIFACTS = {
    'spans': 'timing.jsonl',
    'settings': 'init_coupler.toml',
    'config': 'config.toml',
    'log': 'log.txt',
}
# Artifacts proteus writes into its output directory, copied next to the record
COPIED = ('spans', 'settings')


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
    profiling: ModuleType | None  # proteus_bench.profiling, imported for profiled runs only

    @property
    def output_dir(self) -> Path:
        """Where proteus writes: $PROTEUS_OUTPUT_PATH/<params.out.path>.

        PROTEUS before that variable (#777) writes to <checkout>/output/<params.out.path>.
        """
        path = self.run_dir / 'output' / self.run_id
        return path if path.is_dir() else self.proteus_root / 'output' / self.run_id


def utc_iso(moment: dt.datetime) -> str:
    """UTC with fixed-width microseconds, so string order is time order."""
    return moment.astimezone(dt.UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def detect_adapter(env: dict) -> str:
    if env.get('GITHUB_ACTIONS') == 'true':
        return 'gha'
    return 'slurm' if env.get('SLURM_JOB_ID') else 'local'


def trigger_section(adapter: str, env: dict) -> dict:
    """Adapter and CI or scheduler identifiers; ``started_at`` is set at spawn."""
    trigger = {'adapter': adapter}
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
    for key in COPIED:
        source = ctx.output_dir / ARTIFACTS[key]
        if source.is_file():
            shutil.copy2(source, ctx.run_dir / ARTIFACTS[key])


def finite_or_null(value):
    """``value`` with TOML's inf and nan, which JSON cannot hold, replaced by None."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [finite_or_null(v) for v in value]
    if isinstance(value, dict):
        return {k: finite_or_null(v) for k, v in value.items()}
    return value


def benchmark_section(ctx: RunContext, flat: dict) -> dict:
    digest = settings.settings_hash(flat)
    return {
        'name': Path(ctx.suite['config']).stem,
        'lineage': 'default' if ctx.suite['name'] == 'default' else digest,
        'settings_hash': digest,
        'settings': finite_or_null(settings.comparable(flat)),
        'overrides': ctx.suite['overrides'],
        'config_source': ctx.suite['config'],
        'carry_over_of': None,
    }


def timings_section(events: list[dict], result: ProcessResult) -> dict:
    section = {
        'source': 'spans',
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


def _timing_sections(ctx: RunContext, result: ProcessResult) -> tuple:
    """(checks, backends, outcome, timings) from timing.jsonl, else from the log lines."""
    spans = ctx.run_dir / ARTIFACTS['spans']
    if spans.is_file():
        events, timing_check = checks.timing_contract_check(spans)
        backends = collect.backends(events)
        expected = checks.expected_backends_check(backends, ctx.suite['expected_backends'])
        outcome = collect.outcome(events, result.exit_code, result.timed_out, ctx.timeout_s)
        return [timing_check, expected], backends, outcome, timings_section(events, result)
    iters = logtiming.iterations(result.timing_lines)
    outcome = logtiming.outcome(iters, result.exit_code, result.timed_out, ctx.timeout_s)
    timings = logtiming.timings_section(iters, result.wall_s, result.rusage)
    return [logtiming.check(iters)], {}, outcome, timings


def build_record(ctx: RunContext, result: ProcessResult, profile: tuple[dict, list]) -> dict:
    """The run record, from the context and the files in the run and output directories.

    ``profile`` holds the profile artifacts and notes (both empty when not profiled).
    """
    profile_artifacts, profile_notes = profile
    flat, notes = collect.resolved_settings(ctx.run_dir / ARTIFACTS['settings'], ctx.run_config)
    fingerprint, fp_notes = collect.fingerprint(ctx.output_dir / 'runtime_helpfile.csv')
    run_checks, backends, outcome, timings = _timing_sections(ctx, result)
    all_checks = [*ctx.checks, *run_checks]
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
            all_checks, outcome['status'], profiler, notes + fp_notes + profile_notes
        ),
        'timings': timings,
        'fingerprint': fingerprint,
        'artifacts': {k: p for k, p in ARTIFACTS.items() if (ctx.run_dir / p).is_file()}
        | profile_artifacts,
    }


def write_record(run_dir: Path, record: dict) -> Path:
    path = run_dir / 'record.json'
    # Unescaped, so a home directory with non-ASCII characters still matches
    text = json.dumps(record, indent=2, allow_nan=False, ensure_ascii=False)
    path.write_text(home_pattern().sub('~', text) + '\n', encoding='utf-8')
    return path
