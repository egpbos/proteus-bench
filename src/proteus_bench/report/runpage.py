"""A run page: what ran, where, how it went, and where its time went.

Problems come first: failed checks, the reasons a run is not comparable, and a
run that did not finish are shown above everything else.
"""

from __future__ import annotations

from proteus_bench.report import figures
from proteus_bench.report.render import render
from proteus_bench.report.store import Artifact

ROOT = '../'
PHASE_ORDER = ('setup', 'init', 'loop', 'shutdown')


def page(
    record: dict,
    flags: list[tuple[str, dict]],
    artifacts: list[Artifact],
    previous: str | None,
) -> str:
    """The page for one record.

    ``flags`` are (metric, flag) pairs for this run, ``artifacts`` come from
    ``store.artifacts_of`` and ``previous`` is the run id before this one in
    its group, for the compare link.
    """
    timings = record['timings']
    segments = figures.phase_segments(record)
    rows = figures.iteration_rows(record)
    names = figures.stack_order({n for row in rows for n in row})
    return render(
        'run.html',
        root=ROOT,
        record=record,
        flags=flags,
        artifacts=artifacts,
        previous=previous,
        failed=[c for c in record['checks'] if not c['ok']],
        checks=sorted(record['checks'], key=lambda c: c['ok']),  # failed first
        components=sorted(
            timings['components'],
            key=lambda c: (PHASE_ORDER.index(c['phase']), -c['total_s']),
        ),
        backends=_backends(record),
        segments=segments,
        timed_s=sum(d for _, d in segments),
        phase_chart=figures.themed(figures.phases, record) if segments else None,
        iter_chart=figures.themed(figures.iterations, record) if rows else None,
        iter_names=names,
        iter_rows=list(zip(timings['per_iter'], rows, strict=True)),
    )


def _backends(record: dict) -> list[tuple[str, str, str]]:
    rows = []
    for submodule, entries in sorted(record['backends'].items()):
        for key, value in sorted(entries.items()):
            shown = (
                ', '.join(f'{b}: {n}' for b, n in value.items())
                if isinstance(value, dict)
                else value
            )
            rows.append((submodule, key, shown))
    return rows
