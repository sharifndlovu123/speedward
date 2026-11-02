from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from speedward.core.analysis import plain_number

from ._view import context
from .map import FAST_MS, SLOW_MS, render_map

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)), autoescape=True)


def to_html(report, fast_ms: float = FAST_MS, slow_ms: float = SLOW_MS) -> str:
    """fast_ms/slow_ms: the configured FAST_MS/SLOW_MS (line colours), so exports match the web view."""
    from markupsafe import Markup
    ctx = context(report)
    # The map is built by render_map, which escapes every dynamic string itself.
    ctx["map_svg"] = Markup(render_map(report, fast_ms=fast_ms, slow_ms=slow_ms))  # noqa: S704
    ctx.update(fast_ms=plain_number(fast_ms), slow_ms=plain_number(slow_ms))
    return _env.get_template("report.html.j2").render(**ctx)
