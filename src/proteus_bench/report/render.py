"""The Jinja2 environment of the dashboard: page templates in ``templates/``, autoescaped.

Record and analysis contents are untrusted (anyone who can push to the results
branch publishes them), so every value reaches HTML through autoescaping and
every embedded JSON value through the ``tojson`` filter. Templates read records
with subscripts (``record['outcome']``), so a record key such as ``items`` can
never resolve to a method.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from proteus_bench.report import fmt
from proteus_bench.report.figures import TEMPLATES

PLOTLY_JS = 'https://cdn.jsdelivr.net/npm/plotly.js-basic-dist-min@4.1.1/plotly-basic.min.js'
PLOTLY_INTEGRITY = 'sha384-N2HZsG+IG/3J8CwhGGYz/kmzZ0sprpPWwjhor9ZI4lxuf47i9DXK2/NDGxMaJiEb'

ENV = Environment(
    # The package root holds tokens_head.html, which the flame page shares
    loader=FileSystemLoader([Path(__file__).parent / 'templates', Path(__file__).parents[1]]),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
ENV.policies['json.dumps_kwargs'] = {'sort_keys': True, 'separators': (',', ':')}
ENV.filters |= {
    name: getattr(fmt, name)
    for name in (
        'commit_url',
        'confirmation',
        'fmt_rel',
        'fmt_s',
        'fmt_time',
        'fmt_value',
        'group_label',
        'group_of',
        'run_href',
        'series_href',
    )
}
ENV.globals |= {
    'plotly_js': PLOTLY_JS,
    'plotly_integrity': PLOTLY_INTEGRITY,
    'plotly_templates': TEMPLATES,
}


def render(name: str, **context) -> str:
    """The template ``name`` rendered with ``context``."""
    return ENV.get_template(name).render(**context)
