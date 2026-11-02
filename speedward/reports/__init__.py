from .html_report import to_html
from .json_report import to_json
from .markdown_report import to_markdown

FORMATS = ("json", "md", "html")

_RENDERERS = {"json": to_json, "md": to_markdown, "html": to_html}


def render(report, fmt: str, fast_ms: float | None = None, slow_ms: float | None = None) -> str:
    """fast_ms/slow_ms (FAST_MS/SLOW_MS) only change the HTML map; omitted, the defaults apply."""
    if fmt == "html" and fast_ms is not None and slow_ms is not None:
        return to_html(report, fast_ms=fast_ms, slow_ms=slow_ms)
    return _RENDERERS[fmt](report)
