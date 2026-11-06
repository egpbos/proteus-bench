"""Comment and docstring verbosity of Python files, as JSON on stdout.

Usage: python verbosity.py ROOT FILE... [--diff DIFF] [--block-limit N]
Paths are relative to ROOT; missing and non-Python files are skipped. Per file: line
counts by kind, comment blocks longer than N lines, functions whose docstring is longer
than their body. With --diff (output of `git diff -U0 BASE HEAD`, ROOT being the head
tree), also the added non-blank lines and how many of them are comments or docstrings.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path


def comment_lines(source):
    """Line numbers of comment-only lines."""
    return {
        t.start[0]
        for t in tokenize.generate_tokens(io.StringIO(source).readline)
        if t.type == tokenize.COMMENT and t.line.lstrip().startswith('#')
    }


def blocks(lines):
    """Runs of consecutive line numbers, as (first line, length)."""
    runs = []
    for n in sorted(lines):
        if runs and n == runs[-1][0] + runs[-1][1]:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((n, 1))
    return runs


def docstring(node):
    """(first line, last line) of the node's docstring, or None."""
    first = node.body[0] if node.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.lineno, first.end_lineno
    return None


def walk(node, prefix=''):
    """Yield (qualified name, node) for every function and class, closures included."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f'{prefix}{child.name}'
            yield name, child
            yield from walk(child, f'{name}.')
        else:
            yield from walk(child, prefix)


def measure(path, block_limit, added):
    source = path.read_text(encoding='utf-8')
    text = source.splitlines()
    tree = ast.parse(source)
    comments = comment_lines(source)
    docs, heavy = set(), []
    for name, node in [('<module>', tree), *walk(tree)]:
        span = docstring(node)
        if span is None:
            continue
        docs.update(range(span[0], span[1] + 1))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc, body = span[1] - span[0] + 1, node.end_lineno - span[1]
            if doc > body:
                heavy.append({'name': name, 'doc': doc, 'body': body})
    blank = {i for i, line in enumerate(text, 1) if not line.strip()}
    result = {
        'nonblank_lines': len(text) - len(blank),
        'code_lines': len(text) - len(blank | comments | docs),
        'comment_lines': len(comments),
        'docstring_lines': len(docs - blank),
        'long_comment_blocks': [
            {'line': s, 'lines': n, 'first': text[s - 1].strip()}
            for s, n in blocks(comments)
            if n > block_limit
        ],
        'docstring_longer_than_body': heavy,
    }
    if added is not None:
        nonblank = added - blank
        result['added_nonblank'] = len(nonblank)
        result['added_comment_or_docstring'] = len(nonblank & (comments | docs))
    return result


def added_lines(diff):
    """{path: set of added line numbers} from `git diff -U0` output."""
    added, path = {}, None
    for line in diff.splitlines():
        if line.startswith('+++ '):
            path = line[6:] if line.startswith('+++ b/') else None
        elif path and (m := re.match(r'@@ -\S+ \+(\d+)(?:,(\d+))? @@', line)):
            start, count = int(m[1]), int(m[2] or 1)
            added.setdefault(path, set()).update(range(start, start + count))
    return added


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('files', nargs='+')
    parser.add_argument('--block-limit', type=int, default=2)
    parser.add_argument('--diff', type=Path, help='git diff -U0 BASE HEAD, ROOT being HEAD')
    args = parser.parse_args()
    added = added_lines(args.diff.read_text(encoding='utf-8')) if args.diff else None
    out = {
        f: measure(
            args.root / f, args.block_limit, None if added is None else added.get(f, set())
        )
        for f in args.files
        if f.endswith('.py') and (args.root / f).is_file()
    }
    json.dump(out, sys.stdout, indent=1)


if __name__ == '__main__':
    main()
