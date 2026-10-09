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

import proteus_plotly
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
R6 = 'runs/20260923T031000Z-habrok-default-r6.html'
R8 = 'runs/20260925T031000Z-habrok-default-r8.html'
PLANT = 'q<textarea>"q'
UNANALYSED = {
    'schema': 'proteus-bench-analysis/1',
    'series': [],
}  # raw in the output, it would open an element or end an attribute


class Page(HTMLParser):
    """Parses one page: tag balance, links, table row ids, embedded JSON and charts.

    ``charts`` maps a chart's label up to its first ': ' (the metric on series
    pages) to its figures, theme -> Plotly JSON.
    """

    def __init__(self, text: str):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.links: list[str] = []
        self.row_ids: list[str] = []
        self.charts: dict[str, dict] = {}
        self.scripts: dict[str, str] = {}
        self._script: str | None = None
        self._figure: dict | None = None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.links += [attrs[k] for k in ('href', 'src') if attrs.get(k)]
        if tag == 'tr' and 'data-run' in attrs:
            self.row_ids.append(attrs['data-run'])
        if tag == 'script' and attrs.get('type') == 'application/json':
            self._script = attrs.get('id') or attrs['class']
        if tag == 'div' and attrs.get('class') == 'plot':
            self.charts[attrs['aria-label'].split(': ')[0]] = self._figure
            self._figure = None
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f'</{tag}> closes {self.stack[-1:] or "nothing"}')
            return
        self.stack.pop()

    def handle_data(self, data):
        if self._script == 'figure':
            self._figure = json.loads(data)
        elif self._script:
            self.scripts[self._script] = data
        self._script = None


def points_of(figure: dict) -> dict:
    """The trace of a trend figure that holds one point per run."""
    (trace,) = [t for t in figure['data'] if 'ids' in t]
    return trace


def marks(figure: dict) -> dict[str, tuple[str, str]]:
    """Run id -> (marker symbol, colour) of a trend figure."""
    trace = points_of(figure)
    marker = trace['marker']
    return dict(
        zip(trace['ids'], zip(marker['symbol'], marker['color'], strict=True), strict=True)
    )


def build(tmp_path: Path, records, analysis, repo=None) -> Path:
    store = tmp_path / 'store'
    store.mkdir(exist_ok=True)
    build_site(records, analysis, tmp_path / 'site', store, repo)
    return tmp_path / 'site'


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
        for figures in Page(path.read_text()).charts.values():
            for trace in figures['light']['data']:
                for link in trace.get('customdata', []):
                    assert (path.parent / link).resolve().is_file(), link
                    checked += 1
    assert checked > 300  # the run tables and the chart points alone give well over 300


def test_no_store_file_reaches_the_site(site):
    """Nothing is copied: no files/ directory, and the publisher's flame page is not linked."""
    assert not (site / 'files').exists()
    r8 = parsed(site, R8)
    assert [link for link in r8.links if 'flame' in link] == []
    assert 'not linked' in section_text((site / R8).read_text(), 'Artifacts')


def test_artifacts_link_to_the_results_branch(site, tmp_path, store):
    """With a repository, present artifacts link to its results branch; missing ones say so."""
    r2 = parsed(site, R2)
    base = 'https://github.com/egpbos/proteus-bench/blob/results/'
    assert f'{base}spans/2026/20260919T031000Z-habrok-default-r2.timing.jsonl.gz' in r2.links
    assert [link for link in r2.links if link.endswith('.log.gz')] == []
    assert 'missing from the store' in section_text((site / R2).read_text(), 'Artifacts')
    plain = tmp_path / 'plain'
    build_site(load_records(store), UNANALYSED, plain, store, None)
    spans = 'spans/2026/20260919T031000Z-habrok-default-r2.timing.jsonl.gz'
    assert f'<code>{spans}</code>' in section_text((plain / R2).read_text(), 'Artifacts')
    assert [link for link in parsed(plain, R2).links if 'blob/results' in link] == []


def test_series_charts_have_one_point_per_run_and_the_marks(site, analysis):
    """Each chart has a point per run linking to its page; the boundary before r2 on every habrok chart."""
    habrok = parsed(site, HABROK).charts
    expected = {
        s['key']['metric']: s
        for s in analysis['series']
        if s['key']['machine_class'] == 'habrok-vink'
    }
    assert set(habrok) == set(expected)
    for metric, figures in habrok.items():
        assert set(figures) == {'light', 'dark'}
        points = points_of(figures['light'])
        assert points['ids'] == [p['run_id'] for p in expected[metric]['points']], metric
        assert len(points['ids']) == 8
        assert points['customdata'] == [f'../runs/{i}.html' for i in points['ids']], metric
        boundaries = [
            a for a in figures['light']['layout']['annotations'] if a['text'] == 'settings'
        ]
        assert [a['x'] for a in boundaries] == [0.5], metric  # before r2, the second run
    gha = parsed(site, GHA).charts
    assert all(len(points_of(f['light'])['ids']) == 2 for f in gha.values())
    assert all('annotations' not in f['light']['layout'] for f in gha.values())
    assert all(len(f['light']['data']) == 2 for f in gha.values())  # no baseline band


def test_flags_show_three_confirmation_states(site):
    """r6, r7 atmos confirmed (filled), r8 not yet (outline), r6 interior not confirmed (grey)."""
    charts = parsed(site, HABROK).charts
    light = proteus_plotly.colors('light')
    atmos = marks(charts['loop.atmos.per_iter_median']['light'])
    run = 'habrok-default-r'
    assert atmos[f'20260923T031000Z-{run}6'] == ('triangle-up', light['danger'])
    assert atmos[f'20260924T031000Z-{run}7'] == ('triangle-up', light['danger'])
    assert atmos[f'20260925T031000Z-{run}8'] == ('triangle-up-open', light['danger'])
    assert atmos[f'20260922T031000Z-{run}5'] == ('circle', light['text-d'])
    assert atmos[f'20260921T031000Z-{run}4'] == ('circle-open', light['text-d2'])
    interior = marks(charts['loop.interior.per_iter_median']['light'])
    assert interior[f'20260923T031000Z-{run}6'] == ('triangle-up-open', light['text-d3'])
    assert not any(
        m.startswith('triangle') for m, _ in marks(charts['total']['light']).values()
    )
    dark = marks(charts['loop.atmos.per_iter_median']['dark'])
    assert dark[f'20260922T031000Z-{run}5'][1] == proteus_plotly.colors('dark')['text-d']
    # median per-iteration atmosphere 16.2 x 1.003 s on r6 against the r2, r3, r5
    # baseline median 15 x 0.998 s: +8.5 %; without the init-stage exclusion it is lower
    r6 = (site / R6).read_text()
    assert 'loop.atmos.per_iter_median regression +8.5 %, confirmed' in r6
    assert 'loop.interior.per_iter_median regression' in r6
    assert 'not confirmed by the next run' in r6
    assert 'not yet confirmed' in (site / R8).read_text()


def test_steps_and_boundaries_hover_their_details(site):
    """The atmosphere step sits before r6; the boundary names the changed key and its first run."""
    atmos = parsed(site, HABROK).charts['loop.atmos.per_iter_median']['light']['layout']
    (step,) = [a for a in atmos['annotations'] if a['text'] != 'settings']
    assert step['x'] == pytest.approx(4.5)  # r6 is the sixth run
    assert step['text'] == '+8.5 %'
    assert 'step at run 20260923T031000Z-habrok-default-r6' in step['hovertext']
    dotted = [s for s in atmos['shapes'] if s['line']['dash'] == 'dot']
    assert [s['x0'] for s in dotted] == [pytest.approx(4.5)]
    (boundary,) = [a for a in atmos['annotations'] if a['text'] == 'settings']
    assert 'changed keys: interior_struct.zalmoxis.use_jax' in boundary['hovertext']
    assert 'before run 20260919T031000Z-habrok-default-r2' in boundary['hovertext']


def test_run_charts_hatch_the_second_interior_module(site):
    """Structure (Zalmoxis) and interior (Aragog) bars share a colour; structure is hatched."""
    figure = parsed(site, R1).charts['Time per main-loop iteration by component, 6 iterations']
    bars = {t['name']: t['marker'] for t in figure['light']['data']}
    assert bars['structure']['color'] == bars['interior']['color']
    assert bars['structure']['pattern']['shape'] == '/'
    assert bars['interior']['pattern']['shape'] == ''
    assert bars['atmos']['color'] != bars['interior']['color']
    assert (
        'Phases of the run, 49:21 min timed' in parsed(site, R1).charts
    )  # 21 + 1772 + 1163.2 + 5 s


def test_run_page_puts_problems_first(site):
    """r4: failed checks and not-comparable reasons precede the summary; passed checks stay out."""
    text = (site / R4).read_text()
    problems = text.index('id="problems"')
    assert problems < text.index('<h2>Summary</h2>')
    callout = text[problems : text.index('</section>', problems)]
    assert 'Check failed: expected_backends' in callout
    assert (
        'Not comparable: check expected_backends failed: aragog.solver: expected cvode, got radau'
        in callout
    )
    assert 'clean_tree' not in callout
    assert 'id="problems"' not in (site / R1).read_text()
    failed = (site / 'runs/20260924T120000Z-gha-default-g2.html').read_text()
    assert 'Run failed' in failed
    assert 'AGNI did not converge' in failed


def test_checks_table_lists_failed_checks_first(site):
    """The fixture lists tree, timing, backends; the failed backends check comes first."""
    checks = section_text((site / R4).read_text(), 'Checks')
    order = [checks.index(n) for n in ('expected_backends', 'clean_tree', 'timing_contract')]
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
    assert 'loop.interior' not in habrok  # flagged on r6 only
    assert 'not comparable' in gha  # g2 failed
    assert 'not comparable' not in habrok


def test_filter_index_and_row_ids(site, records):
    """use_jax differs (1 false, 7 true); constant keys are not filterable; rows carry run ids."""
    page = parsed(site, HABROK)
    index = json.loads(page.scripts['settings-index'])
    assert list(index) == ['interior_struct.zalmoxis.use_jax']
    counts = {v: len(ids) for v, ids in index['interior_struct.zalmoxis.use_jax'].items()}
    assert counts == {'false': 1, 'true': 7}
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
    records[0]['checks'][0]['ok'] = False  # failed checks are listed among the problems too
    records[0]['benchmark']['settings'][PLANT] = PLANT
    records[0]['timings']['per_iter'][0]['iter'] = PLANT  # numbers in the schema, not checked
    for series in analysis['series']:
        for point in series['points'][:1]:
            point['commit'] = point['time'] = PLANT
        if series['key']['metric'] == 'total':
            series['unit'] = PLANT
    site = build(tmp_path, records, analysis, 'o/n')
    in_charts = []
    for path in site.glob('**/*.html'):
        text = path.read_text()
        assert '<textarea' not in text, path.name
        assert 'q"q' not in text, path.name
        page = Page(text)
        assert page.stack == [], path.name
        # Plotly reads tags in its text, so chart text is escaped inside the JSON too
        strings = [v for f in page.charts.values() for v in strings_in(f)]
        assert not [v for v in strings if '<textarea' in v], path.name
        in_charts += [v for v in strings if '&lt;textarea' in v]
    assert (site / R1).read_text().count('q&lt;textarea&gt;&#34;q') > 20
    assert len(in_charts) > 20  # hover texts, axis titles, legend names, tick labels


def strings_in(value) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return [value] if isinstance(value, str) else []


def test_only_https_urls_become_links(tmp_path, records):
    """A javascript: run URL is shown as text; an https one is linked."""
    records[0]['trigger']['gha'] = {'run_url': 'javascript:alert(1)'}
    records[1]['trigger']['gha'] = {'run_url': 'https://github.com/o/n/actions/runs/1'}
    site = build(tmp_path, records, UNANALYSED)
    assert [link for link in parsed(site, R1).links if 'javascript' in link] == []
    assert 'javascript:alert(1)' in (site / R1).read_text()
    assert 'https://github.com/o/n/actions/runs/1' in parsed(site, R2).links


def test_groups_differing_only_in_case_get_their_own_pages(tmp_path, records):
    """'All_Options' and 'all_options' slug alike; the key hash keeps both pages."""
    records[0]['benchmark']['name'] = 'All_Options'
    site = build(tmp_path, records, UNANALYSED)
    pages = [p.name for p in (site / 'series').glob('*.html')]
    assert len(pages) == 3
    same_slug = [p for p in pages if p.startswith('all_options--default--habrok-vink--')]
    assert len(same_slug) == 2
    assert series_href(('All_Options', 'default', 'habrok-vink')) != HABROK


def test_output_directory_must_be_new_or_empty(tmp_path, records):
    """A build into an earlier site or any non-empty directory is refused and deletes nothing."""
    site = build(tmp_path, records, UNANALYSED)
    with pytest.raises(ValueError, match='not empty'):
        build(tmp_path, records[:1], UNANALYSED)
    assert (site / R8).is_file()
    empty = tmp_path / 'empty'
    empty.mkdir()
    build_site(records[:1], UNANALYSED, empty, tmp_path / 'store')
    assert (empty / 'index.html').is_file()


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
    """A check without 'ok', an artifact path leaving the store or a list of artifacts names the run."""
    records[2]['checks'] = [{'name': 'clean_tree'}]
    records[3]['artifacts']['log'] = '../outside.log'
    (tmp_path / 'store').mkdir()
    with pytest.raises(
        ValueError, match='20260920T031000Z-habrok-default-r3: malformed record'
    ):
        build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')
    del records[2]
    with pytest.raises(ValueError, match='r4: malformed record, ValueError: artifact path'):
        build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')
    records[2]['artifacts'] = ['log']  # r4, after r3 was removed
    with pytest.raises(ValueError, match='r4: malformed record, AttributeError'):
        build_site(
            records[2:3], analysis | {'series': []}, tmp_path / 'site', tmp_path / 'store'
        )


def test_malformed_part_read_by_shared_pages_names_the_run(tmp_path, records, analysis):
    """An empty component row breaks the compare page too; the run page reports it first."""
    r5 = next(r for r in records if r['run_id'].endswith('-r5'))
    r5['timings']['components'] = [{}]
    (tmp_path / 'store').mkdir()
    with pytest.raises(ValueError, match='r5: malformed record, KeyError') as err:
        build_site(records, analysis, tmp_path / 'site', tmp_path / 'store')
    assert "'phase'" in str(err.value)
    assert not (tmp_path / 'site').exists()


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
    site = build(tmp_path, records, analysis | {'series': [total]})
    index = (site / 'index.html').read_text()
    assert 'baseline median is 0' in index
    assert 'total regression n/a, not yet confirmed' in index
    assert 'threshold n/a' in (site / HABROK).read_text()
