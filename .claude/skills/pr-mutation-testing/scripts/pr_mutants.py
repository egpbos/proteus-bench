"""Mutation-test the lines a pull request changes, and list the surviving mutants to read.

Usage, from a checkout of the PR (an env with the package installed and mutmut==3.8.1):
    python pr_mutants.py --base origin/main [--head HEAD] [--scope line|function]
                         [--tests T ...] [--marker EXPR] [--isolation forkserver|fork]
                         [--out FILE] [--no-stmt] [--keep]

Steps:
1. Copy the committed HEAD tree to a temporary folder (`git archive`), so that a mutant that
   deletes files cannot reach the checkout. Uncommitted changes are not tested.
2. Candidate tests: the test files the PR adds or changes, plus tests/<path>/test_<file>.py of
   each changed source file. Run them once unmutated at the marker (the PR CI tier by default)
   and deselect any test that already fails.
3. mutmut makes the mutants of the changed lines (or of whole changed functions with
   --scope function), finds which candidate tests reach each function, and runs them.
4. Statement deletion: each changed call statement, return, augmented assignment, raise or
   block that mutmut did not mutate is replaced by `pass` (a return by `return None`), and
   the tests run again. mutmut skips decorated functions and never deletes a call.

Each mutant gets a kind: `log` (a logging or warning call), `drop` (an argument or value
replaced by None, or dropped) or `behaviour` (everything else). The places to read are the
changed lines with a surviving mutant that is not a log call, and the changed functions that
no candidate test reaches.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = Path(sys.executable).parent
UNIT = 'unit and not skip and not slow and not integration'
STATUS = {1: 'killed', 3: 'killed', 0: 'survived', 5: 'no tests', 33: 'no tests', 34: 'skipped',
          36: 'timeout', -24: 'timeout', 24: 'timeout', 152: 'timeout', 255: 'timeout',
          -11: 'segfault', -9: 'segfault', None: 'not checked'}
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')
LOG = re.compile(r'^(log|logger|logging|warnings)\.\w+\(|^print\(')
TOKEN = re.compile(r"[rbfuRBFU]*'(?:[^'\\]|\\.)*'|[rbfuRBFU]*\"(?:[^\"\\]|\\.)*\"|\w+|==|!=|<=|>=|\*\*|//|\S")
STMT = (ast.Expr, ast.Return, ast.AugAssign, ast.Raise, ast.If, ast.For, ast.While, ast.With)


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True,
                          text=True).stdout


def changed_lines(repo, base, head):
    out = git(repo, 'diff', '-U0', '--no-renames', f'{base}...{head}', '--', 'src/')
    lines, path = defaultdict(set), None
    for row in out.splitlines():
        if row.startswith('+++ '):
            path = None if row == '+++ /dev/null' else row[6:]
        elif row.startswith('@@') and path and path.endswith('.py'):
            start, n = HUNK.match(row).groups()
            start, n = int(start), 1 if n is None else int(n)
            lines[path] |= set(range(start, start + n)) if n else {start, start + 1}
    return lines


def units(source):
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f'{node.name}.{sub.name}', sub


def kind(old, new):
    if LOG.match(old.strip()):
        return 'log'
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        removed, added = a[i1:i2], b[j1:j2]
        if added == ['None'] and removed and 'None' not in removed and a[i1 - 1:i1] != ['is']:
            return 'drop'
        if not added and removed and removed[-1:] != ['not'] and removed != ['not']:
            before, after = a[i1 - 1:i1], a[i2:i2 + 1]
            if (before in (['('], [',']) and after in ([','], [')'])) or ',' in (removed[0], removed[-1]):
                return 'drop'
    return 'behaviour'


def candidate_tests(repo, base, head, sources):
    changed = git(repo, 'diff', '--name-only', '--diff-filter=AM', f'{base}...{head}', '--', 'tests')
    tests = {p for p in changed.split() if Path(p).name.startswith('test_') and p.endswith('.py')}
    for src in sources:
        rel = Path(*Path(src).parts[2:])
        mirror = Path('tests') / rel.parent / f'test_{rel.name}'
        if git(repo, 'ls-tree', head, str(mirror)).strip():
            tests.add(str(mirror))
    return sorted(tests)


def env(tree=None):
    paths = [str(tree / 'src')] if tree else []
    paths += [str(HERE), os.environ.get('PYTHONPATH')]
    return dict(os.environ, OBJC_DISABLE_INITIALIZE_FORK_SAFETY='YES',
                PYTHONPATH=os.pathsep.join(filter(None, paths)))


def pytest_args(run):
    return ['-p', 'no:cacheprovider', '-p', 'mutmut_sandbox', '-m', run.marker,
            *[f'--deselect={d}' for d in run.deselect]]


def baseline(run):
    """Run the candidate tests unmutated; drop the test files that fail to import and
    deselect the tests that already fail."""
    t0 = time.time()
    r = subprocess.run([str(BIN / 'python'), '-m', 'pytest', '-q', '-rfE', '--continue-on-collection-errors',
                        *pytest_args(run), *run.tests], cwd=run.tree, env=env(run.tree), capture_output=True, text=True)
    failing = {row.split(' ', 1)[1].split(' - ')[0] for row in r.stdout.splitlines()
               if row.startswith(('FAILED ', 'ERROR '))}
    run.dropped = sorted(f for f in failing if '::' not in f)
    run.deselect = sorted(f for f in failing if '::' in f)
    run.tests = [t for t in run.tests if t not in run.dropped]
    return r.returncode, time.time() - t0, r.stdout[-3000:]


def silence(path, keep):
    """Append `# pragma: no mutate` to every line outside `keep`, so that mutmut mutates only
    the selected lines (without it a long file grows to tens of MB of mutant copies)."""
    text = path.read_text()
    ends = {tok.start[0] for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type in (tokenize.NEWLINE, tokenize.NL) and tok.start[1] > 0}
    out = []
    for n, line in enumerate(text.splitlines(keepends=True), start=1):
        if n in ends and n not in keep and line.strip():
            body = line.rstrip('\r\n')
            line = f'{body}  # pragma: no mutate' + line[len(body):]
        out.append(line)
    path.write_text(''.join(out))



def setup_cfg(run, sources):
    copy = sorted(p.name for p in run.tree.iterdir() if p.name not in ('src', 'tests', 'mutants'))
    return '\n'.join(['[mutmut]', 'source_paths=src', f'process_isolation={run.isolation}',
                      'forkserver_warmup=none', 'also_copy=' + '\n    '.join(copy),
                      'only_mutate=' + '\n    '.join(sources),
                      'pytest_add_cli_args_test_selection=' + '\n    '.join(run.tests),
                      'pytest_add_cli_args=' + '\n    '.join(pytest_args(run))]) + '\n'


def changed_functions(repo, head, lines):
    funcs = []
    for path, hit in sorted(lines.items()):
        module = path[len('src/'):-len('.py')].replace('/', '.')
        for name, node in units(git(repo, 'show', f'{head}:{path}')):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            touched = sorted(n for n in hit if first <= n <= node.end_lineno)
            if touched:
                funcs.append({'file': path, 'name': name, 'def_line': node.lineno,
                              'length': node.end_lineno - first + 1, 'changed_lines': touched,
                              'mangled': mangle(module, name)})
    return funcs

def mangle(module, name):
    if '.' in name:
        cls, meth = name.split('.')
        return f'{module}.xǁ{cls}ǁ{meth}'
    return f'{module}.x_{name}'


def mutants_of(tree, src, mangled):
    """Mutant name -> (offset of the changed line from the def line, old, new text)."""
    text = (tree / 'mutants' / src).read_text()
    lines = text.splitlines()
    leaf = mangled.rsplit('.', 1)[1]
    found = {}
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(leaf + '__mutmut_'):
            body = lines[node.lineno - 1:node.end_lineno]
            found[node.name] = [body[0].replace(node.name, leaf)] + body[1:]
    orig = found.pop(f'{leaf}__mutmut_orig', None)
    out = {}
    for name, body in found.items():
        ops = [op for op in difflib.SequenceMatcher(a=orig, b=body, autojunk=False).get_opcodes() if op[0] != 'equal']
        i1, i2, j1, j2 = ops[0][1:]
        out[f'{mangled.rsplit(".", 1)[0]}.{name}'] = (
            i1, ' | '.join(s.strip() for s in orig[i1:i2]), ' | '.join(s.strip() for s in body[j1:j2]))
    return out



def mutmut(run, args, phase):
    t0 = time.time()
    with open(run.tree.parent / 'mutmut.log', 'a') as fh:
        code = subprocess.run([str(BIN / 'mutmut'), *args], cwd=run.tree, env=env(), stdout=fh,
                              stderr=subprocess.STDOUT).returncode
    run.wall[phase] = round(time.time() - t0)
    return code


def run_mutmut(run, funcs, sources):
    """Mutate only the selected lines, map functions to tests, run the mutants."""
    keep = defaultdict(set)
    for f in funcs:
        whole = run.scope == 'function' and f['length'] <= run.max_function_lines
        keep[f['file']] |= set(range(f['def_line'], f['def_line'] + f['length'])) if whole else set(f['changed_lines'])
    original = {src: (run.tree / src).read_text() for src in sources}
    (run.tree / 'setup.cfg').write_text(setup_cfg(run, sources))
    for src in sources:
        silence(run.tree / src, keep[src])
    mutmut(run, ['run', 'no-such-mutant'], 'mutmut_generate_and_map')
    stats = json.loads((run.tree / 'mutants' / 'mutmut-stats.json').read_text())['tests_by_mangled_function_name']
    selected = []
    for f in funcs:
        f['tests_reaching'] = len(stats.get(f['mangled'], []))
        muts = mutants_of(run.tree, f['file'], f['mangled'])
        f['mutants'] = len(muts)
        selected += [{'mutant': name, 'function': f['name'], 'file': f['file'], 'line': f['def_line'] + offset,
                      'changed_line': f['def_line'] + offset in f['changed_lines'], 'kind': kind(old, new),
                      'old': old, 'new': new, 'tool': 'mutmut'} for name, (offset, old, new) in muts.items()]
    if selected:
        mutmut(run, ['run', *[s['mutant'] for s in selected]], 'mutmut_run')
    codes = {}
    for meta in (run.tree / 'mutants').glob('**/*.py.meta'):
        codes.update(json.loads(meta.read_text())['exit_code_by_key'])
    for s in selected:
        s['status'] = STATUS.get(codes.get(s['mutant']), 'suspicious')
    for src, text in original.items():
        (run.tree / src).write_text(text)
    return selected


def stmt_targets(source, lines, mutated_lines):
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, STMT) or node.lineno not in lines:
            continue
        call = isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        if isinstance(node, ast.Expr) and not call:
            continue
        if isinstance(node, ast.Return) and (node.value is None or ast.unparse(node.value) == 'None'):
            continue
        if call and LOG.match(ast.unparse(node.value)):
            continue
        if call or node.lineno not in mutated_lines:
            yield node



def stmt_delete(run, lines, selected, limit):
    mutated = defaultdict(set)
    for s in selected:
        mutated[s['file']].add(s['line'])
    rows = []
    for path in sorted(lines):
        target = run.tree / path
        original = target.read_text()
        src = original.splitlines(keepends=True)
        for node in stmt_targets(original, lines[path], mutated[path]):
            indent = src[node.lineno - 1][:node.col_offset]
            repl = 'return None' if isinstance(node, ast.Return) else 'pass'
            n = node.end_lineno - node.lineno + 1
            target.write_text(''.join(src[:node.lineno - 1] + [f'{indent}{repl}\n'] + ['\n'] * (n - 1)
                                      + src[node.end_lineno:]))
            try:
                r = subprocess.run([str(BIN / 'python'), '-m', 'pytest', '-q', '-x', *pytest_args(run), *run.tests],
                                   cwd=run.tree, env=env(run.tree), capture_output=True, timeout=limit)
                status = 'killed' if r.returncode else 'survived'
            except subprocess.TimeoutExpired:
                status = 'timeout'
            rows.append({'file': path, 'line': node.lineno, 'status': status, 'kind': 'behaviour',
                         'changed_line': True, 'old': ast.unparse(node).splitlines()[0][:150], 'new': repl,
                         'tool': 'statement deletion'})
            target.write_text(original)
    return rows


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--base', required=True)
    ap.add_argument('--head', default='HEAD')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--tests', nargs='*')
    ap.add_argument('--marker', default=UNIT)
    ap.add_argument('--scope', choices=('line', 'function'), default='line')
    ap.add_argument('--max-function-lines', type=int, default=200)
    ap.add_argument('--isolation', choices=('forkserver', 'fork'), default='forkserver',
                    help='fork is faster but crashes once Julia runs in the parent (PROTEUS); use it for JAX packages')
    ap.add_argument('--out')
    ap.add_argument('--no-stmt', action='store_true')
    ap.add_argument('--keep', action='store_true')
    run = ap.parse_args()
    run.deselect, run.dropped, run.wall = [], [], {}
    return run


def main():
    run = parse_args()
    t_start = time.time()
    repo = Path(run.repo).resolve()
    head = git(repo, 'rev-parse', run.head).strip()
    base = git(repo, 'merge-base', run.base, head).strip()
    out = Path(run.out or Path(tempfile.gettempdir()) / f'mutants-{head[:9]}.json')
    lines = {p: s for p, s in changed_lines(repo, base, head).items() if p.endswith('.py') and len(Path(p).parts) > 2}
    funcs = changed_functions(repo, head, lines)
    run.tests = run.tests or candidate_tests(repo, base, head, sorted(lines))
    report = {'base': base, 'head': head, 'scope': run.scope, 'marker': run.marker, 'tests': run.tests,
              'functions': funcs, 'mutants': []}
    work = Path(tempfile.mkdtemp(prefix='mutants_'))
    run.tree = work / 'tree'
    run.tree.mkdir()
    subprocess.run(f'git -C "{repo}" archive {head} | tar -x -C "{run.tree}"', shell=True, check=True)
    if not lines or not run.tests:
        report['note'] = 'no changed source file' if not lines else 'no candidate test file'
    else:
        code, t_base, tail = baseline(run)
        run.wall['baseline'] = round(t_base)
        report['baseline'] = {'exit': code, 'deselected': run.deselect, 'dropped_files': run.dropped}
        if not run.tests or code not in (0, 1) or (code == 1 and not run.deselect and not run.dropped):
            report['note'] = f'the unmutated tests do not run (pytest exit {code})\n{tail}'
    if 'note' not in report:
        report['mutants'] = run_mutmut(run, funcs, sorted(lines))
        if not run.no_stmt:
            t_sd = time.time()
            report['mutants'] += stmt_delete(run, lines, report['mutants'], limit=max(60, 10 * run.wall['baseline']))
            run.wall['statement_deletion'] = round(time.time() - t_sd)
    run.wall['total'] = round(time.time() - t_start)
    report['wall_s'] = run.wall
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    if run.keep:
        print('work folder:', work)
    else:
        shutil.rmtree(work, ignore_errors=True)
    if 'note' in report:
        print(report['note'], f'report: {out}', sep='\n')
    else:
        print_summary(report, out)


def print_summary(report, out):
    on_line = [m for m in report['mutants'] if m['changed_line']]
    caught = [m for m in on_line if m['status'] in ('killed', 'timeout')]
    tested = caught + [m for m in on_line if m['status'] == 'survived']
    beh = [m for m in tested if m['kind'] == 'behaviour']
    beh_caught = [m for m in beh if m['status'] != 'survived']
    places = defaultdict(list)
    for m in tested:
        if m['status'] == 'survived' and m['kind'] != 'log':
            places[(m['file'], m['line'])].append(m)
    untested = [f for f in report['functions'] if f.get('tests_reaching') == 0 and f.get('mutants')]
    n_places = len(places) + len(untested)
    print(f"mutants: {len(caught)} of {len(tested)} killed on changed lines; behaviour {len(beh_caught)} of "
          f"{len(beh)}; {n_places} places to read; {report['wall_s']['total']} s")
    for key, what in (('dropped_files', 'test files that fail to import'), ('deselected', 'tests that fail without mutation')):
        if report['baseline'][key]:
            print(f'{what}:', ', '.join(report['baseline'][key]))
    for (path, line), ms in sorted(places.items()):
        print(f'\n{path}:{line}')
        for m in ms[:4]:
            print(f"  {m['tool']}: {m['old'][:100]}  ->  {m['new'][:100]}")
        if len(ms) > 4:
            print(f'  ... {len(ms) - 4} more')
    for f in untested:
        print(f"\n{f['file']}:{f['def_line']}\n  no candidate test reaches {f['name']}")
    print(f'\nfull report: {out}')


if __name__ == '__main__':
    main()
