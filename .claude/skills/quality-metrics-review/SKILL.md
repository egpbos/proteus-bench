---
name: quality-metrics-review
description: Measure what an AI-written pull request on egpbos/proteus-bench or FormingWorlds/PROTEUS does to code-quality metrics and verbosity (complexity, function and file length, lint findings, unused code, duplication, comment and docstring weight), comparing the base and head trees, and propose fixes whose effect you have measured. Posts one GitHub review prefixed with the guard emoji. Use when asked for a metrics or verbosity review of a PR in those repositories, or after the proteus-bench-review style review of a PR. Not for PRs written by people unless the maintainer asks for that PR.
---

# Quality metrics and verbosity review

You measure what a pull request does to the code it touches and propose fixes that improve the numbers instead of worsening them. Every finding is a number at the base and at the head, and every fix you propose comes with the number it reaches.

## Division of work

- **This skill measures; it does not judge.** Whether a file, command or feature should exist, whether something else already does it, and what a comment says belong to the `proteus-bench-review` style review. Do not repeat those questions.
- **When both run, the style review goes first.** Skip any file the style review proposes to delete or replace.
- **What the other tools cover.** CI enforces the repository's own lint rules. CodeRabbit, SonarQube and the repositories' test-quality checks cover edge cases and test assertions (`.claude/agents/quality-reviewer.md` in proteus-bench, `tools/check_test_quality.py` in PROTEUS).

## Scope and authority

- **Which PRs.** AI-written PRs on `egpbos/proteus-bench` and `FormingWorlds/PROTEUS`, as the lead agent or the maintainer (GitHub `egpbos`) says. A PR written by a colleague only when the maintainer asks for that PR. If you cannot tell who wrote it, ask.
- **You comment; you do not decide.** Never merge, approve or request changes, never mark a PR ready, never dismiss a review, and never push to the PR's branch.
- **Every message you post starts with "💂 ".** That covers the review body, inline comments, replies and chat reports, so they are told apart from the lead agent (🤖), the style review (👮) and the maintainer.
- **Writing rules.**
  - Write in plain English, with no em or en dashes, no internal plan labels and no mention of how a change was produced.
  - In PROTEUS, follow its AGENTS.md "Commit messages and public text" rules; the emoji is the one agreed marker.
- **Where you work.**
  - Measure in your own scratch directory. Never check out, install into, or modify another session's checkout or environment.
  - Run code only from PRs whose head branch is in the repository itself, never from forks.

## Step 1: Get both trees

```bash
gh pr view N -R OWNER/REPO --json baseRefOid,headRefOid,files,author,body
git -C CLONE cat-file -e HEAD_SHA || git clone --filter=blob:none URL scratch/clone
BASE=$(git -C CLONE merge-base BASE_SHA HEAD_SHA)
git -C CLONE archive "$BASE" | tar -x -C scratch/base
git -C CLONE archive HEAD_SHA | tar -x -C scratch/head
git -C CLONE diff --numstat --find-renames "$BASE" HEAD_SHA
git -C CLONE diff -U0 "$BASE" HEAD_SHA > scratch/u0.diff
```

- **Touched files.** These are the `.py` files the PR adds, modifies or renames.
- **zsh.** In zsh, keep file lists in arrays (`files=(a.py b.py)`, then `$files`), or pass them through `xargs` from a file. Brace the variable in revision paths (`"${sha}:path"`), because zsh reads `$sha:p` as a modifier.
- **Limits.** Read the complexity, function-length and file-length limits and the Python target version from AGENTS.md and `pyproject.toml` at the base (complexity 10 where neither sets one). A PR that changes them is measured against the base values, and the change is itself a finding for the maintainer.

## Step 2: Measure

Use these pinned versions with explicit settings, so numbers compare between PRs:

```bash
t() { pixi exec -s 'python>=3.12' -s lizard==1.24.1 -s ruff==0.17.0 -s vulture==2.16 -s pylint==4.1.2 -- "$@"; }
t ruff check --isolated --no-cache --target-version PYVER --output-format json \
  --select E4,E7,E9,F,W,B,C4,C901,SIM,RET,PIE,ERA,UP,PL,ARG,PERF,N \
  --config 'lint.mccabe.max-complexity=LIMIT' FILES        # lint and complexity
t lizard -L LENGTH_LIMIT -a 5 -w FILES                     # length and parameters
t vulture --min-confidence 60 PACKAGE_DIR                  # whole package
t symilar -d 4 -i --ignore-docstrings --ignore-imports --ignore-signatures PACKAGE_FILES
t python -I SKILL_DIR/scripts/verbosity.py TREE FILES --diff scratch/u0.diff
t python -I SKILL_DIR/scripts/verbosity.py BASE_TREE PACKAGE_FILES   # baseline share
```

Run each tool on both trees, except that `--diff` applies only to the head. Per tree, pass only the files that exist in it; on the base, use the old path of a renamed file and add any deleted file whose functions moved into a touched file. Ruff reports E902 for a missing path, and a new file has no base findings. When a tree's list is empty, skip its per-file runs, because ruff and lizard would measure the whole directory and `verbosity.py` needs a file. The package-wide runs still go.

- **Which tool decides what.** Complexity is ruff C901 (the measure the repositories enforce): radon counts asserts and lizard counts boolean operators, so all three disagree. Length and parameters come from lizard, not radon.
- **Package-wide tools.** `vulture` and `symilar` run over the source package (`src/<package>`) only. Tests are left out, because vulture reports pytest fixtures as unused. Keep only findings that involve touched files.
- **Matching across trees.**
  - Match functions by file and qualified name (closures under their parent).
  - Match lint findings by file, rule code and enclosing function, never by message or line: messages contain counts.
  - A function the PR moved or renamed is compared with its old version (same body or docstring), not counted as new.
- **Size of the PR.** From `--numstat`, count the added and removed lines in source, tests and everything else.

## Step 3: Find the regressions

A regression is something that got worse in the touched files at the head:

- **Complexity:** a function over the C901 limit that is new, or that got more complex.
- **Length:**
  - a function over the length limit that is new, or that grew;
  - a function with more than 5 parameters that is new, or that gained one;
  - a file that crossed its limit or grew while over it.
- **Lint:** a rule that the file did not break at the base and breaks at the head. Do not count a rule the file already breaks. In test files, leave out PLR2004 (magic values in asserts) and ARG (stub signatures).
- **Unused code:** a new vulture finding in a touched source file.
- **Duplication:** a new duplicated block that involves a touched file.
- **Verbosity:**
  - a new comment block longer than 2 lines;
  - a new function whose docstring is longer than its body;
  - in a PR that adds at least 50 non-blank source lines, comment and docstring lines that make up a larger share of the added source lines than of the source package at the base, by more than half. Use `added_comment_or_docstring` over `added_nonblank`, against comment plus docstring lines over non-blank lines in the base package. Quote both numbers; the threshold is a heuristic.

Leave out the following:

- the maintainability index and docstring coverage, which reward added comments and docstrings;
- Halstead measures;
- changes in files the style review proposes to remove.

An improvement is worth one line in the review body; do not list every unchanged metric.

## Step 4: Propose a fix for each regression, and measure it

For each regression, write the smallest change that removes it without adding lines elsewhere. In order of preference:

1. delete the unused or duplicated code, or call the helper that already exists;
2. flatten the logic (early return, a lookup table instead of a branch chain, one loop instead of two);
3. cut the docstring to the contract (inputs, outputs, errors, units), or move history from a comment to the commit message. Never cut a constraint, unit or reason the reader needs: if the docstring is long for that reason, drop the finding;
4. split a function only along a real boundary, and only when the total complexity goes down. A split that just moves branches into a helper adds lines and is not a fix.

Then check the fix:

- **Apply it to a copy** of the head tree in your scratch directory and measure the file again.
- **Run the tests of that module.**
  - In proteus-bench: a venv with `pip install -e "COPY[dev,report]"`, then `pytest tests/<mirror of the file>`. `test_provenance` fails on any `git archive` tree because it has no `.git`; that failure says nothing about the fix.
  - In PROTEUS: use an existing environment only if it is your own, then `pytest -m unit tests/<module>/test_<file>.py`.
  - When no environment can run the tests, check the changed code another way and say how. For example, compare the old and the new function on many generated inputs.
- **Keep the fix only if** the metric improves, the line count does not rise, and the tests or the stated check pass.
- **No fix within these rules.** If none exists, name the regression in one line of the review body as "no fix within the rules", or drop it if it is noise.

## Step 5: Post one review

Post a single review with event `COMMENT` on the head commit:

```bash
gh api repos/OWNER/REPO/pulls/N/reviews -X POST --input review.json
```

`review.json` holds `commit_id`, `event: "COMMENT"`, `body`, and `comments: [{path, line, side, body}]`.

- **Comment fields.**
  - `side` is `"RIGHT"` for added or unchanged lines and `"LEFT"` for deleted ones.
  - A multi-line suggestion also needs `start_line` and `start_side`.
  - Every finding goes in this one review. A finding with no line to anchor goes in the body.
- **Body.**
  - First a one-line verdict: "💂 3 regressions in 2 files, each with a measured fix; +173/-23 lines (net +150), 73 % of the added lines in tests."
  - Then a small table with only the regressed metrics: metric, base, head, head with the fixes.
  - Then one line naming what you could not run.
- **Inline comments,** one per regression, at the function or block concerned. Give the metric before and after, the fix, and its measured effect ("complexity 14 to 8, 12 fewer lines, tests pass"), in at most three sentences. A GitHub suggestion block is fine for a fix of up to about 10 lines that you measured and checked.
- **Nothing regressed.** If nothing regressed, post only the body with the PR's size and two or three headline numbers.

Then report in chat:

- the review link;
- each regression and fix as one line;
- the commands you ran;
- what you could not run.

## On later commits

Measure the new head against the same base.

- Reply ("💂 ...") on your own threads whose numbers changed, with the new numbers.
- Resolve only your own threads whose regression is gone.
- Leave decisions to the maintainer.
