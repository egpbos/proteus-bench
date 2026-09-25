"""Tests for proteus_bench.profiling: profiler commands, folded stacks and flame pages.

Contract clauses: wrap_command builds the scalene 2.3.0 recipe (flags, derived
--profile-only, ``---`` before the proteus arguments, JAX_DISABLE_JIT=0) and the
py-spy raw recipe, and refuses unknown or unusable profilers; scalene_to_folded
keeps every sample and refuses empty profiles; collapse_native merges each run of
native frames into one block, Julia or not; the flame page embeds every tree node
in valid HTML; collect writes stacks.folded.gz and flame.html and returns their paths.

Fixture ``scalene-profile.json`` is hand-built to the shape scalene 2.3.0 writes
(tag v2.3.0, ``scalene/scalene_json.py``): ``CombinedStackFrame`` (line 143) has
kind, display_name, filename_or_module, line, ip, offset; each entry is
``[frames, hits]`` (``CombinedStackEntry``, line 375), emitted at line 1151; frames
are the Python chain then the native stack from process entry to leaf (lines
877-935). Fixture ``real-slice.folded`` is 14 lines of a real PROTEUS profile.
"""

from __future__ import annotations

import gzip
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

from proteus_bench import profiling

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

FIXTURES = Path(__file__).parent / 'fixtures'
SCALENE = FIXTURES / 'scalene-profile.json'
SLICE = FIXTURES / 'real-slice.folded'
FIXTURE_TOTAL = 40 + 25 + 12 + 8 + 3 + 2  # hits of the six combined_stacks entries
SLICE_TOTAL = 22562  # awk '{s+=$NF} END {print s}' real-slice.folded
ZALMOXIS_STACK = (
    'start (proteus/cli.py);Proteus.start (proteus/proteus.py);'
    'zalmoxis_solver (proteus/interior_struct/zalmoxis.py);main (zalmoxis/solver.py);'
    '? [];__libc_start_main [libc.so.6];Py_RunMain [python3.12];'
    '_PyEval_EvalFrameDefault [python3.12];dgemm_ [libopenblas.so.0]'
)


def scalene_profile() -> dict:
    return json.loads(SCALENE.read_text())


@pytest.fixture
def tools(monkeypatch, tmp_path):
    """Fake PATH lookups and package discovery; returns the fake script path."""
    script = str(tmp_path / 'env' / 'bin' / 'proteus')
    found = {'scalene': '/opt/bin/scalene', 'py-spy': '/opt/bin/py-spy', 'proteus': script}
    monkeypatch.setattr(profiling.shutil, 'which', found.get)
    dirs = {'proteus': '/co/PROTEUS/src/proteus', 'zalmoxis': '/env/site-packages/zalmoxis'}
    calls = []
    monkeypatch.setattr(
        profiling, 'package_dirs', lambda python, names: calls.append(python) or dirs
    )
    return script, calls


def test_scalene_command_follows_the_recipe(tools, tmp_path):
    """scalene gets the verified flags, the script path, then --- and the proteus args."""
    script, calls = tools
    profile_dir = tmp_path / 'run' / 'profile'
    argv, env = profiling.wrap_command(
        'scalene', ['proteus', 'start', '-c', 'x.toml'], profile_dir
    )
    assert argv == [
        '/opt/bin/scalene', 'run', '--cpu-only', '--profile-all',
        '--profile-only', '/co/PROTEUS/src/proteus/,/env/site-packages/zalmoxis/',
        '--profile-exclude', '.jl,.julia',
        '-o', str(profile_dir / 'scalene-profile.json'),
        script, '---', 'start', '-c', 'x.toml',
    ]  # fmt: skip
    assert env == {'JAX_DISABLE_JIT': '0'}
    sep = argv.index('---')
    assert argv[sep - 1] == script and argv[sep + 1 :] == ['start', '-c', 'x.toml']
    assert profile_dir.is_dir()
    assert calls == [Path(script).parent / 'python']  # the env's own interpreter


def test_scalene_refuses_what_it_cannot_profile(tools, monkeypatch, tmp_path):
    """A missing script, missing scalene or undiscoverable proteus package raises."""
    with pytest.raises(RuntimeError, match='console script'):
        profiling.wrap_command('scalene', ['no-such-proteus', 'start'], tmp_path)
    monkeypatch.setattr(profiling, 'package_dirs', lambda python, names: {'zalmoxis': '/z'})
    with pytest.raises(RuntimeError, match='cannot find the proteus package'):
        profiling.wrap_command('scalene', ['proteus', 'start'], tmp_path)
    monkeypatch.setattr(profiling.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='scalene not found on PATH'):
        profiling.wrap_command('scalene', ['proteus', 'start'], tmp_path)


def test_unknown_profiler_raises(tools, tmp_path):
    """Only scalene and py-spy are profilers; 'none' and typos are refused."""
    for name in ('none', 'Scalene', 'pyspy', ''):
        with pytest.raises(ValueError, match='unknown profiler'):
            profiling.wrap_command(name, ['proteus', 'start'], tmp_path / 'p')
    assert not (tmp_path / 'p').exists()  # nothing is created for a refused profiler


def test_pyspy_command_and_macos_root_rule(tools, monkeypatch, tmp_path):
    """py-spy records raw folded stacks of all subprocesses; on macOS it needs root."""
    monkeypatch.setattr(profiling.sys, 'platform', 'linux')
    argv, env = profiling.wrap_command('py-spy', ['proteus', 'start', '-c', 'x.toml'], tmp_path)
    assert argv == [
        '/opt/bin/py-spy', 'record', '--format', 'raw', '--nolineno', '--subprocesses',
        '-o', str(tmp_path / 'py-spy.folded'), '--', 'proteus', 'start', '-c', 'x.toml',
    ]  # fmt: skip
    assert env == {}  # JAX_DISABLE_JIT is a scalene workaround only
    monkeypatch.setattr(profiling.sys, 'platform', 'darwin')
    monkeypatch.setattr(profiling.os, 'geteuid', lambda: 501)
    with pytest.raises(RuntimeError, match='root on macOS'):
        profiling.wrap_command('py-spy', ['proteus'], tmp_path)
    monkeypatch.setattr(profiling.os, 'geteuid', lambda: 0)
    assert profiling.wrap_command('py-spy', ['proteus'], tmp_path)[0][-1] == 'proteus'


@pytest.mark.smoke
def test_package_dirs_locates_without_the_current_directory(tmp_path, monkeypatch):
    """Installed packages are found by path; a same-named directory in cwd is ignored."""
    site = tmp_path / 'site'
    for pkg in ('pkg_a', 'pkg_a_extra'):
        (site / pkg).mkdir(parents=True)
        (site / pkg / '__init__.py').write_text('raise SystemExit("imported")\n')
    (tmp_path / 'cwd' / 'pkg_b').mkdir(parents=True)
    (tmp_path / 'cwd' / 'pkg_b' / '__init__.py').write_text('')
    monkeypatch.chdir(tmp_path / 'cwd')
    monkeypatch.setenv('PYTHONPATH', str(site))
    found = profiling.package_dirs(Path(sys.executable), ('pkg_a', 'pkg_b', 'missing'))
    assert found == {'pkg_a': str(site / 'pkg_a')}  # located, not imported, and pkg_b skipped
    with pytest.raises(RuntimeError, match='cannot run'):
        profiling.package_dirs(tmp_path / 'no-python', ('pkg_a',))


def test_source_path_keeps_the_package_part():
    """Paths shorten to the package-relative part; site-packages wins over a /src/ above it."""
    cases = {
        '/home/u/PROTEUS/src/proteus/cli.py': 'proteus/cli.py',
        '/home/u/PROTEUS/aragog/src/aragog/solver.py': 'aragog/solver.py',
        '/home/src/env/lib/python3.12/site-packages/numpy/core.py': 'numpy/core.py',
        '/home/src/env/lib/python3.12/json/decoder.py': 'python/json/decoder.py',
        '/tmp/script.py': 'script.py',
        '<frozen runpy>': '<frozen runpy>',
        '': '',
    }
    for path, expected in cases.items():
        assert profiling.source_path(path) == expected, path


def test_scalene_conversion_keeps_every_sample():
    """Labels follow the frame kinds, equal labelled stacks merge, the total is kept."""
    lines = profiling.scalene_to_folded(scalene_profile())
    counts = dict(line.rsplit(' ', 1) for line in lines)
    assert sum(int(n) for n in counts.values()) == FIXTURE_TOTAL
    # The two Zalmoxis entries differ only in the source line, so they merge (40 + 25);
    # without the merge there would be 6 lines, not 5.
    assert len(lines) == 5
    assert counts[ZALMOXIS_STACK] == '65'
    assert counts['blas_thread_server [libopenblas.so.0]'] == '2'  # native-only stack kept


def test_frame_label_edge_cases():
    """Unresolved native frames become '? []'; semicolons cannot split a frame."""
    native = {'kind': 'native', 'display_name': '', 'filename_or_module': ''}
    odd = {'kind': 'py', 'display_name': 'f;g', 'filename_or_module': '/x/src/pkg/a.py'}
    assert profiling.frame_label(native) == '? []'
    assert profiling.frame_label(odd) == 'f,g (pkg/a.py)'


def test_empty_scalene_profiles_are_refused():
    """No stacks, only native stacks, or no combined_stacks at all: a clear error."""
    empty = scalene_profile() | {'combined_stacks': [], 'files': {}}
    with pytest.raises(ValueError, match='--profile-all'):
        profiling.scalene_to_folded(empty)
    native_only = scalene_profile()
    native_only['combined_stacks'] = native_only['combined_stacks'][-1:]
    with pytest.raises(ValueError, match='no samples in PROTEUS code'):
        profiling.scalene_to_folded(native_only)
    with pytest.raises(ValueError, match='not a scalene profile'):
        profiling.scalene_to_folded({'files': {}})


def test_collapse_merges_native_runs():
    """Each run of native frames becomes one block, Julia when any library is Julia's."""
    frames = [
        'a (proteus/x.py)', 'f [libc.so.6]', 'g [python3.12]',
        'b (zalmoxis/y.py)', 'h [libc.so.6]', 'i [libLLVM-16jl.so]',
        'c (proteus/z.py)', 'j [libjulia-internal.so.1.11]',
    ]  # fmt: skip
    assert profiling.collapse_native(frames) == [
        'a (proteus/x.py)', '[native code]',
        'b (zalmoxis/y.py)', '[native code: Julia]',
        'c (proteus/z.py)', '[native code: Julia]',
    ]  # fmt: skip
    assert profiling.collapse_native([]) == []
    assert profiling.collapse_native(['k [libm.so.6]']) == ['[native code]']
    # Collapsing twice changes nothing, so collapsed input can be read back.
    once = profiling.collapse_native(frames)
    assert profiling.collapse_native(once) == once


def test_components_from_frame_paths():
    """Colour keys come from the package in the label; everything else is 'other'."""
    assert profiling.component('main (zalmoxis/solver.py)') == 'zalmoxis'
    assert profiling.component('solve (aragog/solver.py:12)') == 'aragog'  # py-spy lines
    assert profiling.component('run (proteus/atmos_clim/agni.py)') == 'proteus'
    assert profiling.component('[native code: Julia]') == 'julia'
    assert profiling.component('[native code]') == 'native'
    assert profiling.component('update (mors/star.py)') == 'other'
    assert profiling.component('process 12:"python -m x"') == 'other'


class PageParser(HTMLParser):
    """Checks tag balance and collects inline scripts."""

    VOID = {'meta', 'link', 'input', 'br', 'img'}

    def __init__(self):
        super().__init__()
        self.open, self.scripts, self.in_script = [], [], False

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.open.append(tag)
        if tag == 'script' and not dict(attrs).get('src'):
            self.in_script = True
            self.scripts.append('')

    def handle_endtag(self, tag):
        assert self.open and self.open.pop() == tag, f'unbalanced </{tag}>'
        self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts[-1] += data


def page_data(path: Path) -> tuple[dict, PageParser]:
    parser = PageParser()
    parser.feed(path.read_text())
    parser.close()
    assert parser.open == []
    (script,) = parser.scripts
    data = script.split('const DATA = ', 1)[1].split(';\n', 1)[0]
    return json.loads(data), parser


def names(node: dict) -> list[str]:
    return [node['name']] + [n for c in node.get('children', []) for n in names(c)]


def test_flame_page_embeds_every_node(tmp_path):
    """The page is balanced HTML whose data holds the whole collapsed tree."""
    out = tmp_path / 'flame.html'
    lines = profiling.scalene_to_folded(scalene_profile())
    meta = {'title': 'A <b> title', 'run_id': 'r1', 'commit': 'abd4ca53', 'profiler': 'scalene'}
    assert profiling.write_flame_page(lines, out, meta) == FIXTURE_TOTAL
    tree, _ = page_data(out)
    # root, start, Proteus.start, zalmoxis_solver, main, 1 block; run_atmosphere, 1 Julia
    # block; solve, 1 block; update_mors; 1 root block: 12 (18 without collapsing).
    assert len(names(tree)) == 12
    assert names(tree).count('[native code]') == 3
    assert '[native code: Julia]' in names(tree) and 'update_mors (mors/star.py)' in names(tree)
    text = out.read_text()
    assert 'A &lt;b&gt; title' in text and 'A <b> title' not in text
    assert 'run <code>r1</code>' in text and f'{FIXTURE_TOTAL:,} samples' in text


def test_flame_page_survives_hostile_names_and_refuses_empty(tmp_path):
    """A frame named like a closing script tag stays data; no samples is an error."""
    out = tmp_path / 'flame.html'
    profiling.write_flame_page(['evil</script><b> (proteus/x.py) 3'], out, {})
    tree, parser = page_data(out)
    assert names(tree) == ['all samples', 'evil</script><b> (proteus/x.py)']
    assert 'PROTEUS CPU flame graph' in out.read_text()  # default title
    with pytest.raises(ValueError, match='no samples'):
        profiling.write_flame_page([], out, {})
    with pytest.raises(ValueError, match='no samples'):
        profiling.write_flame_page(['a (proteus/x.py) 0'], out, {})


def test_real_slice_matches_the_reference_tree(tmp_path):
    """The real slice keeps its total and gives the tree of the original flame script."""
    lines = profiling.read_folded(SLICE)
    tree, total = profiling.flame_tree(lines)
    assert total == SLICE_TOTAL
    # Node count and component mix from the study's make_flame.py on the same file.
    assert len(names(tree)) == 70
    kinds: dict[str, int] = {}
    stack = list(tree['children'])
    while stack:
        node = stack.pop()
        kinds[node['k']] = kinds.get(node['k'], 0) + 1
        stack += node.get('children', [])
    assert kinds == {'other': 21, 'proteus': 17, 'zalmoxis': 12, 'native': 10, 'aragog': 8,
                     'julia': 1}  # fmt: skip


def test_read_folded_rejects_malformed_lines(tmp_path):
    """A line without a non-negative integer count names the file, line and format."""
    bad = tmp_path / 'bad.folded'
    for tail in ('a;c three', 'a;c -3', 'a;c', ' 3'):
        bad.write_text(f'a;b 3\n\n{tail}\n')
        with pytest.raises(ValueError, match='bad.folded line 2: expected "frame;frame'):
            profiling.read_folded(bad)
    gz = tmp_path / 'ok.folded.gz'
    gz.write_bytes(gzip.compress(b'a;b 3\n\n'))
    assert profiling.read_folded(gz) == ['a;b 3']  # blank lines skipped


def test_collect_scalene_profile_dir(tmp_path):
    """collect writes both artifacts, keeps the samples, and returns run-relative paths."""
    profile_dir = tmp_path / 'run' / 'profile'
    profile_dir.mkdir(parents=True)
    (profile_dir / 'scalene-profile.json').write_text(SCALENE.read_text())
    artifacts = profiling.collect(profile_dir, {'run_id': 'r7'})
    assert artifacts == {'profile': 'profile/stacks.folded.gz', 'flame': 'profile/flame.html'}
    stacks = gzip.decompress((tmp_path / 'run' / artifacts['profile']).read_bytes()).decode()
    assert sum(int(line.rsplit(' ', 1)[1]) for line in stacks.splitlines()) == FIXTURE_TOTAL
    page = (tmp_path / 'run' / artifacts['flame']).read_text()
    assert 'profiler scalene' in page and 'run <code>r7</code>' in page
    # gzip header bytes 4-7 hold the mtime; zero keeps equal stacks byte-identical.
    assert (profile_dir / 'stacks.folded.gz').read_bytes()[4:8] == bytes(4)


def test_collect_pyspy_and_missing_output(tmp_path):
    """py-spy output is used as folded stacks; an empty directory is an error."""
    (tmp_path / 'py-spy.folded').write_text('process 1:"proteus";main (proteus/cli.py) 4\n')
    profiling.collect(tmp_path)
    assert 'profiler py-spy' in (tmp_path / 'flame.html').read_text()
    assert gzip.decompress((tmp_path / 'stacks.folded.gz').read_bytes()).endswith(b' 4\n')
    empty = tmp_path / 'empty'
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match='no profiler output'):
        profiling.collect(empty)
    assert not (empty / 'flame.html').exists()
