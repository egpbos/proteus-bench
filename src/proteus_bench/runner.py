"""Spawn the proteus process, tee its output to a log, and measure it.

The child runs in its own process group, so a timeout kills everything it
started (Julia, profilers). Resource usage comes from ``os.wait4``, which
covers the child and the descendants it reaped.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

MIB = 2**20


@dataclass
class ProcessResult:
    exit_code: int  # negative signal number when killed by a signal
    timed_out: bool
    started_at: dt.datetime  # UTC wall clock just before spawn
    wall_s: float  # monotonic, spawn to reap
    rusage: dict  # max_rss_mb, user_s, sys_s


def max_rss_mb(ru_maxrss: int, system: str) -> float:
    """Peak resident set size in MiB; ``ru_maxrss`` is bytes on macOS, KiB on Linux."""
    return ru_maxrss / MIB if system == 'Darwin' else ru_maxrss / 1024


def _kill_group(pid: int, fired: threading.Event) -> None:
    fired.set()
    with contextlib.suppress(ProcessLookupError):  # the group exited just before the kill
        os.killpg(pid, signal.SIGKILL)


def _tee(stream, log) -> None:
    """Copy the child's combined output to the log file and to our stdout."""
    for line in iter(stream.readline, b''):
        log.write(line)
        log.flush()
        sys.stdout.write(line.decode(errors='replace'))
        sys.stdout.flush()


def spawn(argv: list[str], cwd: Path, env: dict, log_path: Path, timeout_s: float | None):
    """Run ``argv`` to completion (or until ``timeout_s``) and return a ProcessResult."""
    started_at = dt.datetime.now(dt.UTC)
    t_start = time.monotonic()
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )  # fmt: skip
    fired = threading.Event()
    timer = threading.Timer(timeout_s, _kill_group, (proc.pid, fired)) if timeout_s else None
    if timer:
        timer.start()
    try:
        with open(log_path, 'wb') as log:
            _tee(proc.stdout, log)
        _, status, usage = os.wait4(proc.pid, 0)
    except KeyboardInterrupt:
        _kill_group(proc.pid, fired)  # the child's own session gets no terminal SIGINT
        os.wait4(proc.pid, 0)
        raise
    finally:
        if timer:
            timer.cancel()
        proc.stdout.close()
    wall_s = time.monotonic() - t_start
    proc.returncode = os.waitstatus_to_exitcode(status)  # reaped here, not by Popen
    rusage = {
        'max_rss_mb': round(max_rss_mb(usage.ru_maxrss, os.uname().sysname), 1),
        'user_s': round(usage.ru_utime, 3),
        'sys_s': round(usage.ru_stime, 3),
    }
    return ProcessResult(proc.returncode, fired.is_set(), started_at, wall_s, rusage)
