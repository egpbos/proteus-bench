"""Page shell and small HTML building blocks shared by every page.

``root`` is the relative path from a page to the site root (``''`` for
top-level pages, ``'../'`` for pages in a subdirectory), so the site works from
any base URL, including ``file://``.
"""

from __future__ import annotations

from proteus_bench.report.fmt import esc, fmt_rel

FONTS = (
    'https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
    '&family=IBM+Plex+Sans:wght@400;500;600&display=swap'
)


def page(
    title: str, body: str, root: str, meta: list[str] | None = None, script: str = ''
) -> str:
    """A complete HTML document; ``meta`` items are HTML, ``script`` a file under the root."""
    meta_html = ''.join(f'<span>{item}</span>' for item in meta or [])
    script_tag = f'<script src="{root}{script}"></script>' if script else ''
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{esc(FONTS)}">
<link rel="stylesheet" href="{root}style.css">
</head>
<body>
<main>
<nav class="nav"><a href="{root}index.html">Overview</a><a href="{root}compare.html">Compare runs</a></nav>
<header>
<div class="eyebrow">proteus-bench</div>
<h1>{esc(title)}</h1>
<div class="meta">{meta_html}</div>
</header>
{body}
</main>
{script_tag}
</body>
</html>
"""


def section(heading: str, content: str, note: str = '') -> str:
    note_html = f'<p class="note">{note}</p>' if note else ''
    return f'<section><h2>{esc(heading)}</h2>{note_html}{content}</section>'


def table(
    headers: list[str],
    rows: list[list[str]],
    num: frozenset[int] = frozenset(),
    row_ids: list[str] | None = None,
) -> str:
    """A table in a scrolling panel. Cells are HTML; columns in ``num`` align as numbers.

    ``row_ids`` become ``data-run`` attributes, which the filter script uses.
    """

    def cells(row: list[str], tag: str) -> str:
        return ''.join(
            f'<{tag} class="num">{c}</{tag}>' if i in num else f'<{tag}>{c}</{tag}>'
            for i, c in enumerate(row)
        )

    head = f'<thead><tr>{cells([esc(h) for h in headers], "th")}</tr></thead>'
    attrs = [f' data-run="{esc(i)}"' for i in row_ids] if row_ids else [''] * len(rows)
    body = ''.join(
        f'<tr{a}>{cells(row, "td")}</tr>' for a, row in zip(attrs, rows, strict=True)
    )
    return f'<div class="panel scroll"><table>{head}<tbody>{body}</tbody></table></div>'


def kv_table(pairs: list[tuple[str, str]]) -> str:
    """Two-column key/value table; values are HTML."""
    rows = ''.join(f'<tr><th scope="row">{esc(k)}</th><td>{v}</td></tr>' for k, v in pairs)
    return f'<div class="panel scroll"><table class="kv"><tbody>{rows}</tbody></table></div>'


def flag_badge(flag: dict, metric: str = '') -> str:
    """Regression or improvement badge: icon, words and numbers, never colour alone."""
    regression = flag['kind'] == 'regression'
    # filled triangle once confirmed, outline while unconfirmed; up means slower
    icons = ('▲', '△') if regression else ('▼', '▽')
    icon = icons[0] if flag['confirmed'] else icons[1]
    state = 'confirmed' if flag['confirmed'] else 'unconfirmed'
    cls = 'bad' if regression else 'good'
    where = f'{esc(metric)} ' if metric else ''
    return (
        f'<span class="badge {cls}"><span class="icon" aria-hidden="true">{icon}</span>'
        f'{where}{esc(flag["kind"])} {fmt_rel(flag["delta_rel"])}, {state}</span>'
    )


def check_mark(ok: bool) -> str:
    return '<span class="ok">✓ ok</span>' if ok else '<span class="fail">✕ failed</span>'


def comparable_mark(ok: bool) -> str:
    return (
        '<span class="ok">✓ comparable</span>'
        if ok
        else '<span class="warn">○ not comparable</span>'
    )
