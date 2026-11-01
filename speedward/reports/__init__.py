from .html_report import to_html
from .json_report import to_json
from .markdown_report import to_markdown

FORMATS = ("json", "md", "html")

_RENDERERS = {"json": to_json, "md": to_markdown, "html": to_html}


def render(report, fmt: str) -> str:
    return _RENDERERS[fmt](report)
