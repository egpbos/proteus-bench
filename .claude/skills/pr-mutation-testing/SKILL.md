---
name: pr-mutation-testing
description: Check whether the tests of a pull request fail when the changed code is wrong, by mutation testing only the lines the PR changes (mutmut plus statement deletion), and list the surviving mutants to read. Use in a session that writes or fixes a PR on FormingWorlds/PROTEUS or a PROTEUS module, before each push, and in the 💂 metrics review for its "mutants" line.
---

# Mutation testing of the changed lines

The tool makes small fake bugs (mutants) in the lines a PR changes: `<` becomes `<=`, a constant changes, an argument is dropped, a call is deleted. Then it runs the unit tests that reach that code. A test that fails catches the mutant ("killed"). A mutant that no test catches ("survived") marks a changed line whose behaviour no test checks. Coverage cannot show this: a line can run in a test that asserts nothing about its result.

## Setup, once per session

Use your own environment, never another session's.

```bash
python -m venv ENV && . ENV/bin/activate     # or your own pixi env with Python 3.12
pip install -U 'pip>=26.2.2' 'certifi>=2026.10.19'
pip install -e "CHECKOUT[develop]"           # the package under test, as for its unit tests
pip install mutmut==3.8.1
```

Set `FWL_DATA` as you do for the unit tests. `SKILL_DIR` is this folder (`.claude/skills/pr-mutation-testing` in proteus-bench).

## One command per PR

Commit first: the tool tests the committed `HEAD`, in a copy outside your checkout.

```bash
python SKILL_DIR/scripts/pr_mutants.py --repo CHECKOUT --base origin/main
```

- PROTEUS: 1 to 2 minutes on a laptop for a typical PR.
- JAX packages without Julia (aragog): add `--isolation fork`, else every mutant recompiles and times out. Large PRs there take long (aragog#120: 2,734 mutants, 105 minutes); pass `--tests` with the PR's own test files to cut that.
- `--tests` and `--marker` change which tests run. The default is the PR CI tier: the test files the PR changes, plus `tests/<path>/test_<file>.py` of each changed source file, with `-m "unit and not skip and not slow and not integration"`.
- `--scope function` mutates all of each changed function, not only the changed lines.
- The full report is JSON (path on the last line of the output).

## Read the output

```
mutants: 68 of 72 killed on changed lines; behaviour 29 of 29; 2 places to read; 78 s

src/proteus/utils/archive.py:187
  mutmut: tar_file.extractall(dir, members=wanted, filter='data')  ->  tar_file.extractall(dir, members=wanted, filter=None)

src/proteus/utils/archive.py:259
  mutmut: with tarfile.open(_tarfile_from_dir(dir), 'r') as tar_file:  ->  with tarfile.open(_tarfile_from_dir(dir), ) as tar_file:
```

The first place is a real gap: no test checks that unsafe archive members are refused. The second is a false alarm: `'r'` is the default mode.

- A place to read is a changed line with a surviving mutant that is not a log call, or a changed function that no candidate test reaches.
- For each place, ask: if the code were wrong in this way, would a user see a different result? If yes, add a test that fails on the mutant and rerun. If no, it is a false alarm.
- False alarms seen on PROTEUS PRs, about two thirds of all places:
  - the mutant changes only log text or a flag that only switches a log message;
  - the mutant is equivalent: a default argument equal to the value passed (`tarfile.open(path, 'r')`), or a value that cannot occur in practice;
  - the only test that could catch it is in a slower tier (say which test).
- Test files that fail to import, and tests that already fail without any mutant, are listed and left out. Fix or report those first; the code they test was not checked.
- A timeout counts as killed. Many timeouts mean the run is too slow, not that the tests are good.

## The survivor rule

Each surviving mutant on a changed line, other than a log call, gets one of:

1. a test that fails on it, or
2. one line in the PR text that says why it needs none (one of the false-alarm reasons, in your own words).

Put the summary line in the PR text, for example "Mutants: 68 of 72 killed on changed lines; 2 places: `archive.py:187` now pinned by a test, `archive.py:259` is the default mode."

## What it does not cover

- Module-level constants and code outside functions, config files, and non-Python code.
- Decorated functions get statement deletion only (mutmut skips them).
- Slow, smoke and integration tests, unless you pass `--marker`.
- A killed mutant shows that a test fails, not that the test checks the physics. Mutants are syntactic; they do not model wrong physics or wrong units.
