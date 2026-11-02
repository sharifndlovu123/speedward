"""Presentation helpers shared by the HTML and Markdown reports. Only measured data becomes numbers."""
from __future__ import annotations

import re

from speedward.core.analysis import STAGE_NAMES, summarise
from speedward.core.edge import edge_city
from speedward.core.models import (
    DB_UNREADABLE,
    NO_DB,
    NOT_RUN_TEXTS,
    SAME_PROBE,
    LocationResult,
    ScanReport,
)

# The note under the summary depends on which probes the repeat requests really ran on (spec: the
# cache gain counts only same-probe pairs; other repeats are shown labelled, never as a cache figure).
CACHE_NOTE = "Repeat request from the same test machine; not a browser cache test."
CACHE_MIXED_NOTE = ('Cache gain uses only locations repeated on the same test machine; rows marked '
                    '"different probe" are left out of it. Not a browser cache test.')
CACHE_OTHER_NOTE = ("The repeat request ran on a different test machine, so no cache gain is shown; the "
                    "Cache column gives that repeat timing for reference only. Not a browser cache test.")
NO_REPEAT_NOTE = "No repeat request was measured, so no cache gain is shown."
# Why the cache-gain tile is empty.
REPEAT_OTHER = "the repeat ran on a different test machine"
REPEAT_NONE = "no repeat request was measured"
# What each skipped measurement (options["not_run"]) leaves without figures.
SKIP_EFFECT = {"repeat test": "the Cache column and cache gain have no figures",
               "ping": "the Delay column has no figures",
               "network path": "the routes have no data",
               "DNS check": "the DNS answers have no data"}
STAGE_LABELS = {"dns": "DNS", "connect": "Connect", "tls": "TLS", "first_byte": "First byte",
                "download": "Download"}
STATUS_TEXT = {"measured": "measured", "blocked": "blocked", "test_failed": "test failed",
               "not_run": "did not run"}
NO_DATA = "no data"
NA = "not available"


def ms(v: float | None) -> str:
    return NA if v is None else f"{v:.0f} ms"


def numeral(text: str) -> tuple[str, str]:
    """Split a figure like "412 ms" into the number and its unit (with the leading space), so the number
    can be set large. Joined back together it is the same text; "-38%" and words stay whole."""
    m = re.fullmatch(r"(-?\d[\d.,]*%?)(\s+\S+)?", text)
    return (m.group(1), m.group(2) or "") if m else (text, "")


def label(r: LocationResult) -> str:
    return ", ".join(p for p in (r.location.city, r.location.country) if p) or "unknown location"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def excluded_line(s) -> str:
    return (f"{plural(s.excluded, 'location')} excluded from the figures ({s.blocked} blocked); "
            f"{s.measured} measured.")


CDN_EDGE_NOTE = "CDN edge, not necessarily your origin"
SOURCE_TEXT = {"database": "geo-IP database", "manual": "manual (setting)", "manual (pin)": "manual (pin)"}


def _from_header(r: LocationResult) -> bool:
    return r.server_position is not None and r.server_position.source == "cdn header" and bool(r.edge_code)


def answered_by(r: LocationResult) -> str:
    """Who answered a measured result: the CDN edge named by its header, else the IP."""
    if _from_header(r):
        city = edge_city(r.edge_code)
        return f"edge {r.edge_code} ({city})" if city else f"edge {r.edge_code}"
    return r.server_ip or NA


def position_source(r: LocationResult) -> str:
    """Where a measured result's server position came from, or why there is none."""
    if r.server_position is not None:
        if _from_header(r):
            return f"CDN header (cf-ray -{r.edge_code})"
        return SOURCE_TEXT.get(r.server_position.source, r.server_position.source)
    if r.edge_note:
        return f"{r.edge_note} ({r.edge_reason})" if r.edge_reason else r.edge_note
    return NA


def repeat_note(r: LocationResult) -> str:
    """Label for a repeat timing that did not come from the cold run's own probe ("" when it did)."""
    if r.repeat_stages is None or r.repeat_probe in (None, SAME_PROBE):
        return ""
    return f"repeat request ({r.repeat_probe})"


def delay_note(r: LocationResult) -> str:
    if r.ping_ms is None or r.ping_probe in (None, SAME_PROBE):
        return ""
    return f"delay from a {r.ping_probe}"


def probe_suffix(probe: str | None) -> str:
    return f" ({probe})" if probe not in (None, SAME_PROBE) else ""


def skipped(report: ScanReport) -> list[tuple[str, str, str]]:
    """(measurement, fixed text, note) for each measurement that did not run. Only known texts are shown."""
    raw = report.options.get("not_run") if isinstance(report.options, dict) else None
    out = []
    for text in raw if isinstance(raw, list) else []:
        if text in NOT_RUN_TEXTS:
            what, why = text.split(": ", 1)
            out.append((what, text, f"{what[0].upper()}{what[1:]}: {why}, so {SKIP_EFFECT[what]}."))
    return out


def cache_notes(report: ScanReport, s) -> tuple[str, str]:
    """(note under the summary, reason on the empty cache tile; "" when there is a figure)."""
    repeats = [r for r in report.locations if r.status == "measured" and r.repeat_stages is not None]
    same = [r for r in repeats if r.repeat_probe == SAME_PROBE]
    if same:
        note = CACHE_NOTE if len(same) == len(repeats) else CACHE_MIXED_NOTE
    else:
        note = CACHE_OTHER_NOTE if repeats else NO_REPEAT_NOTE
    if s.cache_gain_pct is not None:
        return note, ""
    skip = {what: text for what, text, _ in skipped(report)}
    if "repeat test" in skip:
        return note, skip["repeat test"]
    if not repeats:
        return note, REPEAT_NONE
    return note, REPEAT_OTHER if not same else NA


def table_rows(report: ScanReport) -> list[dict]:
    rows = []
    for r in report.locations:
        base = {"location": label(r), "network": r.location.network or ""}
        if r.status != "measured":
            rows.append({**base, "excluded": True, "cold": NO_DATA, "warm": NO_DATA, "delay": NO_DATA,
                         "warm_note": "", "delay_note": "",
                         "cdn": NO_DATA, "answered": NO_DATA, "source": NO_DATA,
                         "status": f"{STATUS_TEXT[r.status]}: {r.reason or 'no reason given'}"})
            continue
        cold = r.stages.first_byte if r.stages is not None else None
        warm = r.repeat_stages.first_byte if r.repeat_stages is not None else None
        rows.append({**base, "excluded": False, "cold": ms(cold), "warm": ms(warm), "delay": ms(r.ping_ms),
                     "warm_note": repeat_note(r), "delay_note": delay_note(r),
                     "cdn": r.cdn or NA, "answered": answered_by(r), "source": position_source(r),
                     "status": f"HTTP {r.http_status}" if r.http_status is not None else "measured"})
    return rows


def stage_averages(report: ScanReport) -> list[dict]:
    """Average per stage over measured locations; a stage nobody returned is left out, never zero-filled."""
    out = []
    for name in STAGE_NAMES:
        vals = [getattr(r.stages, name) for r in report.locations
                if r.status == "measured" and r.stages is not None and getattr(r.stages, name) is not None]
        if vals:
            out.append({"name": STAGE_LABELS[name], "ms": sum(vals) / len(vals)})
    top = max((s["ms"] for s in out), default=0)
    for s in out:
        s["pct"] = round(s["ms"] / top * 100) if top > 0 else 0
        s["text"] = f"{s['ms']:.0f} ms"
    return out


def server_identity(report: ScanReport) -> list[tuple[str, str]]:
    measured = [r for r in report.locations if r.status == "measured"]

    def uniq(values):
        seen = []
        for v in values:
            if v and v not in seen:
                seen.append(v)
        return ", ".join(seen) or NA

    return [
        ("Answering IP", uniq(r.server_ip for r in measured)),
        ("First-response redirect", uniq(x for r in measured for x in r.redirects) if
         any(r.redirects for r in measured) else "none seen" if measured else NA),
        ("TLS version", uniq(r.tls_version for r in measured)),
        ("HTTP version", uniq(r.http_version for r in measured)),
        ("Server position on the map", server_source(report)),
    ]


def server_source(report: ScanReport) -> str:
    s = report.server
    if s is not None and (s.source == "manual" or not any(r.server_position for r in report.locations)):
        return SOURCE_TEXT.get(s.source, s.source)
    if any(r.status == "measured" and r.server_position is not None for r in report.locations):
        return "from the server that answered each location (see Position source)"
    return NA


def position_note(report: ScanReport) -> str | None:
    """Which note the web UI shows beside the map when no answering server could be placed:
    "setup" (no geo-IP database, or a report from before positions existed), "uncertain" (every
    address was doubtful), or None (something was placed, or SERVER_LAT/SERVER_LON is set)."""
    if report.server is not None and report.server.source == "manual":
        return None
    measured = [r for r in report.locations if r.status == "measured"]
    if any(r.server_position is not None for r in measured):
        return None
    reasons = {r.edge_reason for r in measured}
    if not measured or None in reasons or NO_DB in reasons or DB_UNREADABLE in reasons:
        return "setup"
    return "uncertain"


def dns_rows(report: ScanReport) -> tuple[list[dict], bool]:
    rows, answer_sets = [], set()
    for d in report.dns:
        if d.status == "measured":
            answer_sets.add(tuple(sorted(d.answers)))
            rows.append({"location": ", ".join(p for p in (d.location.city, d.location.country) if p)
                         + probe_suffix(d.probe),
                         "answers": ", ".join(d.answers) or NA, "lookup": ms(d.lookup_ms)})
        else:
            rows.append({"location": ", ".join(p for p in (d.location.city, d.location.country) if p),
                         "answers": f"{NO_DATA} ({STATUS_TEXT[d.status]}: {d.reason or 'no reason given'})",
                         "lookup": NO_DATA})
    return rows, len(answer_sets) > 1


def path_rows(report: ScanReport) -> list[dict]:
    out = []
    for p in report.paths:
        name = ", ".join(x for x in (p.location.city, p.location.country) if x)
        if p.status != "measured":
            out.append({"location": name, "note": f"{NO_DATA} ({STATUS_TEXT[p.status]}: {p.reason})", "hops": []})
            continue
        name += probe_suffix(p.probe)
        hops = [{"n": h.n, "host": h.host or "*", "rtt": ms(h.rtt_ms),
                 "loss": NA if h.loss_pct is None else f"{h.loss_pct:g}%"} for h in p.hops]
        out.append({"location": name, "note": "", "hops": hops})
    return out


def context(report: ScanReport) -> dict:
    s = summarise(report)
    dns, differ = dns_rows(report)
    b = report.browser
    cache = f"{s.cache_gain_pct:.0f}%" if s.cache_gain_pct is not None else "n/a"
    cache_note, cache_na_note = cache_notes(report, s)
    return {
        "r": report, "s": s, "target": report.target.split("://", 1)[-1],
        "avg": ms(s.avg_first_byte) if s.avg_first_byte is not None else NO_DATA,
        "avg_fig": numeral(ms(s.avg_first_byte)) if s.avg_first_byte is not None else (NO_DATA, ""),
        "cache_fig": numeral(cache),
        "cache": cache, "cache_na": s.cache_gain_pct is None, "cache_na_note": cache_na_note,
        "cache_note": cache_note, "skipped": [note for _, _, note in skipped(report)],
        "excluded_line": excluded_line(s),
        "rows": table_rows(report), "stages": stage_averages(report),
        "identity": server_identity(report), "position_note": position_note(report), "dns": dns, "dns_differs": differ, "paths": path_rows(report),
        "browser": None if b is None else {
            "weight": f"{b.page_weight_bytes / 1024:.0f} KB" if b.page_weight_bytes is not None else NA,
            "requests": f"{b.requests} requests" if b.requests is not None else NA,
            "load": ms(b.load_ms), "biggest": [(u, f"{n / 1024:.0f} KB") for u, n in b.biggest]},
    }
