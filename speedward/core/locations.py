"""Default test locations and parsing of user overrides.

Entries hold only API-valid keys (Globalping `magic` filters that were observed to work).
Continent coverage is kept in the parallel CONTINENT_OF mapping, never sent to the API.
"""
from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class Place:
    """A pickable place. `name` is what is sent to Globalping as {"magic": name}. Coordinates are static."""
    name: str
    country: str
    continent: str
    lat: float
    lon: float


CONTINENTS = ("North America", "South America", "Europe", "Africa", "Asia", "Oceania")

CATALOG: list[Place] = [
    Place("New York", "US", "North America", 40.71, -74.01),
    Place("Los Angeles", "US", "North America", 34.05, -118.24),
    Place("Chicago", "US", "North America", 41.88, -87.63),
    Place("Toronto", "CA", "North America", 43.65, -79.38),
    Place("Mexico City", "MX", "North America", 19.43, -99.13),
    Place("Sao Paulo", "BR", "South America", -23.55, -46.63),
    Place("Santiago", "CL", "South America", -33.45, -70.67),
    Place("Buenos Aires", "AR", "South America", -34.60, -58.38),
    Place("Bogota", "CO", "South America", 4.71, -74.07),
    Place("London", "GB", "Europe", 51.51, -0.13),
    Place("Frankfurt", "DE", "Europe", 50.11, 8.68),
    Place("Paris", "FR", "Europe", 48.86, 2.35),
    Place("Stockholm", "SE", "Europe", 59.33, 18.07),
    Place("Madrid", "ES", "Europe", 40.42, -3.70),
    Place("Warsaw", "PL", "Europe", 52.23, 21.01),
    Place("Istanbul", "TR", "Europe", 41.01, 28.98),
    Place("Johannesburg", "ZA", "Africa", -26.20, 28.05),
    Place("Cape Town", "ZA", "Africa", -33.92, 18.42),
    Place("Lagos", "NG", "Africa", 6.52, 3.38),
    Place("Nairobi", "KE", "Africa", -1.29, 36.82),
    Place("Cairo", "EG", "Africa", 30.04, 31.24),
    Place("Dubai", "AE", "Asia", 25.20, 55.27),
    Place("Mumbai", "IN", "Asia", 19.08, 72.88),
    Place("Delhi", "IN", "Asia", 28.61, 77.21),
    Place("Singapore", "SG", "Asia", 1.35, 103.82),
    Place("Jakarta", "ID", "Asia", -6.21, 106.85),
    Place("Hong Kong", "HK", "Asia", 22.32, 114.17),
    Place("Seoul", "KR", "Asia", 37.57, 126.98),
    Place("Tokyo", "JP", "Asia", 35.68, 139.69),
    Place("Sydney", "AU", "Oceania", -33.87, 151.21),
    Place("Melbourne", "AU", "Oceania", -37.81, 144.96),
    Place("Auckland", "NZ", "Oceania", -36.85, 174.76),
]


def per_continent() -> list[str]:
    """The first default place on each continent (the "One per continent" preset)."""
    defaults = {loc["magic"] for loc in DEFAULT_LOCATIONS}
    out = []
    for continent in CONTINENTS:
        for place in CATALOG:
            if place.continent == continent and place.name in defaults:
                out.append(place.name)
                break
    return out
