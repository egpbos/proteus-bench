"""Tests for proteus_bench.theme: the head that loads the design tokens.

Contract clauses: the head loads tokens.css of proteus-tokens 1.3.0 from jsDelivr
with the integrity hash of that file and CORS, so the browser refuses any other
content; every ``--pt-*`` token the generated pages use is declared in that
version, since an undeclared one silently draws nothing.
"""

from __future__ import annotations

import base64
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from proteus_bench import theme

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

SRC = Path(__file__).parents[1] / 'src' / 'proteus_bench'


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == 'link':
            self.links.append(dict(attrs))


def test_head_pins_the_tokens_file_by_hash(tokens_css):
    """The link names version 1.3.0 and carries the sha384 of that file, with CORS."""
    parser = Links()
    parser.feed(theme.HEAD)
    (link,) = parser.links
    assert link['href'] == (
        'https://cdn.jsdelivr.net/npm/@formingworlds/proteus-tokens@1.3.0/tokens.css'
    )
    digest = base64.b64encode(hashlib.sha384(tokens_css.read_bytes()).digest()).decode()
    assert link['integrity'] == f'sha384-{digest}'
    assert link['crossorigin'] == 'anonymous'  # without it the browser drops the stylesheet
    sha256 = base64.b64encode(hashlib.sha256(tokens_css.read_bytes()).digest()).decode()
    assert sha256 == 'GdMs99FiCU4rXlJvTivLFaRlNnykCibd/Bd4LME3Mw0='  # jsDelivr's file list


def test_pages_use_only_declared_tokens(tokens):
    """Every --pt-* name in the page sources exists in tokens 1.3.0; a typo would not."""
    sources = [p for p in SRC.rglob('*') if p.suffix in {'.py', '.html', '.css', '.js'}]
    used = {
        name for p in sources for name in re.findall(r'--pt-[a-z0-9-]*[a-z0-9]', p.read_text())
    }
    assert {'--pt-dom-interior', '--pt-void', '--pt-verdant'} <= used
    assert used - set(tokens['dark']) == set()
