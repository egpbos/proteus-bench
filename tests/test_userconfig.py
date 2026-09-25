"""Tests for proteus_bench.userconfig: defaults, overlay, validation and rendering.

Contract clauses: without a file the defaults apply and follow the XDG dirs
(relative XDG values ignored); a file overlays only the keys it sets; unknown
tables, unknown keys, wrong types, invalid TOML and a relative or empty
store.cache_dir raise ValueError naming the
file; ``render`` output loads back to exactly the values given, with every
other key commented out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proteus_bench import userconfig

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'cfg'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    return tmp_path


def test_defaults_follow_xdg_dirs(xdg, monkeypatch):
    """No file: the store branch is 'results' and paths sit under the XDG dirs."""
    cfg = userconfig.load()
    assert userconfig.config_path() == xdg / 'cfg' / 'proteus-bench' / 'config.toml'
    assert cfg['store']['branch'] == 'results'
    assert cfg['store']['cache_dir'] == str(xdg / 'cache' / 'proteus-bench' / 'store')
    assert cfg['store']['remote'] == ''
    assert cfg['slurm']['sbatch_options'] == []
    # A relative XDG value is invalid per the spec and falls back to the home dir
    monkeypatch.setenv('XDG_CACHE_HOME', 'relative/cache')
    monkeypatch.setenv('HOME', str(xdg / 'home'))
    expected = xdg / 'home' / '.cache' / 'proteus-bench' / 'store'
    assert userconfig.load()['store']['cache_dir'] == str(expected)


def test_file_overlays_only_the_keys_it_sets(xdg):
    """A file setting two keys changes those and keeps every other default."""
    path = userconfig.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        '[store]\nremote = "git@github.com:egpbos/proteus-bench.git"\n'
        '[slurm]\nsbatch_options = ["--partition=regular", "--constraint=vink"]\n'
    )
    cfg = userconfig.load()
    assert cfg['store']['remote'] == 'git@github.com:egpbos/proteus-bench.git'
    assert cfg['slurm']['sbatch_options'] == ['--partition=regular', '--constraint=vink']
    assert cfg['store']['branch'] == 'results'
    assert cfg['runs']['dir'] == 'bench-runs'


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('[stor]\nremote = "x"\n', 'unknown table [stor]'),
        ('[store]\nremot = "x"\n', 'unknown key store.remot'),
        ('[store]\nbranch = 3\n', 'store.branch must be a string'),
        ('[slurm]\nsbatch_options = "--partition=regular"\n', 'must be a list of strings'),
        ('[slurm]\nsbatch_options = ["--nodes=1", 2]\n', 'must be a list of strings'),
        ('machine = "x"\n', 'machine must be a table'),
        ('[store\n', 'not valid TOML'),
        ('[store]\ncache_dir = ""\n', "store.cache_dir must be an absolute path, found ''"),
        ('[store]\ncache_dir = "cache/store"\n', 'must be an absolute path'),
    ],
)
def test_invalid_files_raise_with_the_path(xdg, text, expected):
    """Each kind of mistake raises ValueError naming the file and the offending key."""
    path = xdg / 'custom.toml'
    path.write_text(text)
    with pytest.raises(ValueError, match='custom.toml') as err:
        userconfig.load(path)
    assert expected in str(err.value)


def test_render_round_trips_and_comments_the_rest(xdg):
    """Rendered values load back unchanged, including quotes, newlines and non-ASCII."""
    values = {
        'machine': {'label': 'habrök "vink15"'},
        'slurm': {
            'env_activation': 'eval "$(conda shell.bash hook)"\n. setenv.sh\n',
            'sbatch_options': ['--partition=regular', "--comment=it's"],
        },
    }
    text = userconfig.render(values)
    path = Path(xdg / 'rendered.toml')
    path.write_text(text)
    cfg = userconfig.load(path)
    assert cfg['machine']['label'] == 'habrök "vink15"'
    assert cfg['slurm'] == values['slurm']
    assert cfg == userconfig.defaults() | {
        'machine': {'label': 'habrök "vink15"', 'class': ''},
        'slurm': values['slurm'],
    }
    assert '# branch = "results"' in text  # unset keys stay visible but inactive
    assert '\nbranch' not in text
