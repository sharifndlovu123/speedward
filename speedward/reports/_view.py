"""Presentation helpers shared by the HTML and Markdown reports. Only measured data becomes numbers."""
from __future__ import annotations

from speedward.core.analysis import STAGE_NAMES, summarise
from speedward.core.edge import edge_city
from speedward.core.models import LocationResult, ScanReport

CACHE_NOTE = "Repeat request from the same test machine; not a browser cache test."
CACHE_NA_NOTE = "needs the same test machine for both runs"
STAGE_LABELS = {"dns": "DNS", "connect": "Connect", "tls": "TLS", "first_byte": "First byte",
                "download": "Download"}
STATUS_TEXT = {"measured": "measured", "blocked": "blocked", "test_failed": "test failed",
               "not_run": "did not run"}
NO_DATA = "no data"
NA = "not available"


def ms(v: float | None) -> str:
    return NA if v is None else f"{v:.0f} ms"


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


def table_rows(report: ScanReport) -> list[dict]:
    rows = []
    for r in report.locations:
        base = {"location": label(r), "network": r.location.network or ""}
        if r.status != "measured":
            rows.append({**base, "excluded": True, "cold": NO_DATA, "warm": NO_DATA, "delay": NO_DATA,
                         "cdn": NO_DATA, "answered": NO_DATA, "source": NO_DATA,
                         "status": f"{STATUS_TEXT[r.status]}: {r.reason or 'no reason given'}"})
            continue
        cold = r.stages.first_byte if r.stages is not None else None
        warm = r.repeat_stages.first_byte if r.repeat_stages is not None else None
        rows.append({**base, "excluded": False, "cold": ms(cold), "warm": ms(warm), "delay": ms(r.ping_ms),
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
    ]


def dns_rows(report: ScanReport) -> tuple[list[dict], bool]:
    rows, answer_sets = [], set()
    for d in report.dns:
        if d.status == "measured":
            answer_sets.add(tuple(sorted(d.answers)))
            rows.append({"location": ", ".join(p for p in (d.location.city, d.location.country) if p),
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
        hops = [{"n": h.n, "host": h.host or "*", "rtt": ms(h.rtt_ms),
                 "loss": NA if h.loss_pct is None else f"{h.loss_pct:g}%"} for h in p.hops]
        out.append({"location": name, "note": "", "hops": hops})
    return out


def context(report: ScanReport) -> dict:
    s = summarise(report)
    dns, differ = dns_rows(report)
    b = report.browser
    cache = f"{s.cache_gain_pct:.0f}%" if s.cache_gain_pct is not None else "n/a"
    return {
        "r": report, "s": s, "target": report.target.split("://", 1)[-1],
        "avg": ms(s.avg_first_byte) if s.avg_first_byte is not None else NO_DATA,
        "cache": cache, "cache_na": s.cache_gain_pct is None, "cache_na_note": CACHE_NA_NOTE,
        "cache_note": CACHE_NOTE,
        "excluded_line": excluded_line(s),
        "rows": table_rows(report), "stages": stage_averages(report),
        "identity": server_identity(report), "dns": dns, "dns_differs": differ, "paths": path_rows(report),
        "browser": None if b is None else {
            "weight": f"{b.page_weight_bytes / 1024:.0f} KB" if b.page_weight_bytes is not None else NA,
            "requests": f"{b.requests} requests" if b.requests is not None else NA,
            "load": ms(b.load_ms), "biggest": [(u, f"{n / 1024:.0f} KB") for u, n in b.biggest]},
    }
