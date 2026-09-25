"""Build the whole dashboard site from records and the analysis output.

Layout of the output directory::

    index.html  compare.html  style.css  series.js  compare.js
    series/<benchmark>--<lineage>--<machine_class>.html
    runs/<run_id>.html
    files/<artifact path in the store>     copied spans, logs and profiles
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from proteus_bench.report import comparepage, overview, runpage, seriespage
from proteus_bench.report.fmt import GroupKey, group_of, run_href, series_href
from proteus_bench.report.store import copy_artifacts

ANALYSIS_SCHEMA = 'proteus-bench-analysis/1'
ASSETS = ('style.css', 'series.js', 'compare.js')


def group_series(analysis: dict) -> dict[GroupKey, dict[str, dict]]:
    """Analysis series by group, then by metric."""
    groups: dict[GroupKey, dict[str, dict]] = {}
    for series in analysis['series']:
        key = series['key']
        group = (key['benchmark'], key['lineage'], key['machine_class'])
        groups.setdefault(group, {})[key['metric']] = series
    return groups


def flags_by_run(analysis: dict) -> dict[str, list[tuple[str, dict]]]:
    """Run id -> (metric, flag) for every flag in every series."""
    found: dict[str, list[tuple[str, dict]]] = {}
    for series in analysis['series']:
        for flag in series['flags']:
            found.setdefault(flag['run_id'], []).append((series['key']['metric'], flag))
    return found


def build_site(records: list[dict], analysis: dict, out_dir: Path, store: Path) -> list[Path]:
    """Write the site to ``out_dir`` and return the HTML pages written.

    ``records`` are oldest first (``store.load_records``); artifact files are
    copied from ``store``. Raises ``ValueError`` for an analysis of another schema.
    """
    if analysis.get('schema') != ANALYSIS_SCHEMA:
        raise ValueError(
            f'expected analysis schema {ANALYSIS_SCHEMA!r}, found {analysis.get("schema")!r}'
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = group_series(analysis)
    runs_of: dict[GroupKey, list[dict]] = {}
    for record in records:
        runs_of.setdefault(group_of(record), []).append(record)
        groups.setdefault(group_of(record), {})
    pages = {
        'index.html': overview.render(groups, records),
        'compare.html': comparepage.render(records),
    }
    for group, by_metric in groups.items():
        pages[series_href(group)] = seriespage.render(group, by_metric, runs_of.get(group, []))
    pages |= _run_pages(runs_of, flags_by_run(analysis), store, out_dir)
    for name in ASSETS:
        (out_dir / name).write_text(
            resources.files('proteus_bench.report.templates').joinpath(name).read_text()
        )
    written = []
    for relpath, html in pages.items():
        path = out_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        written.append(path)
    return written


def _run_pages(
    runs_of: dict[GroupKey, list[dict]], flags: dict, store: Path, out_dir: Path
) -> dict[str, str]:
    pages = {}
    for runs in runs_of.values():
        previous = None
        for record in runs:
            artifacts = copy_artifacts(record, store, out_dir / 'files')
            run_id = record['run_id']
            pages[run_href(run_id)] = runpage.render(
                record, flags.get(run_id, []), artifacts, previous
            )
            previous = run_id
    return pages
