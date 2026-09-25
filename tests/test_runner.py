"""Tests for proteus_bench.runner: spawning, logging and measuring the proteus process.

Contract clauses: ru_maxrss is normalised to MiB from bytes (macOS) or KiB
(Linux); combined stdout and stderr reach both the log and our stdout; the
exit code is the child's, negative for a signal; a timeout kills the whole
process group and is flagged; CPU times match the kernel's accounting of
reaped children and peak memory reflects what the child touched.
"""

from __future__ import annotations

import os
import resource
import sys
import time

import pytest

from proteus_bench.runner import max_rss_mb, spawn

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(60)]


@pytest.mark.unit
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
    assert result.started_at.tzinfo is not None and result.wall_s > 0


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
