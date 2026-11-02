"""The Markdown export. Untrusted text (place names, reasons, hostnames, header values, the address) is
escaped once, at its source, with md_text; fixed words and numbers are written as they are."""
import copy

from ._md import md_fold as _c
from ._md import md_text
from ._view import SOURCE_TEXT, context
from .bottleneck import markdown_lines
from .reach import markdown_lines as reach_lines
from .speed import markdown_lines as speed_lines


def _esc(value):
    return None if value is None else md_text(value)


def _escape_location(loc, done: set[int]) -> None:
    if id(loc) in done:  # one Location can be shared by a result, its path and its DNS answer
        return
    done.add(id(loc))
    loc.city, loc.country, loc.network = md_text(loc.city), md_text(loc.country), _esc(loc.network)


def _escaped(report):
    """A copy for the main tables with every untrusted string passed through md_text. The panels
    (reach, speed, diagnosis) escape their own names and are rendered from the original."""
    report = copy.deepcopy(report)
    done: set[int] = set()
    report.target, report.version = md_text(report.target), md_text(report.version)
    if report.server is not None and report.server.source not in SOURCE_TEXT:
        report.server.source = md_text(report.server.source)
    for r in report.locations:
        _escape_location(r.location, done)
        r.reason = md_text(r.reason)
        r.redirects = [md_text(x) for x in r.redirects]
        r.cdn, r.server_ip = _esc(r.cdn), _esc(r.server_ip)
        r.tls_version, r.http_version = _esc(r.tls_version), _esc(r.http_version)
        r.edge_note, r.edge_reason = _esc(r.edge_note), _esc(r.edge_reason)
    for p in report.paths:
        _escape_location(p.location, done)
        p.reason = md_text(p.reason)
        for h in p.hops:
            h.host = _esc(h.host)
    for d in report.dns:
        _escape_location(d.location, done)
        d.reason, d.answers = md_text(d.reason), [md_text(x) for x in d.answers]
    if report.browser is not None:
        report.browser.biggest = [(md_text(u), n) for u, n in report.browser.biggest]
    return report


def to_markdown(report) -> str:
    c = context(_escaped(report))
    L = [f"# Speed report: {_c(c['r'].target)}", "",
         f"Tested {report.started.isoformat()} with speedward {c['r'].version} through Globalping.", "",
         "## Summary", "",
         f"- Average first byte: {c['avg']}",
         f"- Cache gain: {c['cache']}" + (f" ({c['cache_na_note']})" if c["cache_na"] else ""),
         f"- Blocked: {c['s'].blocked}"]
    if c["browser"]:
        L.append(f"- Page weight: {c['browser']['weight']}")
    L += [f"- {c['excluded_line']}", f"- {c['cache_note']}"]
    L += [f"- {note}" for note in c["skipped"]]
    reach = reach_lines(report)
    if reach:
        L += [""] + reach  # cells are escaped there
    speed = speed_lines(report)
    if speed:
        L += [""] + speed  # likewise
    where = markdown_lines(report)
    if where:
        L += [""] + where  # names and reasons are escaped there
    L += ["",
          "## Locations", "",
          "| Location | No cache | Cache | Delay | CDN | Answered by | Position source | Status |",
          "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in c["rows"]:
        warm = r["warm"] + (f", {r['warm_note']}" if r["warm_note"] else "")
        delay = r["delay"] + (f", {r['delay_note']}" if r["delay_note"] else "")
        L.append(f"| {_c(r['location'])} | {r['cold']} | {warm} | {delay} | {_c(r['cdn'])} "
                 f"| {_c(r['answered'])} | {_c(r['source'])} | {_c(r['status'])} |")
    L += ["", "## Stage breakdown (average over measured locations)", ""]
    L += [f"- {s['name']}: {s['text']}" for s in c["stages"]] or ["- no data"]
    L += ["", "## Server identity", ""] + [f"- {k}: {_c(v)}" for k, v in c["identity"]]
    if c["dns"]:
        L += ["", "## DNS", ""]
        if c["dns_differs"]:
            L += ["Answers differ between locations (common with CDNs that answer by region).", ""]
        L += [f"- {_c(d['location'])}: {_c(d['answers'])} ({d['lookup']})" for d in c["dns"]]
    for p in c["paths"]:
        L += ["", f"## Route from {_c(p['location'])}", ""]
        L += [f"- {_c(p['note'])}"] if p["note"] else [
            f"{h['n']}. {_c(h['host'])} {h['rtt']}, loss {h['loss']}" for h in p["hops"]]
    if c["browser"]:
        b = c["browser"]
        L += ["", "## Browser", "", f"- Page weight: {b['weight']}", f"- {b['requests']}", f"- Load: {b['load']}"]
        L += [f"- {_c(u)}: {n}" for u, n in b["biggest"]]
    return "\n".join(L) + "\n"
