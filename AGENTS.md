# proteus-bench: contributor and agent guide

This file is the single source of engineering standards for this repository. Human
contributors, code-review bots (CodeRabbit reads it as its coding guideline) and coding
agents all follow it. `CLAUDE.md` is a symlink to this file.

## What this is

A harness that runs PROTEUS, records per-phase and per-module timings with full
provenance, stores a results history, and renders a static dashboard. Design and
decisions: [FormingWorlds/PROTEUS#916](https://github.com/FormingWorlds/PROTEUS/issues/916)
and `docs/interface.md`.

```
src/proteus_bench/
  cli.py            dispatcher only; each subcommand is commands/<name>.py
  commands/         add_arguments(parser) + main(args) -> int, one module per subcommand
  schemas/          JSON Schemas (versioned interfaces, see docs/interface.md)
  timing.py         reading and checking timing.jsonl
  testing/          fake proteus stub used by the tests
tests/              mirrors src/proteus_bench/
examples/           example timing.jsonl and run record (illustrative values)
```

Commands: `pixi run test`, `pixi run lint`, `proteus-bench validate <files>`.

## Architecture rules

1. **No runtime dependencies.** The harness is installed into PROTEUS environments and
   must never constrain them. Standard library only; `jsonschema` stays optional.
2. **Never import PROTEUS.** Run the `proteus` CLI as a subprocess and read its output
   files. This keeps the harness independent of the PROTEUS version being measured.
3. **Interfaces are versioned files.** `timing.jsonl` and the run record are defined in
   `docs/interface.md` and `src/proteus_bench/schemas/`. Change them only together with
   the docs, the examples and the version rules stated there.
4. **Keep raw data; derive statistics.** Records keep raw spans. Every summary, baseline
   and flag is recomputed from raw data, so better analysis applies to old runs too.
5. **Never fall back silently.** If something runs differently from what was asked (a
   solver fallback, a missing cache, an unavailable profiler), record it in the output.
   A silent fallback is how a whole benchmark campaign once measured the wrong solver.

## Code quality

These limits exist because code is now cheap to produce and expensive to review. Optimise
for the reader, human or model, who has to understand and change the code later.

1. **Small is the default.** Write the least code that does the job. No speculative
   options, parameters, hooks or abstractions "for later". No defensive branches for
   states that cannot occur. More lines are a cost that needs a reason.
2. **Complexity limits.** McCabe complexity <= 10 per function (ruff `C90`, enforced);
   cognitive complexity <= 15 (SonarCloud). Functions aim for < 40 lines, files for
   < 400. Past that, split along a real concern boundary, not arbitrarily.
3. **No duplication.** Search for an existing helper before writing one. Two copies of
   the same logic drift apart. Target < 3 % duplicated lines on new code (SonarCloud).
4. **Locality.** A change to one behaviour should touch one or two files. Keep things
   that change together in one place. Do not reach into another module's `_private`
   helpers; promote them to a public function or move the caller.
5. **Names carry meaning.** Use the domain words (span, phase, lineage, attributed,
   baseline), not generic ones (data, info, process, handle, manager, util). Units go in
   the name when not obvious: `dur_s`, `mem_gb`.
6. **Comments explain why, never what.** Good: a constraint, a unit, a source, a
   non-obvious choice, a known trap. Bad: restating the code, narrating history, or
   describing how the change was made. Docstrings state the contract: inputs, outputs,
   errors, units.
7. **Errors are loud and actionable.** No bare `except`, no `except Exception: pass`.
   Messages say what was expected, what was found, and where.
8. **No dead code.** No commented-out code (ruff `ERA`), no unused parameters, no
   compatibility shims without a caller.

## Tests

Line coverage is not the goal; catching real bugs is. A test that passes for the wrong
reason is worse than none.

1. Every test file starts with `pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]`
   (or `smoke` for tests that start subprocesses) and a docstring listing the contract
   clauses it covers.
2. Every test function has a docstring (the behaviour verified), at least two meaningful
   assertions, at least one edge case and at least one error or limit path.
3. Assertion values must not be copied from the implementation. Pin independently
   derived values, and add a guard that the most plausible wrong result would fail
   (e.g. "without the equilibration solves the total would be 1138 s, not 1768 s").
4. Mutation check: for rule-like code (validators, checkers, detectors), every rule must
   be killed by at least one test when the rule is disabled. Record how you checked.
5. No float `==`; use `pytest.approx` with a stated tolerance. Unit tests < 100 ms.
   Tests never need PROTEUS: use `proteus_bench.testing.fake_proteus`.

## Writing style for anything published

Commit messages, PR titles and bodies, docs, comments and log strings describe the
outcome, not the process. Do not mention AI tools or how a change was produced. No em
dashes or en dashes. Do not hard-wrap PR or issue bodies.

## Review

Every change goes through a pull request. Reviews (human, CodeRabbit, and the strict
review checklist in `.claude/agents/quality-reviewer.md`) apply this file. A finding
is resolved by fixing it or by a stated reason in the PR, never by silence.
