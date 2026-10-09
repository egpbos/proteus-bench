"""Head tags that load the PROTEUS design tokens, shared by the flame page and the dashboard.

Colours, fonts and surfaces come from ``tokens.css`` of
``@formingworlds/proteus-tokens``, loaded with subresource integrity. The tokens
are dark-first and switch to light through ``data-theme="light"`` on the root
element; the head script sets it from the reader's colour-scheme preference.
"""

from __future__ import annotations

TOKENS_URL = 'https://cdn.jsdelivr.net/npm/@formingworlds/proteus-tokens@1.3.0/tokens.css'
TOKENS_INTEGRITY = 'sha384-4fqZF8VUXgYMIlRcwHP0fUt9ZXcaXQmMn60TgloL1H65VBOLDww3oCnf1z/CZ8LV'

HEAD = (
    f'<link rel="stylesheet" href="{TOKENS_URL}" integrity="{TOKENS_INTEGRITY}" '
    'crossorigin="anonymous">\n'
    '<script>{\n'
    "  const light = matchMedia('(prefers-color-scheme: light)');\n"
    '  const root = document.documentElement;\n'
    "  (light.onchange = () => root.dataset.theme = light.matches ? 'light' : 'dark')();\n"
    '}</script>'
)
