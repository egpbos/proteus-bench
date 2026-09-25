"""Tests for proteus_bench.runner: spawning, logging and measuring the proteus process.

Contract clauses: ru_maxrss is normalised to MiB from bytes (macOS) or KiB
(Linux); combined stdout and stderr reach both the log and our stdout; the
exit code is the child's, negative for a signal; a timeout kills the whole
process group and is flagged, but only when the kill ended the run; descendants
left holding the output pipe are killed when the child exits; an interrupt
kills the run and propagates; CPU times match the kernel's accounting of
reaped children and peak memory reflects what the child touched.
"""

from __future__ import annotations

import os
import resource
import signal
import sys
import time

import pytest

from proteus_bench.runner import max_rss_mb, spawn, timed_out

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(60)]


def test_max_rss_units_per_platform():
    """2450 MiB is 2569011200 bytes on macOS and 2508800 KiB on Linux."""
    assert max_rss_mb(2_569_011_200, 'Darwin') == pytest.approx(2450.0, rel=1e-12)
    assert max_rss_mb(2_508_800, 'Linux') == pytest.approx(2450.0, rel=1e-12)
    # Guard: reading macOS bytes as KiB would claim 1024 times more memory
    assert max_rss_mb(2_569_011_200, 'Linux') == pytest.approx(2450.0 * 1024, rel=1e-12)
    assert max_rss_mb(0, 'Darwin') == pytest.approx(0.0, abs=0)


def test_output_is_logged_and_echoed_and_exit_code_kept(tmp_path, capsys):
    """Both streams land in log.txt and on stdout; a non-zero exit code is returned as is."""
    script = "import sys; print('to stdout'); print('to stderr', file=sys.stderr); sys.exit(3)"
    log = tmp_path / 'log.txt'
    result = spawn([sys.executable, '-c', script], tmp_path, dict(os.environ), log, None)
    assert result.exit_code == 3
    assert result.timed_out is False
    assert set(log.read_text().splitlines()) == {'to stdout', 'to stderr'}
    assert 'to stdout' in capsys.readouterr().out
    assert result.started_at.tzinfo is not None
    assert result.wall_s > 0


def test_timeout_kills_the_process_group(tmp_path):
    """A slow child and its own child are both killed; the run is flagged as timed out."""
    pidfile = tmp_path / 'grandchild.pid'
    script = (
        'import subprocess, sys, time\n'
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f'open({str(pidfile)!r}, "w").write(str(p.pid))\n'
        'time.sleep(60)\n'
    )
    result = spawn(
        [sys.executable, '-c', script], tmp_path, dict(os.environ), tmp_path / 'l', 1.0
    )
    assert result.timed_out is True
    assert result.exit_code == -9  # SIGKILL
    assert result.wall_s < 10  # well below the 60 s the child wanted
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 5  # orphans are reaped by init shortly after the kill
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(grandchild)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_rusage_matches_kernel_accounting(tmp_path):
    """User CPU equals the RUSAGE_CHILDREN delta; peak RSS covers a 200 MiB buffer."""
    script = (
        "buf = b'x' * (200 * 2**20)\n"  # written, so resident, unlike a zeroed calloc
        'n = 0\n'
        'for i in range(3_000_000): n += i\n'
    )
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = spawn(
        [sys.executable, '-c', script], tmp_path, dict(os.environ), tmp_path / 'l', None
    )
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    assert result.exit_code == 0
    assert result.rusage['user_s'] == pytest.approx(after.ru_utime - before.ru_utime, abs=2e-3)
    assert result.rusage['user_s'] > 0.05  # the loop costs ~0.1 s or more of CPU
    assert 200 <= result.rusage['max_rss_mb'] < 600  # not 1024x off in either direction


def test_timeout_flag_needs_the_timer_kill():
    """A timer that fires just after a normal exit does not turn the run into a timeout."""
    assert timed_out(True, -signal.SIGKILL) is True
    assert timed_out(True, 0) is False  # exited on its own; the timer fired before cancel
    assert timed_out(True, 1) is False
    assert timed_out(False, -signal.SIGKILL) is False  # killed by something else


def test_descendant_holding_the_pipe_does_not_block(tmp_path):
    """Without --timeout, a background grandchild keeping stdout open is killed at exit."""
    pidfile = tmp_path / 'grandchild.pid'
    script = (
        'import subprocess, sys\n'
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f'open({str(pidfile)!r}, "w").write(str(p.pid))\n'
        "print('parent done')\n"
    )
    t0 = time.monotonic()
    result = spawn(
        [sys.executable, '-c', script], tmp_path, dict(os.environ), tmp_path / 'l', None
    )
    assert time.monotonic() - t0 < 20  # not the 60 s the grandchild sleeps
    assert result.exit_code == 0
    assert result.timed_out is False
    assert (tmp_path / 'l').read_text() == 'parent done\n'
    _wait_dead(int(pidfile.read_text()))


def _wait_dead(pid: int) -> None:
    deadline = time.monotonic() + 5  # orphans are reaped by init shortly after the kill
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_interrupt_kills_the_run_and_propagates(tmp_path, monkeypatch):
    """Ctrl-C does not reach the child's own session, so the harness kills it and re-raises."""
    real_wait4 = os.wait4
    pids = []

    def interrupted_once(pid, options):
        if not pids:
            pids.append(pid)
            raise KeyboardInterrupt
        return real_wait4(pid, options)

    monkeypatch.setattr(os, 'wait4', interrupted_once)
    argv = [sys.executable, '-c', 'import time; time.sleep(60)']
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        spawn(argv, tmp_path, dict(os.environ), tmp_path / 'l', None)
    assert time.monotonic() - t0 < 20
    assert len(pids) == 1
    assert not _alive(pids[0])  # reaped by the harness after the kill
