"""Machine fingerprint and environment of a run: CPU, memory, threads, knobs, build flags.

Runs compare only within one machine class, so the class must separate CPU
models; the default class is ``<os>-<arch>-<cpu model>`` as a slug.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

# Thread counts the proteus CLI sets to 1 at import (PROTEUS src/proteus/cli.py).
# The harness sets them to 1 for the child as well, so the benchmark stays
# single-threaded whatever the site exports.
THREAD_VARS = (
    'OMP_NUM_THREADS',
    'MKL_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'VECLIB_MAXIMUM_THREADS',
)
# Variables that change timing but not physics (caches, JIT, diagnostics, and
# Julia threads, which PROTEUS leaves alone)
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
# Flag spellings from PROTEUS tools/get_socrates.sh: SOCRATES_PORTABLE_FLAGS=1
# rewrites '-Ofast -march=native' to this portable form, and these patterns
# mark a host-specific build.
PORTABLE_FLAGS = '-O2 -fno-fast-math'
NONPORTABLE_FLAGS = re.compile(r'-march=|-mcpu=native|-Ofast|-xHost|-ax[A-Z]')
GIB = 2**30
JULIA_TIMEOUT_S = 10  # a juliaup launcher may be slow to start, never minutes


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


def cpu_model() -> str | None:
    """CPU brand string, or None where the OS does not report one (ARM Linux)."""
    if platform.system() == 'Darwin':
        return _sysctl('machdep.cpu.brand_string')
    return proc_field('/proc/cpuinfo', 'model name')


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
    """The record's ``machine`` section.

    Raises ``ValueError`` when the CPU model is unknown and no ``machine_class``
    is given: a default class would then lump different CPUs together.
    """
    cpu = cpu_model()
    if cpu is None and not machine_class:
        raise ValueError(
            'the CPU model cannot be read on this host; '
            'pass --machine-class to name the comparability group'
        )
    section = {
        'label': label,
        'class': machine_class or slug(f'{platform.system()}-{platform.machine()}-{cpu}'),
        'host': platform.node(),
        'cpu_model': cpu or 'unknown',
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


def julia_version(env: dict) -> str | None:
    """Version of the ``julia`` on the child's PATH, e.g. '1.13.0'.

    None when there is no julia; 'unknown' when it does not answer in time or
    its output is not ``julia version X``.
    """
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
    """``<project>/pixi.lock`` of a pixi environment at ``<project>/.pixi/envs/<name>``.

    pixi 0.79 sets ``CONDA_PREFIX`` to that environment directory under
    ``pixi run``; the target interpreter's ``sys.prefix`` is the same path.
    """
    env_dir = Path(prefix)
    if env_dir.parent.name != 'envs' or env_dir.parent.parent.name != '.pixi':
        return None
    lock = env_dir.parents[2] / 'pixi.lock'
    return lock if lock.is_file() else None


def env_section(env: dict, env_report: dict, profiler: str, profiler_env: dict) -> dict:
    """The record's ``env`` section for the environment given to the proteus process.

    ``env_report`` is the target interpreter's introspection report (``python``,
    ``prefix``). ``profiler_env`` holds the variables the profiler hook added;
    they are knobs. A pixi environment also records its lock file's sha256, since
    each checkout resolves its own lock and pixi.lock is not committed.
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
