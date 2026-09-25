"""Shared fixtures: fake proteus runs, run directories, isolated git stores, fake gh."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from proteus_bench.settings import comparable, flatten, settings_hash
from proteus_bench.testing import fake_proteus
from proteus_bench.timing import read_events

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


def _toml(nested: dict, prefix: str = '') -> str:
    """TOML text for nested tables of strings, numbers and booleans."""
    lines = [f'{k} = {json.dumps(v)}' for k, v in nested.items() if not isinstance(v, dict)]
    for key, value in nested.items():
        if isinstance(value, dict):
            name = f'{prefix}.{key}' if prefix else key
            lines += ['', f'[{name}]', _toml(value, name)]
    return '\n'.join(lines)


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
        (run_dir / 'init_coupler.toml').write_text(_toml(nested) + '\n')
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
        record['artifacts'] = fields.pop(
            'artifacts', {'spans': 'timing.jsonl', 'log': 'log.txt'}
        )
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
