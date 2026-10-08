"""Which code a run measured: git state of checkouts and versions of installed modules.

pip reports stale versions for editable installs, so those are also recorded
by their checkout's git SHA.
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from proteus_bench import __version__, introspect

# PyPI distribution names, as listed in PROTEUS's pyproject.toml dependencies
MODULE_DISTS = (
    'fwl-aragog',
    'fwl-calliope',
    'fwl-janus',
    'fwl-mors',
    'fwl-zalmoxis',
    'fwl-zephyrus',
)
PACKAGES = ('jax', 'jaxlib', 'numpy', 'scipy', 'juliacall', 'scalene')
SHA_LEN = 8


def git(path: Path, *args: str) -> str | None:
    """Stripped stdout of ``git -C path args``, or None when git fails."""
    proc = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_state(path: Path) -> dict | None:
    """``{sha, dirty, branch?, describe}`` when ``path`` is the top of a git work tree.

    None otherwise, so a directory inside another checkout never gets the outer
    commit. ``dirty`` ignores untracked files, such as a runs directory.
    """
    top = git(path, 'rev-parse', '--show-toplevel')
    if top is None or Path(top).resolve() != Path(path).resolve():
        return None
    state = {
        'sha': git(path, 'rev-parse', f'--short={SHA_LEN}', 'HEAD'),
        'dirty': bool(git(path, 'status', '--porcelain', '--untracked-files=no')),
    }
    branch = git(path, 'rev-parse', '--abbrev-ref', 'HEAD')
    if branch and branch != 'HEAD':  # detached, as on CI checkouts
        state['branch'] = branch
    state['describe'] = git(path, 'describe', '--tags', '--always', '--dirty')
    return state


def introspect_env(python: str) -> dict:
    """Report of ``introspect.py`` run by ``python``; ``ValueError`` if it cannot run."""
    argv = [python, introspect.__file__, *MODULE_DISTS, *PACKAGES]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as err:
        raise ValueError(f'cannot run --python {python}: {err}') from err
    if proc.returncode != 0:
        raise ValueError(f'--python {python} failed to report its packages:\n{proc.stderr}')
    return json.loads(proc.stdout)


def proteus_checkout(python: str, origin: str | None) -> Path:
    """The PROTEUS checkout that ``python`` imports ``proteus`` from.

    ``origin`` is ``<checkout>/src/proteus/__init__.py``; the checkout holds
    ``pyproject.toml``, as PROTEUS's ``get_proteus_dir`` requires. Raises
    ``ValueError`` when proteus is not importable or not from a checkout.
    """
    if origin is None:
        raise ValueError(
            f'--python {python} cannot import proteus; activate the PROTEUS environment'
        )
    root = Path(origin).parents[2]
    if not (root / 'pyproject.toml').is_file():
        raise ValueError(
            f'--python {python} imports proteus from {origin}, not from a PROTEUS checkout '
            f'({root} has no pyproject.toml); install PROTEUS editable from its checkout'
        )
    return root


def module_state(info: dict) -> dict:
    """Record entry for one module from its introspected version and direct_url."""
    url = info['direct_url'] or {}
    version = {'version': info['version']}
    if url.get('dir_info', {}).get('editable'):
        checkout = Path(urllib.request.url2pathname(urllib.parse.urlparse(url['url']).path))
        state = git_state(checkout)
        extra = {'sha': state['sha'], 'dirty': state['dirty']} if state else {}
        return {'source': 'editable', **version, **extra}
    if 'vcs_info' in url:
        return {'source': 'git', **version, 'sha': url['vcs_info']['commit_id'][:SHA_LEN]}
    # No direct_url means an index install; a local path or archive is neither
    return {'source': 'unknown' if url else 'pypi', **version}


def checkout_state(path: Path | None) -> dict | None:
    """Entry for a non-Python module checkout (AGNI, SOCRATES); None if absent."""
    if path is None or not path.is_dir():
        return None
    state = git_state(path)
    return (
        {'source': 'git', 'sha': state['sha'], 'dirty': state['dirty']}
        if state
        else {'source': 'unknown'}
    )


def code_section(proteus: dict, env_report: dict, proteus_root: Path, env: dict) -> dict:
    """The record's ``code`` section.

    AGNI is looked up where PROTEUS loads it (``<checkout>/AGNI``),
    SOCRATES at ``$RAD_DIR``.
    """
    dists = env_report['dists']
    modules = {
        name.removeprefix('fwl-'): module_state(dists[name])
        for name in MODULE_DISTS
        if name in dists
    }
    rad_dir = env.get('RAD_DIR')
    for name, path in (
        ('agni', proteus_root / 'AGNI'),
        ('socrates', Path(rad_dir) if rad_dir else None),
    ):
        state = checkout_state(path)
        if state is not None:
            modules[name] = state
    packages = {name: dists[name]['version'] for name in PACKAGES if name in dists}
    return {'proteus': proteus, 'modules': modules, 'packages': packages}


def harness_state() -> dict:
    """Version and, when known, commit of proteus-bench itself."""
    state = {'version': __version__}
    repo = Path(__file__).resolve().parents[2]
    checkout = git_state(repo) if (repo / 'pyproject.toml').is_file() else None
    if checkout:
        state['sha'] = checkout['sha']
        return state
    installed = introspect.describe('proteus-bench')
    vcs = ((installed or {}).get('direct_url') or {}).get('vcs_info')
    if vcs:
        state['sha'] = vcs['commit_id'][:SHA_LEN]
    return state
