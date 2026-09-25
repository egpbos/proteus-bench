"""Tests for proteus_bench.profiling: profiler commands, folded stacks and flame pages.

Contract clauses: wrap_command builds the scalene 2.3.0 recipe (run by the proteus
environment's Python, derived --profile-only, ``---`` before the proteus arguments,
JAX_DISABLE_JIT=0) and the py-spy raw recipe, and refuses unknown or unusable
profilers; profiler_of maps output files to profilers; scalene_to_folded keeps
every sample and refuses empty profiles; collapse_native merges each run of native
frames into one block, Julia or not; the flame page holds the whole tree with self
values summing to the total, in valid HTML with escaped text; collect writes
stacks.folded.gz and flame.html, returns their paths, and refuses ambiguous input.
package_dirs starts a subprocess and is tested in test_profiling_package_dirs.py.

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
from html.parser import HTMLParser
from pathlib import Path

import pytest

from proteus_bench import profiling

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

ZALMOXIS_STACK = (
    'start (proteus/cli.py);Proteus.start (proteus/proteus.py);'
    'zalmoxis_solver (proteus/interior_struct/zalmoxis.py);main (zalmoxis/solver.py);'
    '? [];__libc_start_main [libc.so.6];Py_RunMain [python3.12];'
    '_PyEval_EvalFrameDefault [python3.12];dgemm_ [libopenblas.so.0]'
)


@pytest.fixture
def tools(monkeypatch, tmp_path):
    """Fake PATH lookups and package discovery; returns the script path and probe calls."""
    script = str(tmp_path / 'env' / 'bin' / 'proteus')
    monkeypatch.setattr(
        profiling.shutil, 'which', {'py-spy': '/opt/bin/py-spy', 'proteus': script}.get
    )
    dirs = {
        'proteus': '/co/PROTEUS/src/proteus',
        'zalmoxis': '/env/site-packages/zalmoxis',
        'scalene': '/env/site-packages/scalene',
    }
    calls = []

    def fake_package_dirs(python, names):
        calls.append((python, names))
        return dict(dirs)

    monkeypatch.setattr(profiling, 'package_dirs', fake_package_dirs)
    return script, calls, dirs


def test_scalene_command_follows_the_recipe(tools, tmp_path):
    """The env's Python runs scalene with the verified flags, the script, --- and the args."""
    script, calls, _ = tools
    python = str(Path(script).parent / 'python')
    profile_dir = tmp_path / 'run' / 'profile'
    argv, env = profiling.wrap_command(
        'scalene', ['proteus', 'start', '-c', 'x.toml'], profile_dir
    )
    assert argv == [
        python, '-m', 'scalene', 'run', '--cpu-only', '--profile-all',
        '--profile-only', '/co/PROTEUS/src/proteus/,/env/site-packages/zalmoxis/',
        '--profile-exclude', '.jl,.julia',
        '-o', str(profile_dir / 'scalene-profile.json'),
        script, '---', 'start', '-c', 'x.toml',
    ]  # fmt: skip
    assert env == {'JAX_DISABLE_JIT': '0'}
    sep = argv.index('---')
    assert argv[sep - 1] == script
    assert argv[sep + 1 :] == ['start', '-c', 'x.toml']
    assert profile_dir.is_dir()
    # The interpreter that runs scalene is the one probed, and it is asked for scalene too.
    assert calls[0][0] == Path(python)
    assert 'scalene' in calls[0][1]
    # Limit: no proteus arguments still ends with the separator.
    assert profiling.wrap_command('scalene', ['proteus'], profile_dir)[0][-1] == '---'


def test_scalene_refuses_what_it_cannot_profile(tools, tmp_path):
    """A missing script, scalene or proteus package raises and creates nothing."""
    _, _, dirs = tools
    target = tmp_path / 'p'
    with pytest.raises(RuntimeError, match='console script'):
        profiling.wrap_command('scalene', ['no-such-proteus', 'start'], target)
    del dirs['scalene']
    with pytest.raises(RuntimeError, match='scalene is not installed in the environment'):
        profiling.wrap_command('scalene', ['proteus', 'start'], target)
    dirs['scalene'] = '/env/site-packages/scalene'
    del dirs['proteus']
    with pytest.raises(RuntimeError, match='cannot find the proteus package'):
        profiling.wrap_command('scalene', ['proteus', 'start'], target)
    assert not target.exists()


def test_unknown_profiler_raises(tools, tmp_path):
    """Only scalene and py-spy are profilers; 'none' and typos are refused."""
    for name in ('none', 'Scalene', 'pyspy', ''):
        with pytest.raises(ValueError, match='unknown profiler'):
            profiling.wrap_command(name, ['proteus', 'start'], tmp_path / 'p')
    assert not (tmp_path / 'p').exists()  # nothing is created for a refused profiler


def test_pyspy_command_and_its_limits(tools, monkeypatch, tmp_path):
    """py-spy records raw folded stacks of all subprocesses; macOS needs root."""
    monkeypatch.setattr(profiling.sys, 'platform', 'linux')
    argv, env = profiling.wrap_command('py-spy', ['proteus', 'start', '-c', 'x.toml'], tmp_path)
    assert argv == [
        '/opt/bin/py-spy', 'record', '--format', 'raw', '--nolineno', '--subprocesses',
        '-o', str(tmp_path / 'py-spy.folded'), '--', 'proteus', 'start', '-c', 'x.toml',
    ]  # fmt: skip
    assert env == {}  # JAX_DISABLE_JIT is a scalene guard only
    monkeypatch.setattr(profiling.sys, 'platform', 'darwin')
    monkeypatch.setattr(profiling.os, 'geteuid', lambda: 501)
    with pytest.raises(RuntimeError, match='root on macOS'):
        profiling.wrap_command('py-spy', ['proteus'], tmp_path)
    monkeypatch.setattr(profiling.os, 'geteuid', lambda: 0)
    assert profiling.wrap_command('py-spy', ['proteus'], tmp_path)[0][-1] == 'proteus'
    monkeypatch.setattr(profiling.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='py-spy not found on PATH'):
        profiling.wrap_command('py-spy', ['proteus'], tmp_path)


def test_profiler_of_output_files():
    """JSON is scalene; py-spy is known by its output name; other folded files are unknown."""
    assert profiling.profiler_of(Path('run/profile/scalene-profile.json')) == 'scalene'
    assert profiling.profiler_of(Path('cluster-prof.json')) == 'scalene'
    assert profiling.profiler_of(Path('profile/py-spy.folded')) == 'py-spy'
    assert profiling.profiler_of(Path('profile/stacks.folded.gz')) is None
    assert profiling.profiler_of(Path('py-spy.folded.gz')) is None


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


def test_scalene_conversion_keeps_every_sample(profiles):
    """Labels follow the frame kinds, equal labelled stacks merge, the total is kept."""
    lines = profiling.scalene_to_folded(json.loads(profiles.scalene.read_text()))
    counts = dict(line.rsplit(' ', 1) for line in lines)
    assert sum(int(n) for n in counts.values()) == profiles.scalene_total
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


def test_empty_scalene_profiles_are_refused(profiles):
    """No stacks, only native stacks, or no combined_stacks at all: a clear error."""
    profile = json.loads(profiles.scalene.read_text())
    with pytest.raises(ValueError, match='--profile-all'):
        profiling.scalene_to_folded(profile | {'combined_stacks': [], 'files': {}})
    native_only = profile | {'combined_stacks': profile['combined_stacks'][-1:]}
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
    assert profiling.component('solve (aragog/solver.py:12)') == 'aragog'  # with line number
    assert profiling.component('run (proteus/atmos_clim/agni.py)') == 'proteus'
    assert profiling.component('[native code: Julia]') == 'julia'
    assert profiling.component('[native code]') == 'native'
    assert profiling.component('update (mors/star.py)') == 'other'
    assert profiling.component('thread (0x7f): MainThread') == 'other'
    assert profiling.component('') == 'other'


def test_pyspy_raw_labels_read_like_scalene(tmp_path):
    """A py-spy 0.4.2 raw line passes through unchanged and gets the same colours.

    Shape from py-spy v0.4.2: ``name (short_filename)`` with --nolineno
    (src/flamegraph.rs, Flamegraph::increment), short_filename is the path below the
    outermost package directory (src/python_spy.rs, shorten_filename), and
    --subprocesses adds a root ``process PID:"cmdline"`` frame (src/stack_trace.rs,
    ProcessInfo::to_frame). Function names may be unqualified there.
    """
    raw = tmp_path / 'py-spy.folded'
    line = (
        'process 4242:"/env/bin/python3.12 /env/bin/proteus start -c x.toml";'
        '<module> (proteus);start (proteus/cli.py);main (zalmoxis/solver.py) 7'
    )
    raw.write_text(line + '\n')
    assert profiling.read_folded(raw) == [line]
    tree, total = profiling.flame_tree([line])
    assert total == 7
    process = tree['children'][0]
    assert process['k'] == 'other'  # the process root frame
    assert process['children'][0]['k'] == 'proteus'  # console script, named 'proteus'
    leaf = process['children'][0]['children'][0]['children'][0]
    assert (leaf['name'], leaf['k'], leaf['value']) == (
        'main (zalmoxis/solver.py)',
        'zalmoxis',
        7,
    )


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
        assert self.open, f'</{tag}> without an open element'
        assert self.open.pop() == tag, f'unbalanced </{tag}>'
        self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts[-1] += data


def page_data(path: Path) -> dict:
    parser = PageParser()
    parser.feed(path.read_text())
    parser.close()
    assert parser.open == []
    (script,) = parser.scripts
    return json.loads(script.split('const DATA = ', 1)[1].split(';\n', 1)[0])


def nodes(node: dict) -> list[dict]:
    return [node] + [n for c in node.get('children', []) for n in nodes(c)]


def names(node: dict) -> list[str]:
    return [n['name'] for n in nodes(node)]


def test_flame_page_embeds_every_node_with_its_width(tmp_path, profiles):
    """The page holds the whole collapsed tree; self values add up to the total."""
    out = tmp_path / 'flame.html'
    lines = profiling.scalene_to_folded(json.loads(profiles.scalene.read_text()))
    meta = {'title': 'A <b> title', 'run_id': 'r1', 'commit': 'abd4ca53', 'profiler': 'scalene'}
    assert profiling.write_flame_page(lines, out, meta) == profiles.scalene_total
    tree = page_data(out)
    # root, start, Proteus.start, zalmoxis_solver, main, 1 block; run_atmosphere, 1 Julia
    # block; solve, 1 block; update_mors; 1 root block: 12 (18 without collapsing).
    assert len(nodes(tree)) == 12
    assert names(tree).count('[native code]') == 3
    assert sum(n['value'] for n in nodes(tree)) == profiles.scalene_total
    main = next(n for n in nodes(tree) if n['name'] == 'main (zalmoxis/solver.py)')
    assert main['value'] == 0  # all its samples are in the native block below it
    assert main['children'][0]['value'] == 40 + 25
    text = out.read_text()
    assert 'A &lt;b&gt; title' in text
    assert 'A <b> title' not in text
    assert f'{profiles.scalene_total:,} samples' in text


def test_meta_values_are_escaped(tmp_path):
    """Run ids, commits and profilers are text, never markup; empty ones are left out."""
    out = tmp_path / 'flame.html'
    meta = {'run_id': '<img src=x>', 'commit': 'a&b', 'profiler': '', 'title': None}
    profiling.write_flame_page(['a (proteus/x.py) 3'], out, meta)
    text = out.read_text()
    assert 'run <code>&lt;img src=x&gt;</code>' in text
    assert 'PROTEUS <code>a&amp;b</code>' in text
    assert '<img' not in text
    assert 'profiler ' not in text  # empty value omitted
    assert '<h1>PROTEUS CPU flame graph</h1>' in text  # default title


def test_flame_page_survives_hostile_names_and_refuses_empty(tmp_path):
    """A frame named like a closing script tag stays data; no samples is an error."""
    out = tmp_path / 'flame.html'
    profiling.write_flame_page(['evil</script><b> (proteus/x.py) 3'], out, {})
    assert names(page_data(out)) == ['all samples', 'evil</script><b> (proteus/x.py)']
    with pytest.raises(ValueError, match='no samples'):
        profiling.write_flame_page([], out, {})
    with pytest.raises(ValueError, match='no samples'):
        profiling.write_flame_page(['a (proteus/x.py) 0'], out, {})


def test_real_slice_matches_the_reference_counts(profiles):
    """The real slice keeps its total and its tree shape; a corrupt line is refused."""
    lines = profiling.read_folded(profiles.slice)
    tree, total = profiling.flame_tree(lines)
    assert total == profiles.slice_total
    assert sum(n['value'] for n in nodes(tree)) == total
    # sh tests/fixtures/count_flame_nodes.sh tests/fixtures/real-slice.folded prints
    # 69 nodes below the root, 10 native blocks and 1 Julia block.
    assert len(nodes(tree)) == 69 + 1
    assert names(tree).count('[native code]') == 10
    assert names(tree).count('[native code: Julia]') == 1
    with pytest.raises(ValueError, match='expected "frame;frame'):
        profiling.flame_tree([lines[0].rsplit(' ', 1)[0]])  # count cut off


def test_read_folded_rejects_malformed_lines(tmp_path):
    """A line without a non-negative integer count names the line and the format."""
    bad = tmp_path / 'bad.folded'
    for tail in ('a;c three', 'a;c -3', 'a;c', ' 3'):
        bad.write_text(f'a;b 3\n\n{tail}\n')
        with pytest.raises(ValueError, match='line 2: expected "frame;frame'):
            profiling.read_folded(bad)
    gz = tmp_path / 'ok.folded.gz'
    gz.write_bytes(gzip.compress(b'a;b 3\n\n'))
    assert profiling.read_folded(gz) == ['a;b 3']  # blank lines skipped


def test_collect_scalene_profile_dir(tmp_path, profiles):
    """collect writes both artifacts, keeps the samples, and returns run-relative paths."""
    profile_dir = tmp_path / 'run' / 'profile'
    profile_dir.mkdir(parents=True)
    (profile_dir / 'scalene-profile.json').write_text(profiles.scalene.read_text())
    artifacts = profiling.collect(profile_dir, {'run_id': 'r7'})
    assert artifacts == {'profile': 'profile/stacks.folded.gz', 'flame': 'profile/flame.html'}
    stacks = gzip.decompress((tmp_path / 'run' / artifacts['profile']).read_bytes()).decode()
    assert (
        sum(int(line.rsplit(' ', 1)[1]) for line in stacks.splitlines())
        == profiles.scalene_total
    )
    page = (tmp_path / 'run' / artifacts['flame']).read_text()
    assert 'profiler scalene' in page
    assert 'run <code>r7</code>' in page
    # gzip header bytes 4-7 hold the mtime; zero keeps equal stacks byte-identical.
    assert (profile_dir / 'stacks.folded.gz').read_bytes()[4:8] == bytes(4)


def test_collect_pyspy_missing_and_ambiguous_output(tmp_path):
    """py-spy output is used as folded stacks; no output or two outputs is an error."""
    (tmp_path / 'py-spy.folded').write_text('process 1:"proteus";main (proteus/cli.py) 4\n')
    profiling.collect(tmp_path)
    assert 'profiler py-spy' in (tmp_path / 'flame.html').read_text()
    assert gzip.decompress((tmp_path / 'stacks.folded.gz').read_bytes()).endswith(b' 4\n')
    (tmp_path / 'scalene-profile.json').write_text('{}')
    with pytest.raises(ValueError, match='several profilers'):
        profiling.collect(tmp_path)
    empty = tmp_path / 'empty'
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match='no profiler output'):
        profiling.collect(empty)
    assert not (empty / 'flame.html').exists()


def test_collect_names_the_empty_source(tmp_path):
    """An empty profile fails with the path of the file that holds it."""
    (tmp_path / 'py-spy.folded').write_text('')
    with pytest.raises(ValueError, match=r'py-spy\.folded: no samples'):
        profiling.collect(tmp_path)
    assert not (tmp_path / 'stacks.folded.gz').exists()
