"""Machine fingerprint and environment of a run.

Runs compare only within one machine class, so the default class,
``<os>-<arch>-<cpu model>``, must separate CPU models.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

# Set to 1 for the child, as the proteus CLI does at import (src/proteus/cli.py)
THREAD_VARS = (
    'OMP_NUM_THREADS',
    'MKL_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'VECLIB_MAXIMUM_THREADS',
)
# Variables that change timing but not physics
KNOB_VARS = (
    'JULIA_NUM_THREADS',
    'PROTEUS_PS_CACHE_DIR',
    'PROTEUS_CI_NIGHTLY',
    'JAX_COMPILATION_CACHE_DIR',
    'JAX_DISABLE_JIT',
    'JAX_ENABLE_X64',
    'JAX_PLATFORMS',
    'XLA_FLAGS',
)
# Flag spellings from PROTEUS tools/get_socrates.sh
PORTABLE_FLAGS = '-O2 -fno-fast-math'
NONPORTABLE_FLAGS = re.compile(r'-march=|-mcpu=native|-Ofast|-xHost|-ax[A-Z]')
GIB = 2**30
JULIA_TIMEOUT_S = 10


def slug(text: str) -> str:
    """Lower-case ``text`` with runs of other characters turned into single dashes."""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def _sysctl(name: str) -> str:
    proc = subprocess.run(['sysctl', '-n', name], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def proc_field(path: str, field: str) -> str | None:
    """Value of the first ``field : value`` line of a /proc file, if any."""
    match = re.search(rf'^{field}\s*:\s*(.+)$', Path(path).read_text(), re.MULTILINE)
    return match.group(1).strip() if match else None


def cpu_model() -> str | None:
    """CPU brand string; None on ARM Linux, whose /proc/cpuinfo has no model name."""
    if platform.system() == 'Darwin':
        return _sysctl('machdep.cpu.brand_string')
    return proc_field('/proc/cpuinfo', 'model name')


def mem_gb() -> float:
    """Physical memory in GiB."""
    if platform.system() == 'Darwin':
        return int(_sysctl('hw.memsize')) / GIB
    return int(proc_field('/proc/meminfo', 'MemTotal').split()[0]) * 1024 / GIB  # 'N kB'


def usable_cpus() -> int:
    """CPUs this process may run on (the Slurm or cgroup allocation where visible)."""
    if hasattr(os, 'sched_getaffinity'):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def machine_section(label: str, machine_class: str | None) -> dict:
    """The record's ``machine`` section.

    Raises ``ValueError`` when the CPU model is unknown and no ``machine_class``
    is given, since a default class would lump different CPUs together.
    """
    cpu = cpu_model()
    if cpu is None and not machine_class:
        raise ValueError(
            'the CPU model cannot be read on this host; '
            'pass --machine-class to name the comparability group'
        )
    return {
        'label': label,
        'class': machine_class or slug(f'{platform.system()}-{platform.machine()}-{cpu}'),
        'host': platform.node(),
        'cpu_model': cpu or 'unknown',
        'n_cpus': usable_cpus(),
        'os': f'{platform.system()}-{platform.release()}',
        'arch': platform.machine(),
        'mem_gb': round(mem_gb(), 2),
    }


def socrates_build(rad_dir: str | None) -> str:
    """``portable``, ``native`` or ``unknown`` from the SOCRATES Mk_cmd flags.

    ``bin/Mk_cmd`` wins: it is what make read, and a per-host template may have
    replaced it after ``make/Mk_cmd`` was written (get_socrates.sh).
    """
    if not rad_dir:
        return 'unknown'
    for rel in ('bin/Mk_cmd', 'make/Mk_cmd'):
        path = Path(rad_dir) / rel
        if path.is_file():
            flags = path.read_text()
            if NONPORTABLE_FLAGS.search(flags):
                return 'native'
            return 'portable' if PORTABLE_FLAGS in flags else 'unknown'
    return 'unknown'


def env_manager(env: dict) -> str:
    # pixi also sets CONDA_PREFIX, so it is tested first
    if any(k.startswith('PIXI_') for k in env):
        return 'pixi'
    if env.get('CONDA_PREFIX'):
        return 'conda'
    return 'venv' if env.get('VIRTUAL_ENV') else 'unknown'


def julia_version(env: dict) -> str | None:
    """Version of the ``julia`` on the child's PATH; None without one, else 'unknown'
    when it does not answer ``julia version X`` in time."""
    julia = shutil.which('julia', path=env.get('PATH'))
    if julia is None:
        return None
    try:
        proc = subprocess.run(
            [julia, '--version'], capture_output=True, text=True, timeout=JULIA_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return 'unknown'
    words = proc.stdout.split()
    ok = proc.returncode == 0 and words[:2] == ['julia', 'version'] and len(words) == 3
    return words[2] if ok else 'unknown'


def pixi_lock(prefix: str) -> Path | None:
    """``<project>/pixi.lock`` for an environment ``prefix`` of ``<project>/.pixi/envs/<name>``."""
    env_dir = Path(prefix)
    if env_dir.parent.name != 'envs' or env_dir.parent.parent.name != '.pixi':
        return None
    lock = env_dir.parents[2] / 'pixi.lock'
    return lock if lock.is_file() else None


def env_section(env: dict, env_report: dict, profiler: str, profiler_env: dict) -> dict:
    """The record's ``env`` section for the environment given to the proteus process.

    pixi environments record the sha256 of their pixi.lock: PROTEUS does not
    commit it, so each checkout resolves its own.
    """
    manager = env_manager(env)
    knobs = {var: env.get(var) for var in KNOB_VARS} | profiler_env | {'profiler': profiler}
    if manager == 'pixi':
        lock = pixi_lock(env_report['prefix'])
        knobs['pixi_lock_sha256'] = (
            hashlib.sha256(lock.read_bytes()).hexdigest() if lock else None
        )
    section = {
        'manager': manager,
        'python': env_report['python'],
        'threads': {var: env.get(var, 'unset') for var in THREAD_VARS},
        'knobs': knobs,
        'socrates_build': socrates_build(env.get('RAD_DIR')),
    }
    julia = julia_version(env)
    if julia:
        section['julia'] = julia
    return section
