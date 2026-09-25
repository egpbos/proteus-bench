"""A run page: what ran, where, how it went, and where its time went.

Problems come first: failed checks, the reasons a run is not comparable, and a
run that did not finish are shown above everything else.
"""

from __future__ import annotations

from proteus_bench.report.fmt import (
    commit_link,
    esc,
    fmt_s,
    fmt_time,
    group_label,
    group_of,
    series_href,
)
from proteus_bench.report.layout import (
    check_mark,
    comparable_mark,
    flag_badge,
    kv_table,
    page,
    section,
    table,
)
from proteus_bench.report.runcharts import (
    iteration_rows,
    iteration_stack,
    phase_bar,
    stack_order,
)

ROOT = '../'
PHASE_ORDER = ('setup', 'init', 'loop', 'shutdown')


def render(
    record: dict,
    flags: list[tuple[str, dict]],
    artifacts: dict[str, str | None],
    previous: str | None,
) -> str:
    """The page for one record.

    ``flags`` are (metric, flag) pairs for this run, ``artifacts`` maps artifact
    names to site-relative paths (``None`` when missing from the store) and
    ``previous`` is the run id before this one in its group, for the compare link.
    """
    body = (
        _problems(record)
        + _summary(record, flags, artifacts, previous)
        + section('Phases', f'<div class="panel">{phase_bar(record)}</div>')
        + section(
            'Main-loop iterations',
            f'<div class="panel">{iteration_stack(record)}</div>' + _iter_table(record),
        )
        + section('Components', _components(record))
        + section('Provenance', _provenance(record))
        + section('Machine and environment', _machine(record) + _env(record))
        + section('Checks', _checks(record))
        + section('Backends', _backends(record))
        + section('Artifacts', _artifacts(artifacts))
        + section('Settings', _settings(record))
    )
    meta = [
        esc(fmt_time(record['trigger']['started_at'])),
        f'PROTEUS {commit_link(record["code"]["proteus"]["sha"])}',
        esc(record['machine']['label']),
    ]
    return page(f'Run {record["run_id"]}', body, ROOT, meta)


def _problems(record: dict) -> str:
    failed = [c for c in record['checks'] if not c['ok']]
    parts = [
        f'<li><b>Check failed: {esc(c["name"])}</b>{": " + esc(c["detail"]) if c.get("detail") else ""}</li>'
        for c in failed
    ]
    parts += [
        f'<li>Not comparable: {esc(reason)}</li>'
        for reason in record['comparability']['reasons']
    ]
    outcome = record['outcome']
    if outcome['status'] != 'ok':
        error = f': {esc(outcome["error"])}' if outcome.get('error') else ''
        parts.append(
            f'<li><b>Run {esc(outcome["status"])}</b> (exit code {esc(outcome["exit_code"])}){error}</li>'
        )
    if not parts:
        return ''
    return f'<section class="callout" id="problems"><h2>Problems</h2><ul>{"".join(parts)}</ul></section>'


def _summary(
    record: dict, flags: list[tuple[str, dict]], artifacts: dict, previous: str | None
) -> str:
    outcome, timings, group = record['outcome'], record['timings'], group_of(record)
    rusage = timings.get('rusage', {})
    pairs = [
        ('Series', f'<a href="{ROOT}{series_href(group)}">{esc(group_label(group))}</a>'),
        (
            'Status',
            f'{esc(outcome["status"])}, {esc(outcome.get("termination", "no termination recorded"))}',
        ),
        ('Comparable', comparable_mark(record['comparability']['ok'])),
        ('Iterations', esc(outcome.get('n_iters', 'n/a'))),
        ('Simulated time', f'{outcome["time_yr"]:.4g} yr' if 'time_yr' in outcome else 'n/a'),
        ('Wall time', fmt_s(timings['wall_s'])),
        ('Start-up', fmt_s(timings.get('startup_s'))),
        ('Peak RSS', f'{rusage["max_rss_mb"]:.0f} MB' if 'max_rss_mb' in rusage else 'n/a'),
        ('CPU user / sys', f'{fmt_s(rusage.get("user_s"))} / {fmt_s(rusage.get("sys_s"))}'),
    ]
    if record['benchmark'].get('carry_over_of'):
        pairs.append(
            (
                'Carry-over of',
                f'<span class="mono">{esc(record["benchmark"]["carry_over_of"])}</span>',
            )
        )
    if flags:
        pairs.append(
            (
                'Flags',
                '<div class="badges">' + ''.join(flag_badge(f, m) for m, f in flags) + '</div>',
            )
        )
    if artifacts.get('flame'):
        pairs.append(
            ('Profile', f'<a href="{ROOT}{esc(artifacts["flame"])}"><b>Flame graph</b></a>')
        )
    if previous:
        href = f'{ROOT}compare.html?a={esc(previous)}&amp;b={esc(record["run_id"])}'
        pairs.append(('Compare', f'<a href="{href}">with the previous run of this series</a>'))
    pairs += [
        (f'Fingerprint {k}', f'{v:.6g}') for k, v in record.get('fingerprint', {}).items()
    ]
    return section('Summary', kv_table(pairs))


def _iter_table(record: dict) -> str:
    rows = iteration_rows(record)
    names = stack_order({n for row in rows for n in row})
    body = [
        [esc(it['iter']), fmt_s(it['dur_s'])]
        + [fmt_s(row.get(n)) if n in row else '' for n in names]
        for it, row in zip(record['timings']['per_iter'], rows, strict=True)
    ]
    cols = frozenset(range(len(names) + 2))
    return f'<details><summary>Table view</summary>{table(["Iteration", "Total", *names], body, cols)}</details>'


def _components(record: dict) -> str:
    rows = sorted(
        record['timings']['components'],
        key=lambda c: (PHASE_ORDER.index(c['phase']), -c['total_s']),
    )
    body = [
        [
            esc(c['phase']),
            esc(c['component']),
            esc(c.get('submodule') or ''),
            esc(c.get('backend') or ''),
            fmt_s(c['total_s']),
            esc(c['n_calls']),
        ]
        for c in rows
    ]
    return table(
        ['Phase', 'Component', 'Submodule', 'Backend', 'Total', 'Calls'],
        body,
        frozenset({4, 5}),
    )


def _version(state: dict) -> str:
    text = ' '.join(esc(state[k]) for k in ('version', 'sha') if state.get(k))
    return text + (' <span class="warn">dirty tree</span>' if state.get('dirty') else '')


def _provenance(record: dict) -> str:
    code, proteus, trigger = record['code'], record['code']['proteus'], record['trigger']
    pairs = [
        (
            'PROTEUS',
            commit_link(proteus['sha'])
            + (' <span class="warn">dirty tree</span>' if proteus['dirty'] else ''),
        ),
        (
            'Branch / describe',
            esc(' / '.join(v for v in (proteus.get('branch'), proteus.get('describe')) if v)),
        ),
        (
            'Harness',
            f'{esc(record["harness"]["version"])} <span class="mono">{esc(record["harness"].get("sha", ""))}</span>',
        ),
        ('Trigger', esc(f'{trigger["adapter"]}, by {trigger.get("user", "unknown user")}')),
    ]
    if 'gha' in trigger and 'run_url' in trigger['gha']:
        pairs.append(
            (
                'GitHub Actions run',
                f'<a href="{esc(trigger["gha"]["run_url"])}">{esc(trigger["gha"]["run_url"])}</a>',
            )
        )
    if 'slurm' in trigger:
        pairs.append(('Slurm', esc(', '.join(f'{k} {v}' for k, v in trigger['slurm'].items()))))
    pairs += [
        (f'module {name}', f'{esc(state["source"])}: {_version(state)}')
        for name, state in sorted(code['modules'].items())
    ]
    pairs += [
        (f'package {name}', esc(v)) for name, v in sorted(code.get('packages', {}).items())
    ]
    return kv_table(pairs)


def _machine(record: dict) -> str:
    return kv_table([(k, esc(v)) for k, v in record['machine'].items()])


def _env(record: dict) -> str:
    env = record['env']
    pairs = [
        (k, esc(env[k])) for k in ('manager', 'python', 'julia', 'socrates_build') if k in env
    ]
    pairs += [(k, esc(v)) for k, v in sorted(env['threads'].items())]
    pairs += [
        (f'knob {k}', esc('unset' if v is None else v)) for k, v in sorted(env['knobs'].items())
    ]
    return kv_table(pairs)


def _checks(record: dict) -> str:
    checks = sorted(record['checks'], key=lambda c: c['ok'])  # failed first
    rows = [[esc(c['name']), check_mark(c['ok']), esc(c.get('detail', ''))] for c in checks]
    return (
        table(['Check', 'Result', 'Detail'], rows)
        if rows
        else '<p class="note">No checks recorded.</p>'
    )


def _backends(record: dict) -> str:
    rows = []
    for submodule, entries in sorted(record['backends'].items()):
        for key, value in sorted(entries.items()):
            shown = (
                ', '.join(f'{b}: {n}' for b, n in value.items())
                if isinstance(value, dict)
                else value
            )
            rows.append([esc(submodule), esc(key), esc(shown)])
    return (
        table(['Submodule', 'Key', 'Value or call counts'], rows)
        if rows
        else '<p class="note">No backends recorded.</p>'
    )


def _artifacts(artifacts: dict[str, str | None]) -> str:
    if not artifacts:
        return '<p class="note">The record lists no artifacts.</p>'
    rows = [
        [
            esc(name),
            f'<a href="{ROOT}{esc(path)}">{esc(path.split("/")[-1])}</a>'
            if path
            else '<span class="warn">missing from the store</span>',
        ]
        for name, path in sorted(artifacts.items())
    ]
    return table(['Artifact', 'File'], rows)


def _settings(record: dict) -> str:
    bench = record['benchmark']
    overrides = [
        [esc(k), f'<code>{esc(v)}</code>'] for k, v in sorted(bench['overrides'].items())
    ]
    settings = [
        [esc(k), f'<code>{esc(v)}</code>'] for k, v in sorted(bench['settings'].items())
    ]
    return (
        f'<p class="note">Benchmark {esc(bench["name"])}, lineage <span class="mono">{esc(bench["lineage"])}</span>, '
        f'settings hash <span class="mono">{esc(bench["settings_hash"])}</span>.</p>'
        + (table(['Override', 'Value'], overrides) if overrides else '')
        + f'<details><summary>All {len(settings)} resolved settings</summary>{table(["Key", "Value"], settings)}</details>'
    )
