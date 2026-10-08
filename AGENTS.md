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
  flame_template.html  page template filled by profiling.write_flame_page
  profiling.py      profiler commands, folded stacks and flame pages
  schema.py         loading the schemas; optional shape validation
  settings.py       flattening, hashing and comparing resolved PROTEUS settings
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
   Prefer removing problematic code over adding edge-case handling around it: check why
   the code was introduced (git log, the commit message) and fix the cause instead.
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
6. **Quiet code.** Comments explain why, never what, and only when the why is not
   obvious: a constraint, a unit, a source, a known trap. Leave out the rest, including
   restated code, history and how the change was made. Docstrings state the contract
   briefly: inputs, outputs, errors, units.
7. **Errors are loud and actionable.** No bare `except`, no `except Exception: pass`.
   Messages say what was expected, what was found, and where.
8. **No dead code.** No commented-out code (ruff `ERA`), no unused parameters, no
   compatibility shims without a caller.

## Tests

Line coverage is not the goal; catching real bugs is. A test that passes for the wrong
reason is worse than none.

1. Every test file starts with `pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]`
   and a docstring listing the contract clauses it covers. Tests that need an external
   program or exceed the unit budget go in their own file marked
   `[pytest.mark.smoke, pytest.mark.timeout(60)]`.
2. Every test function has a docstring (the behaviour verified). Every contract clause
   has, somewhere in its file, a normal case, an edge case and an error or limit path.
   Every assertion must be able to fail for a plausible bug; never add one just to reach
   a count.
3. Assertion values must not be copied from the implementation. Pin independently
   derived values, and add a guard that the most plausible wrong result would fail
   (e.g. "without the equilibration solves the total would be 1138 s, not 1768 s").
4. Mutation check: for rule-like code (validators, checkers, detectors, flag logic),
   disabling any single rule must make at least one test fail. Check every rule, not a
   sample. Run the check with
   `PYTHONDONTWRITEBYTECODE=1` after clearing `__pycache__`: a restored file can match the
   mutant's size and modification second, and Python would then run stale bytecode.
5. No float `==`; use `pytest.approx` with a stated tolerance. Unit tests < 100 ms.
   Tests never need PROTEUS: use `proteus_bench.testing.fake_proteus`.

## Writing style for anything published

Commit messages, PR titles and bodies, docs, comments and log strings describe the
outcome, not the process. Do not mention AI tools or how a change was produced. No em
dashes or en dashes. Do not hard-wrap PR or issue bodies. Keep PR descriptions short:
the bare change, no history and no result tables.

## Review

Every change goes through a pull request. Reviews (human, CodeRabbit, and the strict
review checklist in `.claude/agents/quality-reviewer.md`) apply this file. A finding
is resolved by fixing it or by a stated reason in the PR, never by silence.

A pull request goes through three phases:

1. Develop: open it as a draft. CI and SonarQube run on every push; CodeRabbit does not.
2. AI review: when the change is complete, add the `ai-review` label, which switches
   CodeRabbit on for the PR. Address every CodeRabbit and SonarQube finding until CI is
   green, the quality gate passes and CodeRabbit approves the latest commit.
3. Human review: mark the PR ready for review. Keep the label, so CodeRabbit also reviews
   the fixes that follow.
