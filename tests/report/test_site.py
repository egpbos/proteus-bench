"""Tests for the dashboard site builder (proteus_bench.report.site) on the fixture store.

Contract clauses: every page is written (overview, compare, one series page per
group, one run page per record) and parses with every tag closed; every
relative link and asset resolves to a written file; no store file is copied
into the site and artifacts link to the results branch only; each series chart
has one point per analysis point, plus its flags (three confirmation states),
steps and boundaries; the run page puts failed checks and not-comparable
reasons first and sorts failed checks first; the series filter index holds
exactly the settings keys that differ, and the run table rows carry run ids;
record and analysis text never reaches the HTML unescaped, and only https URLs
become links; groups whose names differ only in case get separate pages; a
rebuild removes pages of deleted runs; bad input is refused with its source.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from proteus_bench.report.fmt import series_href
from proteus_bench.report.site import build_site
from proteus_bench.report.store import load_records

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

VOID = {'meta', 'link', 'br', 'input', 'img', 'hr', 'source', 'wbr', 'col', 'area', 'base'}
HABROK = series_href(('all_options', 'default', 'habrok-vink'))
GHA = series_href(('all_options', 'default', 'gha-ubuntu-epyc7763'))
R1 = 'runs/20260918T031000Z-habrok-default-r1.html'
R2 = 'runs/20260919T031000Z-habrok-default-r2.html'
R4 = 'runs/20260921T031000Z-habrok-default-r4.html'
R7 = 'runs/20260924T031000Z-habrok-default-r7.html'
R8 = 'runs/20260925T031000Z-habrok-default-r8.html'
PLANT = 'q<textarea>"q'  # raw in the output, it would open an element or end an attribute


class Page(HTMLParser):
    """Parses one page: tag balance, links, SVG charts, table row ids and embedded JSON."""

    def __init__(self, text: str):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.links: list[str] = []
        self.row_ids: list[str] = []
        self.charts: dict[str, dict[str, list[dict]]] = {}
        self.scripts: dict[str, str] = {}
        self._chart: str | None = None
        self._script: str | None = None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.links += [attrs[k] for k in ('href', 'src') if attrs.get(k)]
        if tag == 'tr' and 'data-run' in attrs:
            self.row_ids.append(attrs['data-run'])
        if tag == 'svg' and 'data-metric' in attrs:
            self._chart = attrs['data-metric']
            self.charts[self._chart] = {'svg': [attrs]}
        elif self._chart:
            self._classes(attrs)
        if tag == 'script' and attrs.get('type') == 'application/json':
            self._script = attrs['id']
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if self._chart:
            self._classes(dict(attrs))

    def _classes(self, attrs: dict) -> None:
        for cls in (attrs.get('class') or '').split():
            self.charts[self._chart].setdefault(cls, []).append(attrs)

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


def build(tmp_path: Path, records, analysis, repo=None) -> Path:
    store = tmp_path / 'store'
    store.mkdir(exist_ok=True)
    build_site(records, analysis, tmp_path / 'site', store, repo)
    return tmp_path / 'site'


@pytest.fixture
def site(tmp_path, store, analysis) -> Path:
    out = tmp_path / 'site'
    build_site(load_records(store), analysis, out, store, 'egpbos/proteus-bench')
    return out


def parsed(site: Path, relpath: str) -> Page:
    return Page((site / relpath).read_text())


def section_text(text: str, heading: str) -> str:
    start = text.index(f'<h2>{heading}</h2>')
    return text[start : text.index('</section>', start)]


def test_every_page_is_written_and_well_formed(site, records):
    """Overview, compare, 2 series pages and 10 run pages; every tag is closed."""
    pages = sorted(p.relative_to(site).as_posix() for p in site.glob('**/*.html'))
    runs = sorted(f'runs/{r["run_id"]}.html' for r in records)
    assert pages == sorted(['compare.html', 'index.html', GHA, HABROK, *runs])
    for relpath in pages:
        page = parsed(site, relpath)
        assert page.errors == [], relpath
        assert page.stack == [], f'{relpath}: unclosed {page.stack}'


def test_empty_store_gives_a_site_that_says_so(tmp_path):
    """No records and no series: the overview and compare pages still build and parse."""
    pages = build(tmp_path, [], {'schema': 'proteus-bench-analysis/1', 'series': []})
    assert sorted(p.name for p in pages.glob('*.html')) == ['compare.html', 'index.html']
    page = parsed(pages, 'index.html')
    assert page.stack == []
    assert page.errors == []
    assert 'No runs in the store yet' in (pages / 'index.html').read_text()


def test_relative_links_resolve(site):
    """Every relative href/src names a written file; every other link is https."""
    checked = 0
    for path in site.glob('**/*.html'):
        for link in Page(path.read_text()).links:
            if link.startswith(('https://', '#')):
                continue
            target = (path.parent / link.split('?')[0].split('#')[0]).resolve()
            assert target.is_file(), f'{path.relative_to(site)} links to missing {link}'
            checked += 1
    assert checked > 100  # the run tables and chart points alone give well over 100 links


def test_no_store_file_reaches_the_site(site):
    """Nothing is copied: no files/ directory, and the publisher's flame page is not linked."""
    assert not (site / 'files').exists()
    r8 = parsed(site, R8)
    assert [link for link in r8.links if 'flame' in link] == []
    assert 'not linked' in section_text((site / R8).read_text(), 'Artifacts')


def test_artifacts_link_to_the_results_branch(site, tmp_path, records, analysis):
    """With a repository, present artifacts link to its results branch; missing ones say so."""
    r2 = parsed(site, R2)
    base = 'https://github.com/egpbos/proteus-bench/blob/results/'
    assert f'{base}spans/2026/20260919T031000Z-habrok-default-r2.timing.jsonl.gz' in r2.links
    assert [link for link in r2.links if link.endswith('.log.gz')] == []
    assert 'missing from the store' in section_text((site / R2).read_text(), 'Artifacts')
    (tmp_path / 'norepo').mkdir()
    plain = build(tmp_path / 'norepo', records, analysis)  # no repository: paths as text only
    assert [link for link in parsed(plain, R2).links if 'blob/results' in link] == []


def test_series_charts_have_one_point_per_run_and_the_marks(site, analysis):
    """Each chart has as many points as its series; boundaries on every habrok chart only."""
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
        assert marks['svg'][0]['role'] == 'group', metric  # linked marks stay reachable
    assert 'step' not in habrok['total']
    assert 'flag' not in habrok['total']
    gha = parsed(site, GHA).charts
    assert all(len(m['pt']) == 2 for m in gha.values())
    assert all('bnd' not in m for m in gha.values())
    assert all('band' not in m for m in gha.values())


def test_flags_show_three_confirmation_states(site):
    """r7 atmos confirmed (filled), r8 atmos not yet (outline), r7 agni rejected (dashed)."""
    charts = parsed(site, HABROK).charts
    atmos = charts['loop.atmos.per_iter_median']
    assert len(atmos['flag']) == 2
    assert len(atmos['hollow']) == 1
    assert 'rejected' not in atmos
    assert len(atmos['step']) == 1
    assert len(charts['submodule.agni.total']['rejected']) == 1
    r7 = (site / R7).read_text()
    assert 'loop.atmos.per_iter_median regression +7.8 %, confirmed' in r7  # 16.2 s vs 15.03 s
    assert 'submodule.agni.total regression' in r7
    assert 'not confirmed by the next run' in r7
    assert 'not yet confirmed' in (site / R8).read_text()


def test_boundary_hover_names_the_changed_keys(site):
    """The boundary's title carries the changed settings key and the first run after it."""
    text = (site / HABROK).read_text()
    assert 'changed keys: interior_struct.zalmoxis.use_jax' in text
    assert 'before run 20260923T031000Z-habrok-default-r6' in text


def test_run_page_puts_problems_first(site):
    """r4: failed checks and not-comparable reasons precede the summary; passed checks stay out."""
    text = (site / R4).read_text()
    problems = text.index('id="problems"')
    assert problems < text.index('<h2>Summary</h2>')
    callout = text[problems : text.index('</section>', problems)]
    assert 'Check failed: cvode_importable' in callout
    assert 'Check failed: expected_backends' in callout
    assert 'Not comparable: backend aragog.solver is radau, expected cvode' in callout
    assert 'proteus_doctor' not in callout
    assert 'id="problems"' not in (site / R1).read_text()
    failed = (site / 'runs/20260924T120000Z-gha-default-g2.html').read_text()
    assert 'Run failed' in failed
    assert 'AGNI did not converge' in failed


def test_checks_table_lists_failed_checks_first(site):
    """The fixture lists cvode, doctor, backends, tree; the table shows both failures first."""
    checks = section_text((site / R4).read_text(), 'Checks')
    order = [
        checks.index(n)
        for n in ('cvode_importable', 'expected_backends', 'proteus_doctor', 'clean_tree')
    ]
    assert order == sorted(order)


def test_compare_link_goes_to_the_previous_run(site):
    """r2 compares with r1 through the query string; r1, the first run, has no compare link."""
    r2_links = parsed(site, R2).links
    assert (
        '../compare.html?a=20260918T031000Z-habrok-default-r1&b=20260919T031000Z-habrok-default-r2'
        in r2_links
    )
    assert [link for link in parsed(site, R1).links if 'compare.html?' in link] == []


def test_overview_cards(site):
    """Only the latest run's flags and comparability show on each group's card."""
    text = (site / 'index.html').read_text()
    cards = [c.split('</article>')[0] for c in text.split('<article')[1:]]
    habrok = next(c for c in cards if 'habrok-vink' in c)
    gha = next(c for c in cards if 'gha-ubuntu' in c)
    assert 'loop.atmos.per_iter_median regression' in habrok  # r8, the latest run
    assert 'submodule.agni.total' not in habrok  # flagged on r7 only
    assert 'not comparable' in gha  # g2 failed
    assert 'not comparable' not in habrok


def test_filter_index_and_row_ids(site, records):
    """use_jax differs (5 false, 3 true); constant keys are not filterable; rows carry run ids."""
    page = parsed(site, HABROK)
    index = json.loads(page.scripts['settings-index'])
    assert list(index) == ['interior_struct.zalmoxis.use_jax']
    counts = {v: len(ids) for v, ids in index['interior_struct.zalmoxis.use_jax'].items()}
    assert counts == {'false': 5, 'true': 3}
    habrok_ids = [r['run_id'] for r in records if r['machine']['class'] == 'habrok-vink']
    assert page.row_ids == habrok_ids[::-1]  # newest first
    assert 'settings-index' not in parsed(site, GHA).scripts
    assert 'same settings' in (site / GHA).read_text()


def plant(value, skip: frozenset = frozenset({'schema', 'run_id', 'phase'})):
    """Every string value in a nested structure replaced by the hostile marker."""
    if isinstance(value, dict):
        return {k: v if k in skip else plant(v, skip) for k, v in value.items()}
    if isinstance(value, list):
        return [plant(v, skip) for v in value]
    return PLANT if isinstance(value, str) else value


def test_record_and_analysis_text_is_always_escaped(tmp_path, records, analysis):
    """Every string field of r1, and r1's points in the analysis, hold markup and quotes."""
    records[0] = plant(records[0])
    records[0]['benchmark']['settings'][PLANT] = PLANT
    records[0]['timings']['per_iter'][0]['iter'] = PLANT  # numbers in the schema, not checked
    for series in analysis['series']:
        for point in series['points'][:1]:
            point['commit'] = point['time'] = PLANT
        if series['key']['metric'] == 'total':
            series['unit'] = PLANT
    site = build(tmp_path, records, analysis, 'o/n')
    for path in site.glob('**/*.html'):
        text = path.read_text()
        assert '<textarea' not in text, path.name  # dates are cut to 10 characters
        assert 'q"q' not in text, path.name
        assert Page(text).stack == [], path.name
    assert (site / R1).read_text().count('q&lt;textarea&gt;&quot;q') > 20


def test_only_https_urls_become_links(tmp_path, records, analysis):
    """A javascript: run URL is shown as text; an https one is linked."""
    records[0]['trigger']['gha'] = {'run_url': 'javascript:alert(1)'}
    records[1]['trigger']['gha'] = {'run_url': 'https://github.com/o/n/actions/runs/1'}
    site = build(tmp_path, records, analysis)
    assert [link for link in parsed(site, R1).links if 'javascript' in link] == []
    assert 'javascript:alert(1)' in (site / R1).read_text()
    assert 'https://github.com/o/n/actions/runs/1' in parsed(site, R2).links


def test_groups_differing_only_in_case_get_their_own_pages(tmp_path, records, analysis):
    """'All_Options' and 'all_options' slug alike; the key hash keeps both pages."""
    records[0]['benchmark']['name'] = 'All_Options'
    site = build(tmp_path, records, analysis)
    pages = [p.name for p in (site / 'series').glob('*.html')]
    assert len(pages) == 3
    same_slug = [p for p in pages if p.startswith('all_options--default--habrok-vink--')]
    assert len(same_slug) == 2
    assert series_href(('All_Options', 'default', 'habrok-vink')) != HABROK


def test_rebuild_removes_deleted_runs_but_never_foreign_files(tmp_path, records, analysis):
    """A second build without the gha runs drops their pages; a non-site directory is kept."""
    site = build(tmp_path, records, analysis)
    (site / 'stale.txt').write_text('left over')
    habrok = [r for r in records if r['machine']['class'] == 'habrok-vink']
    analysis['series'] = [
        s for s in analysis['series'] if s['key']['machine_class'] == 'habrok-vink'
    ]
    build(tmp_path, habrok, analysis)
    assert not (site / 'runs' / '20260924T120000Z-gha-default-g2.html').exists()
    assert not (site / GHA).exists()
    assert not (site / 'stale.txt').exists()
    assert (site / R8).is_file()
    foreign = tmp_path / 'home'
    foreign.mkdir()
    (foreign / 'notes.txt').write_text('keep me')
    with pytest.raises(ValueError, match='refusing to clear'):
        build_site(records, analysis, foreign, tmp_path / 'store')
    assert (foreign / 'notes.txt').read_text() == 'keep me'


def test_analysis_of_another_schema_is_refused(tmp_path, records):
    """A different analysis schema raises instead of rendering a misleading site."""
    other = {'schema': 'proteus-bench-analysis/2', 'series': []}
    with pytest.raises(ValueError, match='proteus-bench-analysis/1'):
        build_site(records, other, tmp_path / 'site', tmp_path)
    assert not (tmp_path / 'site').exists()


def test_series_naming_an_unknown_run_is_refused(tmp_path, records, analysis):
    """A flag on a run that is not a point of its series is an analysis bug, reported with the run."""
    ghost = {'run_id': 'ghost', 'kind': 'regression', 'delta_rel': 0.1, 'delta_abs': 1.0}
    analysis['series'][0]['flags'].append(ghost | {'threshold_rel': 0.05, 'confirmed': None})
    (tmp_path / 'store').mkdir()
    with pytest.raises(ValueError, match="'ghost'"):
        build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')
    assert not (tmp_path / 'site').exists()


def test_malformed_parts_are_reported_with_the_run(tmp_path, records, analysis):
    """A check without 'ok', or an artifact path leaving the store, names the run."""
    records[2]['checks'] = [{'name': 'cvode_importable'}]
    records[3]['artifacts']['log'] = '../outside.log'
    (tmp_path / 'store').mkdir()
    with pytest.raises(
        ValueError, match='20260920T031000Z-habrok-default-r3: malformed record'
    ):
        build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')
    del records[2]
    with pytest.raises(ValueError, match='r4: artifact path'):
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
    null = {'delta_rel': None, 'threshold_rel': None, 'confirmed': None}
    total['flags'] = [{'run_id': last, 'kind': 'regression', 'delta_abs': 1804.8} | null]
    site = build(tmp_path, records, analysis)
    index = (site / 'index.html').read_text()
    assert 'baseline median is 0' in index
    assert 'total regression n/a, not yet confirmed' in index
    assert 'threshold n/a' in (site / HABROK).read_text()
