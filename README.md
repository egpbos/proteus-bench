# proteus-bench

Benchmarking and profiling harness for [PROTEUS](https://github.com/FormingWorlds/PROTEUS).
It runs PROTEUS, records per-phase and per-module timings along with the exact
versions of every module, and publishes a history dashboard.

Status: early development. The interfaces (`docs/interface.md`) are drafted and
the runner works; publishing and the dashboard are not written yet. Design discussion:
[FormingWorlds/PROTEUS#916](https://github.com/FormingWorlds/PROTEUS/issues/916).

## Running a benchmark

Inside an activated PROTEUS environment, from the PROTEUS checkout:

```bash
proteus-bench run                  # the default suite from suites.toml
proteus-bench run --timeout 21600  # kill the run after 6 h
```

Each run gets `bench-runs/<run_id>/` with `record.json` (the run record),
`timing.jsonl`, `init_coupler.toml`, `config.toml` and `log.txt`. Environment
checks (CVODE importable, single-threaded, clean PROTEUS tree) must pass;
`--allow-failed-checks` runs anyway and marks the record as not comparable.
`proteus-bench run --help` lists the other options.

## Development

```bash
pixi run test    # pytest
pixi run lint    # ruff
```

The harness itself has no runtime dependencies. `jsonschema` is optional and
enables shape validation in `proteus-bench validate`.

Tests do not need PROTEUS. They use a fake `proteus` stub
(`python -m proteus_bench.testing.fake_proteus start -c cfg.toml`) that writes
the same output files as a real run.
