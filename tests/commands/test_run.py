"""End-to-end tests of ``proteus-bench run`` against the fake proteus stub.

Contract clauses: a run writes record.json (valid against the schema),
timing.jsonl, init_coupler.toml, config.toml and log.txt into
``<runs-dir>/<run_id>/`` and prints that directory; the suite's iteration cap
and output name reach proteus; failed, crashed, timed-out and fallback-solver
runs are recorded as not comparable with the reason; failing checks stop the
run before anything is written unless --allow-failed-checks; a missing
proteus command is a setup error; thread counts are forced to 1 for the child
and Julia threads are a knob; profiler support is imported only when asked
for, its absence is a clear error, its environment is recorded and its
artifacts are merged into the record.

Timings are derived by hand from ``FAKE_DEFAULTS`` for the 16-iteration cap.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shlex
import sys
import tomllib
from pathlib import Path

import pytest

from proteus_bench import cli, machine, schema, tomlwrite
from proteus_bench.commands.run import make_run_id

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(60)]

DATA = Path(__file__).parents[1] / 'data'
FAKE_CMD = f'{shlex.quote(sys.executable)} -m proteus_bench.testing.fake_proteus'
INIT_S = 1138 + 1 + 420 + 1 + 210 + 2
# 16 x (interior 3.5 + outgas 1 + 0.2 unattributed) + atmos 780 + 15 x 15 + re-solves at 3..15
LOOP_S = 16 * 4.7 + 780 + 15 * 15 + 5 * 140
# The stub writes the helpfile at shutdown only, so a run that stops early has none
NO_HELPFILE = 'runtime_helpfile.csv missing, no physics fingerprint'
EXPECTED_FILES = {'record.json', 'timing.jsonl', 'init_coupler.toml', 'config.toml', 'log.txt'}


@pytest.fixture
def bench(tmp_path, git_repo, cvode_stub, monkeypatch, capsys):
    """Run ``proteus-bench run`` on a fake PROTEUS checkout; return (code, record, output)."""
    for var in (*machine.THREAD_VARS, 'JULIA_NUM_THREADS', 'GITHUB_ACTIONS', 'SLURM_JOB_ID'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(machine, 'julia_version', lambda env: '1.13.0')
    for var, rel in (('FWL_DATA', 'fwl_data'), ('RAD_DIR', 'socrates'), ('FC_DIR', 'fastchem')):
        (tmp_path / rel).mkdir()
        monkeypatch.setenv(var, str(tmp_path / rel))
    (tmp_path / 'socrates' / 'bin').mkdir()
    (tmp_path / 'socrates' / 'bin' / 'radlib.a').write_bytes(b'!<arch>\n')
    root = tmp_path / 'PROTEUS'

    def _run(fake: dict | None = None, *extra: str, cvode: bool = True, cmd: str = FAKE_CMD):
        if not root.exists():
            (root / 'input').mkdir(parents=True)
            text = (DATA / 'all_options.toml').read_text()
            (root / 'input' / 'all_options.toml').write_text(
                text + tomlwrite.dumps({'fake': fake or {}})
            )
            git_repo(root)
        cvode_stub(cvode)
        code = cli.main([
            'run', '--proteus-root', str(root), '--proteus-cmd', cmd,
            '--runs-dir', str(tmp_path / 'runs'), '--machine-label', 'test', *extra,
        ])  # fmt: skip
        captured = capsys.readouterr()
        out = captured.out + captured.err  # setup errors go to stderr, after no run dir
        records = list((tmp_path / 'runs').glob('*/record.json'))
        return code, json.loads(records[0].read_text()) if records else None, out

    return _run


def _run_dir(out: str) -> Path:
    return Path(out.strip().splitlines()[-1])


def test_ok_run_writes_a_complete_valid_record(bench):
    """Exit 0, every file present, schema-valid, comparable, suite cap and timings applied."""
    code, rec, out = bench()
    run_dir = _run_dir(out)
    assert code == 0
    assert {p.name for p in run_dir.iterdir()} == EXPECTED_FILES | {'output'}
    assert schema.shape_problems('record', rec) == []
    assert rec['comparability'] == {'ok': True, 'reasons': []}
    assert rec['outcome']['n_iters'] == 16  # the suite cap, not the stub's default of 4
    assert rec['timings']['phases']['init'] == pytest.approx(INIT_S, abs=1e-6)
    assert rec['timings']['phases']['loop'] == pytest.approx(LOOP_S, abs=1e-6)
    assert 0 <= rec['timings']['startup_s'] <= rec['timings']['wall_s']
    config = tomllib.loads((run_dir / 'config.toml').read_text())
    assert config['params']['out']['path'] == rec['run_id'] == run_dir.name
    assert re.fullmatch(r'\d{8}T\d{6}Z-test-default-[0-9a-f]{4}', rec['run_id'])
    assert '[IT_TIMING] iter=16' in (run_dir / 'log.txt').read_text()
    assert rec['artifacts'] == {
        'spans': 'timing.jsonl',
        'settings': 'init_coupler.toml',
        'config': 'config.toml',
        'log': 'log.txt',
    }
    assert rec['benchmark']['settings']['params.stop.iters.maximum'] == 16
    assert 'params.out.path' not in rec['benchmark']['settings']
    assert rec['env']['julia'] == '1.13.0'
    assert rec['trigger']['adapter'] == 'local'
    assert set(rec['env']['threads'].values()) == {'1'}
    assert [c['name'] for c in rec['checks']] == [
        'cvode_importable',
        'env_dirs',
        'clean_tree',
        'timing_contract',
        'expected_backends',
    ]
    assert (
        cli.main(['validate', str(run_dir / 'record.json'), str(run_dir / 'timing.jsonl')]) == 0
    )


def test_error_run_is_failed(bench):
    """An exception inside PROTEUS: exit 1, status failed with its message."""
    code, rec, _ = bench({'fail': 'error', 'fail_at_iter': 2})
    assert code == 1
    assert rec['outcome']['status'] == 'failed'
    assert rec['outcome']['exit_code'] == 1
    assert rec['outcome']['error'] == 'fake atmosphere solver failure'
    assert rec['comparability']['reasons'] == ['outcome is failed', NO_HELPFILE]
    assert schema.shape_problems('record', rec) == []


def test_killed_run_is_crashed_but_still_readable(bench):
    """A hard kill leaves no run_end: crashed, with the iterations that finished."""
    code, rec, _ = bench({'fail': 'kill', 'fail_at_iter': 3})
    assert code == 1
    assert rec['outcome']['status'] == 'crashed'
    assert rec['outcome']['exit_code'] == 137
    assert [r['iter'] for r in rec['timings']['per_iter']] == [1, 2]
    assert rec['timings']['phases']['loop'] is None  # the loop span never closed
    assert rec['comparability']['reasons'] == ['outcome is crashed', NO_HELPFILE]
    assert schema.shape_problems('record', rec) == []


def test_radau_fallback_is_not_comparable(bench):
    """Aragog reporting Radau while the suite expects CVODE: recorded, but not comparable."""
    code, rec, _ = bench({'aragog_solver': 'radau'})
    assert code == 0  # the run itself worked
    assert rec['backends']['aragog'] == {'solver': 'radau', 'calls': {'radau': 16}}
    assert rec['comparability'] == {
        'ok': False,
        'reasons': ['check expected_backends failed: aragog.solver: expected cvode, got radau'],
    }


def test_failed_check_stops_the_run(bench, tmp_path):
    """Without CVODE the run does not start and nothing is written."""
    code, rec, out = bench(cvode=False)
    assert code == 2
    assert rec is None
    assert not (tmp_path / 'runs').exists()
    assert 'FAIL cvode_importable' in out
    assert '--allow-failed-checks' in out


def test_missing_rad_dir_stops_the_run(bench, monkeypatch, tmp_path):
    """Under pixi run, rc-file exports such as RAD_DIR are absent: say so and stop."""
    monkeypatch.delenv('RAD_DIR')
    code, rec, out = bench()
    assert code == 2
    assert rec is None
    assert 'FAIL env_dirs: RAD_DIR is not set; pass them explicitly' in out
    assert not (tmp_path / 'runs').exists()


def test_failed_check_can_be_overridden(bench):
    """With --allow-failed-checks the run happens and says why it is not comparable."""
    code, rec, _ = bench(None, '--allow-failed-checks', cvode=False)
    assert code == 0
    assert rec['outcome']['status'] == 'ok'
    reasons = rec['comparability']['reasons']
    assert len(reasons) == 1
    assert reasons[0].startswith('check cvode_importable failed')


def test_dirty_checkout_fails_the_clean_tree_check(bench, tmp_path):
    """An edited tracked file in PROTEUS stops the run."""
    bench(cvode=False)  # creates the checkout; stops at the CVODE check
    config = tmp_path / 'PROTEUS' / 'input' / 'all_options.toml'
    config.write_text(config.read_text() + '\n# local edit\n')
    code, rec, out = bench()
    assert code == 2
    assert rec is None
    assert 'FAIL clean_tree' in out
    assert 'ok   cvode_importable' in out


def test_timeout_kills_the_run(bench):
    """A run slower than --timeout is killed and recorded as timeout."""
    code, rec, _ = bench({'sleep_scale': 0.01}, '--timeout', '0.5')  # ~35 s run
    assert code == 1
    assert rec['outcome']['status'] == 'timeout'
    assert rec['outcome']['exit_code'] == -9
    assert rec['timings']['wall_s'] < 10
    assert 'outcome is timeout' in rec['comparability']['reasons']


def test_site_thread_settings_are_overridden_not_refused(bench, monkeypatch):
    """OMP_NUM_THREADS=4 exported by a site becomes 1 for the child; Julia threads are a knob."""
    monkeypatch.setenv('OMP_NUM_THREADS', '4')
    monkeypatch.setenv('JULIA_NUM_THREADS', 'auto')
    code, rec, _ = bench()
    assert code == 0
    assert rec['comparability'] == {'ok': True, 'reasons': []}
    assert rec['env']['threads']['OMP_NUM_THREADS'] == '1'
    assert rec['env']['knobs']['JULIA_NUM_THREADS'] == 'auto'


def test_missing_proteus_command_is_a_setup_error(bench, tmp_path):
    """A command not on PATH exits 2 with a message, before any run directory exists."""
    code, rec, out = bench(cmd='no-such-proteus-binary start')
    assert code == 2
    assert rec is None
    assert not (tmp_path / 'runs').exists()
    assert "'no-such-proteus-binary start': command not found on PATH" in out


def test_missing_profiler_support_is_a_clear_error(bench, monkeypatch):
    """--profiler scalene without proteus_bench.profiling exits 2 before running."""
    monkeypatch.setitem(sys.modules, 'proteus_bench.profiling', None)  # import fails
    code, rec, out = bench(None, '--profiler', 'scalene')
    assert code == 2
    assert rec is None
    assert 'needs proteus_bench.profiling' in out


class FakeProfiling:
    """Stand-in for proteus_bench.profiling with the hook contract of the brief."""

    def __init__(
        self, collect_error: Exception | None = None, wrap_error: Exception | None = None
    ):
        self.wrapped, self.collected = [], []
        self.collect_error, self.wrap_error = collect_error, wrap_error

    def wrap_command(self, profiler, argv, profile_dir):
        if self.wrap_error:
            raise self.wrap_error
        profile_dir.mkdir(parents=True, exist_ok=True)  # as the real hook does
        self.wrapped.append((profiler, argv, profile_dir))
        return argv, {'JAX_DISABLE_JIT': '0'}

    def collect(self, profile_dir, meta):
        self.collected.append((profile_dir, meta))
        if self.collect_error:
            raise self.collect_error
        return {'profile': 'profile/stacks.folded.gz', 'flame': 'profile/flame.html'}


def test_profiler_wraps_the_command_and_its_artifacts_are_recorded(bench, monkeypatch):
    """The hook gets argv and the profile dir; its env is a knob; collect's paths are merged."""
    hook = FakeProfiling()
    monkeypatch.setitem(sys.modules, 'proteus_bench.profiling', hook)
    code, rec, out = bench(None, '--profiler', 'scalene')
    assert code == 0
    profiler, argv, profile_dir = hook.wrapped[0]
    assert profiler == 'scalene'
    assert argv[-3:-1] == ['start', '-c']
    assert profile_dir == _run_dir(out) / 'profile'
    assert hook.collected == [
        (profile_dir, {'run_id': rec['run_id'], 'commit': rec['code']['proteus']['sha']})
    ]
    assert rec['env']['knobs']['JAX_DISABLE_JIT'] == '0'
    assert rec['artifacts']['flame'] == 'profile/flame.html'
    assert rec['artifacts']['profile'] == 'profile/stacks.folded.gz'
    assert rec['comparability']['reasons'] == ['profiled with scalene, which slows the run']


def test_profile_that_cannot_be_collected_still_gives_a_record(bench, monkeypatch):
    """No profiler output after the run: the record is written and says why."""
    hook = FakeProfiling(collect_error=FileNotFoundError('no profiler output in profile'))
    monkeypatch.setitem(sys.modules, 'proteus_bench.profiling', hook)
    code, rec, _ = bench(None, '--profiler', 'py-spy')
    assert code == 0
    assert 'flame' not in rec['artifacts']
    assert (
        'profile not collected: no profiler output in profile'
        in rec['comparability']['reasons']
    )


def test_profiled_run_that_fails_checks_leaves_nothing_behind(bench, monkeypatch, tmp_path):
    """The hook's profile directory is removed again when the checks stop the run."""
    monkeypatch.setitem(sys.modules, 'proteus_bench.profiling', FakeProfiling())
    code, rec, _ = bench(None, '--profiler', 'scalene', cvode=False)
    assert code == 2
    assert rec is None
    assert list((tmp_path / 'runs').iterdir()) == []


def test_unusable_profiler_is_a_setup_error(bench, monkeypatch):
    """A hook that cannot use the profiler here stops the run with its message."""
    hook = FakeProfiling(wrap_error=RuntimeError('scalene is not installed'))
    monkeypatch.setitem(sys.modules, 'proteus_bench.profiling', hook)
    code, rec, out = bench(None, '--profiler', 'scalene')
    assert code == 2
    assert rec is None
    assert '--profiler scalene: scalene is not installed' in out


def test_non_git_root_is_refused(tmp_path, capsys):
    """Provenance needs a git checkout: a plain directory is a setup error."""
    (tmp_path / 'input').mkdir()
    (tmp_path / 'input' / 'all_options.toml').write_text('[params.out]\npath = "x"\n')
    code = cli.main(['run', '--proteus-root', str(tmp_path), '--runs-dir', str(tmp_path / 'r')])
    assert code == 2
    assert 'not the top of a git checkout' in capsys.readouterr().err


def test_run_ids_are_sortable_and_distinct():
    """Time first so ids sort by start; a random suffix separates same-second runs."""
    now = dt.datetime(2026, 9, 25, 3, 10, 0, tzinfo=dt.UTC)
    ids = {make_run_id(now, 'Hábrók vink15', 'default') for _ in range(50)}
    assert len(ids) > 40  # 65536 suffixes: a few collisions at most
    sample = ids.pop()
    assert re.fullmatch(r'20260925T031000Z-h-br-k-vink15-default-[0-9a-f]{4}', sample)
    assert re.fullmatch(
        r'20260925T031000Z-default-[0-9a-f]{4}', make_run_id(now, '!!', 'default')
    )
