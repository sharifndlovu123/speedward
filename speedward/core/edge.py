"""Where the server that answered each probe is. Offline only: a bundled airport table and the local
geo-IP database, never a network call, so the scanned target is never leaked to a third party.

A position is kept only when it is trustworthy. Shared (anycast) addresses are registered at one
place in geo-IP databases although many sites answer for them, so for those the database is not
believed unless every probe's delay fits it. Anything doubtful gets no position and a fixed note.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from speedward.core.geo import _public_ip
from speedward.core.models import EDGE_NOTES, EDGE_REASONS, POSITION_SOURCES, ServerPosition

# --- thresholds -------------------------------------------------------------------------------

# An address answering probes on this many continents is treated as shared (anycast or CDN).
SHARED_MIN_CONTINENTS = 2
# A CDN header city further than this from the database position for the same result: one is wrong.
HEADER_DB_MAX_KM = 2500
# Light in a vacuum covers about 300 km per ms, so a round trip of 1 ms reaches at most 150 km away.
# A TCP connect time shorter than distance / this cannot come from a server at that distance.
LIGHT_KM_PER_RTT_MS = 150
# Geo-IP positions are city level; this much error is allowed before the delay check counts.
DB_SLACK_KM = 300

# --- fixed wording (stored in reports, so the sets live in models and are validated on load) -----

UNCERTAIN, NOT_AVAILABLE = EDGE_NOTES
(SHARED, DISAGREE, TOO_FAST, PRIVATE, NO_IP, NO_DB, NOT_IN_DB, DB_UNREADABLE) = EDGE_REASONS
DATABASE, CDN_HEADER = POSITION_SOURCES

# --- the airport table ------------------------------------------------------------------------


@dataclass(frozen=True)
class Airport:
    city: str
    country: str
    lat: float
    lon: float


@lru_cache(maxsize=1)
def iata() -> dict[str, Airport]:
    """Bundled table (scripts/build_iata.py): IATA code -> city and position."""
    doc = json.loads(Path(__file__).with_name("iata.json").read_text(encoding="utf-8"))
    return {code: Airport(e["city"], e["country"], float(e["lat"]), float(e["lon"]))
            for code, e in doc["codes"].items()}


def edge_city(code: str | None) -> str | None:
    entry = iata().get(code) if code else None
    return entry.city if entry else None


# Observed: "cf-ray: a53cca1a6ccf518e-TXL" (16 hex digits, a dash, the airport code in capitals).
_CF_RAY = re.compile(r"[0-9a-f]{16}-([A-Z]{3})")


def cf_ray_code(headers) -> str | None:
    """The airport code at the end of Cloudflare's cf-ray header; None for anything else.

    Only Cloudflare: other CDNs' edge headers have no format we have seen and can rely on.
    """
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "cf-ray":
            m = _CF_RAY.fullmatch(value) if isinstance(value, str) else None
            return m.group(1) if m else None
    return None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


# --- placement --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """What one measured result tells us about the server that answered it."""
    ip: str | None
    continent: str | None
    lat: float | None  # the probe's position
    lon: float | None
    connect_ms: float | None  # TCP connect: one round trip to `ip`
    edge_code: str | None  # from cf-ray


@dataclass(frozen=True)
class Placement:
    position: ServerPosition | None
    note: str | None  # one of EDGE_NOTES when position is None
    reason: str | None  # one of EDGE_REASONS when position is None


def _too_fast(ans: Answer, lat: float, lon: float) -> bool:
    if ans.connect_ms is None or ans.lat is None or ans.lon is None:
        return False
    reach = max(0.0, distance_km(ans.lat, ans.lon, lat, lon) - DB_SLACK_KM)
    return ans.connect_ms < reach / LIGHT_KM_PER_RTT_MS


def _fits(group: list[Answer], lat: float, lon: float, need_all: bool) -> bool:
    """True when no probe's delay rules the position out (and, with need_all, every probe has one)."""
    if need_all and any(x.connect_ms is None or x.lat is None or x.lon is None for x in group):
        return False
    return not any(_too_fast(x, lat, lon) for x in group)


def place(answers: Sequence[Answer | None],
          lookup: Callable[[str], tuple[float, float] | str]) -> list[Placement | None]:
    """One Placement per answer (None stays None, for results that were not measured).

    `lookup(ip)` is the geo-IP database: (lat, lon), or one of NO_DB, NOT_IN_DB, DB_UNREADABLE.
    It is called once per distinct public address.
    """
    groups: dict[str, list[tuple[int, Answer]]] = {}
    for i, ans in enumerate(answers):
        if ans is not None and ans.ip:
            groups.setdefault(ans.ip, []).append((i, ans))

    db: dict[str, tuple[float, float] | str] = {}
    shared: dict[str, bool] = {}
    for ip, members in groups.items():
        # An unknown continent counts as one of its own: we cannot show it is the same.
        continents = {x.continent or f"?{i}" for i, x in members}
        shared[ip] = len(continents) >= SHARED_MIN_CONTINENTS
        db[ip] = lookup(ip.strip()) if _public_ip(ip) else PRIVATE

    out: list[Placement | None] = []
    for ans in answers:
        if ans is None:
            out.append(None)
            continue
        airport = iata().get(ans.edge_code) if ans.edge_code else None
        found = db.get(ans.ip) if ans.ip else NO_IP
        is_shared = bool(ans.ip) and shared[ans.ip]
        if airport is not None:
            if (isinstance(found, tuple) and not is_shared
                    and distance_km(airport.lat, airport.lon, *found) > HEADER_DB_MAX_KM):
                out.append(Placement(None, UNCERTAIN, DISAGREE))
            else:
                out.append(Placement(ServerPosition(airport.lat, airport.lon, CDN_HEADER), None, None))
        elif isinstance(found, tuple):
            group = [x for _, x in groups[ans.ip]]
            if _fits(group, *found, need_all=is_shared):
                out.append(Placement(ServerPosition(found[0], found[1], DATABASE), None, None))
            else:
                out.append(Placement(None, UNCERTAIN, SHARED if is_shared else TOO_FAST))
        else:
            out.append(Placement(None, NOT_AVAILABLE, found if found in EDGE_REASONS else DB_UNREADABLE))
    return out
