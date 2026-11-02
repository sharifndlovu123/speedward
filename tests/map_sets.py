"""The place sets measured in Task 22 (labels.py), as synthetic reports: no Globalping, fixed times."""
from datetime import UTC, datetime

from speedward.core.locations import CATALOG, DEFAULT_LOCATIONS
from speedward.core.models import Location, LocationResult, ScanReport, ServerPosition, Stages

NOW = datetime(2026, 11, 2, tzinfo=UTC)
BY = {p.name: p for p in CATALOG}
TIMES = (38, 120, 260, 410, 920)
SETS = {
    "default-12": [d["magic"] for d in DEFAULT_LOCATIONS],
    "europe-7": ["London", "Frankfurt", "Paris", "Stockholm", "Madrid", "Warsaw", "Istanbul"],
    "all-32": [p.name for p in CATALOG],
}
BLOCKED = ("Frankfurt", "Lagos")
FRANKFURT = ServerPosition(50.11, 8.68, "manual")


def result(i: int, name: str, blocked: bool = False) -> LocationResult:
    p = BY[name]
    loc = Location(p.country, p.name, p.lat, p.lon, 1, "Net", "isp")
    if blocked:
        return LocationResult(loc, "blocked", "connection timed out")
    fb = TIMES[i % len(TIMES)]
    return LocationResult(loc, "measured", stages=Stages(dns=5, connect=20, tls=20, first_byte=fb, download=5,
                                                         total=fb + 50))


def report(names, blocked=(), server=FRANKFURT) -> ScanReport:
    """The server is set by hand to Frankfurt, as in Task 22, so its label competes with Frankfurt's."""
    locs = [result(i, n, n in blocked) for i, n in enumerate(names)]
    return ScanReport(target="https://example.com/", started=NOW, finished=NOW, version="0.1.0", options={},
                      server=server, locations=locs, paths=[], dns=[], browser=None)
