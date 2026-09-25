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
    assert code == 0
    assert f'wrote {xdg}' in capsys.readouterr().out
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


def test_awkward_values_round_trip(xdg):
    """Quotes, backslashes, control characters, DEL and non-BMP text load back unchanged."""
    snippet = 'eval "$(conda shell.bash hook)"\n. setenv.sh\t# C:\\path \x00\x7f 😀'
    assert cli.main(['init', '--env-activation', snippet, '--machine-label', 'habrök']) == 0
    cfg = userconfig.load()
    assert cfg['slurm']['env_activation'] == snippet
    assert cfg['machine']['label'] == 'habrök'
    assert '\x7f' not in xdg.read_text()  # written as an escape, since TOML forbids raw DEL


def test_invalid_value_is_refused_before_writing(xdg, capsys):
    """A relative --cache-dir would make publishing reset the working directory: not written."""
    assert cli.main(['init', '--cache-dir', 'store-cache']) == 1
    out = capsys.readouterr().out
    assert "store.cache_dir must be an absolute path, found 'store-cache'" in out
    assert out.endswith('not written\n')
    assert not xdg.exists()
