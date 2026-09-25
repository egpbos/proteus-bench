"""Tests for proteus_bench.machine: machine fingerprint and run environment.

Contract clauses: slugs are lower-case dash-separated; /proc fields parse
(Linux branch) and memory converts kB to GiB; the default machine class is
os-arch-cpu; the SOCRATES build mode follows the flag strings of PROTEUS's
get_socrates.sh, preferring bin/Mk_cmd over make/Mk_cmd; the env manager is
detected with pixi before conda; env records threads as strings and knobs
(Julia threads, profiler variables); an unknown CPU needs --machine-class;
the Julia version is read with a timeout and is 'unknown' when unreadable.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

import pytest

from proteus_bench import machine

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

# sha256 of b'version: 6\n' from `printf 'version: 6\n' | shasum -a 256`
PIXI_LOCK_SHA256 = '543a04374255b067aa56895cf75ca341ee416ed2bbc29e7bdd2ac1a6a8026642'

# Configure output line as written by SOCRATES, and the portable rewrite from
# PROTEUS tools/get_socrates.sh ('-Ofast -march=native' -> '-O2 -fno-fast-math')
NATIVE_LINE = 'FORTCOMP = gfortran -Ofast -march=native -fopenmp\n'
PORTABLE_LINE = 'FORTCOMP = gfortran -O2 -fno-fast-math -fopenmp\n'


def test_slug():
    """Runs of non-alphanumerics become one dash, with no dash at either end."""
    assert machine.slug('Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz') == (
        'intel-r-xeon-r-platinum-8370c-cpu-2-80ghz'
    )
    assert machine.slug('  Apple M5 Pro ') == 'apple-m5-pro'
    assert machine.slug('***') == ''


def test_proc_fields_and_linux_memory(tmp_path, monkeypatch):
    """The first matching /proc line wins; MemTotal in kB becomes GiB."""
    cpuinfo = tmp_path / 'cpuinfo'
    cpuinfo.write_text(
        'processor\t: 0\nmodel name\t: AMD EPYC 7763 64-Core Processor\n'
        'processor\t: 1\nmodel name\t: other\n'
    )
    assert machine.proc_field(str(cpuinfo), 'model name') == 'AMD EPYC 7763 64-Core Processor'
    assert machine.proc_field(str(cpuinfo), 'flags') is None
    assert machine.proc_field(str(tmp_path / 'absent'), 'model name') is None
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(machine, 'proc_field', lambda path, field: '16777216 kB')
    assert machine.mem_gb() == pytest.approx(16.0, rel=1e-12)  # 2**24 KiB = 16 GiB


def test_machine_section_defaults_class_to_os_arch_cpu(monkeypatch):
    """Without --machine-class the class is a slug of OS, architecture and CPU model."""
    monkeypatch.setattr(machine, 'cpu_model', lambda: 'AMD EPYC 7763 64-Core Processor')
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(platform, 'machine', lambda: 'x86_64')
    section = machine.machine_section('gha', None)
    assert section['class'] == 'linux-x86-64-amd-epyc-7763-64-core-processor'
    assert section['label'] == 'gha'
    assert section['n_cpus'] >= 1
    assert (
        machine.machine_section('gha', 'gha-ubuntu-epyc7763')['class'] == 'gha-ubuntu-epyc7763'
    )


def test_socrates_build_mode(tmp_path):
    """native / portable / unknown from Mk_cmd; bin/Mk_cmd is what make read, so it wins."""
    (tmp_path / 'make').mkdir()
    (tmp_path / 'make' / 'Mk_cmd').write_text(PORTABLE_LINE)
    assert machine.socrates_build(str(tmp_path)) == 'portable'
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'Mk_cmd').write_text(NATIVE_LINE)  # per-host template replaced it
    assert machine.socrates_build(str(tmp_path)) == 'native'
    (tmp_path / 'bin' / 'Mk_cmd').write_text('FORTCOMP = gfortran -O3\n')
    assert machine.socrates_build(str(tmp_path)) == 'unknown'
    assert machine.socrates_build(None) == 'unknown'
    assert machine.socrates_build(str(tmp_path / 'absent')) == 'unknown'


def test_env_manager_detection():
    """pixi sets CONDA_PREFIX too, so pixi variables take precedence."""
    assert (
        machine.env_manager({'PIXI_PROJECT_ROOT': '/p', 'CONDA_PREFIX': '/p/.pixi'}) == 'pixi'
    )
    assert machine.env_manager({'CONDA_PREFIX': '/opt/conda/envs/proteus'}) == 'conda'
    assert machine.env_manager({'VIRTUAL_ENV': '/v'}) == 'venv'
    assert machine.env_manager({}) == 'unknown'


def test_env_section(monkeypatch, tmp_path):
    """Threads are strings ('unset' when absent); knobs hold Julia threads and profiler env."""
    monkeypatch.setattr(machine, 'julia_version', lambda env: None)
    env = {'OMP_NUM_THREADS': '1', 'JULIA_NUM_THREADS': 'auto', 'RAD_DIR': str(tmp_path)}
    report = {'python': '3.12.14', 'prefix': '/opt/conda/envs/proteus'}
    section = machine.env_section(env, report, 'scalene', {'JAX_DISABLE_JIT': '0'})
    assert section['threads']['OMP_NUM_THREADS'] == '1'
    assert section['threads']['MKL_NUM_THREADS'] == 'unset'
    assert 'JULIA_NUM_THREADS' not in section['threads']  # PROTEUS does not set it
    assert section['knobs']['JULIA_NUM_THREADS'] == 'auto'
    assert section['knobs']['JAX_DISABLE_JIT'] == '0'  # from the profiler hook only
    assert section['knobs']['PROTEUS_PS_CACHE_DIR'] is None
    assert section['knobs']['profiler'] == 'scalene'
    assert 'julia' not in section
    assert section['socrates_build'] == 'unknown'
    assert 'pixi_lock_sha256' not in section['knobs']  # not a pixi environment


def test_pixi_environment_records_its_lock_hash(monkeypatch, tmp_path):
    """Under pixi, the sha256 of <project>/pixi.lock is a knob; a missing lock is None."""
    monkeypatch.setattr(machine, 'julia_version', lambda env: None)
    prefix = tmp_path / 'PROTEUS' / '.pixi' / 'envs' / 'default'
    prefix.mkdir(parents=True)
    (tmp_path / 'PROTEUS' / 'pixi.lock').write_bytes(b'version: 6\n')
    env = {'PIXI_PROJECT_ROOT': str(tmp_path / 'PROTEUS'), 'CONDA_PREFIX': str(prefix)}
    report = {'python': '3.12.14', 'prefix': str(prefix)}
    knobs = machine.env_section(env, report, 'none', {})['knobs']
    assert knobs['pixi_lock_sha256'] == PIXI_LOCK_SHA256
    # Only <project>/.pixi/envs/<name> is an environment, not other .pixi subdirectories
    assert machine.pixi_lock(str(tmp_path / 'PROTEUS' / '.pixi' / 'cache' / 'x')) is None
    (tmp_path / 'PROTEUS' / 'pixi.lock').unlink()
    assert machine.env_section(env, report, 'none', {})['knobs']['pixi_lock_sha256'] is None
    assert machine.pixi_lock(str(tmp_path / 'conda' / 'envs' / 'proteus')) is None


def test_unknown_cpu_needs_an_explicit_class(monkeypatch):
    """ARM Linux reports no model name: refuse a default class instead of 'unknown'."""
    monkeypatch.setattr(machine, 'cpu_model', lambda: None)
    with pytest.raises(ValueError, match='--machine-class'):
        machine.machine_section('arm-box', None)
    section = machine.machine_section('arm-box', 'habrok-arm')
    assert section['class'] == 'habrok-arm'
    assert section['cpu_model'] == 'unknown'


def test_cpu_model_per_os(monkeypatch):
    """macOS asks sysctl; Linux reads /proc/cpuinfo; a missing sysctl gives None."""
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(machine, 'proc_field', lambda path, field: f'{path}:{field}')
    assert machine.cpu_model() == '/proc/cpuinfo:model name'
    monkeypatch.setattr(platform, 'system', lambda: 'Darwin')

    def no_sysctl(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, 'run', no_sysctl)
    assert machine.cpu_model() is None
    assert machine.mem_gb() is None


def test_usable_cpus_prefers_the_affinity_mask(monkeypatch):
    """A Slurm allocation of 3 CPUs on a 64-CPU node reads 3; without affinity, cpu_count."""
    monkeypatch.setattr(os, 'sched_getaffinity', lambda pid: {0, 5, 9}, raising=False)
    monkeypatch.setattr(os, 'cpu_count', lambda: 64)
    assert machine.usable_cpus() == 3
    monkeypatch.delattr(os, 'sched_getaffinity')
    assert machine.usable_cpus() == 64
    monkeypatch.setattr(os, 'cpu_count', lambda: None)
    assert machine.usable_cpus() == 1


def test_julia_version_reads_or_says_unknown(monkeypatch):
    """'julia version 1.13.0' gives 1.13.0; a hang or odd output gives 'unknown'."""
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: '/opt/bin/julia')
    answers = iter([
        subprocess.CompletedProcess([], 0, 'julia version 1.13.0\n', ''),
        subprocess.CompletedProcess([], 0, 'Installing Julia 1.13.0 ...\n', ''),
        subprocess.CompletedProcess([], 0, 'juliaup: channel missing\n', ''),
        subprocess.CompletedProcess([], 1, '', 'error'),
    ])  # fmt: skip
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(kwargs['timeout'])
        return next(answers)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert machine.julia_version({}) == '1.13.0'
    assert machine.julia_version({}) == 'unknown'
    assert machine.julia_version({}) == 'unknown'  # three words, but not 'julia version X'
    assert machine.julia_version({}) == 'unknown'
    assert seen == [machine.JULIA_TIMEOUT_S] * 4

    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])

    monkeypatch.setattr(subprocess, 'run', hang)
    assert machine.julia_version({}) == 'unknown'
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: None)
    assert machine.julia_version({'PATH': '/nowhere'}) is None
