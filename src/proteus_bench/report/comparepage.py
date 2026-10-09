"""The compare page: two runs chosen by ``?a=<run_id>&b=<run_id>``, per-component deltas.

The page embeds per-run totals (wall, phases, phase.component sums of the
record's ``timings.components`` rows) and ``compare.js`` renders the table for
the chosen pair.
"""

from __future__ import annotations

from proteus_bench.report.fmt import fmt_time, group_label, group_of
from proteus_bench.report.render import render


def run_totals(record: dict) -> dict[str, float]:
    """Seconds per compared quantity: ``total``, each phase, and ``<phase>.<component>``."""
    timings = record['timings']
    totals = {'total': timings['wall_s']}
    totals |= {phase: dur for phase, dur in timings['phases'].items() if dur is not None}
    for row in timings['components']:
        key = f'{row["phase"]}.{row["component"]}'
        totals[key] = totals.get(key, 0.0) + row['total_s']
    return totals


def compare_data(records: list[dict]) -> dict:
    """What ``compare.js`` reads: runs newest first with a label and their totals."""
    runs = [
        {
            'id': r['run_id'],
            'label': (
                f'{fmt_time(r["trigger"]["started_at"])} {r["code"]["proteus"]["sha"][:8]} '
                f'{group_label(group_of(r))}'
            ),
            'comparable': r['comparability']['ok'],
            'group': list(group_of(r)),
            'n_iters': r['outcome'].get('n_iters'),
            'totals': run_totals(r),
        }
        for r in reversed(records)
    ]
    return {'runs': runs}


def page(records: list[dict]) -> str:
    return render('compare.html', root='', data=compare_data(records))
