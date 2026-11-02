"""Words, the table and the diagram notes for "Is it fast?". Pure rendering of speed_summary(report).

Every cell is a symbol and a word (never colour alone): ● measured, ○ blocked, ◌ no data. A finding says
what the timings indicate and ends with a "Check:" prompt, never a cause; with fewer than MIN_PLACES places
it says there are not enough. Page size on the wire is not available and is never estimated. A report
without speed checks (or from before they existed) renders nothing.
"""
from __future__ import annotations

import copy
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from speedward.core.checks import COMPRESSION, CONSISTENCY, DNS_TRACE, HTTP2, MTR
from speedward.core.speed import (
    CONSISTENCY_RUNS,
    VARIES_MIN_MS,
    VARIES_RATIO,
    Overall,
    PlaceSpeed,
    SpeedSummary,
    speed_summary,
)
from speedward.reports._md import md_fold, md_text

SYMBOL = {"measured": "●", "blocked": "○", "no_data": "◌"}
STATUS_TEXT = {"test_failed": "test failed", "not_run": "did not run", "blocked": "blocked"}
HEADER = {CONSISTENCY: "Consistency", HTTP2: "HTTP/2", COMPRESSION: "Compression", DNS_TRACE: "DNS trace",
          MTR: "Path"}
STEP = {"local": "local resolver", "root": "root", "tld": "TLD", "delegation": "delegation",
        "authoritative": "authoritative"}
KEY = ("● measured · ○ blocked (the site side refused or timed out) · ◌ no data (the Globalping side failed or "
       "the check did not run; left out of every figure). Marked by symbol and word, never colour alone.")
NO_SIZE = ("Page size on the wire is not available: Globalping does not return it (the answer is chunked and "
           "its body is cut at 10 KB), so it is never estimated.")
ONE_RUN = "Each check is one run per place (consistency: 5), so small differences are noise."
MAX_NAMES = 5
DNS_CHECK = {
    "local": "the resolver the visitors' own networks use; it is outside the site's control.",
    "root": "nothing at the site: the root servers are shared by every name.",
    "tld": "the top-level domain's servers are outside the site's control; longer TTLs on the NS records keep "
           "them out of most lookups.",
    "delegation": "the delegation chain: each extra level adds a lookup.",
    "authoritative": "the DNS provider's response time and how long its answers are kept (TTL).",
}

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)), autoescape=True)
CSS = (Path(__file__).parent / "speed.css").read_text(encoding="utf-8")


def _ms(v: float | None) -> str:
    if v is None:
        return "not available"
    return f"{v:.0f} ms" if v >= 1 else "under 1 ms"


def _signed(v: float) -> str:
    return f"{v:+.0f} ms" if abs(v) >= 1 else "within 1 ms"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:MAX_NAMES])
    more = len(names) - MAX_NAMES
    return shown + (f" and {more} more" if more > 0 else "")


# --- cells -----------------------------------------------------------------------------------------

def _consistency_cell(p: PlaceSpeed) -> tuple[str, str]:
    c = p.consistency
    word = {"steady": "steady", "varies": "varies"}.get(c.verdict, f"{c.runs} of {CONSISTENCY_RUNS} runs")
    detail = f"server wait median {_ms(c.median_ms)} · slowest {_ms(c.slowest_ms)}"
    if c.verdict == "too_few_runs":
        detail += " · not judged"
    if c.blocked:
        detail += f" · {_plural(c.blocked, 'run')} blocked"
    if c.other_probe:
        detail += f" · {_plural(c.other_probe, 'run')} on another machine left out"
    return word, detail


def _http2_cell(p: PlaceSpeed) -> tuple[str, str]:
    h = p.http2
    if not h.offered:
        return "not offered", ("HTTP/1.1 only" if h.version is None else f"answered {h.version}")
    if h.first_byte_diff_ms is None:
        return "HTTP/2", "no same-machine HTTP/1.1 run to compare"
    parts = [f"first byte {_signed(h.first_byte_diff_ms)}"]
    if h.connect_diff_ms is not None:
        parts.append(f"connect {_signed(h.connect_diff_ms)}")
    if h.tls_diff_ms is not None:
        parts.append(f"TLS {_signed(h.tls_diff_ms)}")
    return "HTTP/2", " · ".join(parts) + " vs HTTP/1.1"


def _compression_cell(p: PlaceSpeed) -> tuple[str, str]:
    c = p.compression
    if c.encoding == "none":
        word, detail = "not compressed", "no Content-Encoding"
    else:
        word, detail = "compressed", c.encoding
    if c.download_diff_ms is not None:
        detail += f" · download {_signed(c.download_diff_ms)} vs the main test"
    return word, detail


def _trace_cell(p: PlaceSpeed) -> tuple[str, str]:
    t = p.dns_trace
    word = f"slowest: {STEP[t.slowest.kind]}" if t.slowest is not None else "every step under 1 ms"
    detail = " · ".join(f"{STEP[s.kind]} {_ms(s.ms)}" for s in t.steps)
    if t.slowest is not None:
        detail += f" · slowest server asked: {t.slowest.server}"
    return word, detail


def _path_cell(p: PlaceSpeed) -> tuple[str, str]:
    x = p.path
    if not x.reached:
        return "no end point", (f"the site did not answer this path test ({_plural(x.hops, 'hop')}, "
                                f"{x.silent} silent); no loss judged")
    if x.bad_hop is not None:
        word = f"bad hop {x.bad_hop.n}"
        detail = f"{x.bad_hop.loss_pct:g}% loss from {x.bad_hop.host} to the site"
    else:
        word = "no bad hop"
        detail = f"loss at the site {x.site_loss_pct or 0:g}%"
    if x.jitter_ms is not None:
        detail += f" · jitter {x.jitter_ms:g} ms"
    if x.long_link is not None:
        detail += f" · +{x.long_link.rise_ms:.0f} ms at hop {x.long_link.n} ({x.long_link.host})"
    return word, detail


_CELL = {CONSISTENCY: ("consistency", _consistency_cell), HTTP2: ("http2", _http2_cell),
         COMPRESSION: ("compression", _compression_cell), DNS_TRACE: ("dns_trace", _trace_cell),
         MTR: ("path", _path_cell)}


def cell_text(p: PlaceSpeed, check: str) -> dict:
    attr, fn = _CELL[check]
    if getattr(p, attr) is not None:
        word, detail = fn(p)
        return {"state": "measured", "symbol": SYMBOL["measured"], "word": word, "detail": detail}
    gone = p.gone[check]
    state = "blocked" if gone.status == "blocked" else "no_data"
    word = "blocked" if state == "blocked" else "no data"
    detail = gone.reason or "no reason given"
    if state == "no_data":
        detail = f"{STATUS_TEXT.get(gone.status, gone.status)}: {detail}"
    return {"state": state, "symbol": SYMBOL[state], "word": word, "detail": detail}


# --- findings ----------------------------------------------------------------------------------------

def _too_few(o: Overall, what: str, s: SpeedSummary) -> dict:
    return {"check": o.check, "text": (f"{HEADER[o.check]}: not enough places for a conclusion "
                                       f"({_plural(o.judged, 'place')} {what}, at least {s.min_places} needed)."),
            "prompt": ""}


def finding(o: Overall, s: SpeedSummary) -> dict:
    n = _plural(o.judged, "place")
    if o.check == CONSISTENCY:
        if o.verdict == "too_few":
            return _too_few(o, f"with {CONSISTENCY_RUNS} runs", s)
        if o.verdict == "steady":
            return {"check": o.check, "prompt": "",
                    "text": (f"The timings indicate the server wait is steady: {CONSISTENCY_RUNS} runs each in {n}, "
                             f"no slowest run at {VARIES_RATIO:g} times its place's median and {VARIES_MIN_MS:.0f} ms "
                             "above it.")}
        return {"check": o.check,
                "text": (f"The timings indicate the server wait varies in {len(o.places)} of {n} "
                         f"({_names(o.places)}): from {_ms(o.low_ms)} to {_ms(o.high_ms)} over {CONSISTENCY_RUNS} runs."),
                "prompt": "Check: origin load, slow pages or database calls, and cache rules that cause a MISS on "
                          "some runs."}
    if o.check == HTTP2:
        if o.verdict == "too_few":
            return _too_few(o, "with an answer", s)
        if o.verdict == "none":
            return {"check": o.check, "text": f"The timings indicate HTTP/2 is not offered in any of {n}.",
                    "prompt": "Check: whether HTTP/2 is turned on at the server or CDN (one connection then "
                              "carries many requests)."}
        text = (f"HTTP/2 is offered in all {n}." if o.verdict == "all" else
                f"HTTP/2 is offered in {o.count} of {n}; not in {_names(o.places)}.")
        if o.ms is not None:
            text += f" The timings indicate a first byte {_signed(o.ms)} against HTTP/1.1 (median, one run each)."
        prompt = "" if o.verdict == "all" else "Check: that every server or edge behind the address offers HTTP/2."
        return {"check": o.check, "text": text, "prompt": prompt}
    if o.check == COMPRESSION:
        if o.verdict == "too_few":
            return _too_few(o, "with an answer", s)
        if o.verdict == "all":
            return {"check": o.check, "prompt": "",
                    "text": f"The first response is compressed in all {n} ({', '.join(o.encodings)})."}
        if o.verdict == "none":
            return {"check": o.check, "text": f"The first response is not compressed in any of {n}.",
                    "prompt": "Check: brotli or gzip for text responses at the server or CDN."}
        return {"check": o.check,
                "text": f"The first response is not compressed in {len(o.places)} of {n} ({_names(o.places)}).",
                "prompt": "Check: that every server or edge compresses the same way."}
    if o.check == DNS_TRACE:
        if o.verdict == "too_few":
            return _too_few(o, "with a trace", s)
        if o.kind is None:
            return {"check": o.check, "prompt": "", "text": f"Every DNS trace step took under 1 ms in {n}."}
        return {"check": o.check,
                "text": (f"The timings indicate the slowest DNS step is the {STEP[o.kind]} in {o.count} of {n} "
                         f"(median {_ms(o.ms)})."),
                "prompt": "Check: " + DNS_CHECK[o.kind]}
    if o.verdict == "too_few":
        return _too_few(o, "where the site answered the path test", s)
    if o.verdict == "bad_hop":
        return {"check": o.check,
                "text": (f"The timings indicate a bad hop in {o.count} of {n} ({_names(o.places)}): loss from one "
                         "hop that carries through to the site."),
                "prompt": "Check: the network at that hop (outside the site's control unless it is the host's own)."}
    text = f"No bad hop in {n}: no loss carries through to the site."
    if o.places:
        text += (f" Long links (a rise that stays to the end) in {_names(o.places)}: the timings indicate distance.")
    return {"check": o.check, "text": text,
            "prompt": "Check: regions or edges near visitors, if far places are slow." if o.places else ""}


# --- notes on the "where the time goes" diagram ----------------------------------------------------

def _place_notes(p: PlaceSpeed) -> dict[str, str]:
    out = {}
    c = p.consistency
    if c is not None:
        out["server_wait"] = {"steady": f"steady over {CONSISTENCY_RUNS} runs",
                              "varies": f"varies {c.fastest_ms:.0f} to {c.slowest_ms:.0f} ms"}.get(
            c.verdict, f"{c.runs} of {CONSISTENCY_RUNS} runs answered")
    if p.tls12:
        out["tls"] = "TLS 1.2 costs one more round trip"
    if p.compression is not None:
        out["download"] = ("not compressed" if p.compression.encoding == "none"
                           else f"compressed ({p.compression.encoding})")
    if p.dns_trace is not None and p.dns_trace.slowest is not None:
        out["dns"] = f"slowest step: {STEP[p.dns_trace.slowest.kind]} {_ms(p.dns_trace.slowest.ms)}"
    if p.path is not None and p.path.reached:
        x = p.path
        if x.bad_hop is not None:
            out["connect"] = f"bad hop {x.bad_hop.n}: {x.bad_hop.loss_pct:g}% loss to the site"
        elif x.long_link is not None:
            out["connect"] = f"no bad hop; +{x.long_link.rise_ms:.0f} ms at hop {x.long_link.n} (long link)"
        else:
            out["connect"] = "no bad hop"
    return out


def diagram_notes(s: SpeedSummary) -> dict:
    """{"overall": {stage: text}, "places": {row: {stage: text}}}. Overall notes need MIN_PLACES places."""
    overall: dict[str, str] = {}
    for o in s.overall:
        if o.verdict == "too_few":
            continue
        n = o.judged
        if o.check == CONSISTENCY:
            overall["server_wait"] = (f"steady over {CONSISTENCY_RUNS} runs in {n} places" if o.verdict == "steady"
                                      else f"varies {o.low_ms:.0f} to {o.high_ms:.0f} ms ({len(o.places)} of {n} places)")
        elif o.check == COMPRESSION:
            overall["download"] = {"none": "not compressed", "all": f"compressed ({', '.join(o.encodings)})"}.get(
                o.verdict, f"not compressed in {len(o.places)} of {n} places")
        elif o.check == DNS_TRACE and o.kind is not None:
            overall["dns"] = f"slowest step: {STEP[o.kind]} in {o.count} of {n} places (median {_ms(o.ms)})"
        elif o.check == MTR:
            overall["connect"] = (f"bad hop in {o.count} of {n} places" if o.verdict == "bad_hop"
                                  else "no bad hop" + ("; long links: distance" if o.places else ""))
    if s.tls12:
        overall["tls"] = f"TLS 1.2 costs one more round trip ({_names(s.tls12)})"
    return {"overall": overall, "places": {p.row: _place_notes(p) for p in s.places}}


# --- the panel -------------------------------------------------------------------------------------

def view(s: SpeedSummary) -> dict:
    findings = [finding(o, s) for o in s.overall]
    notes = [ONE_RUN] + ([NO_SIZE] if COMPRESSION in s.checks else [])
    return {
        "kicker": (f"Is it fast? · {_plural(len(s.places), 'place')} · {len(s.checks)} speed "
                   f"check{'' if len(s.checks) == 1 else 's'}"),
        "findings": findings,
        "headers": [HEADER[c] for c in s.checks],
        "rows": [{"row": p.row, "place": p.place, "cells": [cell_text(p, c) for c in s.checks]} for p in s.places],
        "key": KEY, "notes": notes,
    }


def render_speed(report, interactive: bool = False, heading: str = "h2") -> str:
    """The panel as escaped HTML ("" for a report without speed checks)."""
    if report.speed is None:
        return ""
    from markupsafe import Markup
    css = Markup(CSS)  # noqa: S704 - a file shipped with this package, not data
    return _env.get_template("speed.html.j2").render(v=view(speed_summary(report)), interactive=interactive,
                                                     h=heading, css=css)


def _escaped(s: SpeedSummary, esc) -> SpeedSummary:
    """A copy with every untrusted name passed through `esc` (the figures are untouched)."""
    s = copy.deepcopy(s)
    s.tls12 = [esc(x) for x in s.tls12]
    for o in s.overall:
        o.places = [esc(x) for x in o.places]
    for p in s.places:
        p.place = esc(p.place)
        for gone in p.gone.values():  # a reason in a posted report is untrusted text too
            gone.reason = esc(gone.reason)
        if p.dns_trace is not None:
            steps = list(p.dns_trace.steps)
            if p.dns_trace.slowest is not None and all(x is not p.dns_trace.slowest for x in steps):
                steps.append(p.dns_trace.slowest)  # deepcopy keeps the slowest step shared with steps
            for step in steps:
                step.server = esc(step.server)
        if p.path is not None:
            for h in (p.path.bad_hop, p.path.long_link, p.path.worst_loss):
                if h is not None:
                    h.host = esc(h.host)
    return s


def notes_for(report, esc=None) -> dict | None:
    """The diagram notes; esc (e.g. md_text) is applied to every untrusted name in them."""
    if report.speed is None:
        return None
    s = speed_summary(report)
    return diagram_notes(_escaped(s, esc) if esc is not None else s)


_md = md_fold


def markdown_lines(report) -> list[str]:
    if report.speed is None:
        return []
    v = view(_escaped(speed_summary(report), md_text))
    L = ["## Is it fast? Speed checks", ""]
    L += [_md(f["text"] + (" " + f["prompt"] if f["prompt"] else "")) for f in v["findings"]]
    if v["headers"]:
        L += ["", "| Place | " + " | ".join(v["headers"]) + " |", "|" + " --- |" * (len(v["headers"]) + 1)]
        for r in v["rows"]:
            cells = [_md(f"{c['symbol']} {c['word']}" + (f" {c['detail']}" if c["detail"] else "")) for c in r["cells"]]
            L.append(f"| {_md(r['place'])} | " + " | ".join(cells) + " |")
    L += ["", v["key"]] + v["notes"]
    return L


def cli_lines(report) -> list[str]:
    if report.speed is None:
        return []
    v = view(speed_summary(report))
    lines = []
    for f in v["findings"]:
        lines.append(f"Is it fast? {f['text']}" + (f" {f['prompt']}" if f["prompt"] else ""))
    return lines
