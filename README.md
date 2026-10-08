# proteus-bench

Benchmarking and profiling harness for [PROTEUS](https://github.com/FormingWorlds/PROTEUS).
It runs PROTEUS, records per-phase and per-module timings along with the exact
versions of every module, and publishes a history dashboard.

Status: early development. The interfaces (`docs/interface.md`) are drafted, and
the runner and publishing work; the dashboard is not written yet. Design discussion:
[FormingWorlds/PROTEUS#916](https://github.com/FormingWorlds/PROTEUS/issues/916).

## Installation

proteus-bench is installed twice, for two kinds of use:

- Runs happen inside the PROTEUS environment. With that environment active,
  install the bare package; its only dependency is the small `tomli-w`, so it
  changes almost nothing else there:

  ```bash
  pip install "proteus-bench @ git+https://github.com/egpbos/proteus-bench"
  ```

- Everything else (analysis, reports, publishing, validation) runs from an
  environment of its own, usually with both extras:

  ```bash
  python -m venv ~/.venvs/proteus-bench
  ~/.venvs/proteus-bench/bin/pip install "proteus-bench[analysis,publish] @ git+https://github.com/egpbos/proteus-bench"
  ```

`analysis` (asv's step detector) is needed by `analyse` and the dashboard
build; `publish` (jsonschema and rfc3339-validator) by `publish` and `lineage-check`,
which refuse records they cannot check against the schema.
`validate` uses jsonschema when it is there and checks the timing rules either
way.

## Running a benchmark

With the PROTEUS environment active:

```bash
proteus-bench run                  # the default suite
proteus-bench run --timeout 21600  # kill the run after 6 h
```

The run measures the PROTEUS checkout that the `proteus` command on PATH
imports, in that command's environment; to measure another checkout, activate
its environment. PROTEUS must be installed from a git checkout
(`pip install -e`); a PROTEUS installed from a wheel is refused.

Each run gets `bench-runs/<run_id>/` with `record.json` (the run record),
`timing.jsonl`, `init_coupler.toml`, `config.toml` and `log.txt`. The proteus
process runs with the BLAS and OpenMP thread counts set to 1, as the proteus CLI
does itself. The PROTEUS tree must be clean (no changes to tracked files);
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

## Publishing

From the separate proteus-bench environment:

```bash
proteus-bench publish bench-runs/<run_id>
```

This adds the run to the `results` branch of the store repository. For a
store on GitHub it also starts the dashboard build with `gh`, or prints the
command when `gh` is missing. Runs made in GitHub Actions are uploaded there as one
artifact per run directory; download them first:

```bash
gh run download <gha-run-id> -R FormingWorlds/PROTEUS -D bench-runs/gha-<gha-run-id>
proteus-bench publish bench-runs/gha-<gha-run-id>/*
```

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
