"""Tests for proteus_bench.machine: machine fingerprint and run environment.

Contract clauses: slugs are lower-case dash-separated; /proc fields parse
(Linux branch) and memory converts kB to GiB; the default machine class is
os-arch-cpu; the SOCRATES build mode follows the flag strings of PROTEUS's
get_socrates.sh, preferring bin/Mk_cmd over make/Mk_cmd; the env manager is
detected with pixi before conda; env records threads as strings and knobs.
"""

from __future__ import annotations

import platform

import pytest

from proteus_bench import machine

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

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
    assert section['label'] == 'gha' and section['n_cpus'] >= 1
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
    """Threads are strings ('unset' when absent); knobs include the profiler."""
    monkeypatch.setattr(machine, 'julia_version', lambda: None)
    env = {'OMP_NUM_THREADS': '1', 'JAX_DISABLE_JIT': '0', 'RAD_DIR': str(tmp_path)}
    section = machine.env_section(env, '3.12.14', 'scalene')
    assert section['threads']['OMP_NUM_THREADS'] == '1'
    assert section['threads']['JULIA_NUM_THREADS'] == 'unset'
    assert section['knobs']['JAX_DISABLE_JIT'] == '0'
    assert section['knobs']['PROTEUS_PS_CACHE_DIR'] is None
    assert section['knobs']['profiler'] == 'scalene'
    assert 'julia' not in section and section['socrates_build'] == 'unknown'
