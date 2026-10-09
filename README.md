# proteus-bench results store

Raw results of proteus-bench runs: one record per run plus its spans, log,
configs and profile. Files are only ever added, never changed. The layout is
specified in `docs/interface.md` ("Results store") on the main branch of this
repository. Add runs with `proteus-bench publish RUN_DIR`.
