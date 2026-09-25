"""Machine fingerprint and environment of a run: CPU, memory, threads, knobs, build flags.

Runs compare only within one machine class, so the class must separate CPU
models; the default class is ``<os>-<arch>-<cpu model>`` as a slug.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

# Thread counts; the benchmark is defined single-threaded
THREAD_VARS = (
    'OMP_NUM_THREADS',
    'MKL_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'VECLIB_MAXIMUM_THREADS',
    'JULIA_NUM_THREADS',
)
# Variables that change timing but not physics (caches, JIT, diagnostics)
KNOB_VARS = (
    'PROTEUS_PS_CACHE_DIR',
    'PROTEUS_CI_NIGHTLY',
    'JAX_COMPILATION_CACHE_DIR',
    'JAX_DISABLE_JIT',
    'JAX_ENABLE_X64',
    'JAX_PLATFORMS',
    'XLA_FLAGS',
)
# Flag spellings from PROTEUS tools/get_socrates.sh: SOCRATES_PORTABLE_FLAGS=1
# rewrites '-Ofast -march=native' to this portable form, and these patterns
# mark a host-specific build.
PORTABLE_FLAGS = '-O2 -fno-fast-math'
NONPORTABLE_FLAGS = re.compile(r'-march=|-mcpu=native|-Ofast|-xHost|-ax[A-Z]')
GIB = 2**30


def slug(text: str) -> str:
    """Lower-case ``text`` with runs of other characters turned into single dashes."""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def _sysctl(name: str) -> str | None:
    try:
        proc = subprocess.run(['sysctl', '-n', name], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def proc_field(path: str, field: str) -> str | None:
    """Value of the first ``field : value`` line of a /proc file, if any."""
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    match = re.search(rf'^{field}\s*:\s*(.+)$', text, re.MULTILINE)
    return match.group(1).strip() if match else None


def cpu_model() -> str:
    if platform.system() == 'Darwin':
        return _sysctl('machdep.cpu.brand_string') or 'unknown'
    return proc_field('/proc/cpuinfo', 'model name') or 'unknown'


def mem_gb() -> float | None:
    """Physical memory in GiB."""
    if platform.system() == 'Darwin':
        size = _sysctl('hw.memsize')
        return int(size) / GIB if size else None
    total = proc_field('/proc/meminfo', 'MemTotal')  # e.g. '16318540 kB'
    return int(total.split()[0]) * 1024 / GIB if total else None


def usable_cpus() -> int:
    """CPUs this process may run on (the Slurm or cgroup allocation where visible)."""
    if hasattr(os, 'sched_getaffinity'):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def machine_section(label: str, machine_class: str | None) -> dict:
    cpu = cpu_model()
    section = {
        'label': label,
        'class': machine_class or slug(f'{platform.system()}-{platform.machine()}-{cpu}'),
        'host': platform.node(),
        'cpu_model': cpu,
        'n_cpus': usable_cpus(),
        'os': f'{platform.system()}-{platform.release()}',
        'arch': platform.machine(),
    }
    mem = mem_gb()
    if mem is not None:
        section['mem_gb'] = round(mem, 2)
    return section


def socrates_build(rad_dir: str | None) -> str:
    """``portable``, ``native`` or ``unknown`` from the SOCRATES Mk_cmd flags.

    ``bin/Mk_cmd`` is the file make read; get_socrates.sh notes that per-host
    templates may replace it after ``make/Mk_cmd`` was written, so it wins.
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


def julia_version() -> str | None:
    """Version of the ``julia`` on PATH, e.g. '1.13.0', or None."""
    julia = shutil.which('julia')
    if julia is None:
        return None
    proc = subprocess.run([julia, '--version'], capture_output=True, text=True, timeout=120)
    return proc.stdout.split()[-1] if proc.returncode == 0 and proc.stdout else None


def env_section(env: dict, python_version: str, profiler: str) -> dict:
    """The record's ``env`` section for the environment given to the proteus process."""
    section = {
        'manager': env_manager(env),
        'python': python_version,
        'threads': {var: env.get(var, 'unset') for var in THREAD_VARS},
        'knobs': {var: env.get(var) for var in KNOB_VARS} | {'profiler': profiler},
        'socrates_build': socrates_build(env.get('RAD_DIR')),
    }
    julia = julia_version()
    if julia:
        section['julia'] = julia
    return section
