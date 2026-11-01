from ._view import CACHE_NOTE, context


def _c(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def to_markdown(report) -> str:
    c = context(report)
    L = [f"# Speed report: {_c(report.target)}", "",
         f"Tested {report.started.isoformat()} with speedward {report.version} through Globalping.", "",
         "## Summary", "",
         f"- Average first byte: {c['avg']}",
         f"- Cache gain: {c['cache']}" + (f" ({c['cache_na_note']})" if c["cache_na"] else ""),
         f"- Blocked: {c['s'].blocked}"]
    if c["browser"]:
        L.append(f"- Page weight: {c['browser']['weight']}")
    L += [f"- {c['excluded_line']}", f"- {CACHE_NOTE}", "",
          "## Locations", "",
          "| Location | No cache | Cache | Delay | CDN | Status |", "| --- | --- | --- | --- | --- | --- |"]
    for r in c["rows"]:
        L.append(f"| {_c(r['location'])} | {r['cold']} | {r['warm']} | {r['delay']} | {_c(r['cdn'])} "
                 f"| {_c(r['status'])} |")
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
        L += [f"- {p['note']}"] if p["note"] else [
            f"{h['n']}. {_c(h['host'])} {h['rtt']}, loss {h['loss']}" for h in p["hops"]]
    if c["browser"]:
        b = c["browser"]
        L += ["", "## Browser", "", f"- Page weight: {b['weight']}", f"- {b['requests']}", f"- Load: {b['load']}"]
        L += [f"- {_c(u)}: {n}" for u, n in b["biggest"]]
    return "\n".join(L) + "\n"
