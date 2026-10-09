"""The overview page (``index.html``): one card per series group, then recent runs."""

from __future__ import annotations

from proteus_bench.report.fmt import GroupKey, fmt_rel, fmt_value
from proteus_bench.report.render import render

RECENT_RUNS = 20
STATS = (
    ('total', 'Total'),
    ('init', 'Init'),
    ('loop_per_iter_median', 'Loop / iter (median)'),
)


def page(groups: dict[GroupKey, dict[str, dict]], records: list[dict]) -> str:
    cards = [_card(group, by_metric) for group, by_metric in sorted(groups.items())]
    recent = list(reversed(records[-RECENT_RUNS:]))
    return render(
        'index.html', root='', cards=cards, records=records, recent=recent, n_recent=RECENT_RUNS
    )


def latest_point(by_metric: dict[str, dict]) -> dict:
    """Latest point of the group's ``total`` series (else of any series)."""
    series = by_metric.get('total') or next(iter(by_metric.values()))
    return series['points'][-1]


def _stat(by_metric: dict[str, dict], metric: str, run_id: str) -> tuple[str, str]:
    """(value, change against the baseline) of ``metric`` for the run."""
    series = by_metric.get(metric)
    point = (
        next((p for p in series['points'] if p['run_id'] == run_id), None) if series else None
    )
    if point is None:
        return 'n/a', 'no baseline yet'
    median = series['baseline']['median'] if series['baseline'] else None
    if median is None:
        change = 'no baseline yet'
    elif median == 0:
        change = 'baseline median is 0'
    else:
        change = f'{fmt_rel(point["value"] / median - 1)} vs baseline'
    return fmt_value(point['value'], series['unit']), change


def _card(group: GroupKey, by_metric: dict[str, dict]) -> dict:
    if not by_metric:
        return {'group': group, 'latest': None}
    latest = latest_point(by_metric)
    run_id = latest['run_id']
    return {
        'group': group,
        'latest': latest,
        'stats': [(label, *_stat(by_metric, m, run_id)) for m, label in STATS],
        'flags': [
            (m, f)
            for m, s in sorted(by_metric.items())
            for f in s['flags']
            if f['run_id'] == run_id
        ],
    }
