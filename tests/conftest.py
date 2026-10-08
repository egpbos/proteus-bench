"""Shared fixtures: fake proteus runs, synthetic run histories, profiling fixture
files, run directories, isolated git stores and a fake gh."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import random
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomli_w

from proteus_bench import store
from proteus_bench.settings import comparable, flatten, settings_hash
from proteus_bench.testing import fake_proteus
from proteus_bench.timing import read_events

EXAMPLE_RECORD = Path(__file__).parents[1] / 'examples' / 'record.json'


@pytest.fixture
def noisy_factors():
    """Per-run time factors: seeded Gaussian noise, times (1 + step_rel) from run step_at."""

    def _factors(n, *, noise_rel=0.015, step_at=None, step_rel=0.0, seed=42):
        rng = random.Random(seed)
        return [
            (1 + (step_rel if step_at is not None and i >= step_at else 0.0))
            * (1 + rng.gauss(0, noise_rel))
            for i in range(n)
        ]

    return _factors


def _scale_times(timings: dict, factor: float) -> None:
    """Multiply every duration in a record's timings by factor, in place."""
    timings['wall_s'] *= factor
    timings['startup_s'] *= factor
    timings['phases'] = {
        k: v if v is None else v * factor for k, v in timings['phases'].items()
    }
    for row in timings['components']:
        row['total_s'] *= factor
    for row in timings['per_iter']:
        row['dur_s'] *= factor
        row['components'] = {k: v * factor for k, v in row['components'].items()}


@pytest.fixture
def example_record():
    """A fresh copy of examples/record.json (wall_s 2961.2 s, 6 iterations)."""
    return json.loads(EXAMPLE_RECORD.read_text())


@pytest.fixture
def make_records(example_record):
    """Make a daily history from examples/record.json, one record per time factor.

    Run i starts i days after 2026-01-01 03:10 UTC, has run_id
    ``<start>-run{i:03d}`` and every duration multiplied by ``factors[i]``;
    ``edit(i, record)`` adjusts a record afterwards (comparability, settings,
    single components).
    """
    base = copy.deepcopy(example_record)
    start = datetime(2026, 1, 1, 3, 10, tzinfo=UTC)

    def _make(factors, edit=None):
        records = []
        for i, factor in enumerate(factors):
            record = copy.deepcopy(base)
            started = start + timedelta(days=i)
            record['run_id'] = f'{started:%Y%m%dT%H%M%SZ}-run{i:03d}'
            record['trigger']['started_at'] = started.isoformat()
            record['code']['proteus']['sha'] = f'{i:08x}'
            _scale_times(record['timings'], factor)
            if edit is not None:
                edit(i, record)
            records.append(record)
        return records

    return _make


EXAMPLES = Path(__file__).parent.parent / 'examples'
DEFAULT_SETTINGS = {
    'params': {'out': {'path': 'bench'}, 'stop': {'iters': {'maximum': 6}}},
    'interior_struct': {'module': 'zalmoxis', 'zalmoxis': {'use_jax': True}},
}


@pytest.fixture
def run_fake(tmp_path, capsys):
    """Run the stub in-process; return (exit code, output dir, events or None)."""

    def _run(fake: dict | None = None, iters: int = 4, timing: bool = True):
        cfg = {'params': {'out': {'path': 'run'}, 'stop': {'iters': {'maximum': iters}}}}
        if fake:
            cfg['fake'] = fake
        outdir = tmp_path / 'run'
        code = fake_proteus.run(cfg, outdir, timing=timing)
        capsys.readouterr()  # keep the stub's log lines out of test output
        path = outdir / 'timing.jsonl'
        return code, outdir, read_events(path) if path.exists() else None

    return _run


@pytest.fixture
def good_events(run_fake):
    """Events of a default four-iteration fake run that finished normally."""
    code, _, events = run_fake()
    assert code == 0
    return events


@pytest.fixture
def profiles():
    """Profiling fixture files and their sample totals, counted outside proteus_bench."""
    fixtures = Path(__file__).parent / 'fixtures'
    return SimpleNamespace(
        scalene=fixtures / 'scalene-profile.json',
        scalene_total=40 + 25 + 12 + 8 + 3 + 2,  # hits of its six combined_stacks entries
        slice=fixtures / 'real-slice.folded',
        slice_total=22562,  # awk '{s += $NF} END {print s}' real-slice.folded
    )


def _git(path: Path, *args: str) -> str:
    # A fixed throwaway identity, so fixture commits work where git has none configured
    env = {
        **os.environ,
        'GIT_AUTHOR_NAME': 'fixture',
        'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
        'GIT_COMMITTER_NAME': 'fixture',
        'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
    }
    proc = subprocess.run(
        ['git', '-C', str(path), '-c', 'commit.gpgsign=false', *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def git_repo():
    """Make ``path`` a git repository with its files in one commit on branch main.

    Returns the full commit sha.
    """

    def _make(path: Path) -> str:
        path.mkdir(parents=True, exist_ok=True)
        _git(path, 'init', '-q', '-b', 'main')
        _git(path, 'add', '-A')
        _git(path, 'commit', '-q', '--allow-empty', '-m', 'fixture')
        return _git(path, 'rev-parse', 'HEAD')

    return _make


@pytest.fixture
def make_run_dir(tmp_path):
    """Build a run directory like ``proteus-bench run`` writes, from the examples.

    The record's settings and hash come from the ``init_coupler.toml`` written
    here, so they agree. ``started_at`` follows the run id unless given.
    Keyword fields: started_at, lineage, carry_over_of, machine_class, status,
    artifacts.
    """

    def _make(
        run_id: str = '20260925T031000Z-habrok-default-a1b2',
        *,
        root: Path | None = None,
        settings: dict | None = None,
        **fields,
    ) -> Path:
        run_dir = (root or tmp_path / 'runs') / run_id
        run_dir.mkdir(parents=True)
        nested = copy.deepcopy(settings or DEFAULT_SETTINGS)
        nested['params']['out']['path'] = run_id  # per-run key: must not change the hash
        (run_dir / 'init_coupler.toml').write_text(tomli_w.dumps(nested))
        shutil.copyfile(EXAMPLES / 'timing.jsonl', run_dir / 'timing.jsonl')
        (run_dir / 'log.txt').write_text(f'[ INFO ] run {run_id}\n')
        (run_dir / 'config.toml').write_text('[params.stop.iters]\nmaximum = 6\n')
        record = json.loads((EXAMPLES / 'record.json').read_text())
        flat = flatten(nested)
        stamp = dt.datetime.strptime(run_id[:16], '%Y%m%dT%H%M%SZ')
        record['run_id'] = run_id
        record['trigger']['started_at'] = fields.pop(
            'started_at', stamp.strftime('%Y-%m-%dT%H:%M:%SZ')
        )
        record['benchmark'] |= {
            'settings': comparable(flat),
            'settings_hash': settings_hash(flat),
        }
        record['benchmark'] |= {
            k: fields.pop(k) for k in ('lineage', 'carry_over_of') if k in fields
        }
        record['machine']['class'] = fields.pop('machine_class', 'habrok-vink')
        record['outcome']['status'] = fields.pop('status', 'ok')
        default_artifacts = {k: v for k, v in store.ARTIFACTS.items() if (run_dir / v).exists()}
        record['artifacts'] = fields.pop('artifacts', default_artifacts)
        assert not fields, f'unknown fields {fields}'
        (run_dir / 'record.json').write_text(json.dumps(record, indent=2))
        return run_dir

    return _make


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """Isolate git and the XDG dirs from the user's; return the git config file.

    Tests may append to the file, e.g. ``url.<local>.insteadOf`` rules.
    """
    gitconfig = tmp_path / 'gitconfig'
    gitconfig.write_text(
        '[user]\n\tname = Bench Test\n\temail = bench@example.org\n'
        '[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n'
    )
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(gitconfig))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg-config'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg-cache'))
    return gitconfig


@pytest.fixture
def bare_remote(tmp_path, git_env):
    """An empty bare repository standing in for the store's remote."""
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    return remote


FAKE_GH = """\
#!{python}
import json, os, shutil, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ['FAKE_GH_LOG'], 'a') as fh:
    fh.write(json.dumps(args) + '\\n')
if args[:2] == ['run', 'download']:
    dest = Path(args[args.index('-D') + 1])
    for artifact in sorted(Path(os.environ['FAKE_GH_ARTIFACTS']).iterdir()):
        shutil.copytree(artifact, dest / artifact.name)
sys.stderr.write(os.environ.get('FAKE_GH_STDERR', ''))
sys.exit(int(os.environ.get('FAKE_GH_EXIT', '0')))
"""


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A ``gh`` first on PATH that logs its argv; ``run download`` copies ``artifacts``.

    Each subdirectory of ``artifacts`` stands for one uploaded artifact, as gh
    extracts it. ``calls()`` returns the argv lists in call order.
    """
    bin_dir = tmp_path / 'fake-bin'
    bin_dir.mkdir()
    script = bin_dir / 'gh'
    script.write_text(FAKE_GH.format(python=sys.executable))
    script.chmod(0o755)
    log, artifacts = tmp_path / 'gh-calls.jsonl', tmp_path / 'gh-artifacts'
    artifacts.mkdir()
    monkeypatch.setenv('PATH', f'{bin_dir}:{os.environ["PATH"]}')
    monkeypatch.setenv('FAKE_GH_LOG', str(log))
    monkeypatch.setenv('FAKE_GH_ARTIFACTS', str(artifacts))

    def calls() -> list[list[str]]:
        return (
            [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        )

    return SimpleNamespace(artifacts=artifacts, calls=calls)
