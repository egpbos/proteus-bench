"""Tests for ``proteus-bench prepare`` (proteus_bench.commands.prepare).

Contract clauses: an older commit gets branch bench/<sha> with the patches
committed on top, so the tree is clean and the parent is the original commit; a
commit that already logs [INIT_TIMING] is checked out unchanged; a patch that
does not apply leaves a clean tree and exit 1; uncommitted changes or an
unknown commit stop it with exit 2; the bundled patches add every line
proteus-bench reads.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from proteus_bench import cli
from proteus_bench.commands import prepare

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

SOURCE = 'src/proteus/proteus.py'
PATCH = f"""diff --git a/{SOURCE} b/{SOURCE}
--- a/{SOURCE}
+++ b/{SOURCE}
@@ -1 +1,2 @@
 print('loop')
+print('[INIT_TIMING] total=0.000')
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ['git', '-C', str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path, git_env, git_repo, monkeypatch):
    """A PROTEUS-like checkout whose one commit predates the timing lines; returns (root, sha)."""
    root = tmp_path / 'PROTEUS'
    (root / 'src' / 'proteus').mkdir(parents=True)
    (root / SOURCE).write_text("print('loop')\n")
    sha = git_repo(root)
    patches = tmp_path / 'patches'
    patches.mkdir()
    (patches / '01-timing.patch').write_text(PATCH)
    monkeypatch.setattr(prepare, 'PATCHES', patches)
    return root, sha


def test_old_commit_is_patched_on_a_bench_branch(checkout, capsys):
    """bench/<sha> holds one commit with the patch; its parent is the commit asked for."""
    root, sha = checkout
    assert cli.main(['prepare', sha, '--proteus', str(root)]) == 0
    assert git(root, 'rev-parse', '--abbrev-ref', 'HEAD') == f'bench/{sha[:8]}'
    assert git(root, 'rev-parse', 'HEAD^') == sha
    assert '[INIT_TIMING]' in (root / SOURCE).read_text()
    assert git(root, 'status', '--porcelain') == ''
    assert f'bench/{sha[:8]}' in capsys.readouterr().out


def test_commit_that_logs_the_lines_is_left_as_it_is(checkout, capsys):
    """Preparing the patched commit again checks it out detached, with no new branch."""
    root, sha = checkout
    cli.main(['prepare', sha, '--proteus', str(root)])
    patched = git(root, 'rev-parse', 'HEAD')
    assert cli.main(['prepare', patched, '--proteus', str(root)]) == 0
    assert git(root, 'rev-parse', 'HEAD') == patched
    assert git(root, 'rev-parse', '--abbrev-ref', 'HEAD') == 'HEAD'  # detached
    assert 'already logs the timing lines' in capsys.readouterr().out


def test_patch_that_does_not_apply_leaves_a_clean_tree(checkout, capsys):
    """Changed context makes the patch fail: exit 1, the reason, and no half-applied files."""
    root, _ = checkout
    (root / SOURCE).write_text("print('other loop')\n")
    git(root, 'commit', '-q', '-am', 'change the loop')
    assert cli.main(['prepare', 'HEAD', '--proteus', str(root)]) == 1
    assert '01-timing.patch does not apply' in capsys.readouterr().err
    assert git(root, 'status', '--porcelain') == ''


def test_uncommitted_changes_or_an_unknown_commit_stop_it(checkout, capsys):
    """Nothing is switched in a dirty checkout, and a bad commit name is named."""
    root, sha = checkout
    assert cli.main(['prepare', 'no-such-commit', '--proteus', str(root)]) == 2
    (root / SOURCE).write_text("print('edited')\n")
    assert cli.main(['prepare', sha, '--proteus', str(root)]) == 2
    assert 'uncommitted changes' in capsys.readouterr().err
    assert git(root, 'rev-parse', '--abbrev-ref', 'HEAD') == 'main'


def test_bundled_patches_add_the_lines_proteus_bench_reads():
    """The shipped patches log [INIT_TIMING], init_stage on [IT_TIMING] and [BACKEND]."""
    texts = {
        p.name: p.read_text() for p in prepare.PATCHES.iterdir() if p.name.endswith('.patch')
    }
    assert sorted(texts) == ['01-timing-lines.patch', '02-aragog-backend.patch']
    added = '\n'.join(
        line for text in texts.values() for line in text.splitlines() if line.startswith('+')
    )
    for mark in ('[INIT_TIMING]', "'[IT_TIMING] iter=%d init_stage=%d %s'", '[BACKEND] aragog'):
        assert mark in added, mark
