"""Tests for the dashboard's Jinja2 environment (proteus_bench.report.render).

Contract clauses: every value is HTML-escaped, in text and in attributes;
JSON embedded in a script element cannot close it and decodes to the same
value; a template naming a value it was not given fails instead of rendering
an empty string; the pages load the tokens and plotly.js pinned by hash.
"""

from __future__ import annotations

import json
from importlib import resources

import jinja2
import pytest

from proteus_bench.report.render import ENV, PLOTLY_INTEGRITY, PLOTLY_JS, render

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def test_values_are_escaped_in_text_and_attributes():
    """Markup and quotes in a value come out as entities."""
    html = ENV.from_string('<a title="{{ v }}">{{ v }}</a>').render(v='"><script>x</script>')
    assert html == (
        '<a title="&#34;&gt;&lt;script&gt;x&lt;/script&gt;">'
        '&#34;&gt;&lt;script&gt;x&lt;/script&gt;</a>'
    )


def test_tojson_cannot_close_its_script():
    """``</script>`` inside a value is escaped yet decodes back to the same value."""
    value = {'k': "</script><b>'&"}
    html = ENV.from_string('<script type="application/json">{{ v | tojson }}</script>').render(
        v=value
    )
    body = html.removeprefix('<script type="application/json">').removesuffix('</script>')
    assert '<' not in body
    assert json.loads(body) == value


def test_missing_values_fail_loudly():
    """A misspelt name raises rather than rendering as an empty string."""
    template = ENV.from_string('{{ recrod }}')
    with pytest.raises(jinja2.UndefinedError):
        template.render(record={})


def test_pages_load_pinned_tokens_and_plotly():
    """Pages carry the tokens head the flame page shares; charts load plotly.js by hash."""
    compare = render('compare.html', root='', data={'runs': []})
    head = resources.files('proteus_bench').joinpath('tokens_head.html').read_text()
    assert head.strip() in compare
    assert '@formingworlds/proteus-tokens@1.3.0/tokens.css" integrity="sha384-' in head
    assert 'plotly' not in compare  # no charts, no plotly.js
    scripts = ENV.from_string(
        "{% from 'macros.html' import plotly_script %}{{ plotly_script('../') }}"
    ).render()
    assert (
        f'src="{PLOTLY_JS}" integrity="{PLOTLY_INTEGRITY}" crossorigin="anonymous"' in scripts
    )
    assert '@4.1.1/' in PLOTLY_JS  # the plotly.js that plotly.py 7.1 writes figures for
    assert '<script src="../charts.js"></script>' in scripts
    assert json.loads(
        scripts.split('id="plotly-templates">')[1].split('</script>')[0]
    ).keys() == {
        'light',
        'dark',
    }
