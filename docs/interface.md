# Interfaces, version 1

Two things cross component boundaries:

- PROTEUS's timing log lines: written by PROTEUS, read by proteus-bench.
- The run record: written by proteus-bench, read by the dashboard and analysis.

The results store (last section) holds each run's record with its log and
configs.

The JSON Schema in `src/proteus_bench/schemas/record-v1.schema.json` fixes the
shape of a record, and `proteus-bench validate record.json` checks a file
against it. The values in `examples/record.json` are illustrative.

## Timing log lines

Under `PROTEUS_TIMING=1`, PROTEUS logs where the wall time of a run goes:

| Line | Since | Meaning |
|---|---|---|
| `[IT_TIMING] iter=N <component>=<s> ... other=<s> total=<s>` | #678 | one per main-loop iteration |
| `[INIT_TIMING] <step>=<s> ... other=<s> total=<s>` | FormingWorlds/PROTEUS#983 | once, just before the loop: data, structure, equilibrate, stellar, orbit |
| `[BACKEND] <submodule> <key>=<value>` | FormingWorlds/PROTEUS#983 | a module's choice, e.g. `aragog solver=cvode` |

Seconds have three decimals, and in each line the steps plus `other` add up to
`total`. A PROTEUS test pins this form, since a change to it breaks readers. A
key the reader does not know becomes a component of its own, so a new step or
module needs no reader change.

proteus-bench builds a record's timings from these lines
(`proteus_bench.logtiming`). The runner notes when each line arrives; an
iteration starts at its arrival minus its `total`, the loop runs from the first
iteration's start to the last line, init is everything before and shutdown
everything after. With an `[INIT_TIMING]` line, init is broken into its steps,
`startup_s` is the time before PROTEUS's init began, and init's `other` row
holds the rest (start-up included). `[BACKEND]` lines fill `backends` and the
`expected_backends` check runs; without them it is left out. A `timing_log`
check needs at least one `[IT_TIMING]` line and no unreadable one (a torn,
garbled or non-finite line). Components carry no submodule, and every iteration counts in the
per-iteration medians, including the first ones (init stage).
PROTEUS before #777 ignores `PROTEUS_OUTPUT_PATH`; its outputs are read from
`<checkout>/output/<run_id>`.

### Component names

PROTEUS's bucket names: `interior`, `structure`, `orbit`, `stellar`, `escape`,
`outgas`, `atmos`, `chem`, `write`, `plots`, `archive`, and `data` for the data
check. Consumers must accept names they don't know, since new names are an
additive change.

## Run record

One JSON file per run. See `record-v1.schema.json` for every field. Key points:

- The run's log is stored with it (`artifacts.log`). Its timing lines hold the
  seconds per step, so component statistics can be recomputed from old runs;
  the phase split also needs the arrival times, which only the record keeps.
- `benchmark.settings` is the flattened resolved config read from PROTEUS's
  `init_coupler.toml`, without the per-run keys (`proteus_bench.settings.PER_RUN_KEYS`:
  output name, resume and offline flags). `settings_hash` is
  `proteus_bench.settings.settings_hash(settings)`. `lineage` is `default` for the
  default-settings series.
- `timings.components` has one row per (phase, component, submodule, backend).
  The phase's unattributed remainder is a row with `component = other`, so the
  rows for a phase add up to its entry in `timings.phases`.
- `code.proteus.base` places the run on the PROTEUS history: the newest
  `origin/main` commit that the measured HEAD contains, so a run of main plus
  local patches counts as the main commit it builds on. It is absent when the
  checkout has no `origin/main`; fetch before running, or the base is the last
  fetched main commit.
- `comparability` says whether the run may enter baselines, and if not, why.
  Examples: uncommitted changes in PROTEUS, an unexpected backend such as the Radau
  fallback, or a physics fingerprint that doesn't match the series.

## Analysis output (internal)

`proteus-bench analyse <store>` (or `proteus_bench.analysis.analyse(records)`)
turns run records into series for the dashboard. Unlike the two files above it
is not a versioned interface: its only consumer is the dashboard in this
repository, and the format may change together with the dashboard. It is
derived data, recomputed from the records every time, and has no JSON Schema.
The `schema` field (`proteus-bench-analysis/1`) only names the format.

- `series[]`, one per (benchmark, lineage, machine class, metric):
  - `key`: `benchmark`, `lineage`, `machine_class`, `metric`. Metrics: `total`,
    each non-null phase (`setup`, `init`, `loop`, `shutdown`), `n_iters`,
    `loop_per_iter_median`, `init.<component>`,
    `loop.<component>.per_iter_median`, `submodule.<name>.total`.
  - `unit`: `s`, or `count` for `n_iters`.
  - `points[]`: `run_id`, `commit`, `base` (the record's `code.proteus.base`
    or `null`), `time` (the run's `trigger.started_at`), `value`, `comparable`,
    `settings_hash`; ordered by time, then run id.
  - `baseline`: `median`, `sigma` (1.4826 x MAD), `n` over the last 10
    comparable points of the latest settings segment, or `null` with fewer
    than 3.
  - `flags[]`: `run_id`, `kind` (`regression` or `improvement`), `delta_rel`,
    `delta_abs`, `threshold_rel`, `confirmed`. A run flags when its distance
    from the baseline median exceeds max(3 sigma, 5 % of the median), so 5 % is
    the smallest threshold, and also exceeds 1 s (0 for `n_iters`).
    `delta_rel` and `threshold_rel` are `null` when the baseline median is 0.
    `confirmed` is `null` while there is no later comparable run with the same
    settings, `true` when that next run exceeds the same threshold in the same
    direction, and `false` otherwise.
  - `steps[]`: `after_run_id` (first run of the new level), `before`, `after`
    (level medians, without excursion runs of fewer than 3 runs), `delta_rel`
    (relative to `before`).
  - `boundaries[]`: `run_id` (first comparable run with the new settings),
    `reason` (`settings_changed`), `changed_keys`. Non-comparable runs make no
    boundary.

The full rules are stated in the `proteus_bench.analysis` module docstring.

## Profiles

A profiled run (`proteus-bench run --profiler scalene|py-spy`) keeps its profile
in the run directory:

```
profile/
  scalene-profile.json   scalene's own output (scalene runs), or
  py-spy.folded          py-spy's raw output (py-spy runs); exactly one of the two
  stacks.folded.gz       folded stacks derived from it (gzip, mtime 0)
  flame.html             flame-graph page built from the folded stacks
```

The record's `artifacts.profile` and `artifacts.flame` point at the last two.

`stacks.folded.gz` is UTF-8 text, one line per distinct stack:
`frame;frame;...;frame count`. Frames run from the outermost call to the leaf;
`count` is a non-negative integer number of samples, separated from the stack by
the last space on the line. Frames never contain `;`. The counts add up to every
sample in the profiler output (for scalene: the hits of `combined_stacks`, which
only holds samples that came with a native stack).

Frame labels:

- Python frame: `function (package/path.py)`. The path is shortened to the part
  below `site-packages/`, the standard library (as `python/...`), or `src/`.
- Native frame: `symbol [library]`, with `?` for an unresolved symbol and `[]`
  for an unknown library. A label ending in `]` is native.
- py-spy output has the same shape (with `--nolineno`), plus a root
  `process PID:"command line"` frame per process. Its function names may be
  unqualified where scalene's are qualified (`start` against `Proteus.start`).

The stored stacks keep every native frame. The flame page merges each run of
consecutive native frames into one `[native code]` block, or
`[native code: Julia]` when any library in the run is Julia's (`libjulia-*`,
`libLLVM-*jl`), and colours frames by component: `zalmoxis`, `proteus` and
`aragog` from the first path element of Python frames, then `julia`, `native`
and `other`. Aragog and Zalmoxis both take the PROTEUS interior colour, Zalmoxis
hatched; PROTEUS, other Python and native code take colours outside the module
domains.

`flame.html` is a single file: data, styles and code are inline, except d3 7.9.0,
d3-flame-graph 5.0.0 and the PROTEUS design tokens (`tokens.css` of
`@formingworlds/proteus-tokens` 1.3.0), which load from jsDelivr with subresource
integrity hashes. It follows the reader's light or dark preference. The dashboard
pages load the same tokens the same way, and plotly.js (the basic bundle) from
jsDelivr with a subresource integrity hash for their charts.

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
| `settings` | `init_coupler.toml` | `settings/<hex>.toml` | if `outcome.status` is `ok` |
| `config` | `config.toml` | `configs/<YYYY>/<run_id>.toml` | always |
| `log` | `log.txt` | `logs/<YYYY>/<run_id>.log.gz` | always |
| `profile` | `profile/stacks.folded.gz` | `profiles/<YYYY>/<run_id>/stacks.folded.gz` | no |
| `flame` | `profile/flame.html` | `profiles/<YYYY>/<run_id>/flame.html` | no |

The record goes to `records/<YYYY>/<run_id>.json`. `<YYYY>` is the first four
characters of the run id, which is the year of its UTC start. `<hex>` is the
settings hash without its `sha256:` prefix. Other files in the run directory,
such as raw profiler output, are not published.
`log.txt` is gzipped with the gzip header time set to 0, and all other files
are copied unchanged.

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
5. **Carry-over records.** A carry-over run has
   `benchmark.lineage` set to the previous settings hash and
   `benchmark.carry_over_of` set to the default run that moved to the new
   settings. One carry-over is run per move, where a move is identified by
   series, previous hash and new hash.
6. **Publication marker.** After a publish, each run directory has a
   `.published` file with the store commit that holds the run.

The branch starts as an orphan with a short `README.md` that points here.
