"""Run one scan through Globalping and turn the raw answers into a ScanReport.

Order: cold http, warm http (same probes when Globalping can reuse them), ping, then the optional
mtr path and dns measurements. Every number shown comes from a result `classify` called
"measured"; anything else carries a reason and no timings. Field names follow
docs/globalping-api-notes.md.
"""
from __future__ import annotations

import re
import statistics
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from speedward import __version__
from speedward.core.analysis import cdn_status
from speedward.core.bottleneck import Rules, diagnose
from speedward.core.budget import Plan
from speedward.core.checks import (
    COMPRESSION,
    CONSISTENCY,
    CONSISTENCY_EXTRA,
    DNS_TRACE,
    HTTP2,
    IPV6,
    MTR,
    MTR_PACKETS,
    PATH_PACKETS,
    PLAIN_HTTP,
    PORT,
    PORT_PACKETS,
    REACH_CHECKS,
    RESOLVER_IPS,
    RESOLVERS_CHECK,
    SPEED_CHECKS,
)
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
    PathHop,
    PathResult,
    ReachResult,
    ScanReport,
    ServerPosition,
    SpeedResult,
    Stages,
    TraceStep,
    clean,
)
from speedward.core.speed import SpeedRules
from speedward.core.target import is_ip_host, normalise_target

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
             geo: Geolocator | None = None, rules: Rules | None = None,
             speed_rules: SpeedRules | None = None) -> ScanReport:
    """rules / speed_rules: the configured thresholds (speedward.core.config.analysis_rules); the
    defaults when omitted. The report keeps them, so its panels and exports are judged by them."""
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
        return _report(target, started, plan, scan, None, results, [], [], [] if _reach_on(plan) else None,
                       [] if _speed_on(plan) else None, rules, speed_rules)

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
    # With the mtr check ticked as well, this one measurement serves both (plan.shared): one test per
    # place, with the larger of the two packet counts.
    paths: list[PathResult] = []
    shared_mtr = None
    if plan.traceroute:
        progress("Tracing the network path")
        shared = plan.shared(MTR)
        options = {"packets": max(PATH_PACKETS, MTR_PACKETS)} if shared else None
        doc, reused, why = scan.measure("network path", "mtr", cold_id, options, fallback_locations=plan.locations)
        paths = _paths(cold, doc, why, reused)
        if shared:
            shared_mtr = (doc, reused, why)
            if doc is None:  # the path check had its answer from this test too: say so for it as well
                scan.skipped.append(f"path loss check: {why}")

    # 5. Optional DNS answers.
    dns: list[DnsResult] = []
    if plan.dns:
        progress("Checking DNS answers")
        doc, reused, why = scan.measure("DNS check", "dns", cold_id, None, fallback_locations=plan.locations)
        dns = _dns(cold, doc, why, reused)

    # 6. The ticked "More checks", on the same probes.
    reach = _reach(scan, cold, cold_id, parts, http_opts, plan) if _reach_on(plan) else None
    # 6b. The ticked "Is it fast?" checks, on the same probes.
    speed = _speed(scan, cold, cold_id, parts, http_opts, plan, shared_mtr) if _speed_on(plan) else None

    # 7. Where the server that answered each measured result is (offline: airport table, geo-IP).
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
    return _report(target, started, plan, scan, server, results, paths, dns, reach, speed, rules, speed_rules)


def _reach_on(plan: Plan) -> bool:
    return any(c in REACH_CHECKS for c in plan.checks)


def _speed_on(plan: Plan) -> bool:
    return any(c in SPEED_CHECKS for c in plan.checks)


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


# --- "More checks" (speedward/core/checks.py) ---------------------------------------------------------

_DNS_CODE = re.compile(r"[A-Z]{2,12}")
NOT_A_NAME = "the address is an IP address, so there is no name to try this with"
NO_CHECK_RESULT = "no result for this probe"
UNREADABLE = "the answer could not be read"
# What reading one probe's odd answer can raise (the strict models raise ValueError).
UNREADABLE_ERRORS = (ValueError, TypeError, ArithmeticError, AttributeError, KeyError, IndexError)


def _check_results(cold, doc, why: str, reused: bool, build, **fixed) -> list[ReachResult]:
    """One ReachResult per cold probe. `build(result_dict) -> dict` adds the check's own fields to a
    result `classify` called measured; it may also turn it into "blocked" by returning a status."""
    out = []
    matcher = _Matcher(_items(doc))
    for row, (probe, _) in enumerate(cold):
        loc = _location(probe)
        if doc is None:
            out.append(ReachResult(row=row, location=loc, status="not_run" if why == LIMIT_REACHED else "test_failed",
                                   reason=why, **fixed))
            continue
        item, same = matcher.take(probe)
        if item is None:
            out.append(ReachResult(row=row, location=loc, status="not_run", reason=NO_CHECK_RESULT, **fixed))
            continue
        try:
            out.append(_check_result(row, loc, item, same, reused, build, fixed))
        except UNREADABLE_ERRORS:  # one odd answer is that probe's "test failed", never the scan's end
            out.append(ReachResult(row=row, location=loc, status="test_failed", reason=UNREADABLE, **fixed))
    return out


def _check_result(row: int, loc, item: dict, same: bool, reused: bool, build, fixed: dict) -> ReachResult:
    status, reason = classify(item)
    res = _result(item)
    if fixed.get("check") == RESOLVERS_CHECK and status != "measured":
        # The test's "target" is the public resolver here: a lookup that did not finish is never the
        # site's doing. Only a finished answer (no address, NXDOMAIN) can be blocked (_dns_fields).
        status, reason = "test_failed", f"the resolver lookup failed: {reason}"
    if status != "measured":
        code = res.get("statusCode") if status == "blocked" and isinstance(res.get("statusCode"), int) else None
        extra = {"http_status": code} if code is not None and fixed.get("check") in (IPV6, PLAIN_HTTP) else {}
        return ReachResult(row=row, location=loc, status=status, reason=reason, **fixed, **extra)
    fields = build(res)
    if fields.get("status", "measured") != "measured":
        return ReachResult(row=row, location=loc, **fields, **fixed)
    return ReachResult(row=row, location=loc, status="measured", reason="", **fields,
                       probe=_probe_match(reused, same), **fixed)


def _not_a_name(cold, **fixed) -> list[ReachResult]:
    return [ReachResult(row=row, location=_location(p), status="not_run", reason=NOT_A_NAME, **fixed)
            for row, (p, _) in enumerate(cold)]


def _http_fields(res: dict) -> dict:
    code = res.get("statusCode") if isinstance(res.get("statusCode"), int) else None
    return {"ms": _stages(res).total, "http_status": code}


def _plain_http_fields(res: dict) -> dict:
    fields = _http_fields(res)
    code = fields["http_status"]
    if code is not None and 300 <= code < 400:
        where = _location_header(_headers(res)) or ""
        fields["redirect"] = "https" if where.lower().startswith("https://") else "other"
    return fields


def _dns_fields(res: dict) -> dict:
    code = res.get("statusCodeName")
    answers = [a for a in res.get("answers") or [] if isinstance(a, dict)]
    timings = res.get("timings") if isinstance(res.get("timings"), dict) else {}
    if code == "NOERROR":
        if any(a.get("type") == "A" and isinstance(a.get("value"), str) for a in answers):
            return {"ms": _num(timings.get("total"))}
        return {"status": "blocked", "reason": "the name has no IPv4 address (A record) on this resolver"}
    if code == "NXDOMAIN":
        return {"status": "blocked", "reason": "the name was not found (NXDOMAIN)"}
    # SERVFAIL, REFUSED and the rest can be the resolver's own trouble: never counted against the site.
    shown = f" ({code})" if isinstance(code, str) and _DNS_CODE.fullmatch(code) else ""
    return {"status": "test_failed", "reason": f"the resolver could not answer{shown}"}


def _port_fields(port: int):
    def build(res: dict) -> dict:
        stats = res.get("stats") if isinstance(res.get("stats"), dict) else {}
        rcv, avg, loss = _num(stats.get("rcv")), _num(stats.get("avg")), _num(stats.get("loss"))
        if rcv == 0 or loss == 100:  # observed: a closed port still "finishes", with no reply at all
            return {"status": "blocked", "reason": f"no answer on port {port} (closed or filtered)"}
        if rcv is None or rcv < 0 or avg is None or avg < 0:
            return {"status": "test_failed", "reason": "the reply counts were not available"}
        return {"ms": avg, "loss_pct": loss if loss is not None and 0 <= loss <= 100 else None}
    return build


def _reach(scan: _Scan, cold, cold_id: str, parts, http_opts: dict, plan: Plan) -> list[ReachResult]:
    host = scan.host
    is_ip = is_ip_host(host)
    out: list[ReachResult] = []

    def run(what: str, type: str, options: dict, build, **fixed):
        doc, reused, why = scan.measure(what, type, cold_id, options, fallback_locations=plan.locations)
        out.extend(_check_results(cold, doc, why, reused, build, **fixed))

    if IPV6 in plan.checks:
        if is_ip:
            out.extend(_not_a_name(cold, check=IPV6))
        else:
            scan.progress("Checking IPv6")
            run("IPv6 check", "http", {**http_opts, "ipVersion": 6}, _http_fields, check=IPV6)
    if PLAIN_HTTP in plan.checks:
        scan.progress("Checking plain HTTP")
        run("plain HTTP check", "http", {"request": dict(http_opts["request"]), "protocol": "HTTP"},
            _plain_http_fields, check=PLAIN_HTTP)
    if RESOLVERS_CHECK in plan.checks:
        if is_ip:
            for ip in RESOLVER_IPS:
                out.extend(_not_a_name(cold, check=RESOLVERS_CHECK, resolver=ip))
        else:
            scan.progress("Checking public DNS resolvers")
            for ip in RESOLVER_IPS:
                run("DNS resolver check", "dns", {"resolver": ip}, _dns_fields, check=RESOLVERS_CHECK, resolver=ip)
    if PORT in plan.checks:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        scan.progress("Checking the port")
        run("port check", "ping", {"protocol": "TCP", "port": port, "packets": PORT_PACKETS},
            _port_fields(port), check=PORT, port=port)
    return out


# --- "Is it fast?" (speedward/core/checks.py SPEED_CHECKS; summed up in speedward/core/speed.py) ------

PLAIN_ADDRESS = "the address is plain HTTP; HTTP/2 is tried over HTTPS only"
_ALPN = re.compile(r"alpn", re.IGNORECASE)  # observed inside "tls_parse_stoc_alpn"
_TOKEN = re.compile(r"[a-z0-9][a-z0-9 ,.-]{0,39}")


def _speed_results(cold, doc, why: str, reused: bool, build, failed=None, **fixed) -> list[SpeedResult]:
    """One SpeedResult per cold probe. `build(result_dict) -> dict` gives the check's own fields for a
    result `classify` called measured. `failed(item, status, reason) -> (status, reason) or dict` can
    re-read one it did not (a dict means measured, with those fields)."""
    out = []
    matcher = _Matcher(_items(doc))
    for row, (probe, _) in enumerate(cold):
        loc = _location(probe)
        if doc is None:
            out.append(SpeedResult(row=row, location=loc, status="not_run" if why == LIMIT_REACHED else "test_failed",
                                   reason=why, **fixed))
            continue
        item, same = matcher.take(probe)
        if item is None:
            out.append(SpeedResult(row=row, location=loc, status="not_run", reason=NO_CHECK_RESULT, **fixed))
            continue
        try:
            out.append(_speed_result(row, loc, item, same, reused, build, failed, fixed))
        except UNREADABLE_ERRORS:  # one odd answer is that probe's "test failed", never the scan's end
            out.append(SpeedResult(row=row, location=loc, status="test_failed", reason=UNREADABLE, **fixed))
    return out


def _speed_result(row: int, loc, item: dict, same: bool, reused: bool, build, failed, fixed: dict) -> SpeedResult:
    status, reason = classify(item)
    fields = None
    if status == "measured":
        fields = build(_result(item))
    elif failed is not None:
        again = failed(_result(item), status, reason)
        if isinstance(again, dict):
            fields = again
        else:
            status, reason = again
    if fields is None:
        return SpeedResult(row=row, location=loc, status=status, reason=reason, **fixed)
    if fields.get("status", "measured") != "measured":
        return SpeedResult(row=row, location=loc, **fields, **fixed)
    return SpeedResult(row=row, location=loc, status="measured", reason="", **fields,
                       probe=_probe_match(reused, same), **fixed)


def _not_run(cold, reason: str, **fixed) -> list[SpeedResult]:
    return [SpeedResult(row=row, location=_location(p), status="not_run", reason=reason, **fixed)
            for row, (p, _) in enumerate(cold)]


def _timed(res: dict) -> dict:
    return {"stages": _stages(res)}


def _http2_fields(res: dict) -> dict:
    version = _http_version(res)
    if version is None:
        return {"status": "test_failed", "reason": "the HTTP version of the answer could not be read"}
    if not version.startswith("HTTP/2"):  # not observed (no fallback was seen), kept as an answer
        return {"http_version": version, "h2_offered": False}
    return {"stages": _stages(res), "http_version": version, "h2_offered": True}


def _http2_failed(res: dict, status: str, reason: str):
    """Observed: a server without HTTP/2 makes the probe fail the TLS handshake on ALPN (no fallback to
    HTTP/1.1). That is the answer to the question, not the site refusing: never "blocked"."""
    raw = res.get("rawOutput") if isinstance(res.get("rawOutput"), str) else ""
    if res.get("status") == "failed" and res.get("failureSource") == "target" and _ALPN.search(raw):
        return {"h2_offered": False}
    return status, reason


# Content types worth compressing: text, and the structured-text formats served as application/*.
_COMPRESSIBLE = re.compile(r"text/[\w.+-]+|application/(?:json|javascript|ecmascript|xml|xhtml\+xml|"
                           r"[\w.-]+\+(?:json|xml))|image/svg\+xml")
_MIME = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}")


def _header(res: dict, name: str):
    value = None
    for key, v in _headers(res).items():
        if isinstance(key, str) and key.lower() == name:
            value = v[0] if isinstance(v, list) and v else v
    return value


def _nothing_to_judge(res: dict) -> str | None:
    """Why this answer says nothing about compression, or None when it does: only a 2xx answer with a
    body of a text type is "compressed" or "not compressed"."""
    code = res.get("statusCode")
    if not isinstance(code, int) or isinstance(code, bool) or not 200 <= code < 300:
        shown = f" (HTTP {code})" if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599 else ""
        return f"the first response was a redirect or an error{shown}, so compression was not judged"
    if code in (204, 205):
        return f"nothing to compress: the first response has no body (HTTP {code})"
    kind = _header(res, "content-type")
    kind = kind.split(";", 1)[0].strip().lower() if isinstance(kind, str) else ""
    if not kind:
        return "nothing to compress: the first response has no content type"
    if not _COMPRESSIBLE.fullmatch(kind):
        shown = f" ({kind})" if _MIME.fullmatch(kind) else ""
        return f"nothing to compress: the first response is not text{shown}"
    return None


def _compression_fields(res: dict) -> dict:
    why = _nothing_to_judge(res)
    if why is not None:
        return {"status": "not_run", "reason": why}
    value = _header(res, "content-encoding")
    if not isinstance(value, str) or not value.strip():
        encoding = "none"
    else:
        encoding = value.strip().lower()
        encoding = encoding if _TOKEN.fullmatch(encoding) else "unrecognised"
    return {"stages": _stages(res), "encoding": encoding}


def _trace_kind(i: int, last: int, server: str, seen_root: bool, seen_tld: bool) -> str:
    if i == 0:
        return "local"
    if server.lower().rstrip(".").endswith("root-servers.net"):
        return "root"
    if i == last:
        return "authoritative"
    return "delegation" if seen_tld or not seen_root else "tld"


def _trace_fields(res: dict) -> dict:
    hops = [h for h in res.get("hops") or [] if isinstance(h, dict)][:16]
    steps, seen_root, seen_tld = [], False, False
    for i, h in enumerate(hops):
        server = _str(h.get("resolver")) or "unknown server"
        kind = _trace_kind(i, len(hops) - 1, server, seen_root, seen_tld)
        seen_root = seen_root or kind == "root"
        seen_tld = seen_tld or kind == "tld"
        timings = h.get("timings") if isinstance(h.get("timings"), dict) else {}
        ms = _num(timings.get("total"))
        steps.append(TraceStep(kind, server, ms if ms is not None and 0 <= ms <= 1e9 else None))
    if not steps:
        return {"status": "test_failed", "reason": "the trace returned no steps"}
    return {"trace": steps}


def _trace_failed(res: dict, status: str, reason: str):
    # The trace asks DNS servers, not the site: a trace that did not finish is never the site's doing.
    return "test_failed", f"the DNS trace failed: {reason}"


def _jitter(timings) -> float | None:
    rtts = [t.get("rtt") for t in timings or [] if isinstance(t, dict)]
    rtts = [v for v in rtts if _num(v) is not None and 0 <= v <= 1e9]
    if len(rtts) < 2:
        return None
    return round(statistics.fmean(abs(b - a) for a, b in zip(rtts, rtts[1:])), 1)


def _pct(value) -> float | None:
    v = _num(value)
    return v if v is not None and 0 <= v <= 100 else None


def _ms_or_none(value) -> float | None:
    v = _num(value)
    return v if v is not None and 0 <= v <= 1e9 else None


def _mtr_fields(res: dict) -> dict:
    hops = []
    for n, hop in enumerate([h for h in res.get("hops") or [] if isinstance(h, dict)][:64], start=1):
        stats = hop.get("stats") if isinstance(hop.get("stats"), dict) else {}
        address = _str(hop.get("resolvedAddress"))
        if address is not None and clean(address) == "*":
            address = None  # "*" is reserved for a hop that did not answer
        asns = hop.get("asn")
        asn = asns[0] if isinstance(asns, list) and asns and isinstance(asns[0], int) \
            and not isinstance(asns[0], bool) else None
        if address is None:  # observed: a silent hop has no address, 100% loss and no times
            hops.append(PathHop(n, "*", None, None, 100 if _pct(stats.get("loss")) == 100 else None, None))
            continue
        name = _str(hop.get("resolvedHostname"))
        if name is None or clean(name).strip() in ("", "*"):
            name = address
        hops.append(PathHop(n, name, asn, _ms_or_none(stats.get("avg")), _pct(stats.get("loss")),
                            _jitter(hop.get("timings"))))
    if not hops:
        return {"status": "test_failed", "reason": "the path test returned no hops"}
    last = [h for h in res.get("hops") or [] if isinstance(h, dict)][:64][-1]
    site = _str(res.get("resolvedAddress"))
    reached = (site is not None and hops[-1].host != "*" and clean(site) != "*"
               and _str(last.get("resolvedAddress")) == site)
    return {"hops": hops, "reached": reached}


def _mtr_failed(res: dict, status: str, reason: str):
    # A path test that did not finish says nothing about the site refusing visitors.
    return "test_failed", f"the path test failed: {reason}"


def _speed(scan: _Scan, cold, cold_id: str, parts, http_opts: dict, plan: Plan,
           shared_mtr: tuple | None = None) -> list[SpeedResult]:
    is_ip = is_ip_host(scan.host)
    out: list[SpeedResult] = []

    def run(what: str, type: str, options: dict, build, failed=None, **fixed):
        doc, reused, why = scan.measure(what, type, cold_id, options, fallback_locations=plan.locations)
        out.extend(_speed_results(cold, doc, why, reused, build, failed, **fixed))

    if CONSISTENCY in plan.checks:
        scan.progress("Repeating the test for consistency")
        for k in range(1, CONSISTENCY_EXTRA + 1):
            run("consistency check", "http", http_opts, _timed, check=CONSISTENCY, run=k)
    if HTTP2 in plan.checks:
        if parts.scheme != "https":
            out.extend(_not_run(cold, PLAIN_ADDRESS, check=HTTP2))
        else:
            scan.progress("Trying HTTP/2")
            run("HTTP/2 check", "http", {**http_opts, "protocol": "HTTP2"}, _http2_fields, _http2_failed, check=HTTP2)
    if COMPRESSION in plan.checks:
        scan.progress("Checking compression")
        request = {**http_opts["request"], "headers": {"Accept-Encoding": "br, gzip"}}
        run("compression check", "http", {**http_opts, "request": request}, _compression_fields, check=COMPRESSION)
    if DNS_TRACE in plan.checks:
        if is_ip:
            out.extend(_not_run(cold, NOT_A_NAME, check=DNS_TRACE))
        else:
            scan.progress("Tracing the DNS lookup")
            run("DNS trace check", "dns", {"trace": True}, _trace_fields, _trace_failed, check=DNS_TRACE)
    if MTR in plan.checks and shared_mtr is not None:  # the Network path measurement, read again
        doc, reused, why = shared_mtr
        out.extend(_speed_results(cold, doc, why, reused, _mtr_fields, _mtr_failed, check=MTR))
    elif MTR in plan.checks:
        scan.progress("Measuring loss along the path")
        run("path loss check", "mtr", {"packets": MTR_PACKETS}, _mtr_fields, _mtr_failed, check=MTR)
    return out


def _report(target, started, plan: Plan, scan: _Scan, server, results, paths, dns, reach=None,
            speed=None, rules: Rules | None = None, speed_rules: SpeedRules | None = None) -> ScanReport:
    options = {"locations": [dict(s) for s in plan.locations], "traceroute": plan.traceroute,
               "dns": plan.dns, "browser": plan.browser}
    if plan.checks:
        options["checks"] = list(plan.checks)
    if scan.skipped:
        options["not_run"] = list(scan.skipped)
    report = ScanReport(target=target, started=started, finished=datetime.now(UTC),
                        version=__version__, options=options, server=server, locations=results,
                        paths=paths, dns=dns, browser=None, reach=reach, speed=speed, speed_rules=speed_rules)
    report.diagnosis = diagnose(report, rules)
    return report
