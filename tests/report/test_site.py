"""Tests for the dashboard site builder (proteus_bench.report.site) on the fixture store.

Contract clauses: every page is written (overview, compare, one series page per
group, one run page per record) and parses with every tag closed; every
relative link and asset resolves to a written file; each series chart has one
point per analysis point, plus its flags, steps and boundaries; the run page
puts failed checks first; the series filter index holds exactly the settings
keys that differ between runs; record text cannot break out of HTML or of an
embedded script; an analysis of another schema or naming unknown runs is refused.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from proteus_bench.report.site import build_site
from proteus_bench.report.store import load_records

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

VOID = {'meta', 'link', 'br', 'input', 'img', 'hr', 'source', 'wbr', 'col', 'area', 'base'}
HABROK = 'series/all_options--default--habrok-vink.html'
GHA = 'series/all_options--default--gha-ubuntu-epyc7763.html'
R4 = 'runs/20260921T031000Z-habrok-default-r4.html'
R8 = 'runs/20260925T031000Z-habrok-default-r8.html'


class Page(HTMLParser):
    """Parses one page: tag balance, links, SVG charts and embedded JSON."""

    def __init__(self, text: str):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.links: list[str] = []
        self.charts: dict[str, dict[str, list[dict]]] = {}
        self.scripts: dict[str, str] = {}
        self._chart: str | None = None
        self._script: str | None = None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.links += [attrs[k] for k in ('href', 'src') if attrs.get(k)]
        if tag == 'svg' and 'data-metric' in attrs:
            self._chart = attrs['data-metric']
            self.charts[self._chart] = {}
        elif self._chart:
            for cls in (attrs.get('class') or '').split():
                self.charts[self._chart].setdefault(cls, []).append(attrs)
        if tag == 'script' and attrs.get('type') == 'application/json':
            self._script = attrs['id']
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if self._chart:
            for cls in (dict(attrs).get('class') or '').split():
                self.charts[self._chart].setdefault(cls, []).append(dict(attrs))

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f'</{tag}> closes {self.stack[-1:] or "nothing"}')
            return
        self.stack.pop()
        if tag == 'svg':
            self._chart = None

    def handle_data(self, data):
        if self._script:
            self.scripts[self._script] = data
            self._script = None


@pytest.fixture
def site(tmp_path, store, analysis) -> Path:
    out = tmp_path / 'site'
    build_site(load_records(store), analysis, out, store)
    return out


def parsed(site: Path, relpath: str) -> Page:
    return Page((site / relpath).read_text())


def test_every_page_is_written_and_well_formed(site, records):
    """Overview, compare, 2 series pages and 10 run pages; every tag is closed."""
    pages = sorted(
        p.relative_to(site).as_posix() for p in site.glob('**/*.html') if 'files' not in p.parts
    )
    expected = ['compare.html', 'index.html', GHA, HABROK] + sorted(
        f'runs/{r["run_id"]}.html' for r in records
    )
    assert pages == sorted(expected)
    for relpath in pages:
        page = parsed(site, relpath)
        assert page.errors == [], relpath
        assert page.stack == [], f'{relpath}: unclosed {page.stack}'


def test_empty_store_gives_a_site_that_says_so(tmp_path):
    """No records and no series: the overview and compare pages still build and parse."""
    (tmp_path / 'store' / 'records').mkdir(parents=True)
    empty = {'schema': 'proteus-bench-analysis/1', 'series': []}
    pages = build_site([], empty, tmp_path / 'site', tmp_path / 'store')
    assert sorted(p.name for p in pages) == ['compare.html', 'index.html']
    page = parsed(tmp_path / 'site', 'index.html')
    assert page.stack == [] and page.errors == []
    assert 'No runs in the store yet' in (tmp_path / 'site' / 'index.html').read_text()


def test_relative_links_resolve(site):
    """Every relative href/src of every page names a written file; external links are https."""
    checked = 0
    for path in site.glob('**/*.html'):
        if 'files' in path.parts:
            continue
        for link in Page(path.read_text()).links:
            if link.startswith(('https://', '#')):
                continue
            target = (path.parent / link.split('?')[0].split('#')[0]).resolve()
            assert target.is_file(), f'{path.relative_to(site)} links to missing {link}'
            checked += 1
    assert checked > 100  # the run tables and chart points alone give well over 100 links


def test_flame_graph_is_copied_and_linked_missing_log_is_stated(site):
    """r8 links its copied flame page; r2's log, absent from the store, is named as missing."""
    r8 = parsed(site, R8)
    flame = [link for link in r8.links if link.endswith('.flame.html')]
    assert flame == ['../files/profiles/2026/20260925T031000Z-habrok-default-r8.flame.html'] * 2
    assert (site / R8).parent.joinpath(flame[0]).resolve().is_file()
    r2 = (site / 'runs/20260919T031000Z-habrok-default-r2.html').read_text()
    assert 'missing from the store' in r2
    assert '.log.gz' not in r2.split('Artifacts')[-1].split('Settings')[0]


def test_series_charts_have_one_point_per_run_and_the_marks(site, analysis):
    """Each chart has as many points as its series; flags, steps and boundaries are drawn."""
    habrok = parsed(site, HABROK).charts
    expected = {
        s['key']['metric']: s
        for s in analysis['series']
        if s['key']['machine_class'] == 'habrok-vink'
    }
    assert set(habrok) == set(expected)
    for metric, marks in habrok.items():
        assert len(marks['pt']) == len(expected[metric]['points']) == 8, metric
        assert len(marks.get('bnd', [])) == 1, metric
    atmos = habrok['loop.atmos.per_iter_median']
    assert len(atmos['flag']) == 2
    assert len(atmos['hollow']) == 1  # r7 unconfirmed; r8 confirmed is filled
    assert len(atmos['step']) == 1
    assert 'step' not in habrok['total'] and 'flag' not in habrok['total']
    gha = parsed(site, GHA).charts
    assert all(len(m['pt']) == 2 and 'bnd' not in m and 'band' not in m for m in gha.values())


def test_boundary_hover_names_the_changed_keys(site):
    """The boundary's title carries the changed settings key and the first run after it."""
    text = (site / HABROK).read_text()
    assert 'changed keys: interior_struct.zalmoxis.use_jax' in text
    assert 'before run 20260923T031000Z-habrok-default-r6' in text


def test_run_page_puts_failed_checks_first(site):
    """r4's failed checks and not-comparable reasons come before the summary; r1 has none."""
    text = (site / R4).read_text()
    problems = text.index('id="problems"')
    assert problems < text.index('<h2>Summary</h2>')
    callout = text[problems : text.index('</section>', problems)]
    assert 'Check failed: cvode_importable' in callout
    assert 'Check failed: expected_backends' in callout
    assert 'proteus_doctor' not in callout  # passed checks stay out of the callout
    assert (
        'id="problems"'
        not in (site / 'runs/20260918T031000Z-habrok-default-r1.html').read_text()
    )
    failed = (site / 'runs/20260924T120000Z-gha-default-g2.html').read_text()
    assert 'Run failed' in failed and 'AGNI did not converge' in failed


def test_filter_index_holds_only_differing_settings(site):
    """use_jax differs (5 false, 3 true); constant keys are not filterable; one-setting groups say so."""
    index = json.loads(parsed(site, HABROK).scripts['settings-index'])
    assert list(index) == ['interior_struct.zalmoxis.use_jax']
    assert {v: len(ids) for v, ids in index['interior_struct.zalmoxis.use_jax'].items()} == {
        'false': 5,
        'true': 3,
    }
    gha = parsed(site, GHA)
    assert 'settings-index' not in gha.scripts
    assert 'same settings' in (site / GHA).read_text()


def test_record_text_is_escaped(tmp_path, records, analysis):
    """A hostile settings value cannot close the embedded script or inject markup."""
    evil = '</script><img src=x onerror=alert(1)>'
    records[0]['benchmark']['settings']['interior_struct.zalmoxis.use_jax'] = evil
    (tmp_path / 'store').mkdir()
    build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')
    for relpath in (HABROK, 'runs/20260918T031000Z-habrok-default-r1.html'):
        text = (tmp_path / 'site' / relpath).read_text()
        assert '<img src=x' not in text, relpath
        assert Page(text).stack == []
    index = json.loads(parsed(tmp_path / 'site', HABROK).scripts['settings-index'])
    assert json.dumps(evil) in index['interior_struct.zalmoxis.use_jax']


def test_analysis_of_another_schema_is_refused(tmp_path, records):
    """A different analysis schema raises instead of rendering a misleading site."""
    with pytest.raises(ValueError, match='proteus-bench-analysis/1'):
        build_site(
            records, {'schema': 'proteus-bench-analysis/2', 'series': []}, tmp_path, tmp_path
        )
    assert not (tmp_path / 'index.html').exists()


def test_series_naming_an_unknown_run_is_refused(tmp_path, records, analysis):
    """A flag on a run that is not a point of its series is an analysis bug, reported with the run."""
    analysis['series'][0]['flags'].append(
        {
            'run_id': 'ghost',
            'kind': 'regression',
            'delta_rel': 0.1,
            'delta_abs': 1.0,
            'threshold_rel': 0.05,
            'confirmed': False,
        }
    )
    (tmp_path / 'store').mkdir()
    with pytest.raises(ValueError, match="'ghost'"):
        build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')
    with pytest.raises(ValueError, match='stay inside the store'):
        records[0]['artifacts']['log'] = '../outside.log'
        analysis['series'][0]['flags'].pop()
        build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')


def test_zero_baseline_and_null_relative_values_render(tmp_path, records, analysis):
    """Baseline median 0 and a flag with null delta_rel/threshold_rel give n/a, not a crash."""
    total = next(
        s
        for s in analysis['series']
        if s['key']['metric'] == 'total' and s['key']['machine_class'] == 'habrok-vink'
    )
    total['baseline'] = {'median': 0.0, 'sigma': 0.0, 'n': 3}
    last = total['points'][-1]['run_id']
    total['flags'] = [
        {
            'run_id': last,
            'kind': 'regression',
            'delta_rel': None,
            'delta_abs': 1804.8,
            'threshold_rel': None,
            'confirmed': False,
        }
    ]
    (tmp_path / 'store').mkdir()
    build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')
    index = (tmp_path / 'site' / 'index.html').read_text()
    assert 'baseline median is 0' in index
    assert 'total regression n/a, unconfirmed' in index
    assert 'threshold n/a' in (tmp_path / 'site' / HABROK).read_text()
