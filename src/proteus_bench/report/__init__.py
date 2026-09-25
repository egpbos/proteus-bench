"""Static HTML dashboard built from run records and the analysis output.

``site.build_site`` writes the whole site; ``proteus-bench report`` is the CLI
front end. Pages are plain HTML with inline SVG charts and a little vanilla
JavaScript (filtering, two-run compare); no template engine and no runtime
dependencies.
"""
