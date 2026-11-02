"""Run one scan through Globalping and turn the raw answers into a ScanReport.

Order: cold http, warm http (same probes when Globalping can reuse them), ping, then the optional
mtr path and dns measurements. Every number shown comes from a result `classify` called
"measured"; anything else carries a reason and no timings. Field names follow
docs/globalping-api-notes.md.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from speedward import __version__
from speedward.core.analysis import cdn_status
from speedward.core.budget import Plan
from speedward.core.edge import Answer, cf_ray_code, place
from speedward.core.geo import Geolocator
from speedward.core.globalping import (
    GlobalpingClient,
    GlobalpingError,
    ProbeReuseUnavailable,
    RateLimited,
    classify,
)
from speedward.core.models import (
    GLOBALPING_FAILED,
    LIMIT_REACHED,
    NO_DB,
    OTHER_PROBE,
    SAME_CITY,
    SAME_PROBE,
    DnsResult,
    Hop,
    Location,
    LocationResult,
    PathResult,
    ScanReport,
    ServerPosition,
    Stages,
)
from speedward.core.target import normalise_target

_HTTP_VERSION = re.compile(r"^HTTP/(\d(?:\.\d)?)\s")


# Why a scan could not start, in fixed words (no target, no exception text): shown by the web UI and CLI.
START_MESSAGES = {
    "limit": "The scan could not start: Globalping limit reached. Try again when the hourly allowance resets.",
    "no_probe": ("The scan could not start: no test machine found for one of the places you chose. "
                 "Change or remove that place and try again."),
    "key": "The scan could not start: Globalping rejected the API key. Check the GLOBALPING_TOKEN setting.",
    "address": "The scan could not start: the address was not accepted. Check it and try again.",
    "other": "The scan could not start because Globalping could not be used. Try again later.",
}


def start_message(kind: str) -> str:
    return START_MESSAGES.get(kind, START_MESSAGES["other"])


def _start_kind(e: GlobalpingError) -> str:
    return {429: "limit", 422: "no_probe", 401: "key", 400: "address"}.get(getattr(e, "status", None), "other")


class ScanError(Exception):
    """The scan could not start at all. The message is safe to show (no token) but may hold the target,
    so the web UI and CLI show start_message(kind) instead; `kind` is a START_MESSAGES key."""

    def __init__(self, message: str = "", kind: str = "other"):
        super().__init__(message)
        self.kind = kind if kind in START_MESSAGES else "other"


# --- small parsers -------------------------------------------------------

def _num(value) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _str(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _location(probe: dict) -> Location:
    tags = [t for t in (probe.get("tags") or []) if isinstance(t, str)]
    network_type = ("isp" if "eyeball-network" in tags
                    else "datacenter" if "datacenter-network" in tags else None)
    asn = probe.get("asn")
    return Location(
        country=_str(probe.get("country")) or "",
        city=_str(probe.get("city")) or "",
        lat=_num(probe.get("latitude")),
        lon=_num(probe.get("longitude")),
        asn=asn if isinstance(asn, int) and not isinstance(asn, bool) else None,
        network=_str(probe.get("network")),
        network_type=network_type,
    )


def _identity(probe: dict) -> tuple:
    """Everything we know about a probe. Two results are the same probe only if all of it matches."""
    tags = tuple(sorted(t for t in (probe.get("tags") or []) if isinstance(t, str)))
    return (probe.get("country"), probe.get("city"), probe.get("asn"), probe.get("network"), tags)


def _place(probe: dict) -> tuple:
    return (probe.get("country"), probe.get("city"))


def _stages(result: dict) -> Stages:
    t = result.get("timings") if isinstance(result.get("timings"), dict) else {}
    return Stages(dns=_num(t.get("dns")), connect=_num(t.get("tcp")), tls=_num(t.get("tls")),
                  first_byte=_num(t.get("firstByte")), download=_num(t.get("download")),
                  total=_num(t.get("total")))


def _http_version(result: dict) -> str | None:
    raw = result.get("rawOutput")
    if not isinstance(raw, str):
        return None
    m = _HTTP_VERSION.match(raw.split("\n", 1)[0] + " ")
    return f"HTTP/{m.group(1)}" if m else None


def _headers(result: dict) -> dict:
    h = result.get("headers")
    return h if isinstance(h, dict) else {}


def _location_header(headers: dict) -> str | None:
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "location":
            if isinstance(value, list):
                value = value[0] if value else None
            return _str(value)
    return None


def _items(doc: dict | None) -> list[tuple[dict, dict]]:
    out = []
    for item in (doc or {}).get("results") or []:
        if isinstance(item, dict):
            probe = item.get("probe") if isinstance(item.get("probe"), dict) else {}
            out.append((probe, item))
    return out


def _result(item: dict) -> dict:
    r = item.get("result")
    return r if isinstance(r, dict) else {}


def _magic_label(spec: dict) -> str:
    for key in ("magic", "city", "country", "continent", "region"):
        if isinstance(spec.get(key), str):
            return spec[key]
    return "unknown location"


# --- matching -------------------------------------------------------------

class _Matcher:
    """Pairs later results with cold results: same probe first, then same city (other probe)."""

    def __init__(self, items: list[tuple[dict, dict]]):
        self.unused = list(items)

    def take(self, probe: dict) -> tuple[dict | None, bool]:
        for keyfn, same in ((_identity, True), (_place, False)):
            for i, (p, item) in enumerate(self.unused):
                if keyfn(p) == keyfn(probe):
                    self.unused.pop(i)
                    return item, same
        return None, False


def _probe_match(reused: bool, same: bool) -> str:
    """How a later result relates to the cold run's probe. Only a reused probe that matches in every
    known detail is the same probe; fresh probes are never assumed to be the same machine."""
    if reused and same:
        return SAME_PROBE
    return OTHER_PROBE if same else SAME_CITY


# --- the scan ---------------------------------------------------------------

class _Scan:
    def __init__(self, client: GlobalpingClient, host: str, progress: Callable[[str], None]):
        self.client = client
        self.host = host
        self.progress = progress
        self.limit_hit = False
        self.skipped: list[str] = []

    def measure(self, what: str, type: str, locations, options: dict | None,
                fallback_locations=None) -> tuple[dict | None, bool, str]:
        """Create and wait. Returns (doc, reused_probes, reason when doc is None)."""
        if self.limit_hit:
            self.skipped.append(f"{what}: {LIMIT_REACHED}")
            return None, False, LIMIT_REACHED
        reused = isinstance(locations, str)
        try:
            try:
                mid = self.client.create(type, self.host, locations, options)
            except ProbeReuseUnavailable:
                if fallback_locations is None:
                    raise
                self.progress(f"Earlier probes are gone; {what} uses new probes")
                reused = False
                mid = self.client.create(type, self.host, fallback_locations, options)
            return self.client.wait(mid), reused, ""
        except RateLimited:
            self.limit_hit = True
            self.skipped.append(f"{what}: {LIMIT_REACHED}")
            return None, False, LIMIT_REACHED
        except GlobalpingError:
            self.skipped.append(f"{what}: {GLOBALPING_FAILED}")
            return None, False, GLOBALPING_FAILED


def _http_options(parts) -> dict:
    request = {"method": "GET", "path": parts.path or "/"}
    if parts.query:
        request["query"] = parts.query
    options = {"request": request, "protocol": parts.scheme.upper()}
    if parts.port is not None:
        options["port"] = parts.port
    return options


def _cold_fields(probe: dict, item: dict) -> dict:
    """LocationResult keyword arguments for one cold result. The result object is built once, at
    the end, with its final status, so a non-measured location can never pick up a timing."""
    res = _result(item)
    status, reason = classify(item)
    code = res.get("statusCode") if isinstance(res.get("statusCode"), int) else None
    fields = {"location": _location(probe), "status": status, "reason": reason}
    if status == "measured":
        headers = _headers(res)
        tls = res.get("tls") if isinstance(res.get("tls"), dict) else {}
        redirect = _location_header(headers) if code is not None and 300 <= code < 400 else None
        fields.update(stages=_stages(res), http_status=code, cdn=cdn_status(headers),
                      server_ip=_str(res.get("resolvedAddress")), tls_version=_str(tls.get("protocol")),
                      http_version=_http_version(res), redirects=[redirect] if redirect else [],
                      edge_code=cf_ray_code(headers))  # only the derived code; headers are dropped
    elif status == "blocked":
        fields["http_status"] = code
    return fields


def run_scan(url: str, plan: Plan, client: GlobalpingClient,
             on_progress: Callable[[str], None] | None = None,
             geo: Geolocator | None = None) -> ScanReport:
    def progress(line: str) -> None:
        if on_progress is not None:
            on_progress(line)

    try:
        target = normalise_target(url)
    except ValueError as e:
        raise ScanError(str(e), "address") from None
    parts = urlsplit(target)
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal; Globalping takes it bare
        host = host.strip("[]")
    http_opts = _http_options(parts)
    started = datetime.now(UTC)
    scan = _Scan(client, host, progress)
    n = len(plan.locations)

    # 1. Cold run. If this cannot even be created there is nothing to show.
    progress(f"Testing the first visit from {n} locations")
    try:
        cold_id = client.create("http", host, plan.locations, http_opts)
    except RateLimited:
        raise ScanError(f"{LIMIT_REACHED}; no test could be started", "limit") from None
    except GlobalpingError as e:
        raise ScanError(f"The test could not be started: {e}", _start_kind(e)) from None
    try:
        cold_doc = client.wait(cold_id)
    except GlobalpingError:
        cold_doc = None

    if cold_doc is None:
        reason = "the test results could not be fetched from Globalping"
        results = [LocationResult(Location("", _magic_label(s), None, None, None, None, None),
                                  "test_failed", reason) for s in plan.locations]
        return _report(target, started, plan, scan, None, results, [], [])

    cold = _items(cold_doc)
    fields = [_cold_fields(p, item) for p, item in cold]
    measured_idx = [i for i, f in enumerate(fields) if f["status"] == "measured"]

    # 2. Warm run on the same probes.
    progress("Repeating the test on the same probes")
    warm_doc, reused, _ = scan.measure("repeat test", "http", cold_id, http_opts,
                                       fallback_locations=plan.locations)
    warm = _Matcher(_items(warm_doc))
    warm_for = {}
    for i, (probe, _) in enumerate(cold):
        item, same = warm.take(probe)
        warm_for[i] = (item, same)
    for i in measured_idx:
        item, same = warm_for[i]
        if item is not None and classify(item)[0] == "measured":
            fields[i]["repeat_stages"] = _stages(_result(item))
            fields[i]["repeat_same_probe"] = bool(reused and same)
            fields[i]["repeat_probe"] = _probe_match(reused, same)

    # 3. Ping from the same probes.
    progress("Measuring network delay")
    ping_doc, ping_reused, _ = scan.measure("ping", "ping", cold_id, None, fallback_locations=plan.locations)
    ping = _Matcher(_items(ping_doc))
    for i, (probe, _) in enumerate(cold):
        item, same = ping.take(probe)
        if i in measured_idx and item is not None and classify(item)[0] == "measured":
            stats = _result(item).get("stats")
            avg = _num(stats.get("avg")) if isinstance(stats, dict) else None
            if avg is not None:
                fields[i].update(ping_ms=avg, ping_probe=_probe_match(ping_reused, same))

    # 4. Optional path (mtr: gives per-hop ASN and loss, traceroute does not).
    paths: list[PathResult] = []
    if plan.traceroute:
        progress("Tracing the network path")
        doc, reused, why = scan.measure("network path", "mtr", cold_id, None, fallback_locations=plan.locations)
        paths = _paths(cold, doc, why, reused)

    # 5. Optional DNS answers.
    dns: list[DnsResult] = []
    if plan.dns:
        progress("Checking DNS answers")
        doc, reused, why = scan.measure("DNS check", "dns", cold_id, None, fallback_locations=plan.locations)
        dns = _dns(cold, doc, why, reused)

    # 6. Where the server that answered each measured result is (offline: airport table, geo-IP).
    _place_servers(cold, fields, geo)
    results = [LocationResult(**f) for f in fields]

    # Locations Globalping found no probe for.
    shortfall = n - len(cold)
    if shortfall > 0:
        seen = {str(p.get(k) or "").lower() for p, _ in cold for k in ("city", "country")}
        unseen = [_magic_label(s) for s in plan.locations if _magic_label(s).lower() not in seen]
        labels = unseen if len(unseen) == shortfall else ["unmatched location"] * shortfall
        for label in labels:
            results.append(LocationResult(Location("", label, None, None, None, None, None),
                                          "not_run", "no probe was available for this location"))

    server = _server(geo, results)
    progress("Finished")
    return _report(target, started, plan, scan, server, results, paths, dns)


def _place_servers(cold, fields: list[dict], geo: Geolocator | None) -> None:
    answers = []
    for (probe, _), f in zip(cold, fields):
        if f["status"] != "measured":
            answers.append(None)
            continue
        loc, stages = f["location"], f.get("stages")
        answers.append(Answer(ip=f.get("server_ip"), continent=_str(probe.get("continent")), lat=loc.lat,
                              lon=loc.lon, connect_ms=stages.connect if stages is not None else None,
                              edge_code=f.get("edge_code")))
    lookup = geo.lookup if geo is not None else (lambda ip: NO_DB)
    for f, placed in zip(fields, place(answers, lookup)):
        if placed is not None:
            f.update(server_position=placed.position, edge_note=placed.note, edge_reason=placed.reason)


def _server(geo: Geolocator | None, results: list[LocationResult]) -> ServerPosition | None:
    """The one "Server" position: a configured SERVER_LAT/SERVER_LON always; the web form's pin only
    when no result could be placed (it is the person's claim, never a measurement)."""
    if geo is None:
        return None
    if geo.override is not None:
        return ServerPosition(geo.override[0], geo.override[1], "manual")
    if geo.pin is not None and not any(r.server_position is not None for r in results):
        return ServerPosition(geo.pin[0], geo.pin[1], "manual (pin)")
    return None


def _paths(cold, doc, why: str, reused: bool = False) -> list[PathResult]:
    out = []
    matcher = _Matcher(_items(doc))
    for probe, _ in cold:
        loc = _location(probe)
        if doc is None:
            out.append(PathResult(loc, [], "not_run" if why == LIMIT_REACHED else "test_failed", why))
            continue
        item, same = matcher.take(probe)
        if item is None:
            out.append(PathResult(loc, [], "not_run", "no path result for this probe"))
            continue
        status, reason = classify(item)
        if status != "measured":
            out.append(PathResult(loc, [], status, reason))
            continue
        hops = []
        for n, hop in enumerate(_result(item).get("hops") or [], start=1):
            if not isinstance(hop, dict):
                continue
            asns = hop.get("asn")
            asn = asns[0] if isinstance(asns, list) and asns and isinstance(asns[0], int) else None
            stats = hop.get("stats") if isinstance(hop.get("stats"), dict) else {}
            host = _str(hop.get("resolvedHostname")) or _str(hop.get("resolvedAddress")) or "*"
            hops.append(Hop(n, host, asn, _num(stats.get("avg")), _num(stats.get("loss"))))
        out.append(PathResult(loc, hops, "measured", "", _probe_match(reused, same)))
    return out


def _dns(cold, doc, why: str, reused: bool = False) -> list[DnsResult]:
    out = []
    matcher = _Matcher(_items(doc))
    for probe, _ in cold:
        loc = _location(probe)
        if doc is None:
            out.append(DnsResult(loc, "not_run" if why == LIMIT_REACHED else "test_failed", why, [], None))
            continue
        item, same = matcher.take(probe)
        if item is None:
            out.append(DnsResult(loc, "not_run", "no DNS result for this probe", [], None))
            continue
        status, reason = classify(item)
        if status != "measured":
            out.append(DnsResult(loc, status, reason, [], None))
            continue
        res = _result(item)
        answers = [a["value"] for a in res.get("answers") or []
                   if isinstance(a, dict) and isinstance(a.get("value"), str)]
        timings = res.get("timings") if isinstance(res.get("timings"), dict) else {}
        out.append(DnsResult(loc, "measured", "", answers, _num(timings.get("total")), _probe_match(reused, same)))
    return out


def _report(target, started, plan: Plan, scan: _Scan, server, results, paths, dns) -> ScanReport:
    options = {"locations": [dict(s) for s in plan.locations], "traceroute": plan.traceroute,
               "dns": plan.dns, "browser": plan.browser}
    if scan.skipped:
        options["not_run"] = list(scan.skipped)
    return ScanReport(target=target, started=started, finished=datetime.now(UTC),
                      version=__version__, options=options, server=server, locations=results,
                      paths=paths, dns=dns, browser=None)
