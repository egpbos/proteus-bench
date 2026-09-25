"""Tests for proteus_bench.introspect, the metadata script run in the PROTEUS env.

Contract clauses: an installed distribution reports its version and its
direct_url.json (null for an index install); a missing one is left out; the
script output is one JSON object with the Python version.
"""

from __future__ import annotations

import json
import platform
import sys
from importlib import metadata

import pytest

from proteus_bench import introspect

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_describe_installed_and_missing():
    """pytest is installed from the index; the editable harness has an editable direct_url."""
    info = introspect.describe('pytest')
    assert info['version'] == metadata.version('pytest')
    assert info['direct_url'] is None
    harness = introspect.describe('proteus-bench')
    assert harness['direct_url']['dir_info'] == {'editable': True}
    assert introspect.describe('fwl-no-such-distribution') is None


def test_main_prints_one_json_object(capsys):
    """Missing names are omitted and the interpreter version is included."""
    introspect.main(['pytest', 'fwl-no-such-distribution'])
    report = json.loads(capsys.readouterr().out)
    assert report['python'] == platform.python_version()
    assert report['prefix'] == sys.prefix
    assert list(report['dists']) == ['pytest']
    introspect.main([])
    assert json.loads(capsys.readouterr().out)['dists'] == {}
