# proteus-bench

Benchmarking and profiling harness for [PROTEUS](https://github.com/FormingWorlds/PROTEUS).
It runs PROTEUS, records per-phase and per-module timings along with the exact
versions of every module, and publishes a history dashboard.

Status: early development. The interfaces (`docs/interface.md`) are drafted and
the runner works; publishing and the dashboard are not written yet. Design discussion:
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

## Running a benchmark

With the PROTEUS environment activated, call proteus-bench from its own
environment:

```bash
~/.venvs/proteus-bench/bin/proteus-bench run                  # the default suite
~/.venvs/proteus-bench/bin/proteus-bench run --timeout 21600  # kill the run after 6 h
```

`proteus` and `python` come from PATH, so from the PROTEUS environment;
`--proteus-cmd` and `--python` select others.

Each run gets `bench-runs/<run_id>/` with `record.json` (the run record),
`timing.jsonl`, `init_coupler.toml`, `config.toml` and `log.txt`. The proteus
process runs with the BLAS and OpenMP thread counts set to 1, as the proteus CLI
does itself. The run measures the PROTEUS checkout that `proteus` is imported
from; to measure another checkout, activate its environment. That tree must be
clean (no changes to tracked files);
`--allow-failed-checks` runs anyway and marks the record as not comparable.
PROTEUS stops a run itself when CVODE or an environment variable it needs is
missing; `proteus doctor` checks them beforehand.
Suites live in `src/proteus_bench/suites.toml`.
`proteus-bench run --help` lists the other options.

proteus runs with `--offline`, so no download is ever timed and missing data
fails the run. Fetch the data once beforehand: `proteus get reference` covers
the `dummy` suite; the `default` suite also needs the data `proteus get` fetches
for its modules (`stellar`, `spectral`, `surfaces`, and
`interiordata --config-path input/all_options.toml`).

## Development

```bash
pixi run test    # pytest
pixi run lint    # ruff
```

`pixi.lock` is not committed, so `pixi install` resolves the newest versions
`pyproject.toml` allows and two checkouts can get different tool versions.

Tests do not need PROTEUS. They use a fake `proteus` stub
(`python -m proteus_bench.testing.fake_proteus start -c cfg.toml`) that writes
the same output files as a real run.
