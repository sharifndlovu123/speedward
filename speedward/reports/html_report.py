from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ._view import context
from .map import render_map

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)), autoescape=True)


def to_html(report) -> str:
    # The map is built by render_map, which escapes every dynamic string itself.
    from markupsafe import Markup
    ctx = context(report)
    ctx["map_svg"] = Markup(render_map(report))  # noqa: S704  render_map escapes every dynamic string
    return _env.get_template("report.html.j2").render(**ctx)
