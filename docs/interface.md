# Interfaces, version 1

Two files cross component boundaries:

- `timing.jsonl`: written by PROTEUS, read by proteus-bench.
- The run record: written by proteus-bench, read by the dashboard and analysis.

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
  `init_coupler.toml`, without the per-run keys (`proteus_bench.settings.PER_RUN_KEYS`:
  output name, resume and offline flags). `settings_hash` is
  `proteus_bench.settings.settings_hash(settings)`. `lineage` is `default` for the
  default-settings series.
- `timings.components` has one row per (phase, component, submodule, backend).
  The phase's unattributed remainder is a row with `component = other`, so the
  rows for a phase add up to its entry in `timings.phases`.
- `comparability` says whether the run may enter baselines, and if not, why.
  Examples: a failed environment check, an unexpected backend such as the Radau
  fallback, or a physics fingerprint that doesn't match the series.
