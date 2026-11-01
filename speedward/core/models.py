"""Plain data types shared by every layer. No framework imports."""
from __future__ import annotations

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
