"""Default test locations and parsing of user overrides.

Entries hold only API-valid keys (Globalping `magic` filters that were observed to work).
Continent coverage is kept in the parallel CONTINENT_OF mapping, never sent to the API.
"""
from __future__ import annotations

DEFAULT_LOCATIONS: list[dict] = [
    {"magic": "London"},
    {"magic": "Frankfurt"},
    {"magic": "New York"},
    {"magic": "Los Angeles"},
    {"magic": "Sao Paulo"},
    {"magic": "Johannesburg"},
    {"magic": "Lagos"},
    {"magic": "Mumbai"},
    {"magic": "Singapore"},
    {"magic": "Tokyo"},
    {"magic": "Sydney"},
    {"magic": "Dubai"},
]

CONTINENT_OF: dict[str, str] = {
    "London": "Europe",
    "Frankfurt": "Europe",
    "New York": "North America",
    "Los Angeles": "North America",
    "Sao Paulo": "South America",
    "Johannesburg": "Africa",
    "Lagos": "Africa",
    "Mumbai": "Asia",
    "Singapore": "Asia",
    "Tokyo": "Asia",
    "Sydney": "Oceania",
    "Dubai": "Asia",
}


def parse_locations(spec: str | None) -> list[dict]:
    """Comma-separated overrides; each part (with optional `+` filters) becomes a magic spec."""
    if spec is None or not spec.strip():
        return [dict(loc) for loc in DEFAULT_LOCATIONS]
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError("The locations list has an empty entry; remove the extra comma.")
        out.append({"magic": part})
    return out
