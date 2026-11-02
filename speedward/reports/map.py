"""World map as an inline SVG string. A circle per test location, a square at the server that answered
it (or one "Server" square), and a line between them coloured by cold first-byte time.

Land outlines are bundled (Natural Earth 110m, public domain, see scripts/build_worldmap.py); the map
never fetches tiles or any other resource. All dynamic text is escaped here.
"""
from __future__ import annotations

import json
from functools import lru_cache
from html import escape
from pathlib import Path

from speedward.core.config import DEFAULT_FAST_MS, DEFAULT_SLOW_MS
from speedward.core.locations import CATALOG
from speedward.core.models import UNCERTAIN, LocationResult, ScanReport

from ._view import CDN_EDGE_NOTE, NA, SOURCE_TEXT, _from_header, answered_by, position_source

FAST_MS = DEFAULT_FAST_MS
UNKNOWN_HINT = "set GEOIP_DB or SERVER_LAT/SERVER_LON"
UNKNOWN_HINT_WEB = "set GEOIP_DB, drop the pin or set SERVER_LAT/SERVER_LON"
# A square closer than this (map pixels at width 1000) to its circle is drawn as a frame around it.
HERE_PX = 9
SLOW_MS = DEFAULT_SLOW_MS

_STATUS_TEXT = {"blocked": "blocked", "test_failed": "test failed", "not_run": "did not run"}

_DARK = ("--bg:#141414;--land:#2A2723;--ink:#EDE8DF;--fast:#4CC38A;--mid:#E0A43A;--slow:#FF6B5A;"
         "--off:#8C877F;--pin:#E8553D;--sq:#E8553D")
_STYLE = (
    # Paper and ink, matching the page tokens. Speed colours reach 3:1 on land and paper; the red accent
    # (--pin, --sq) marks servers only. Checked in tests/test_contrast.py.
    ".sw-map{--bg:#F0EAE0;--land:#E1D9CB;--ink:#1C1C1C;--fast:#1A7F37;--mid:#8F5400;--slow:#B3261E;"
    "--off:#76716A;--pin:#C62F17;--sq:#C62F17}"
    f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]) .sw-map{{{_DARK}}}}}'
    f':root[data-theme="dark"] .sw-map{{{_DARK}}}'
    ".sw-map text{fill:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,"
    "sans-serif;font-weight:500;paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}"
    # A paper halo keeps each dot clear of the lines under it.
    ".sw-dot{stroke:var(--bg);stroke-width:1.5}"
    # No data: a hollow ring, so the state is not told by colour alone (the label says why).
    ".sw-dot.sw-none{fill:var(--bg);stroke:var(--off);stroke-width:2}"
    # Keyboard focus ring: a stroke, because SVG outlines do not draw in Firefox. Ink on the map
    # background is well above 3:1 in light and dark. The selected dot keeps an ink stroke.
    ".sw-dot:focus,.sw-pick:focus,.sw-sq:focus{outline:none}"
    # Squares: the server that answered (small), or the one Server (larger), both in the accent red.
    ".sw-sq{fill:var(--sq);stroke:var(--bg);stroke-width:1}.sw-server{fill:var(--pin);stroke-width:2}"
    ".sw-here{fill:none;stroke:var(--sq);stroke-width:1.5}"
    ".sw-sq:focus-visible{stroke:var(--ink);stroke-width:3}"
    ".sw-dot.sw-sel{stroke:var(--ink);stroke-width:3}"
    ".sw-dot:focus-visible,.sw-pick:focus-visible{outline:none;stroke:var(--ink);stroke-width:4;"
    "filter:drop-shadow(0 0 2px var(--bg))}"
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


def _single_origin(measured: list[LocationResult], located: list[LocationResult]) -> bool:
    """Every measured result that has an address reached the same one, all of them were placed, at one
    spot, by the database (not CDN edges). Two placed results at least: one alone proves nothing."""
    with_ip = [r for r in measured if r.server_ip]
    return (len(located) >= 2 and len({r.server_ip for r in with_ip}) == 1
            and all(r.server_position is not None for r in with_ip)
            and len({r.server_ip for r in located}) == 1
            and len({(r.server_position.lat, r.server_position.lon) for r in located}) == 1
            and all(r.server_position.source == "database" for r in located))


def _anchor_text(report: ScanReport, measured: list[LocationResult],
                 located: list[LocationResult]) -> tuple[str, str] | None:
    """(label, tooltip) for the one "Server" square, or None when each result gets its own square."""
    s = report.server
    if s is not None and s.source == "manual":
        return "Server (manual setting)", "Server: manual (setting)"
    if _single_origin(measured, located):
        r = located[0]
        return ("Server (all probes reached the same IP)",
                f"Server (all probes reached the same IP): {r.server_ip}, {position_source(r)}")
    if located:
        return None
    if s is not None and s.source == "manual (pin)":
        return "Server (pin)", "Server: manual (pin), your own claim"
    if s is not None:  # a report from before Task 16, placed by the database from one IP
        return "Server", f"Server: {SOURCE_TEXT.get(s.source, s.source)}"
    return None


def render_map(report: ScanReport, width: int = 1000, fast_ms: float = FAST_MS,
               slow_ms: float = SLOW_MS, pin_hint: bool = False) -> str:
    """pin_hint: the map is shown in the web UI, where a pin can be dropped (not in CLI reports or files)."""
    W = width
    H = W / 2
    k = W / 1000
    fs = max(9.0, 11 * k)

    def proj(lat: float, lon: float) -> tuple[float, float]:
        return (lon + 180) / 360 * W, (90 - lat) / 180 * H

    def arc(sx, sy, x, y, style):
        mx, my = (sx + x) / 2, (sy + y) / 2
        dist = ((x - sx) ** 2 + (y - sy) ** 2) ** 0.5
        cy = my - dist * 0.25
        return f'<path d="M{_num(sx)},{_num(sy)} Q{_num(mx)},{_num(cy)} {_num(x)},{_num(y)}" {style}/>'

    def square(cls, x, y, size, text):
        t = escape(text)
        return (f'<rect class="{cls}" tabindex="0" role="img" aria-label="{t}" x="{_num(x - size / 2)}" '
                f'y="{_num(y - size / 2)}" width="{_num(size)}" height="{_num(size)}"><title>{t}</title></rect>')

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

    measured = [r for r in report.locations if r.status == "measured"]
    located = [r for r in measured if r.server_position is not None]
    anchor = _anchor_text(report, measured, located)
    manual = report.server is not None and report.server.source == "manual"
    edges = anchor is None and bool(located)
    ips_differ = len({r.server_ip for r in measured if r.server_ip}) > 1

    if anchor is not None:
        if report.server is not None and (manual or not located):
            sx, sy = proj(report.server.lat, report.server.lon)
        else:
            sx, sy = proj(located[0].server_position.lat, located[0].server_position.lon)
    else:
        sx, sy = 18 * k, H - 22 * k

    # One square per answering spot: results at the same place from the same address share it.
    spots: dict[tuple, list[LocationResult]] = {}
    if edges:
        for r in located:
            p = r.server_position
            spots.setdefault((r.server_ip, p.lat, p.lon, p.source, r.edge_code), []).append(r)

    arcs, squares, dots, labels = [], [], [], []
    for row, r in enumerate(report.locations):
        if r.location.lat is None or r.location.lon is None:
            continue
        x, y = proj(r.location.lat, r.location.lon)
        name = _city(r)
        fb = r.stages.first_byte if (r.status == "measured" and r.stages is not None) else None
        dashed = (f'stroke="var(--off)" stroke-width="{_num(1.2 * k)}" fill="none" '
                  f'stroke-dasharray="{_num(5 * k)} {_num(4 * k)}" opacity="0.45"')
        if fb is not None:
            col = _colour(fb, fast_ms, slow_ms)
            line = f'stroke="{col}" stroke-width="{_num(1.6 * k)}" fill="none"'
            dot = f'fill="{col}"'
            none = ""
            text = f"{name} {fb:.0f}ms"
        else:
            if r.status == "measured":
                text = f"{name} no data"
            else:
                text = f"{name} {_STATUS_TEXT[r.status]}: {r.reason or 'no reason given'}"
            line = dashed
            dot = 'fill="var(--off)"'
            none = " sw-none"
        title = text
        if r.status == "measured" and r.edge_note and not manual:
            title += f"; {r.edge_note} ({r.edge_reason}), no square drawn"
            if r.edge_note == UNCERTAIN:
                text += f", {UNCERTAIN}"
        if anchor is not None:
            # From the one Server: every probe for a configured position or a pin; for a single
            # origin only the results that reached it, plus the dashed lines of unreachable ones.
            if manual or not located or r.server_position is not None or r.status != "measured":
                arcs.append(arc(sx, sy, x, y, line))
        elif edges and r.server_position is not None:
            qx, qy = proj(r.server_position.lat, r.server_position.lon)
            arcs.append(arc(x, y, qx, qy, line))
        # data-row ties the dot to its row in the results table (the web UI highlights it on click).
        dots.append(f'<circle class="sw-dot{none}" data-row="{row}" tabindex="0" cx="{_num(x)}" cy="{_num(y)}" '
                    f'r="{_num(4 * k)}" {dot}>'
                    f"<title>{escape(title)}</title></circle>")
        dim = "" if fb is not None else ' opacity="0.7"'
        labels.append(f'<text x="{_num(x + 9 * k)}" y="{_num(y - 6 * k)}" font-size="{_num(fs)}"'
                      f"{dim}>{escape(text)}</text>")

    on_top = []
    for group in spots.values():
        r = group[0]
        qx, qy = proj(r.server_position.lat, r.server_position.lon)
        # An edge in the probe's own city would hide under its circle: frame the circle instead, at
        # the true spot (nothing is moved), drawn above the dots.
        here = any(g.location.lat is not None and g.location.lon is not None
                   and ((qx - proj(g.location.lat, g.location.lon)[0]) ** 2
                        + (qy - proj(g.location.lat, g.location.lon)[1]) ** 2) ** 0.5 < HERE_PX * k
                   for g in group)
        names = ", ".join(_city(g) for g in group)
        text = f"Server that answered {names}: {r.server_ip or NA}, {position_source(r)}"
        if _from_header(r):
            text += f", {answered_by(r)}"
        if ips_differ or r.server_position.source == "cdn header":
            text += f". {CDN_EDGE_NOTE}"
        if here:
            on_top.append(square("sw-sq sw-here", qx, qy, 14 * k, text))
        else:
            squares.append(square("sw-sq", qx, qy, 7 * k, text))

    out += arcs + squares + dots + on_top
    if anchor is not None:
        label_text, tip = anchor
        out.append(square("sw-sq sw-server", sx, sy, 12 * k, tip))
        out.append(f'<text x="{_num(sx + 10 * k)}" y="{_num(sy + 4 * k)}" font-size="{_num(fs)}" '
                   f'font-weight="600">{escape(label_text)}</text>')
    elif not located:
        out.append(f'<text x="{_num(sx)}" y="{_num(sy + 4 * k)}" font-size="{_num(fs)}" font-weight="600">'
                   f"Server position unknown: {UNKNOWN_HINT_WEB if pin_hint else UNKNOWN_HINT}</text>")
    out += labels
    if unplaced:
        names = ", ".join(escape(_city(r)) for r in unplaced)
        out.append(f'<text x="{_num(8 * k)}" y="{_num(H + 17 * k)}" font-size="{_num(fs)}">'
                   f"Not shown on map: {names}</text>")
    out.append("</svg>")
    return "".join(out)


_PICK_STYLE = (
    # Not chosen: a paper dot with an ink ring. Chosen: a filled ink dot. Shape, not colour, tells them apart.
    ".sw-pick{fill:var(--bg);stroke:var(--ink);stroke-width:1.5;cursor:pointer}"
    '.sw-pick[aria-checked="true"]{fill:var(--ink);stroke:var(--bg);stroke-width:2}'
    # After the checked rule and more specific, so the ring shows on checked and unchecked picks.
    '.sw-pick:focus-visible,.sw-pick[aria-checked="true"]:focus-visible{stroke:var(--ink);stroke-width:4}'
    # A chosen dot is already ink, so its focus also lights the ring drawn just after it: a gap, then an
    # outer ink ring (the dot's own stroke stays ink). Focus never looks like a slightly bigger dot.
    ".sw-pick-ring{fill:none;stroke:none;pointer-events:none}"
    '.sw-pick[aria-checked="true"]:focus-visible+.sw-pick-ring{stroke:var(--ink);stroke-width:2.5}'
    "@keyframes sw-pulse{50%{opacity:.35}}"
    ".sw-scanning .sw-pick[aria-checked=\"true\"]{animation:sw-pulse 1.2s ease-in-out infinite}"
    ".sw-locked .sw-pick{cursor:default}"
    "@media (prefers-reduced-motion:reduce){.sw-scanning .sw-pick{animation:none}}"
    # The server pin: drag it, or focus it and use the arrow keys. The ring only shows on keyboard focus.
    ".sw-pin{cursor:grab;touch-action:none}.sw-pin:focus{outline:none}"
    ".sw-pin .sw-body{fill:var(--pin);stroke:var(--bg);stroke-width:2}"
    ".sw-pin .sw-ring{fill:none;stroke:none}"
    # Unset: hollow, dashed and faded so it does not read as a real server location.
    ".sw-pin.sw-unset .sw-body{fill:var(--bg);stroke:var(--pin);stroke-dasharray:4 3}"
    ".sw-pin:focus-visible .sw-ring{stroke:var(--ink);stroke-width:3}"
    ".sw-pin.sw-drag{cursor:grabbing}.sw-locked .sw-pin{cursor:default}"
    ".sw-pin text{fill:var(--ink);font-weight:600;text-transform:uppercase;letter-spacing:.1em;"
    "pointer-events:none}"
    ".sw-preview path{fill:none;stroke:var(--pin);stroke-width:1.4;opacity:.4;pointer-events:none}"
)


def render_picker_map(selected: list[str], width: int = 1000) -> str:
    """The location picker: one toggle dot per catalog place, filled blue when selected, grey ring when not.

    Same land and style as render_map. No outside resources; every dynamic string is escaped.
    """
    W = width
    H = W / 2
    k = W / 1000
    chosen = set(selected)
    out = [
        f'<svg class="sw-map sw-picker" viewBox="0 0 {_num(W)} {_num(H)}" '
        f'role="group" aria-label="World map: choose test locations">',
        f"<style>{_STYLE}{_PICK_STYLE}</style>",
        f'<rect width="{_num(W)}" height="{_num(H)}" fill="var(--bg)"/>',
    ]
    scale = f' transform="scale({k:g})"' if W != 1000 else ""
    out.append(f'<g fill="var(--land)"{scale}>' + "".join(f'<path d="{d}"/>' for d in _land()) + "</g>")
    # Faint server-to-place lines; the page script fills this layer while a pin is set (SVG, no outside data).
    out.append('<g class="sw-preview" aria-hidden="true"></g>')
    for p in CATALOG:
        x = (p.lon + 180) / 360 * W
        y = (90 - p.lat) / 180 * H
        on = "true" if p.name in chosen else "false"
        out.append(f'<circle class="sw-pick" data-place="{escape(p.name, quote=True)}" tabindex="0" '
                   f'role="checkbox" aria-checked="{on}" cx="{_num(x)}" cy="{_num(y)}" r="{_num(7 * k)}">'
                   f"<title>{escape(p.name)}, {escape(p.country)}</title></circle>")
        out.append(f'<circle class="sw-pick-ring" aria-hidden="true" cx="{_num(x)}" cy="{_num(y)}" r="{_num(13.5 * k)}"/>')
    # Resting place of the unset pin: open Pacific on the right, clear of the side panels and the
    # bottom strip so it can be grabbed. The script moves it (data-home is that spot).
    hx, hy = _num(345 / 360 * W), _num(H / 2)
    out.append(f'<g class="sw-pin sw-unset" tabindex="0" role="application" aria-roledescription="draggable pin" '
               f'aria-label="Server position pin, not set" aria-describedby="pin-help" '
               f'data-home="{hx},{hy}" transform="translate({hx},{hy})">'
               f'<circle class="sw-ring" r="{_num(13 * k)}"/><circle class="sw-body" r="{_num(9 * k)}"/>'
               f'<text x="{_num(15 * k)}" y="{_num(4 * k)}" font-size="{_num(max(9.0, 11 * k))}">Server (drag me)</text></g>')
    out.append("</svg>")
    return "".join(out)
