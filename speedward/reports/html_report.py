from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from speedward.core.analysis import plain_number

from ._view import context
from .bottleneck import render_bottleneck
from .map import FAST_MS, SLOW_MS, render_map
from .reach import MAP_LEGEND, render_reach

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)), autoescape=True)


def to_html(report, fast_ms: float = FAST_MS, slow_ms: float = SLOW_MS) -> str:
    """fast_ms/slow_ms: the configured FAST_MS/SLOW_MS (line colours), so exports match the web view."""
    from markupsafe import Markup
    ctx = context(report)
    # The map is built by render_map, which escapes every dynamic string itself.
    ctx["map_svg"] = Markup(render_map(report, fast_ms=fast_ms, slow_ms=slow_ms))  # noqa: S704
    # The panel is rendered by our own autoescaping template ("" for a report without a diagnosis).
    ctx["bottleneck"] = Markup(render_bottleneck(report))  # noqa: S704
    # Likewise "Who can reach it" ("" for a report without checks).
    ctx["reach"] = Markup(render_reach(report))  # noqa: S704
    ctx["reach_legend"] = MAP_LEGEND if report.reach is not None else ""
    ctx.update(fast_ms=plain_number(fast_ms), slow_ms=plain_number(slow_ms))
    return _env.get_template("report.html.j2").render(**ctx)
