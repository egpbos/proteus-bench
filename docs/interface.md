# Interfaces, version 1

Two files cross component boundaries:

- `timing.jsonl`: written by PROTEUS, read by proteus-bench.
- The run record: written by proteus-bench, read by the dashboard and analysis.

The results store (last section) holds both, one set of files per run.

The JSON Schemas in `src/proteus_bench/schemas/` fix the shape of each file.
`proteus_bench.timing.check_events` checks the rules for `timing.jsonl` that a
schema cannot express. The `validate` command runs both:

```bash
proteus-bench validate <output>/timing.jsonl record.json
```

The values in `examples/` are illustrative. The timing example comes from the fake
stub, and the record example is derived from it.

## timing.jsonl

### When and where

PROTEUS writes `<output>/timing.jsonl` when `PROTEUS_TIMING=1` is set, the same
switch that enables the `[IT_TIMING]` log lines, which stay as they are. The file
holds one JSON object per line. Each line is flushed as it is written, so a run
that crashes still leaves every event up to the crash. A killed process can leave
a torn final line, which readers drop.

### Clock

- `t0` is the number of seconds on a monotonic clock since the `run_start` event,
  for example `time.perf_counter()` minus its value at `run_start`.
- `dur` is a duration in seconds.
- `run_start.wall` gives the UTC wall-clock time at `t0 = 0`, so the harness can
  measure start-up time: from process spawn to `run_start`, which covers imports
  and Julia start-up.
- Round values to microseconds. Checks allow 1 ms of slack.

### Events

| `ev` | Required fields | Meaning |
|---|---|---|
| `run_start` | `wall`, `pid` | First line. Optional: `proteus_version`, `config_path`, `resume`. |
| `backend` | `t0`, `submodule`, `key`, `value` | A choice made once per run, e.g. `aragog` / `solver` / `cvode` or `radau`. It records the solver actually used, not the one configured. |
| `span` | `id`, `parent`, `name`, `t0`, `dur` | A timed interval, written when it closes, so children are written before their parents. |
| `run_end` | `t0`, `status` | Last line when the run exits through Python. `status` is `ok`, `error` or `interrupted`. Optional: `termination` (which stop criterion ended the run), `n_iters`, `time_yr`, `error`. |

Every event also has `"v": 1`.

### Span tree rules

1. **Ids.** `id` is unique within a run and assigned when the span opens.
   `parent` is the id of the enclosing span, or `null` for a root.
2. **Roots are phases.** Root spans are named `setup`, `init`, `loop` or
   `shutdown`. Each appears at most once, and they don't overlap.
3. **Containment.** A child lies within its parent's time window.
4. **Iterations.** Main-loop iterations are spans named `iter` directly under
   `loop`. Only these spans carry the `iter` field, and the numbers increase.
   Iteration metadata such as `init_stage` or `time_yr` goes in `extra`.
5. **Attribution.** A span with a `component` has its time attributed to that
   component. At most one span on any root-to-leaf path carries a `component`,
   and attributed spans never overlap in time. As a result, the attributed time
   plus the unattributed remainder (`other`) always equals the phase total.
   Group spans such as `equilibrate` carry no component. Their children do.
6. **Submodule and backend.** An attributed span also names the `submodule`
   actually called (`zalmoxis`, `aragog`, `agni`, `calliope`, `mors`, `dummy`,
   and so on) and, where it applies, the `backend` path taken for this call
   (`numpy` or `jax` for Zalmoxis).
7. **Failure.** A span left through an exception has `"ok": false`. Without a
   `run_end` event, the run crashed. Spans that were still open are then absent,
   and references to them are allowed.
8. **Detail.** Sub-detail inside an attributed span, such as JIT compile vs
   solve, goes in child spans without a `component`, or in `extra`.

### Component names

Use the `[IT_TIMING]` bucket names: `interior`, `structure`, `orbit`, `stellar`,
`escape`, `outgas`, `atmos`, `chem`, `write`, `plots`, `archive`. Also `data`,
for downloads. Consumers must accept names they don't know, since new names are
an additive change.

### Versioning

- Adding optional fields, event kinds or component names keeps `"v": 1`.
- Renaming or removing anything, or changing a meaning, bumps `v`.
- proteus-bench reads the current version and the previous one.

### Expected span layout for PROTEUS (target for the emitter)

```
init
  structure            component=structure submodule=zalmoxis backend=numpy|jax
  equilibrate
    outgas             component=outgas    submodule=calliope
    structure          component=structure submodule=zalmoxis
  star                 component=stellar   submodule=mors
  orbit                component=orbit
loop
  iter (iter=N)
    interior           component=interior  submodule=aragog|spider|dummy
    structure          component=structure (only on re-solve iterations)
    orbit, stellar, escape, outgas, atmos, chem, write, plots, archive
shutdown
```

The fake stub in `proteus_bench.testing.fake_proteus` produces this layout.
Its `TimingWriter` class is a reference emitter.

## Run record

One JSON file per run. See `record-v1.schema.json` for every field. Key points:

- Raw spans are kept in a sidecar file (`artifacts.spans`). Every statistic can
  be recomputed from them, so better analysis applies to old runs too.
- `benchmark.settings` is the flattened resolved config read from PROTEUS's
  `init_coupler.toml`. `settings_hash` covers it with per-run fields such as
  paths removed. `lineage` is `default` for the default-settings series.
- `timings.components` has one row per (phase, component, submodule, backend).
  The phase's unattributed remainder is a row with `component = other`, so the
  rows for a phase add up to its entry in `timings.phases`.
- `comparability` says whether the run may enter baselines, and if not, why.
  Examples: a failed environment check, an unexpected backend such as the Radau
  fallback, or a physics fingerprint that doesn't match the series.

## Results store

`proteus-bench publish` adds run directories to a git branch, `results` by
default, of the proteus-bench repository. `proteus_bench.store` implements this
section. `ARTIFACTS` is the artifact contract in code, and `store_path` maps each
file to its place in the store.

### Artifacts

A run directory holds `record.json` plus the files below. A record's `artifacts`
may name only these keys, each with exactly this run-directory path. When the
run is published, `artifacts` lists every one of these files that exists, with
its store path.

| Key | Run directory | Store | Required |
|---|---|---|---|
| `spans` | `timing.jsonl` | `spans/<YYYY>/<run_id>.timing.jsonl.gz` | if `outcome.status` is `ok` |
| `settings` | `init_coupler.toml` | `settings/<hex>.toml` | if `outcome.status` is `ok` |
| `config` | `config.toml` | `configs/<YYYY>/<run_id>.toml` | always |
| `log` | `log.txt` | `logs/<YYYY>/<run_id>.log.gz` | always |
| `profile` | `profile/stacks.folded.gz` | `profiles/<YYYY>/<run_id>/stacks.folded.gz` | no |
| `flame` | `profile/flame.html` | `profiles/<YYYY>/<run_id>/flame.html` | no |

The record goes to `records/<YYYY>/<run_id>.json`. `<YYYY>` is the first four
characters of the run id, which is the year of its UTC start. `<hex>` is the
settings hash without its `sha256:` prefix. Every other file under `profile/`,
such as raw profiler output, is copied to `profiles/<YYYY>/<run_id>/` as well.
`timing.jsonl` and `log.txt` are gzipped with the gzip header time set to 0,
and all other files are copied unchanged.

### Rules

1. **Add only.** A publish adds files and never changes existing ones, so
   publishers on different machines don't conflict. A run already in the store
   is skipped. The dashboard, the index and all statistics are built from these
   files; none of them is committed.
2. **One settings file per hash.** `settings/<hex>.toml` is the first published
   `init_coupler.toml` with that hash. Runs with the same hash differ only in
   per-run keys, such as the output path. The hash of `init_coupler.toml`,
   computed with `proteus_bench.settings`, must equal the record's
   `benchmark.settings_hash`.
3. **Run directories are untrusted.** The store feeds a public site. Symbolic
   links are refused, and `run_id` and `settings_hash` must fully match their
   schema patterns because they become file names.
4. **Resolved settings.** Only runs that finished `ok` and have `settings` and
   `config` artifacts define a lineage's settings. A run that failed before PROTEUS wrote
   `init_coupler.toml` is stored, but its hash is not used for lineage.
5. **Carry-over records.** A carry-over run (decision D4) has
   `benchmark.lineage` set to the previous settings hash and
   `benchmark.carry_over_of` set to the default run that moved to the new
   settings. One carry-over is run per move, where a move is identified by
   series, previous hash and new hash.
6. **Publication marker.** After a publish, each run directory has a
   `.published` file with the store commit that holds the run.

The branch starts as an orphan with a short `README.md` that points here.
