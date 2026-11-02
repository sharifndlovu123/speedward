"""Plain data types shared by every layer. No framework imports."""
from __future__ import annotations

import copy
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from speedward.core.checks import IPV6, PLAIN_HTTP, PORT, REACH_CHECKS, RESOLVER_IPS, RESOLVERS_CHECK

if TYPE_CHECKING:  # bottleneck imports this module; at run time it is imported where it is used
    from speedward.core.bottleneck import Diagnosis

SCHEMA_VERSION = 1

# C0/C1 control characters except tab and newline: target-controlled text must never be able to
# carry terminal escape sequences into a report.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Bidi overrides/isolates/marks and invisible format characters (they can make a report read as
# something else) are removed; Unicode line and paragraph separators become spaces.
_INVISIBLE = re.compile("[\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
_SEPARATORS = re.compile("[\u2028\u2029]")


def clean(text: str) -> str:
    return _SEPARATORS.sub(" ", _INVISIBLE.sub("", _CONTROL.sub("�", text)))


STATUSES = ("measured", "blocked", "test_failed", "not_run")

# Measurements a scan could not run, kept in options["not_run"] as "<what>: <why>". Fixed wording only:
# no target and no exception text ever goes in, and from_dict accepts nothing else.
LIMIT_REACHED = "Globalping limit reached"
GLOBALPING_FAILED = "the test failed on the Globalping side"
NOT_RUN_WHAT = ("repeat test", "ping", "network path", "DNS check",
                "IPv6 check", "plain HTTP check", "DNS resolver check", "port check")
NOT_RUN_WHY = (LIMIT_REACHED, GLOBALPING_FAILED)
NOT_RUN_TEXTS = tuple(f"{what}: {why}" for what in NOT_RUN_WHAT for why in NOT_RUN_WHY)

# Which probe a later measurement (repeat request, ping, path, DNS) came from, compared with the cold
# run's probe. Only "same probe" is a like-for-like pairing; the cache gain uses nothing else.
SAME_PROBE, OTHER_PROBE, SAME_CITY = PROBE_MATCHES = ("same probe", "different probe",
                                                      "different probe, same city")

# Where the server that answered one result is (see speedward/core/edge.py, which uses these).
POSITION_SOURCES = ("database", "cdn header")
UNCERTAIN, NOT_AVAILABLE = EDGE_NOTES = ("edge location uncertain", "position not available")
SHARED = "shared address"
DISAGREE = "geo-IP database and CDN header disagree"
TOO_FAST = "delay too short for the database position"
PRIVATE = "private address"
NO_IP = "no answering IP"
NO_DB = "no geo-IP database set"
NOT_IN_DB = "not in the geo-IP database"
DB_UNREADABLE = "geo-IP database could not be read"
HEADER_TOO_FAST = "delay too short for the CDN header position"
BAD_IP = "answering IP could not be read"
EDGE_REASONS = (SHARED, DISAGREE, TOO_FAST, PRIVATE, NO_IP, NO_DB, NOT_IN_DB, DB_UNREADABLE, HEADER_TOO_FAST,
                BAD_IP)
_EDGE_CODE = re.compile(r"[A-Z]{3}")


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status!r}")


def _check_probe(value: str | None, what: str) -> None:
    if value is not None and value not in PROBE_MATCHES:
        raise ValueError(f"{what} is not a known probe match")


@dataclass
class Location:
    country: str
    city: str
    lat: float | None
    lon: float | None
    asn: int | None
    network: str | None
    network_type: str | None  # "isp" | "datacenter" | None

    def __post_init__(self):
        self.country, self.city = clean(self.country), clean(self.city)
        if self.network is not None:
            self.network = clean(self.network)


@dataclass
class Stages:
    """Milliseconds; a field the API does not return is None."""
    dns: float | None = None
    connect: float | None = None
    tls: float | None = None
    first_byte: float | None = None
    download: float | None = None
    total: float | None = None


@dataclass
class LocationResult:
    location: Location
    status: str
    reason: str = ""
    stages: Stages | None = None
    repeat_stages: Stages | None = None
    ping_ms: float | None = None
    http_status: int | None = None
    cdn: str | None = None  # "HIT" | "MISS" | None
    server_ip: str | None = None
    tls_version: str | None = None
    http_version: str | None = None
    # Redirects are not followed: this holds only the first response's Location header, if any.
    redirects: list[str] = field(default_factory=list)
    repeat_same_probe: bool = False
    # The server that answered, placed offline: a position (source in POSITION_SOURCES), or no
    # position with a note (EDGE_NOTES) and the reason (EDGE_REASONS). Never both.
    server_position: ServerPosition | None = None
    edge_code: str | None = None  # airport code from the cf-ray header; raw headers are never kept
    edge_note: str | None = None
    edge_reason: str | None = None
    # PROBE_MATCHES: which probe the repeat request and the ping came from. None: no such timing (or,
    # for ping_probe, a report from before this was recorded).
    repeat_probe: str | None = None
    ping_probe: str | None = None

    def __post_init__(self):
        _check_status(self.status)
        if self.repeat_stages is not None and self.repeat_probe is None:
            # Older reports only had the flag: anything not flagged is never claimed as the same probe.
            self.repeat_probe = SAME_PROBE if self.repeat_same_probe else OTHER_PROBE
        _check_probe(self.repeat_probe, "repeat_probe")
        _check_probe(self.ping_probe, "ping_probe")
        if self.repeat_probe is not None and self.repeat_stages is None:
            raise ValueError("a repeat probe match needs a repeat timing")
        if self.ping_probe is not None and self.ping_ms is None:
            raise ValueError("a ping probe match needs a ping timing")
        if self.repeat_same_probe and self.repeat_probe not in (None, SAME_PROBE):
            raise ValueError("repeat_same_probe contradicts repeat_probe")
        if self.repeat_probe == SAME_PROBE and not self.repeat_same_probe:
            raise ValueError("repeat_probe contradicts repeat_same_probe")
        if self.status != "measured" and (
            self.stages is not None or self.repeat_stages is not None or self.ping_ms is not None
        ):
            raise ValueError("only a measured result may carry timings")
        if self.status != "measured" and (self.server_position is not None or self.edge_code is not None
                                          or self.edge_note is not None or self.edge_reason is not None):
            raise ValueError("only a measured result may say which server answered")
        if self.server_position is not None and self.server_position.source not in POSITION_SOURCES:
            raise ValueError("server position source is not known")
        if self.edge_code is not None and not _EDGE_CODE.fullmatch(self.edge_code):
            raise ValueError("edge code must be three capital letters")
        if (self.edge_note is None) != (self.edge_reason is None):
            raise ValueError("an edge note and its reason go together")
        if self.edge_note is not None and (self.edge_note not in EDGE_NOTES or self.edge_reason not in EDGE_REASONS):
            raise ValueError("edge note or reason is not known")
        if self.edge_note is not None and self.server_position is not None:
            raise ValueError("a result has either a server position or an edge note")
        self.reason = clean(self.reason)
        self.redirects = [clean(r) for r in self.redirects]
        for name in ("server_ip", "tls_version", "http_version", "cdn"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, clean(value))


@dataclass
class Hop:
    n: int
    host: str
    asn: int | None
    rtt_ms: float | None
    loss_pct: float | None

    def __post_init__(self):
        self.host = clean(self.host)


@dataclass
class PathResult:
    location: Location
    hops: list[Hop]
    status: str
    reason: str
    probe: str | None = None  # PROBE_MATCHES against the cold run's probe; only on a measured path

    def __post_init__(self):
        _check_status(self.status)
        if self.status != "measured" and self.hops:
            raise ValueError("only a measured path may carry hops")
        _check_probe(self.probe, "probe")
        if self.status != "measured" and self.probe is not None:
            raise ValueError("only a measured path names its probe")
        self.reason = clean(self.reason)


@dataclass
class DnsResult:
    location: Location
    status: str
    reason: str
    answers: list[str]
    lookup_ms: float | None
    probe: str | None = None  # PROBE_MATCHES against the cold run's probe; only on a measured result

    def __post_init__(self):
        _check_status(self.status)
        if self.status != "measured" and (self.answers or self.lookup_ms is not None):
            raise ValueError("only a measured DNS result may carry answers or timing")
        _check_probe(self.probe, "probe")
        if self.status != "measured" and self.probe is not None:
            raise ValueError("only a measured DNS result names its probe")
        self.reason = clean(self.reason)
        self.answers = [clean(a) for a in self.answers]


# Where a plain-HTTP answer sent the visitor (only the first response; redirects are not followed).
REDIRECTS = ("https", "other")


@dataclass
class ReachResult:
    """One "More checks" result for one place (speedward/core/checks.py). `row` is the place's index in
    ScanReport.locations. Only a measured result carries a time or other figures; blocked (the site side
    refused, timed out, has no address or no open port) and test_failed / not_run carry only a reason."""
    check: str
    row: int
    location: Location
    status: str
    reason: str
    ms: float | None = None
    resolver: str | None = None  # the "dns" check: which public resolver was asked
    redirect: str | None = None  # the "http" check, measured: REDIRECTS, or None when it answered itself
    http_status: int | None = None
    port: int | None = None  # the "port" check: the TCP port tried
    loss_pct: float | None = None  # the "port" check, measured: share of attempts that got no answer
    probe: str | None = None  # PROBE_MATCHES against the cold run's probe; only on a measured result

    def __post_init__(self):
        _check_status(self.status)
        if self.check not in REACH_CHECKS:
            raise ValueError("unknown check")
        if isinstance(self.row, bool) or not isinstance(self.row, int) or self.row < 0:
            raise ValueError("row must be a whole number, 0 or more")
        if (self.check == RESOLVERS_CHECK) != (self.resolver is not None):
            raise ValueError("a resolver goes with the dns check only")
        if self.resolver is not None and self.resolver not in RESOLVER_IPS:
            raise ValueError("resolver is not one of the known public resolvers")
        if (self.check == PORT) != (self.port is not None):
            raise ValueError("a port goes with the port check only")
        if self.port is not None and not 1 <= self.port <= 65535:
            raise ValueError("port is out of range")
        if self.redirect is not None and (self.check != PLAIN_HTTP or self.redirect not in REDIRECTS):
            raise ValueError("redirect is only for the http check and must be known")
        if self.loss_pct is not None and self.check != PORT:
            raise ValueError("loss is only for the port check")
        if self.http_status is not None and self.check not in (PLAIN_HTTP, IPV6):
            raise ValueError("an HTTP status goes with an http check only")
        _check_probe(self.probe, "probe")
        if self.status != "measured" and (self.ms is not None or self.loss_pct is not None
                                          or self.redirect is not None or self.probe is not None):
            raise ValueError("only a measured check result may carry figures")
        self.reason = clean(self.reason)


@dataclass
class ServerPosition:
    lat: float
    lon: float
    source: str

    def __post_init__(self):
        self.source = clean(self.source)


@dataclass
class BrowserResult:
    page_weight_bytes: int | None
    requests: int | None
    load_ms: float | None
    biggest: list[tuple[str, int]] = field(default_factory=list)

    def __post_init__(self):
        self.biggest = [(clean(url), size) for url, size in self.biggest]


@dataclass
class ScanReport:
    target: str
    started: datetime
    finished: datetime
    version: str
    options: dict
    server: ServerPosition | None
    locations: list[LocationResult]
    paths: list[PathResult]
    dns: list[DnsResult]
    browser: BrowserResult | None
    # "Where the time goes" (speedward/core/bottleneck.py), set by the runner. None for a report from
    # before it existed: such a report shows no panel and is never given a diagnosis afterwards.
    diagnosis: Diagnosis | None = None
    # "More checks" results (speedward/core/reach.py builds the "Who can reach it" grid from them). None
    # when no check was ticked, and for a report from before they existed: no grid is shown then.
    reach: list[ReachResult] | None = None

    def __post_init__(self):
        self.target, self.version = clean(self.target), clean(self.version)

    def to_dict(self) -> dict:
        out = {
            "schema_version": SCHEMA_VERSION,
            "target": self.target,
            "started": self.started.isoformat(),
            "finished": self.finished.isoformat(),
            "version": self.version,
            "options": self.options,
            "server": asdict(self.server) if self.server else None,
            "locations": [asdict(r) for r in self.locations],
            "paths": [asdict(p) for p in self.paths],
            "dns": [asdict(d) for d in self.dns],
            "browser": asdict(self.browser) if self.browser else None,
        }
        if self.diagnosis is not None:  # a report from before the diagnosis exports exactly as it came
            out["diagnosis"] = self.diagnosis.to_dict()
        if self.reach is not None:  # likewise a report without checks: no keys, no grid
            from speedward.core.reach import reach_grid
            out["reach"] = [asdict(x) for x in self.reach]
            out["reach_grid"] = reach_grid(self).to_dict()
        return out

    @classmethod
    def from_dict(cls, data) -> ScanReport:
        """Rebuild a report from `to_dict` output (for example a copy the browser saved).

        The input is untrusted: anything missing, of the wrong type or of another schema version
        raises ValueError. Every text field goes through the same cleaning as a fresh scan. A diagnosis
        is accepted only if it is exactly what the report's own results give (diagnosis_from_dict).
        """
        from speedward.core.bottleneck import diagnosis_from_dict
        from speedward.core.reach import grid_from_dict

        d = _obj(data, "report")
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported schema_version")
        # Posted reports are untrusted: no scan has more places than Globalping allows in one measurement.
        if len(_list(d, "locations")) > MAX_LOCATIONS:
            raise ValueError("too many locations")
        server = d.get("server")
        browser = d.get("browser")
        report = cls(
            target=_s(d, "target"),
            started=_when(d, "started"),
            finished=_when(d, "finished"),
            version=_s(d, "version"),
            options=_options(d.get("options")),
            server=None if server is None else _server(_obj(server, "server")),
            locations=[_location_result(_obj(x, "location result")) for x in _list(d, "locations")],
            paths=[_path(_obj(x, "path")) for x in _list(d, "paths")],
            dns=[_dns(_obj(x, "dns result")) for x in _list(d, "dns")],
            browser=None if browser is None else _browser(_obj(browser, "browser")),
        )
        if d.get("diagnosis") is not None:
            report.diagnosis = diagnosis_from_dict(d["diagnosis"], report)
        if d.get("reach") is not None:
            report.reach = _reach_list(d["reach"], len(report.locations))
            grid_from_dict(d.get("reach_grid"), report)  # must be exactly what the results give
        elif d.get("reach_grid") is not None:
            raise ValueError("a reach grid needs its results")
        return report


# --- from_dict helpers: strict type checks, ValueError on anything unexpected ----------------------

def _options(value) -> dict:
    options = copy.deepcopy(_obj(value, "options"))
    if "checks" in options:
        checks = options["checks"]
        if (not isinstance(checks, list) or not all(isinstance(x, str) and x in REACH_CHECKS for x in checks)
                or len(set(checks)) != len(checks)):
            raise ValueError("checks must list known checks, each once")
    if "not_run" in options:
        skipped = options["not_run"]
        if not isinstance(skipped, list) or not all(isinstance(x, str) and x in NOT_RUN_TEXTS for x in skipped):
            raise ValueError("not_run must list known measurements that did not run")
    return options


def _obj(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be an object")
    return value


def _list(d: dict, key: str) -> list:
    value = d.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _s(d: dict, key: str) -> str:
    value = d.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value


def _s_or_none(d: dict, key: str) -> str | None:
    value = d.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be text or null")
    return value


def _int_or_none(d: dict, key: str) -> int | None:
    value = d.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{key} must be a whole number or null")
    return value


# No real timing comes near this (about 11.6 days in ms); anything above it is refused, so summaries
# such as statistics.fmean can never overflow.
MAX_MS = 1e9


def _num_or_none(d: dict, key: str, lo: float = -math.inf, hi: float = math.inf) -> float | None:
    value = d.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be a number or null")
    try:  # a huge JSON integer (e.g. 10**400) overflows float conversion
        finite = math.isfinite(value)
    except ArithmeticError:
        finite = False
    if not finite:
        raise ValueError(f"{key} must be a finite number or null")
    if not lo <= value <= hi:
        raise ValueError(f"{key} is out of range")
    return value


def _str_list(d: dict, key: str) -> list[str]:
    items = _list(d, key)
    if not all(isinstance(x, str) for x in items):
        raise ValueError(f"{key} must be a list of text")
    return list(items)


def _when(d: dict, key: str) -> datetime:
    try:
        return datetime.fromisoformat(_s(d, key))
    except ValueError:
        raise ValueError(f"{key} must be an ISO date and time") from None


def _location(d) -> Location:
    d = _obj(d, "location")
    return Location(country=_s(d, "country"), city=_s(d, "city"),
                    lat=_num_or_none(d, "lat", -90, 90), lon=_num_or_none(d, "lon", -180, 180),
                    asn=_int_or_none(d, "asn"), network=_s_or_none(d, "network"),
                    network_type=_s_or_none(d, "network_type"))


def _stages(d: dict, key: str) -> Stages | None:
    value = d.get(key)
    if value is None:
        return None
    value = _obj(value, key)
    return Stages(**{name: _num_or_none(value, name, 0, MAX_MS) for name in
                     ("dns", "connect", "tls", "first_byte", "download", "total")})


def _location_result(d: dict) -> LocationResult:
    same = d.get("repeat_same_probe", False)
    if not isinstance(same, bool):
        raise ValueError("repeat_same_probe must be true or false")
    return LocationResult(
        location=_location(d.get("location")), status=_s(d, "status"), reason=_s(d, "reason"),
        stages=_stages(d, "stages"), repeat_stages=_stages(d, "repeat_stages"),
        ping_ms=_num_or_none(d, "ping_ms", 0, MAX_MS), http_status=_int_or_none(d, "http_status"),
        cdn=_s_or_none(d, "cdn"), server_ip=_s_or_none(d, "server_ip"),
        tls_version=_s_or_none(d, "tls_version"), http_version=_s_or_none(d, "http_version"),
        redirects=_str_list(d, "redirects"), repeat_same_probe=same,
        # Absent in reports from before Task 16: they load with no position and no note.
        server_position=_result_position(d.get("server_position")),
        edge_code=_s_or_none(d, "edge_code"), edge_note=_s_or_none(d, "edge_note"),
        edge_reason=_s_or_none(d, "edge_reason"),
        # Absent before the final review: derived in LocationResult, never as "same probe" unless flagged.
        repeat_probe=_s_or_none(d, "repeat_probe"), ping_probe=_s_or_none(d, "ping_probe"),
    )


def _result_position(value) -> ServerPosition | None:
    if value is None:
        return None
    d = _obj(value, "server_position")
    lat, lon = _num_or_none(d, "lat", -90, 90), _num_or_none(d, "lon", -180, 180)
    if lat is None or lon is None:
        raise ValueError("server position needs lat and lon")
    source = _s(d, "source")
    if source not in POSITION_SOURCES:
        raise ValueError("server position source is not known")
    return ServerPosition(lat, lon, source)


def _hop(d) -> Hop:
    d = _obj(d, "hop")
    n = d.get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError("hop number must be a whole number")
    return Hop(n, _s(d, "host"), _int_or_none(d, "asn"), _num_or_none(d, "rtt_ms", 0, MAX_MS),
               _num_or_none(d, "loss_pct", 0, 100))


def _path(d: dict) -> PathResult:
    return PathResult(_location(d.get("location")), [_hop(h) for h in _list(d, "hops")],
                      _s(d, "status"), _s(d, "reason"), _s_or_none(d, "probe"))


def _dns(d: dict) -> DnsResult:
    return DnsResult(_location(d.get("location")), _s(d, "status"), _s(d, "reason"),
                     _str_list(d, "answers"), _num_or_none(d, "lookup_ms", 0, MAX_MS), _s_or_none(d, "probe"))


# At most this many places in one report (Globalping's largest probe count per measurement, with a key).
MAX_LOCATIONS = 500
# Results per place: one per check, the dns check one per resolver.
REACH_PER_PLACE = len(REACH_CHECKS) - 1 + len(RESOLVER_IPS)


def _reach_list(value, rows: int) -> list[ReachResult]:
    if not isinstance(value, list):
        raise ValueError("reach must be a list")
    if len(value) > rows * REACH_PER_PLACE:  # checked before any item is read
        raise ValueError("more reach results than places and checks allow")
    out, seen = [], set()
    for x in value:
        d = _obj(x, "reach result")
        row = d.get("row")
        if isinstance(row, bool) or not isinstance(row, int) or not 0 <= row < rows:
            raise ValueError("reach row must name a location")
        r = ReachResult(check=_s(d, "check"), row=row, location=_location(d.get("location")),
                        status=_s(d, "status"), reason=_s(d, "reason"), ms=_num_or_none(d, "ms", 0, MAX_MS),
                        resolver=_s_or_none(d, "resolver"), redirect=_s_or_none(d, "redirect"),
                        http_status=_int_or_none(d, "http_status"), port=_int_or_none(d, "port"),
                        loss_pct=_num_or_none(d, "loss_pct", 0, 100), probe=_s_or_none(d, "probe"))
        key = (r.check, r.row, r.resolver)
        if key in seen:
            raise ValueError("one result per check, place and resolver")
        seen.add(key)
        out.append(r)
    return out


# What Geolocator emits: a configured position, a geo-IP database, or the web form's pin.
SERVER_SOURCES = ("manual", "manual (pin)", "database")


def _server(d: dict) -> ServerPosition:
    lat, lon = _num_or_none(d, "lat", -90, 90), _num_or_none(d, "lon", -180, 180)
    if lat is None or lon is None:
        raise ValueError("server position needs lat and lon")
    source = _s(d, "source")
    if source not in SERVER_SOURCES:
        raise ValueError("server position source is not known")
    return ServerPosition(lat, lon, source)


def _browser(d: dict) -> BrowserResult:
    biggest = []
    for item in _list(d, "biggest"):
        if (not isinstance(item, (list, tuple)) or len(item) != 2 or not isinstance(item[0], str)
                or not isinstance(item[1], int) or isinstance(item[1], bool)):
            raise ValueError("biggest must be a list of [url, bytes] pairs")
        biggest.append((item[0], item[1]))
    return BrowserResult(_int_or_none(d, "page_weight_bytes"), _int_or_none(d, "requests"),
                         _num_or_none(d, "load_ms", 0, MAX_MS), biggest)
