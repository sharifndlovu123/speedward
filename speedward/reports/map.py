"""World map as an inline SVG string: server-to-location arcs coloured by cold first-byte time.

Land outlines are bundled (Natural Earth 110m, public domain, see scripts/build_worldmap.py); the map
never fetches tiles or any other resource. All dynamic text is escaped here.
"""
from __future__ import annotations

import json
from functools import lru_cache
from html import escape
from pathlib import Path

from speedward.core.models import LocationResult, ScanReport

FAST_MS = 300
SLOW_MS = 800

_STATUS_TEXT = {"blocked": "blocked", "test_failed": "test failed", "not_run": "did not run"}

_STYLE = (
    ".sw-map{--land:#d9dee5;--fast:#1a9850;--mid:#e0a100;--slow:#d73027;--off:#8a8f98;--ink:#1f2933;"
    "--bg:#f6f8fa;--pin:#2b5fd9}"
    "@media (prefers-color-scheme:dark){.sw-map{--land:#2e3640;--fast:#4cc38a;--mid:#f0b429;"
    "--slow:#ff6b61;--off:#7b838d;--ink:#e6e9ee;--bg:#161b22;--pin:#7aa2ff}}"
    ".sw-map text{fill:var(--ink);font-family:system-ui,sans-serif}"
)


@lru_cache(maxsize=1)
def _land() -> list[str]:
    data = json.loads((Path(__file__).with_name("worldmap.svg.json")).read_text())
    return data["paths"]


def _colour(first_byte: float, fast: float, slow: float) -> str:
    if first_byte <= fast:
        return "var(--fast)"
    if first_byte >= slow:
        return "var(--slow)"
    return "var(--mid)"


def _num(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _city(r: LocationResult) -> str:
    return r.location.city or r.location.country or "unknown"


def render_map(report: ScanReport, width: int = 1000, fast_ms: float = FAST_MS,
               slow_ms: float = SLOW_MS) -> str:
    W = width
    H = W / 2
    k = W / 1000
    fs = max(9.0, 11 * k)

    def proj(lat: float, lon: float) -> tuple[float, float]:
        return (lon + 180) / 360 * W, (90 - lat) / 180 * H

    unplaced = [r for r in report.locations if r.location.lat is None or r.location.lon is None]
    extra = 26 * k if unplaced else 0
    out = [
        f'<svg class="sw-map" viewBox="0 0 {_num(W)} {_num(H + extra)}" '
        f'role="img" aria-label="World map of response times from each test location">',
        f"<style>{_STYLE}</style>",
        f'<rect width="{_num(W)}" height="{_num(H + extra)}" fill="var(--bg)"/>',
    ]
    scale = f' transform="scale({k:g})"' if W != 1000 else ""
    out.append(f'<g fill="var(--land)"{scale}>' + "".join(f'<path d="{d}"/>' for d in _land()) + "</g>")

    if report.server is not None:
        sx, sy = proj(report.server.lat, report.server.lon)
        server_label = "Server"
    else:
        sx, sy = 18 * k, H - 22 * k
        server_label = "Server position unknown"

    arcs, dots, labels = [], [], []
    for row, r in enumerate(report.locations):
        if r.location.lat is None or r.location.lon is None:
            continue
        x, y = proj(r.location.lat, r.location.lon)
        name = _city(r)
        fb = r.stages.first_byte if (r.status == "measured" and r.stages is not None) else None
        if fb is not None:
            col = _colour(fb, fast_ms, slow_ms)
            line = f'stroke="{col}" stroke-width="{_num(1.6 * k)}" fill="none" opacity="0.85"'
            dot = f'fill="{col}"'
            text = f"{name} {fb:.0f}ms"
            title = text
        else:
            if r.status == "measured":
                reason = "no data"
                text = f"{name} no data"
            else:
                reason = r.reason or "no reason given"
                text = f"{name} {_STATUS_TEXT[r.status]}: {reason}"
            line = (f'stroke="var(--off)" stroke-width="{_num(1.2 * k)}" fill="none" '
                    f'stroke-dasharray="{_num(5 * k)} {_num(4 * k)}" opacity="0.45"')
            dot = 'fill="var(--off)" opacity="0.6"'
            title = text
        if report.server is not None:
            mx, my = (sx + x) / 2, (sy + y) / 2
            dist = ((x - sx) ** 2 + (y - sy) ** 2) ** 0.5
            cy = my - dist * 0.25
            arcs.append(f'<path d="M{_num(sx)},{_num(sy)} Q{_num(mx)},{_num(cy)} {_num(x)},{_num(y)}" {line}/>')
        # data-row ties the dot to its row in the results table (the web UI highlights it on click).
        dots.append(f'<circle class="sw-dot" data-row="{row}" tabindex="0" cx="{_num(x)}" cy="{_num(y)}" '
                    f'r="{_num(4 * k)}" {dot}>'
                    f"<title>{escape(title)}</title></circle>")
        dim = "" if fb is not None else ' opacity="0.7"'
        labels.append(f'<text x="{_num(x + 6 * k)}" y="{_num(y - 6 * k)}" font-size="{_num(fs)}"'
                      f"{dim}>{escape(text)}</text>")

    out += arcs + dots
    out.append(f'<circle cx="{_num(sx)}" cy="{_num(sy)}" r="{_num(6 * k)}" fill="var(--pin)" '
               f'stroke="var(--bg)" stroke-width="{_num(2 * k)}"/>')
    out.append(f'<text x="{_num(sx + 10 * k)}" y="{_num(sy + 4 * k)}" font-size="{_num(fs)}" '
               f'font-weight="600">{server_label}</text>')
    out += labels
    if unplaced:
        names = ", ".join(escape(_city(r)) for r in unplaced)
        out.append(f'<text x="{_num(8 * k)}" y="{_num(H + 17 * k)}" font-size="{_num(fs)}">'
                   f"Not shown on map: {names}</text>")
    out.append("</svg>")
    return "".join(out)
