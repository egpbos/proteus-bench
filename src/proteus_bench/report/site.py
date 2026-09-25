"""Build the whole dashboard site from records and the analysis output.

Layout of the output directory::

    index.html  compare.html  style.css  series.js  compare.js
    series/<benchmark>--<lineage>--<machine_class>--<hash>.html
    runs/<run_id>.html

No store file is copied into the site: artifacts link to the results branch
on GitHub, because store files come from publishers and the site is public.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from proteus_bench.report import comparepage, overview, runpage, seriespage
from proteus_bench.report.fmt import GroupKey, group_of, run_href, series_href
from proteus_bench.report.store import artifacts_of

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


def build_site(
    records: list[dict], analysis: dict, out_dir: Path, store: Path, repo: str | None = None
) -> list[Path]:
    """Write the site to ``out_dir``, replacing an earlier site there; return the pages.

    ``records`` are oldest first (``store.load_records``). ``repo`` (owner/name)
    makes artifact links point at its results branch; without it the store
    paths are shown as text. Raises ``ValueError`` for an analysis of another
    schema, and refuses to clear an ``out_dir`` that holds something other
    than an earlier site.
    """
    if analysis.get('schema') != ANALYSIS_SCHEMA:
        raise ValueError(
            f'expected analysis schema {ANALYSIS_SCHEMA!r}, found {analysis.get("schema")!r}'
        )
    pages = render_pages(records, analysis, store, repo)
    _clear(out_dir)
    for name in ASSETS:
        template = resources.files('proteus_bench.report.templates').joinpath(name)
        (out_dir / name).write_text(template.read_text())
    written = []
    for relpath, html in pages.items():
        path = out_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        written.append(path)
    return written


def render_pages(
    records: list[dict], analysis: dict, store: Path, repo: str | None
) -> dict[str, str]:
    """Site-relative path -> HTML for every page, before anything is written."""
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
    flags = flags_by_run(analysis)
    for runs in runs_of.values():
        previous = None
        for record in runs:
            run_id = record['run_id']
            artifacts = artifacts_of(record, store, repo)
            pages[run_href(run_id)] = _run_page(
                record, flags.get(run_id, []), artifacts, previous
            )
            previous = run_id
    return pages


def _run_page(record: dict, flags: list, artifacts: list, previous: str | None) -> str:
    """The run page, with a malformed optional part reported against its run id."""
    try:
        return runpage.render(record, flags, artifacts, previous)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as err:
        raise ValueError(
            f'run {record["run_id"]}: malformed record, {type(err).__name__}: {err}'
        ) from err


def _clear(out_dir: Path) -> None:
    """Remove an earlier site so deleted runs disappear; never remove anything else."""
    if out_dir.exists():
        if any(out_dir.iterdir()) and not (out_dir / 'index.html').is_file():
            raise ValueError(
                f'{out_dir} is not empty and holds no earlier site; refusing to clear it'
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
