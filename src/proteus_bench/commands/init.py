"""``proteus-bench init``: write the user config file.

Every key is written with a comment; the ones given as options are active and
the rest stay commented out at their defaults. An existing file is kept unless
``--force`` is given.
"""

from __future__ import annotations

import argparse
import tomllib

from proteus_bench import userconfig

# option dest -> (table, key) in the config file
OPTIONS = {
    'machine_label': ('machine', 'label'),
    'machine_class': ('machine', 'class'),
    'remote': ('store', 'remote'),
    'branch': ('store', 'branch'),
    'cache_dir': ('store', 'cache_dir'),
    'runs_dir': ('runs', 'dir'),
    'env_activation': ('slurm', 'env_activation'),
    'sbatch_options': ('slurm', 'sbatch_options'),
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    for dest, (table, key) in OPTIONS.items():
        flag = '--' + dest.replace('_', '-')
        if dest == 'sbatch_options':
            parser.add_argument(
                '--sbatch-option',
                dest=dest,
                action='append',
                help='repeat for several; use --sbatch-option=--partition=X for values starting with -',
            )
        else:
            parser.add_argument(flag, dest=dest, help=f'sets {table}.{key}')
    parser.add_argument('--force', action='store_true', help='overwrite an existing file')


def main(args: argparse.Namespace) -> int:
    path = userconfig.config_path()
    if path.exists() and not args.force:
        print(f'{path} exists; not overwritten (use --force to replace it)')
        return 1
    values: dict[str, dict] = {}
    for dest, (table, key) in OPTIONS.items():
        value = getattr(args, dest)
        if value is not None:
            values.setdefault(table, {})[key] = value
    text = userconfig.render(values)
    try:
        userconfig.merge(tomllib.loads(text), path)  # refuse values load() would reject
    except ValueError as err:
        print(f'{err}\nnot written')
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f'wrote {path}')
    return 0
