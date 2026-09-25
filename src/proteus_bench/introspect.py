"""Report installed distributions of the interpreter that runs this file, as JSON.

Run as a script by the target environment's Python (``python introspect.py
NAME...``), so it must import nothing from proteus_bench and never import the
packages it reports on: metadata only. Prints ``{"python": version, "prefix":
sys.prefix, "dists": {name: {"version": v, "direct_url": {...} or null}}}``;
names that are not installed are left out.
"""

from __future__ import annotations

import json
import platform
import sys
from importlib import metadata


def describe(name: str) -> dict | None:
    """Version and PEP 610 ``direct_url.json`` of one distribution, or None."""
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    direct_url = dist.read_text('direct_url.json')
    return {
        'version': dist.version,
        'direct_url': json.loads(direct_url) if direct_url else None,
    }


def main(names: list[str]) -> None:
    dists = {name: describe(name) for name in names}
    found = {name: info for name, info in dists.items() if info is not None}
    report = {'python': platform.python_version(), 'prefix': sys.prefix, 'dists': found}
    print(json.dumps(report))


if __name__ == '__main__':
    main(sys.argv[1:])
