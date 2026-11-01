"""Server position lookup. Offline only: no network calls, so the scanned
target is never leaked to a third party."""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping

from speedward.core.models import ServerPosition


def _public_ip(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_unspecified or addr.is_multicast
    )


def _valid_override(override):
    if override is None:
        return None
    try:
        lat, lon = float(override[0]), float(override[1])
    except (TypeError, ValueError, IndexError):
        raise ValueError("server position override must be (lat, lon) numbers")
    if not (-90 <= lat <= 90):
        raise ValueError(f"latitude {lat} out of range -90..90")
    if not (-180 <= lon <= 180):
        raise ValueError(f"longitude {lon} out of range -180..180")
    return lat, lon


def parse_pin(lat, lon):
    """A (lat, lon) pair from the web form's server pin, or None when both are absent.

    Accepts numbers or numeric text. ValueError (fixed text, never the values) when only one is given
    or either is not a finite number in range.
    """
    absent = (None, "")
    if lat in absent and lon in absent:
        return None
    if lat in absent or lon in absent or isinstance(lat, bool) or isinstance(lon, bool):
        raise ValueError("server pin needs both a latitude and a longitude")
    try:
        return _valid_override((lat, lon))
    except ValueError:
        raise ValueError("server pin is not a valid position") from None


class Geolocator:
    """Where the server is. Precedence: configured override, then the geo-IP database, then a manual
    pin from the web form (lowest, so it is never mistaken for a measured location)."""

    def __init__(self, db_path: str | None, override: tuple[float, float] | None,
                 pin: tuple[float, float] | None = None):
        self.db_path = db_path
        self.override = _valid_override(override)
        self.pin = None if pin is None else parse_pin(*pin)

    def _pinned(self) -> ServerPosition | None:
        return None if self.pin is None else ServerPosition(self.pin[0], self.pin[1], "manual (pin)")

    def locate(self, ip: str | None) -> ServerPosition | None:
        if self.override is not None:
            return ServerPosition(self.override[0], self.override[1], "manual")
        if not self.db_path or not _public_ip(ip):
            return self._pinned()
        try:
            import os
            if not os.path.isfile(self.db_path):
                return self._pinned()
            import maxminddb  # optional "geoip" extra
            reader = maxminddb.open_database(self.db_path)
            try:
                record = reader.get(ip.strip())
            finally:
                reader.close()
            loc = record["location"]
            lat, lon = float(loc["latitude"]), float(loc["longitude"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return self._pinned()
            return ServerPosition(lat, lon, "database")
        except Exception:
            return self._pinned()


def geolocator_from_env(environ: Mapping[str, str]) -> Geolocator:
    db = environ.get("GEOIP_DB") or None
    lat = (environ.get("SERVER_LAT") or "").strip()
    lon = (environ.get("SERVER_LON") or "").strip()
    if bool(lat) != bool(lon):
        raise ValueError("SERVER_LAT and SERVER_LON must be set together")
    override = None
    if lat:
        try:
            override = (float(lat), float(lon))
        except ValueError:
            raise ValueError("SERVER_LAT and SERVER_LON must be numbers")
    return Geolocator(db, override)
