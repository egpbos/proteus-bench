# proteus-bench

Benchmarking and profiling harness for [PROTEUS](https://github.com/FormingWorlds/PROTEUS).
It runs PROTEUS, records per-phase and per-module timings along with the exact
versions of every module, and publishes a history dashboard.

Status: early development. The interfaces (`docs/interface.md`) are drafted, and
the runner, publishing and the dashboard (`proteus-bench report --store DIR --out
SITE`, deployed by `.github/workflows/pages.yml`) work. The dashboard is a static
site: Jinja2 pages styled by the PROTEUS design tokens, with Plotly charts that
follow the reader's light or dark preference. Design discussion:
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
  environment of its own, usually with all three extras:

  ```bash
  python -m venv ~/.venvs/proteus-bench
  ~/.venvs/proteus-bench/bin/pip install "proteus-bench[analysis,publish,report] @ git+https://github.com/egpbos/proteus-bench"
  ```

`analysis` (asv's step detector) is needed by `analyse` and the dashboard
build; `report` (Jinja2, Plotly and proteus-plotly, the PROTEUS Plotly theme) by
the dashboard build; `publish` (jsonschema and rfc3339-validator) by `publish` and `lineage-check`,
which refuse records they cannot check against the schema.
`validate` checks a record against the schema when jsonschema is there, and
otherwise only that the file is JSON.

## Running a benchmark

With the PROTEUS environment active:

```bash
proteus-bench run --machine-label my-laptop                  # the default suite
proteus-bench run --machine-label my-laptop --timeout 21600  # kill the run after 6 h
```

The machine label names the machine in run ids and on the dashboard; it is
required outside GitHub Actions.

The run measures the PROTEUS checkout that the `proteus` command on PATH
imports, in that command's environment; to measure another checkout, activate
its environment. PROTEUS must be installed from a git checkout
(`pip install -e`); a PROTEUS installed from a wheel is refused.
The record places the run on the PROTEUS history at the newest `origin/main`
commit the checkout contains, which is how the series pages order runs by
commit; fetch in the PROTEUS checkout first so that ref is current.

Each run gets `bench-runs/<run_id>/` with `record.json` (the run record),
`init_coupler.toml`, `config.toml` and `log.txt`. The timings come from the
lines PROTEUS logs under `PROTEUS_TIMING=1` (docs/interface.md, "Timing log
lines"). The proteus
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

### Older PROTEUS commits

PROTEUS logs all the timing lines since FormingWorlds/PROTEUS#983. To benchmark
an older commit (back to #678), patch it first, in a clean checkout with the
full history:

```bash
proteus-bench prepare <commit> --proteus path/to/PROTEUS
```

This leaves the checkout on branch `bench/<sha>`, one commit with the patches
on top of `<commit>`. Install that commit as usual (`pip install -e`, with its own module versions), then run.

To run an old commit as it ran when it was merged, use the modules and data
of that time:

- Python modules: `pip install --uploaded-prior-to <merge time> -e .` picks
  the versions that were current at the merge.
- AGNI and SOCRATES: check out the refs the commit pins in `pyproject.toml`
  (`python tools/_module_pins.py agni ref`, likewise `socrates`). A pinned
  AGNI may refuse some Julia versions; its `Project.toml` lists the ones it
  accepts.
- SOCRATES pins before c3296586 build with GCC 16.2, but GCC 16.2 hides their
  C-bound symbols from the Julia wrapper. Before `./configure` and
  `./build_code`, apply the three-line export fix of c3296586 in the SOCRATES
  checkout:
  `patch -N -p1 < path/to/proteus-bench/docs/socrates-gcc16-export.patch`. Pins that
  already hold one of its hunks skip it, and `patch` then exits 1. The record
  marks the patched SOCRATES as dirty.
- AGNI stores `RAD_DIR` and `FWL_DATA` in its Julia precompile cache. Give
  each commit its own `JULIA_DEPOT_PATH`, and use it for the AGNI build, the
  data download and the runs alike.
- Data: commits from before fwl-io name their own Zenodo records in
  `src/proteus/utils/data.py` and read an older directory layout. Give each
  commit its own `FWL_DATA` and download into it with that commit's code.

## Publishing

From the separate proteus-bench environment:

```bash
proteus-bench publish bench-runs/<run_id>
```

This adds the run to the `results` branch of the store repository, which is
public. A record holds no host or user name, and the run replaces the home
directory, host name and user name in `log.txt` with `~`, `<host>` and `<user>`.
The machine label is published as given, so choose one that does not identify you. For a
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
