"""Profiled runs: wrap the proteus command in a profiler, turn its output into folded
stacks and build a self-contained flame-graph page.

A profiled run writes into ``<run>/profile/``: the profiler's own output
(``scalene-profile.json`` or ``py-spy.folded``), then ``collect`` adds
``stacks.folded.gz`` (one ``frame;frame;frame count`` line per distinct stack,
outermost frame first) and ``flame.html``.

Frame labels: Python frames are ``function (package/path.py)``, native frames
are ``symbol [library]``, so a label ending in ``]`` is native.
"""

from __future__ import annotations

import gzip
import html
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from importlib import resources
from pathlib import Path

SCALENE_OUTPUT = 'scalene-profile.json'
PYSPY_OUTPUT = 'py-spy.folded'
STACKS_FILE = 'stacks.folded.gz'
FLAME_FILE = 'flame.html'

# Python packages of the PROTEUS ecosystem whose frames scalene records. scalene's
# --profile-only keeps a file when its path contains one of these directories, so
# modules installed from PyPI are covered as well as editable checkouts.
ECOSYSTEM_PACKAGES = (
    'aragog',
    'boreas',
    'calliope',
    'janus',
    'mors',
    'proteus',
    'vulcan',
    'zalmoxis',
    'zephyrus',
)
# Components with their own colour on the flame page; other Python is 'other'.
COLOURED_PACKAGES = frozenset({'aragog', 'proteus', 'zalmoxis'})
NATIVE_BLOCK = '[native code]'
NATIVE_JULIA_BLOCK = '[native code: Julia]'

_FIND_PACKAGES = """
import importlib.util, json, sys
found = {}
for name in sys.argv[1:]:
    spec = importlib.util.find_spec(name)
    if spec is not None and spec.submodule_search_locations:
        found[name] = list(spec.submodule_search_locations)[0]
print(json.dumps(found))
"""


def wrap_command(
    profiler: str, argv: list[str], profile_dir: Path
) -> tuple[list[str], dict[str, str]]:
    """The command that runs ``argv`` under ``profiler``, and extra environment variables.

    ``argv[0]`` is the proteus console script (a name on PATH or a path). The
    profiler writes its output into ``profile_dir``, which is created. Raises
    ValueError for an unknown profiler and RuntimeError when the profiler or the
    script cannot be used here.
    """
    if profiler not in ('scalene', 'py-spy'):
        raise ValueError(f'unknown profiler {profiler!r}; expected scalene or py-spy')
    tool = shutil.which(profiler)
    if tool is None:
        raise RuntimeError(f'{profiler} not found on PATH; install it where proteus runs')
    profile_dir.mkdir(parents=True, exist_ok=True)
    if profiler == 'scalene':
        # scalene 2.3.0 leaves JAX JIT alone unless --disable-jit is given, but a
        # disabled JIT breaks Zalmoxis (TracerArrayConversionError), so pin it on.
        return _scalene_argv(tool, argv, profile_dir), {'JAX_DISABLE_JIT': '0'}
    return _pyspy_argv(tool, argv, profile_dir), {}


def _scalene_argv(tool: str, argv: list[str], profile_dir: Path) -> list[str]:
    # scalene runs a Python file, not a command on PATH, so it needs the script path.
    script = shutil.which(argv[0])
    if script is None:
        raise RuntimeError(f'{argv[0]!r} not found; scalene needs the proteus console script')
    dirs = package_dirs(Path(script).parent / 'python', ECOSYSTEM_PACKAGES)
    if 'proteus' not in dirs:
        raise RuntimeError(f'the Python next to {script} cannot find the proteus package')
    # The trailing separator keeps e.g. .../proteus/ from matching .../proteus_bench/.
    only = ','.join(os.path.join(d, '') for d in dirs.values())
    return [
        tool, 'run', '--cpu-only',
        '--profile-all',  # without it the profile is empty: PROTEUS is outside the script dir
        '--profile-only', only,
        '--profile-exclude', '.jl,.julia',  # tracing Julia files through juliacall crashes
        '-o', str(profile_dir / SCALENE_OUTPUT),
        script, '---', *argv[1:],
    ]  # fmt: skip


def _pyspy_argv(tool: str, argv: list[str], profile_dir: Path) -> list[str]:
    if sys.platform == 'darwin' and os.geteuid() != 0:
        raise RuntimeError('py-spy needs root on macOS; use scalene or run as root')
    # raw is py-spy's folded format; --nolineno gives the same labels as scalene_to_folded.
    return [
        tool, 'record', '--format', 'raw', '--nolineno', '--subprocesses',
        '-o', str(profile_dir / PYSPY_OUTPUT), '--', *argv,
    ]  # fmt: skip


def package_dirs(python: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Package name -> source directory, as seen by ``python``, for the installed ones.

    Uses ``importlib.util.find_spec`` in a subprocess, which locates a package
    without importing it. Raises RuntimeError if the interpreter fails.
    """
    # -P: do not put the current directory first, as the console script does not either.
    cmd = [str(python), '-P', '-c', _FIND_PACKAGES, *names]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except OSError as err:
        raise RuntimeError(f'cannot run {python} to locate packages: {err}') from err
    if result.returncode != 0:
        raise RuntimeError(f'{python} failed to locate packages: {result.stderr.strip()}')
    return json.loads(result.stdout)


def source_path(filename: str) -> str:
    """Short, package-relative form of a Python source path, e.g. ``zalmoxis/solver.py``."""
    i = filename.rfind('/site-packages/')
    if i >= 0:
        return filename[i + len('/site-packages/') :]
    stdlib = re.search(r'/lib/python3\.\d+/(.+)$', filename)
    if stdlib:
        return 'python/' + stdlib.group(1)
    i = filename.rfind('/src/')
    if i >= 0:
        return filename[i + len('/src/') :]
    return os.path.basename(filename) or filename


def frame_label(frame: dict) -> str:
    """Folded-stack label of one scalene ``combined_stacks`` frame."""
    name = (frame['display_name'] or '?').replace(';', ',')
    where = frame['filename_or_module'] or ''
    if frame['kind'] == 'py':
        return f'{name} ({source_path(where)})'
    return f'{name} [{os.path.basename(where)}]'


def scalene_to_folded(profile: dict) -> list[str]:
    """Folded stacks from a scalene 2.3.0 JSON profile's ``combined_stacks``.

    Each entry there is ``[frames, hits]`` with frames outermost first: the
    Python chain, then the native stack from process entry to leaf. Identical
    labelled stacks are merged; the sum of counts equals the sum of hits. Raises
    ValueError when there is nothing to draw.
    """
    if 'combined_stacks' not in profile:
        raise ValueError(f'not a scalene profile: no combined_stacks (keys: {sorted(profile)})')
    counts: Counter[str] = Counter()
    python_samples = 0
    for frames, hits in profile['combined_stacks']:
        counts[';'.join(frame_label(f) for f in frames)] += hits
        if any(f['kind'] == 'py' for f in frames):
            python_samples += hits
    if python_samples == 0:
        raise ValueError(
            'scalene profile has no samples in PROTEUS code; record it with '
            '--profile-all and a --profile-only that matches the PROTEUS sources'
        )
    return [f'{stack} {n}' for stack, n in counts.items() if n > 0]


def parse_folded_line(line: str) -> tuple[list[str], int]:
    """Frames and sample count of one ``frame;frame count`` line."""
    stack, _, count = line.rpartition(' ')
    if not stack or not count.isdigit():
        raise ValueError(f'expected "frame;frame;frame count", found {line[:80]!r}')
    return stack.split(';'), int(count)


def read_folded(path: Path) -> list[str]:
    """Non-empty lines of a ``.folded`` or ``.folded.gz`` file, each checked."""
    data = path.read_bytes()
    if path.suffix == '.gz':
        data = gzip.decompress(data)
    lines = [line for line in data.decode().splitlines() if line.strip()]
    for i, line in enumerate(lines, 1):
        try:
            parse_folded_line(line)
        except ValueError as err:
            raise ValueError(f'{path} line {i}: {err}') from None
    return lines


def load_stacks(path: Path) -> list[str]:
    """Folded stacks from a scalene JSON profile (``*.json``) or a folded file."""
    if path.suffix != '.json':
        return read_folded(path)
    try:
        profile = json.loads(path.read_text())
    except json.JSONDecodeError as err:
        raise ValueError(f'{path}: not valid JSON ({err.msg})') from None
    return scalene_to_folded(profile)


def is_julia_library(library: str) -> bool:
    """Whether a native library belongs to Julia (libjulia-*, Julia's libLLVM-*jl)."""
    return 'julia' in library.lower() or 'jl' in library


def collapse_native(frames: list[str]) -> list[str]:
    """Merge each run of consecutive native frames into one block (Julia or not)."""
    out: list[str] = []
    run: list[str] = []
    for frame in frames:
        if frame.endswith(']'):
            run.append(frame)
            continue
        if run:
            out.append(_native_block(run))
            run = []
        out.append(frame)
    if run:
        out.append(_native_block(run))
    return out


def _native_block(run: list[str]) -> str:
    libraries = [f[f.rfind('[') + 1 : -1] for f in run]
    return NATIVE_JULIA_BLOCK if any(map(is_julia_library, libraries)) else NATIVE_BLOCK


def component(label: str) -> str:
    """Colour key of a frame: zalmoxis, proteus, aragog, julia, native or other."""
    if label == NATIVE_JULIA_BLOCK:
        return 'julia'
    if label.endswith(']'):
        return 'native'
    if label.endswith(')') and '(' in label:
        package = label[label.rfind('(') + 1 : -1].split('/')[0]
        if package in COLOURED_PACKAGES:
            return package
    return 'other'


def flame_tree(folded_lines: list[str]) -> tuple[dict, int]:
    """d3-flame-graph tree (``value`` = self samples) and the total sample count."""
    root: dict = {'name': 'all samples', 'value': 0, 'children': {}}
    total = 0
    for line in folded_lines:
        frames, count = parse_folded_line(line)
        total += count
        node = root
        for frame in collapse_native(frames):
            node = node['children'].setdefault(
                frame, {'name': frame, 'value': 0, 'children': {}, 'k': component(frame)}
            )
        node['value'] += count

    def finish(node: dict) -> dict:
        out = {k: v for k, v in node.items() if k != 'children'}
        if node['children']:
            out['children'] = [finish(c) for c in node['children'].values()]
        return out

    return finish(root), total


def write_flame_page(folded_lines: list[str], out_html: Path, meta: dict) -> int:
    """Write the flame-graph page and return the number of samples drawn.

    ``meta`` may hold ``title``, ``run_id``, ``commit`` and ``profiler``; missing
    or empty ones are left out. Raises ValueError when there are no samples.
    """
    tree, total = flame_tree(folded_lines)
    if total == 0:
        raise ValueError('no samples to draw: the profile is empty')
    formats = (
        ('run_id', 'run <code>{}</code>'),
        ('commit', 'PROTEUS <code>{}</code>'),
        ('profiler', 'profiler {}'),
    )
    spans = [fmt.format(html.escape(meta[key])) for key, fmt in formats if meta.get(key)]
    spans.append(f'{total:,} samples')
    fields = {
        'TITLE': html.escape(meta.get('title') or 'PROTEUS CPU flame graph'),
        'META': ''.join(f'<span>{s}</span>' for s in spans),
        # '<' escaped so a frame name can never close the script element.
        'DATA': json.dumps(tree, separators=(',', ':')).replace('<', '\\u003c'),
    }
    template = resources.files('proteus_bench').joinpath('flame_template.html').read_text()
    page = re.sub(r'__(TITLE|META|DATA)__', lambda m: fields[m.group(1)], template)
    out_html.write_text(page)
    return total


def collect(profile_dir: Path, meta: dict | None = None) -> dict[str, str]:
    """Build ``stacks.folded.gz`` and ``flame.html`` from a profiled run's output.

    Returns artifact paths relative to the run directory (the parent of
    ``profile_dir``) under the keys ``profile`` and ``flame``. Raises
    FileNotFoundError when no profiler output exists, ValueError when it is empty.
    """
    sources = {SCALENE_OUTPUT: 'scalene', PYSPY_OUTPUT: 'py-spy'}
    found = [name for name in sources if (profile_dir / name).exists()]
    if not found:
        raise FileNotFoundError(
            f'no profiler output in {profile_dir}: expected {sorted(sources)}'
        )
    lines = load_stacks(profile_dir / found[0])
    write_flame_page(
        lines, profile_dir / FLAME_FILE, {'profiler': sources[found[0]], **(meta or {})}
    )
    text = ''.join(line + '\n' for line in lines)
    # mtime=0 keeps the file byte-identical for identical stacks.
    (profile_dir / STACKS_FILE).write_bytes(gzip.compress(text.encode(), mtime=0))
    return {
        'profile': f'{profile_dir.name}/{STACKS_FILE}',
        'flame': f'{profile_dir.name}/{FLAME_FILE}',
    }
