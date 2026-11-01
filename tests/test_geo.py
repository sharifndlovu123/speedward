import socket
import sys
import types

import pytest

from speedward.core.geo import Geolocator, geolocator_from_env, parse_pin
from speedward.core.models import ServerPosition


def install_fake_maxminddb(monkeypatch, record, opened=None):
    class Reader:
        def __init__(self, path):
            if opened is not None:
                opened.append(path)

        def get(self, ip):
            return record

        def close(self):
            pass

    mod = types.SimpleNamespace(open_database=Reader)
    monkeypatch.setitem(sys.modules, "maxminddb", mod)


def test_override_wins(tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")
    install_fake_maxminddb(monkeypatch, {"location": {"latitude": 1, "longitude": 2}})
    pos = Geolocator(str(db), (-26.2, 28.0)).locate("8.8.8.8")
    assert pos == ServerPosition(-26.2, 28.0, "manual")


def test_override_applies_without_ip():
    assert Geolocator(None, (10, 20)).locate(None).source == "manual"


def test_no_database_returns_none():
    assert Geolocator(None, None).locate("8.8.8.8") is None


def test_missing_database_file_returns_none(tmp_path):
    assert Geolocator(str(tmp_path / "nope.mmdb"), None).locate("8.8.8.8") is None


@pytest.mark.parametrize("ip", ["10.0.0.1", "127.0.0.1", "169.254.1.1", "192.168.1.1",
                                "0.0.0.0", "224.0.0.1", "240.0.0.1", "::1", "fe80::1",
                                "ff02::1", "::", "not-an-ip", "", None])
def test_private_ip_returns_none(ip, tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")
    install_fake_maxminddb(monkeypatch, {"location": {"latitude": 1, "longitude": 2}})
    assert Geolocator(str(db), None).locate(ip) is None


def test_database_lookup(tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")
    opened = []
    install_fake_maxminddb(monkeypatch, {"location": {"latitude": 51.5, "longitude": -0.12}}, opened)
    pos = Geolocator(str(db), None).locate("8.8.8.8")
    assert pos == ServerPosition(51.5, -0.12, "database")
    assert opened == [str(db)]


def test_record_without_location_returns_none(tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")
    install_fake_maxminddb(monkeypatch, {"country": {}})
    assert Geolocator(str(db), None).locate("8.8.8.8") is None
    install_fake_maxminddb(monkeypatch, None)
    assert Geolocator(str(db), None).locate("8.8.8.8") is None


def test_corrupt_database_returns_none(tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")

    def boom(path):
        raise ValueError("corrupt")

    monkeypatch.setitem(sys.modules, "maxminddb", types.SimpleNamespace(open_database=boom))
    assert Geolocator(str(db), None).locate("8.8.8.8") is None


def test_missing_maxminddb_returns_none(tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "maxminddb", None)  # forces ImportError
    assert Geolocator(str(db), None).locate("8.8.8.8") is None


@pytest.mark.parametrize("bad", [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_invalid_override_raises(bad):
    with pytest.raises(ValueError):
        Geolocator(None, bad)


def test_locate_makes_no_network_connection(tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")
    install_fake_maxminddb(monkeypatch, {"location": {"latitude": 1, "longitude": 2}})

    def deny(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    assert Geolocator(str(db), None).locate("8.8.8.8").source == "database"
    assert Geolocator(None, (1, 2)).locate("8.8.8.8").source == "manual"


def test_env_override():
    g = geolocator_from_env({"SERVER_LAT": "-26.2", "SERVER_LON": "28.04"})
    assert g.locate(None) == ServerPosition(-26.2, 28.04, "manual")


def test_env_database_only():
    assert geolocator_from_env({"GEOIP_DB": "/nope.mmdb"}).locate("8.8.8.8") is None
    assert geolocator_from_env({}).locate("8.8.8.8") is None


@pytest.mark.parametrize("env", [{"SERVER_LAT": "1"}, {"SERVER_LON": "1"}])
def test_env_one_without_other_raises(env):
    with pytest.raises(ValueError, match="SERVER_LAT and SERVER_LON"):
        geolocator_from_env(env)


def test_env_non_numeric_raises():
    with pytest.raises(ValueError):
        geolocator_from_env({"SERVER_LAT": "x", "SERVER_LON": "1"})


# --- manual pin (the web form's server pin) ----------------------------------------------------

def test_pin_used_when_nothing_else_known():
    pos = Geolocator(None, None, pin=(-26.2, 28.0)).locate(None)
    assert pos == ServerPosition(-26.2, 28.0, "manual (pin)")


def test_override_beats_pin():
    assert Geolocator(None, (1, 2), pin=(3, 4)).locate("8.8.8.8") == ServerPosition(1, 2, "manual")


def test_database_beats_pin(tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")
    install_fake_maxminddb(monkeypatch, {"location": {"latitude": 51.5, "longitude": -0.12}})
    assert Geolocator(str(db), None, pin=(3, 4)).locate("8.8.8.8").source == "database"


def test_pin_fills_in_when_database_has_no_answer(tmp_path, monkeypatch):
    db = tmp_path / "x.mmdb"
    db.write_bytes(b"x")
    install_fake_maxminddb(monkeypatch, None)
    assert Geolocator(str(db), None, pin=(3, 4)).locate("8.8.8.8") == ServerPosition(3, 4, "manual (pin)")


def test_no_pin_no_position():
    assert Geolocator(None, None).locate(None) is None


@pytest.mark.parametrize("lat,lon", [("12.5", "-33"), (12.5, -33), ("90", "180"), ("-90", "-180")])
def test_parse_pin_accepts(lat, lon):
    assert parse_pin(lat, lon) == (float(lat), float(lon))


@pytest.mark.parametrize("lat,lon", [("91", "0"), ("0", "181"), ("-90.1", "0"), ("nan", "0"), ("0", "inf"),
                                     ("abc", "0"), ("", "5"), ("5", ""), (None, "5"), ("1e999", "0"),
                                     (True, 0)])
def test_parse_pin_rejects(lat, lon):
    with pytest.raises(ValueError):
        parse_pin(lat, lon)


def test_parse_pin_none_when_both_absent():
    assert parse_pin(None, None) is None
    assert parse_pin("", "") is None


def test_bad_pin_rejected_by_geolocator():
    with pytest.raises(ValueError):
        Geolocator(None, None, pin=(100, 0))


@pytest.mark.parametrize("pin", [(True, 0), (0, False), ("x", 1), (float("nan"), 0)])
def test_geolocator_pin_goes_through_parse_pin_with_fixed_message(pin):
    with pytest.raises(ValueError) as exc:
        Geolocator(None, None, pin=pin)
    assert "nan" not in str(exc.value) and "x" != str(exc.value)
    assert str(exc.value) in ("server pin is not a valid position",
                              "server pin needs both a latitude and a longitude")
