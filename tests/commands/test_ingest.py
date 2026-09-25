"""Tests for ``proteus-bench ingest`` (proteus_bench.commands.ingest), with a fake gh.

Contract clauses: gh is called as ``run download <id> -D <staging> [-R repo]``;
every downloaded directory holding record.json is checked and moved to
``<runs-dir>/<run_id>``, and the staging dir is removed; artifacts without a
run directory, a failing check or a failing gh give exit 1 (downloads kept
for inspection); an existing run dir is kept; --publish pushes the runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from proteus_bench import cli, userconfig

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(60)]

RUN_A = '20260925T031000Z-gha-default-a1b2'
RUN_B = '20260925T041000Z-gha-default-c3d4'


@pytest.fixture
def runs_dir(tmp_path, git_env):
    """The runs dir, set through the user config."""
    path = userconfig.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(userconfig.render({'runs': {'dir': str(tmp_path / 'bench-runs')}}))
    return tmp_path / 'bench-runs'


def _upload(make_run_dir, fake_gh, artifact, run_id):
    """Place a run directory as the content of one uploaded artifact."""
    run_dir = make_run_dir(run_id, root=fake_gh.artifacts)
    run_dir.rename(fake_gh.artifacts / artifact)
    return fake_gh.artifacts / artifact


def test_ingest_moves_run_dirs(make_run_dir, fake_gh, runs_dir, capsys):
    """Two artifacts become two run dirs named by run id; other artifacts are ignored."""
    _upload(make_run_dir, fake_gh, 'bench-record', RUN_A)
    _upload(make_run_dir, fake_gh, 'bench-record-2', RUN_B)
    (fake_gh.artifacts / 'coverage').mkdir()
    (fake_gh.artifacts / 'coverage' / 'coverage.xml').write_text('<coverage/>')
    code = cli.main(['ingest', '--gha-run', '123456', '--repo', 'egpbos/PROTEUS'])
    assert code == 0
    staging = runs_dir / '.gha-123456'
    assert fake_gh.calls() == [
        ['run', 'download', '123456', '-D', str(staging), '-R', 'egpbos/PROTEUS']
    ]
    assert sorted(p.name for p in runs_dir.iterdir()) == [RUN_A, RUN_B]
    record = json.loads((runs_dir / RUN_A / 'record.json').read_text())
    assert record['run_id'] == RUN_A
    assert (runs_dir / RUN_A / 'timing.jsonl').is_file()
    assert f'ingested {runs_dir / RUN_B}' in capsys.readouterr().out


def test_existing_run_dir_is_kept(make_run_dir, fake_gh, runs_dir, capsys):
    """Ingesting the same GHA run twice keeps the first copy and still succeeds."""
    _upload(make_run_dir, fake_gh, 'bench-record', RUN_A)
    assert cli.main(['ingest', '--gha-run', '7']) == 0
    (runs_dir / RUN_A / 'log.txt').write_text('local notes\n')
    assert cli.main(['ingest', '--gha-run', '7']) == 0
    assert 'kept it and dropped the downloaded copy' in capsys.readouterr().out
    assert (runs_dir / RUN_A / 'log.txt').read_text() == 'local notes\n'
    assert fake_gh.calls()[1] == ['run', 'download', '7', '-D', str(runs_dir / '.gha-7')]


def test_no_run_dir_or_bad_run_fails(make_run_dir, fake_gh, runs_dir, capsys):
    """Artifacts without record.json, or with an invalid run, give exit 1 and keep the files."""
    (fake_gh.artifacts / 'logs').mkdir()
    (fake_gh.artifacts / 'logs' / 'x.txt').write_text('x')
    assert cli.main(['ingest', '--gha-run', '8']) == 1
    assert 'no run directory' in capsys.readouterr().out
    bad = _upload(make_run_dir, fake_gh, 'bench-record', RUN_A)
    (bad / 'log.txt').unlink()
    assert cli.main(['ingest', '--gha-run', '9']) == 1
    out = capsys.readouterr().out
    assert 'missing log.txt' in out
    assert 'downloaded files kept in' in out
    assert (runs_dir / '.gha-9' / 'bench-record' / 'record.json').is_file()
    assert not (runs_dir / RUN_A).exists()
    # Fixed upstream and downloaded again: the kept staging dir is replaced, not merged
    (bad / 'log.txt').write_text('log\n')
    assert cli.main(['ingest', '--gha-run', '9']) == 0
    assert (runs_dir / RUN_A / 'log.txt').read_text() == 'log\n'
    assert not (runs_dir / '.gha-9').exists()


def test_gh_failure_and_missing_gh(fake_gh, runs_dir, monkeypatch, capsys):
    """gh's error is shown with its exit code; without gh the command says where to get it."""
    monkeypatch.setenv('FAKE_GH_EXIT', '1')
    monkeypatch.setenv('FAKE_GH_STDERR', 'no valid artifacts found to download')
    assert cli.main(['ingest', '--gha-run', '10']) == 1
    assert 'failed (exit 1): no valid artifacts found' in capsys.readouterr().out
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, 'which', lambda name: None if name == 'gh' else real_which(name)
    )
    assert cli.main(['ingest', '--gha-run', '10']) == 1
    assert 'https://cli.github.com' in capsys.readouterr().out
    assert len(fake_gh.calls()) == 1


def test_invalid_config_is_reported(fake_gh, runs_dir, capsys):
    """A broken user config gives exit 1 with the file and key, before gh runs."""
    userconfig.config_path().write_text('[runs]\ndirectory = "/data"\n')
    assert cli.main(['ingest', '--gha-run', '12']) == 1
    out = capsys.readouterr().out
    assert f'{userconfig.config_path()}: unknown key runs.directory' in out
    assert fake_gh.calls() == []


def test_ingest_and_publish(make_run_dir, fake_gh, runs_dir, bare_remote, capsys):
    """--publish pushes the ingested run to the store and marks it published."""
    _upload(make_run_dir, fake_gh, 'bench-record', RUN_A)
    code = cli.main(['ingest', '--gha-run', '11', '--publish', '--remote', str(bare_remote)])
    assert code == 0
    tip = subprocess.run(
        ['git', 'rev-parse', 'results'], cwd=bare_remote, capture_output=True, text=True
    ).stdout.strip()
    assert (runs_dir / RUN_A / '.published').read_text() == tip + '\n'
    assert f'published {RUN_A}' in capsys.readouterr().out
