"""Tests for ``proteus-bench init`` (proteus_bench.commands.init).

Contract clauses: writes the config at the XDG path with the given options
active and loadable; repeated --sbatch-option collects a list; an existing
file is left untouched without --force and replaced with it.
"""

from __future__ import annotations

import pytest

from proteus_bench import cli, userconfig

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'cfg'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    return tmp_path / 'cfg' / 'proteus-bench' / 'config.toml'


def test_init_writes_options_as_values(xdg, capsys):
    """Given options load back; others keep their defaults."""
    code = cli.main(
        [
            'init',
            '--machine-label', 'vink15',
            '--machine-class', 'habrok-vink',
            '--remote', 'git@github.com:egpbos/proteus-bench.git',
            '--sbatch-option=--partition=regular',  # '=' because the value starts with --
            '--sbatch-option=--constraint=vink',
        ]
    )  # fmt: skip
    assert code == 0 and f'wrote {xdg}' in capsys.readouterr().out
    cfg = userconfig.load()
    assert cfg['machine'] == {'label': 'vink15', 'class': 'habrok-vink'}
    assert cfg['store']['remote'] == 'git@github.com:egpbos/proteus-bench.git'
    assert cfg['slurm']['sbatch_options'] == ['--partition=regular', '--constraint=vink']
    assert cfg['store']['branch'] == 'results'
    assert '# env_activation = ""' in xdg.read_text()


def test_existing_file_needs_force(xdg, capsys):
    """Without --force an existing config is kept byte for byte; with it, replaced."""
    xdg.parent.mkdir(parents=True)
    xdg.write_text('[runs]\ndir = "/scratch/runs"\n')
    assert cli.main(['init', '--runs-dir', '/data/runs']) == 1
    assert 'use --force' in capsys.readouterr().out
    assert xdg.read_text() == '[runs]\ndir = "/scratch/runs"\n'
    assert cli.main(['init', '--runs-dir', '/data/runs', '--force']) == 0
    assert userconfig.load()['runs']['dir'] == '/data/runs'
    assert userconfig.load()['machine']['label'] == ''
