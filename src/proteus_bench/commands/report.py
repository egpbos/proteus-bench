"""``proteus-bench report``: build the static dashboard from a results store.

Reads ``<store>/records/**/*.json``, runs the analysis over all of them and
writes the site to ``--out``, replacing an earlier site there. An empty store
gives a site that says so. Artifact links point at the ``results`` branch of
``--repo`` (default: ``$GITHUB_REPOSITORY``, set in GitHub Actions); without a
repository the store paths are shown as text.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from proteus_bench.report.site import build_site
from proteus_bench.report.store import load_records


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--store',
        type=Path,
        required=True,
        help='results store (a checkout of the results branch)',
    )
    parser.add_argument(
        '--out', type=Path, required=True, help='directory to write the site to'
    )
    parser.add_argument(
        '--repo',
        default=os.environ.get('GITHUB_REPOSITORY'),
        help='owner/name of the repository whose results branch artifacts link to',
    )


def main(args: argparse.Namespace) -> int:
    from proteus_bench.analysis import analyse  # on use, so other subcommands never need it

    if not args.store.is_dir():
        print(f'store {args.store} is not a directory')
        return 1
    records = load_records(args.store)
    pages = build_site(records, analyse(records), args.out, args.store, args.repo or None)
    links = (
        f'artifact links to {args.repo}' if args.repo else 'no repository, so no artifact links'
    )
    print(f'{args.out}: {len(pages)} pages from {len(records)} runs, {links}')
    return 0
