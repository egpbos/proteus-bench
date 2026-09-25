"""``proteus-bench`` command-line entry point.

Each subcommand lives in its own module under ``proteus_bench.commands`` and
exposes ``add_arguments(parser)`` and ``main(args) -> int``. Register new
subcommands in ``COMMANDS``; keep this file free of command logic.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from proteus_bench import __version__

# name -> (module under proteus_bench.commands, one-line help)
COMMANDS = {
    'flame': ('flame', 'build a flame-graph page from a scalene JSON or folded-stacks file'),
    'validate': ('validate', 'check timing.jsonl and run-record files against the schemas'),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='proteus-bench', description=__doc__.splitlines()[0])
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    sub = parser.add_subparsers(dest='command', required=True)
    for name, (module, help_text) in COMMANDS.items():
        mod = importlib.import_module(f'proteus_bench.commands.{module}')
        cmd = sub.add_parser(name, help=help_text, description=help_text)
        mod.add_arguments(cmd)
        cmd.set_defaults(_main=mod.main)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args._main(args)


if __name__ == '__main__':
    sys.exit(main())
