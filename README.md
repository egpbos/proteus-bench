# proteus-bench

Benchmarking and profiling harness for [PROTEUS](https://github.com/FormingWorlds/PROTEUS).
It runs PROTEUS, records per-phase and per-module timings along with the exact
versions of every module, and publishes a history dashboard.

Status: early development. The interfaces (`docs/interface.md`) are drafted; the
runner, publishing and dashboard are not written yet. Design discussion:
[FormingWorlds/PROTEUS#916](https://github.com/FormingWorlds/PROTEUS/issues/916).

## Installation

Install proteus-bench in its own environment, not in the PROTEUS one, so its
dependencies never change the environment being measured:

```bash
python -m venv ~/.venvs/proteus-bench
~/.venvs/proteus-bench/bin/pip install "proteus-bench[analysis] @ git+https://github.com/egpbos/proteus-bench"
```

The harness itself has no runtime dependencies. The `analysis` extra adds
[asv](https://github.com/airspeed-velocity/asv), whose step detector
`proteus-bench analyse` uses. `jsonschema` is optional and enables shape
validation in `proteus-bench validate`.

## Development

```bash
pixi run test    # pytest
pixi run lint    # ruff
```

Tests do not need PROTEUS. They use a fake `proteus` stub
(`python -m proteus_bench.testing.fake_proteus start -c cfg.toml`) that writes
the same output files as a real run.
