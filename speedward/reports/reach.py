"""Words and the table for "Who can reach it". Pure rendering of reach_grid(report).

Every cell is a symbol and a word (never colour alone): ● reached, ○ blocked / closed, ◌ no data. The
finding says what the results indicate and ends with a "Check:" prompt, never a cause. A report without
checks (or from before they existed) renders nothing.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from speedward.core.checks import IPV6, PLAIN_HTTP, PORT, RESOLVER_IPS, RESOLVERS_CHECK
from speedward.core.models import SAME_PROBE
from speedward.core.reach import MAIN, Cell, Grid, reach_grid

SYMBOL = {"reached": "●", "blocked": "○", "no_data": "◌"}
STATUS_TEXT = {"test_failed": "test failed", "not_run": "did not run"}
KEY = ("● reached (time) · ○ blocked / closed (the site side refused, timed out or has no address) · "
       "◌ no data (the Globalping side failed or the check did not run; left out of every figure). "
       "Marked by symbol and word, never colour alone.")
MAP_LEGEND = ("With more checks, each dot shows the worst result for its place: an open ink ring means at "
              "least one check was blocked there (its label says which).")
MAX_NAMES = 5

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)), autoescape=True)
CSS = (Path(__file__).parent / "reach.css").read_text(encoding="utf-8")


def _ms(v: float | None) -> str:
    if v is None:
        return "time not available"
    return f"{v:.0f} ms" if v >= 1 else "under 1 ms"


def header(grid: Grid, column: str) -> str:
    s = grid.scheme.upper()
    return {MAIN: f"{s} v4", IPV6: f"{s} v6", PLAIN_HTTP: "HTTP", RESOLVERS_CHECK: "DNS",
            PORT: f"Port {grid.port}" if grid.port else "Port"}[column]


def _what(grid: Grid, column: str) -> str:
    """How a column reads inside a sentence."""
    s = grid.scheme.upper()
    return {MAIN: f"cannot reach the site over {s} (IPv4)", IPV6: "cannot reach the site over IPv6",
            PLAIN_HTTP: "cannot reach the site over plain HTTP",
            RESOLVERS_CHECK: "get no address for the name from a public resolver",
            PORT: f"find port {grid.port or 443} closed"}[column]


def _check(grid: Grid, column: str) -> str:
    return {MAIN: "the firewall and any regional or bot blocking",
            IPV6: "AAAA record and IPv6 firewall",
            PLAIN_HTTP: "whether port 80 is open and redirects to HTTPS",
            RESOLVERS_CHECK: "the DNS records and provider (the name should resolve on every public resolver)",
            PORT: f"the firewall for port {grid.port or 443}"}[column]


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:MAX_NAMES])
    more = len(names) - MAX_NAMES
    return shown + (f" and {more} more" if more > 0 else "")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def finding(grid: Grid) -> tuple[str, str]:
    """(the finding, the check prompt; "" when there is nothing to check)."""
    if grid.verdict == "too_few":
        return (f"Not enough places for a conclusion: {_plural(grid.judged, 'place')} with a result, at least "
                f"{grid.min_places} needed. The grid shows each result as it came.", "")
    if grid.verdict == "all_reached":
        return (f"The timings indicate: every place with a result reached the site in every check that ran "
                f"({_plural(grid.judged, 'place')}).", "")
    parts = [f"{f.blocked} of {_plural(f.judged, 'place')} {_what(grid, f.column)} ({_names(f.places)})"
             for f in grid.findings]
    text = "The timings indicate: " + "; ".join(parts)
    if grid.nowhere:
        text += f"; and {_names(grid.nowhere)} cannot reach it at all"
    checks = [_check(grid, f.column) for f in grid.findings]
    if grid.nowhere:
        checks.append("whether a regional block applies")
    return text + ".", "Check: " + "; ".join(checks) + "."


def _probe_note(probe: str | None) -> str:
    return f" ({probe})" if probe not in (None, SAME_PROBE) else ""


def cell_text(grid: Grid, cell: Cell) -> dict:
    """{"symbol", "word", "detail", "state"} for one cell. Only measured parts give numbers."""
    measured = [p for p in cell.parts if p.status == "measured"]
    blocked = [p for p in cell.parts if p.status == "blocked"]
    out = {"state": cell.state, "symbol": SYMBOL[cell.state]}
    if cell.state == "blocked":
        p = blocked[0]
        word = "closed" if cell.column == PORT else "blocked"
        detail = p.reason or "no reason given"
        if p.resolver:
            detail = f"{p.resolver}: {detail}"
        return {**out, "word": word, "detail": detail}
    if cell.state == "no_data":
        p = cell.parts[0]
        return {**out, "word": "no data",
                "detail": f"{STATUS_TEXT.get(p.status, p.status)}: {p.reason or 'no reason given'}"}
    p = measured[0]
    if cell.column == PLAIN_HTTP:
        word = {"https": "→ HTTPS", "other": "redirects, not to HTTPS"}.get(p.redirect, "served over HTTP")
        detail = f"HTTP {p.http_status}" if p.http_status is not None else ""
    elif cell.column == RESOLVERS_CHECK:
        word = "resolves"
        by = {q.resolver: q for q in measured}
        detail = " · ".join(f"{ip} {_ms(by[ip].ms)}" for ip in RESOLVER_IPS if ip in by)
    elif cell.column == PORT:
        word = "open"
        detail = _ms(p.ms) + (f", {p.loss_pct:g}% no answer" if p.loss_pct else "")
    else:
        word = "reached"
        detail = _ms(p.ms)
    return {**out, "word": word, "detail": detail + _probe_note(p.probe)}


def view(grid: Grid) -> dict:
    text, check = finding(grid)
    return {
        "kicker": f"Who can reach it · {_plural(len(grid.rows), 'place')} · {len(grid.columns) - 1} "
                  f"check{'' if len(grid.columns) == 2 else 's'} beside the main test",
        "finding": text, "check": check,
        "headers": [header(grid, c) for c in grid.columns],
        "rows": [{"row": g.row, "place": g.place, "cells": [cell_text(grid, c) for c in g.cells]}
                 for g in grid.rows],
        "key": KEY,
        "resolvers": f"DNS: asked {' and '.join(RESOLVER_IPS)} (public resolvers)"
                     if RESOLVERS_CHECK in grid.columns else "",
    }


def render_reach(report, interactive: bool = False, heading: str = "h2") -> str:
    """The panel as escaped HTML ("" for a report without checks)."""
    if report.reach is None:
        return ""
    from markupsafe import Markup
    css = Markup(CSS)  # noqa: S704 - a file shipped with this package, not data
    return _env.get_template("reach.html.j2").render(v=view(reach_grid(report)), interactive=interactive,
                                                     h=heading, css=css)


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def markdown_lines(report) -> list[str]:
    if report.reach is None:
        return []
    v = view(reach_grid(report))
    L = ["## Who can reach it", "", _md(v["finding"])]
    if v["check"]:
        L.append(_md(v["check"]))
    L += ["", "| Place | " + " | ".join(v["headers"]) + " |", "|" + " --- |" * (len(v["headers"]) + 1)]
    for r in v["rows"]:
        cells = [_md(f"{c['symbol']} {c['word']}" + (f" {c['detail']}" if c["detail"] else "")) for c in r["cells"]]
        L.append(f"| {_md(r['place'])} | " + " | ".join(cells) + " |")
    L += ["", v["key"]]
    if v["resolvers"]:
        L.append(v["resolvers"] + ".")
    return L


def cli_lines(report) -> list[str]:
    if report.reach is None:
        return []
    v = view(reach_grid(report))
    return [f"Who can reach it: {v['finding']}"] + ([v["check"]] if v["check"] else [])
