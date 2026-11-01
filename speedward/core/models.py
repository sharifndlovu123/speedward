"""Plain data types shared by every layer. No framework imports."""
from __future__ import annotations

import copy
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

SCHEMA_VERSION = 1

# C0/C1 control characters except tab and newline: target-controlled text must never be able to
# carry terminal escape sequences into a report.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(text: str) -> str:
    return _CONTROL.sub("�", text)


STATUSES = ("measured", "blocked", "test_failed", "not_run")


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status!r}")


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

    def __post_init__(self):
        _check_status(self.status)
        if self.status != "measured" and (
            self.stages is not None or self.repeat_stages is not None or self.ping_ms is not None
        ):
            raise ValueError("only a measured result may carry timings")
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

    def __post_init__(self):
        _check_status(self.status)
        if self.status != "measured" and self.hops:
            raise ValueError("only a measured path may carry hops")
        self.reason = clean(self.reason)


@dataclass
class DnsResult:
    location: Location
    status: str
    reason: str
    answers: list[str]
    lookup_ms: float | None

    def __post_init__(self):
        _check_status(self.status)
        if self.status != "measured" and (self.answers or self.lookup_ms is not None):
            raise ValueError("only a measured DNS result may carry answers or timing")
        self.reason = clean(self.reason)
        self.answers = [clean(a) for a in self.answers]


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

    def to_dict(self) -> dict:
        return {
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

    @classmethod
    def from_dict(cls, data) -> "ScanReport":
        """Rebuild a report from `to_dict` output (for example a copy the browser saved).

        The input is untrusted: anything missing, of the wrong type or of another schema version
        raises ValueError. Every text field goes through the same cleaning as a fresh scan.
        """
        d = _obj(data, "report")
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported schema_version")
        server = d.get("server")
        browser = d.get("browser")
        return cls(
            target=_s(d, "target"),
            started=_when(d, "started"),
            finished=_when(d, "finished"),
            version=_s(d, "version"),
            options=copy.deepcopy(_obj(d.get("options"), "options")),
            server=None if server is None else _server(_obj(server, "server")),
            locations=[_location_result(_obj(x, "location result")) for x in _list(d, "locations")],
            paths=[_path(_obj(x, "path")) for x in _list(d, "paths")],
            dns=[_dns(_obj(x, "dns result")) for x in _list(d, "dns")],
            browser=None if browser is None else _browser(_obj(browser, "browser")),
        )


# --- from_dict helpers: strict type checks, ValueError on anything unexpected ----------------------

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
    return Stages(**{name: _num_or_none(value, name, 0) for name in
                     ("dns", "connect", "tls", "first_byte", "download", "total")})


def _location_result(d: dict) -> LocationResult:
    same = d.get("repeat_same_probe", False)
    if not isinstance(same, bool):
        raise ValueError("repeat_same_probe must be true or false")
    return LocationResult(
        location=_location(d.get("location")), status=_s(d, "status"), reason=_s(d, "reason"),
        stages=_stages(d, "stages"), repeat_stages=_stages(d, "repeat_stages"),
        ping_ms=_num_or_none(d, "ping_ms", 0), http_status=_int_or_none(d, "http_status"),
        cdn=_s_or_none(d, "cdn"), server_ip=_s_or_none(d, "server_ip"),
        tls_version=_s_or_none(d, "tls_version"), http_version=_s_or_none(d, "http_version"),
        redirects=_str_list(d, "redirects"), repeat_same_probe=same,
    )


def _hop(d) -> Hop:
    d = _obj(d, "hop")
    n = d.get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError("hop number must be a whole number")
    return Hop(n, _s(d, "host"), _int_or_none(d, "asn"), _num_or_none(d, "rtt_ms", 0),
               _num_or_none(d, "loss_pct", 0, 100))


def _path(d: dict) -> PathResult:
    return PathResult(_location(d.get("location")), [_hop(h) for h in _list(d, "hops")],
                      _s(d, "status"), _s(d, "reason"))


def _dns(d: dict) -> DnsResult:
    return DnsResult(_location(d.get("location")), _s(d, "status"), _s(d, "reason"),
                     _str_list(d, "answers"), _num_or_none(d, "lookup_ms", 0))


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
                         _num_or_none(d, "load_ms", 0), biggest)
