"""Where the server that answered each probe is: cf-ray parsing, the bundled airport table, and the
rules that decide when a position is trustworthy enough to draw."""
import json
import socket
from pathlib import Path

import pytest

from speedward.core import edge
from speedward.core.edge import Answer, Placement, cf_ray_code, distance_km, edge_city, iata, place
from speedward.core.models import ServerPosition

TABLE = Path(edge.__file__).with_name("iata.json")

# Probe positions (city level, as Globalping gives them).
BERLIN = dict(lat=52.52, lon=13.41)
LONDON = dict(lat=51.51, lon=-0.13)
TOKYO = dict(lat=35.69, lon=139.69)
SYDNEY = dict(lat=-33.87, lon=151.21)
FRANKFURT_DB = (50.11, 8.68)
SF_DB = (37.77, -122.42)


def lookup_of(table):
    """A fake geo-IP database: ip -> (lat, lon), or a reason text for every other ip."""
    def lookup(ip):
        return table.get(ip, edge.NOT_IN_DB)
    return lookup


def no_db(ip):
    return edge.NO_DB


# --- cf-ray ------------------------------------------------------------------------------------

def test_cf_ray_suffix_is_the_airport_code():
    assert cf_ray_code({"cf-ray": "a53cca1a6ccf518e-TXL"}) == "TXL"  # observed in the API notes
    assert cf_ray_code({"CF-Ray": "a53cca1b5a96cb50-NRT"}) == "NRT"


@pytest.mark.parametrize("value", ["", "NRT", "-NRT", "a53cca1a6ccf518e-NR", "a53cca1a6ccf518e-NRTX",
                                   "a53cca1a6ccf518e-nrt", "a53cca1a6ccf518e-NRT extra", "zz-NRT",
                                   "a53cca1a6ccf518e-N1T", ["a53cca1a6ccf518e-NRT"], None, 5])
def test_cf_ray_anything_else_is_no_code(value):
    assert cf_ray_code({"cf-ray": value}) is None


def test_no_cf_ray_header_is_no_code():
    assert cf_ray_code({"server": "cloudflare"}) is None
    assert cf_ray_code({}) is None
    assert cf_ray_code(None) is None


# --- the bundled airport table ------------------------------------------------------------------

def test_table_codes_are_unique_in_the_file():
    pairs = []

    def keep(items):
        pairs.append([k for k, _ in items])
        return dict(items)

    json.loads(TABLE.read_text(), object_pairs_hook=keep)
    for keys in pairs:
        assert len(keys) == len(set(keys))


def test_table_entries_are_sane():
    table = iata()
    assert len(table) >= 250
    for code, e in table.items():
        assert len(code) == 3 and code.isalpha() and code.isupper(), code
        assert -90 <= e.lat <= 90 and -180 <= e.lon <= 180, code
        assert e.city and e.city.strip() == e.city, code
        assert len(e.country) == 2 and e.country.isupper(), code


def test_table_has_a_source_note():
    doc = json.loads(TABLE.read_text())
    assert "OurAirports" in doc["source"] and "public domain" in doc["source"]


def test_known_codes_are_where_they_should_be():
    t = iata()
    assert t["NRT"].city == "Tokyo" and distance_km(t["NRT"].lat, t["NRT"].lon, 35.69, 139.69) < 100
    assert t["TXL"].city == "Berlin" and distance_km(t["TXL"].lat, t["TXL"].lon, 52.52, 13.41) < 30
    assert t["JNB"].city == "Johannesburg" and t["LHR"].city == "London"
    assert edge_city("NRT") == "Tokyo" and edge_city("ZZZ") is None and edge_city(None) is None


def test_distance_is_great_circle_km():
    assert distance_km(0, 0, 0, 0) == 0
    assert 340 < distance_km(51.51, -0.13, 48.86, 2.35) < 350  # London to Paris
    assert 8800 < distance_km(BERLIN["lat"], BERLIN["lon"], TOKYO["lat"], TOKYO["lon"]) < 9100


# --- placement rules ------------------------------------------------------------------------------

def a(ip="93.184.216.34", continent="EU", where=BERLIN, connect=None, code=None):
    return Answer(ip=ip, continent=continent, lat=where["lat"], lon=where["lon"], connect_ms=connect,
                  edge_code=code)


def test_thresholds_are_named_constants():
    assert edge.SHARED_MIN_CONTINENTS == 2
    assert edge.HEADER_DB_MAX_KM == 2500
    assert edge.LIGHT_KM_PER_RTT_MS == 150


def test_database_position_for_an_ip_seen_on_one_continent():
    out = place([a(), a(where=LONDON)], lookup_of({"93.184.216.34": FRANKFURT_DB}))
    assert out == [Placement(ServerPosition(*FRANKFURT_DB, "database"), None, None)] * 2


def test_cdn_header_wins_even_when_the_ip_is_shared_across_continents():
    out = place([a(ip="104.16.1.1", code="TXL"), a(ip="104.16.1.1", continent="AS", where=TOKYO, code="NRT")],
                lookup_of({"104.16.1.1": SF_DB}))
    t = iata()
    assert out[0] == Placement(ServerPosition(t["TXL"].lat, t["TXL"].lon, "cdn header"), None, None)
    assert out[1].position == ServerPosition(t["NRT"].lat, t["NRT"].lon, "cdn header")


def test_cdn_header_works_without_a_database():
    out = place([a(code="NRT", continent="AS", where=TOKYO)], no_db)
    assert out[0].position.source == "cdn header"


def test_cdn_header_far_from_the_database_position_is_uncertain():
    # IP seen on one continent only, so the database is not known to be unreliable; they disagree.
    out = place([a(code="NRT", continent="AS", where=TOKYO)], lookup_of({"93.184.216.34": FRANKFURT_DB}))
    assert out == [Placement(None, edge.UNCERTAIN, edge.DISAGREE)]


def test_cdn_header_close_to_the_database_position_is_used():
    out = place([a(code="FRA")], lookup_of({"93.184.216.34": FRANKFURT_DB}))
    assert out[0].position.source == "cdn header"


def test_disagreement_threshold_is_strictly_more_than_2500_km(monkeypatch):
    t = iata()["FRA"]
    near = (t.lat, t.lon + 0.01)
    d = distance_km(t.lat, t.lon, *near)
    monkeypatch.setattr(edge, "HEADER_DB_MAX_KM", d)
    assert place([a(code="FRA")], lookup_of({"93.184.216.34": near}))[0].position is not None
    monkeypatch.setattr(edge, "HEADER_DB_MAX_KM", d * 0.99)
    assert place([a(code="FRA")], lookup_of({"93.184.216.34": near}))[0].note == edge.UNCERTAIN


def test_unknown_header_code_falls_back_to_the_database():
    out = place([a(code="ZZZ")], lookup_of({"93.184.216.34": FRANKFURT_DB}))
    assert out[0].position.source == "database"


def test_shared_ip_without_delays_is_uncertain():
    out = place([a(ip="104.16.1.1"), a(ip="104.16.1.1", continent="AF", where=dict(lat=6.5, lon=3.4))],
                lookup_of({"104.16.1.1": SF_DB}))
    assert out == [Placement(None, edge.UNCERTAIN, edge.SHARED)] * 2


def test_shared_ip_where_one_probe_answers_too_fast_for_the_database_place_is_uncertain():
    # An anycast address: Berlin connects in 2 ms to an address the database puts in San Francisco.
    out = place([a(ip="104.16.1.1", connect=2), a(ip="104.16.1.1", continent="AS", where=TOKYO, connect=3)],
                lookup_of({"104.16.1.1": SF_DB}))
    assert all(p == Placement(None, edge.UNCERTAIN, edge.SHARED) for p in out)


def test_single_origin_seen_on_several_continents_is_confirmed_by_delays():
    # One server in Frankfurt: every probe's connect time is long enough for that distance.
    out = place([a(connect=12, where=BERLIN), a(continent="AS", where=TOKYO, connect=230),
                 a(continent="OC", where=SYDNEY, connect=290)], lookup_of({"93.184.216.34": FRANKFURT_DB}))
    assert all(p.position == ServerPosition(*FRANKFURT_DB, "database") for p in out)


def test_single_origin_needs_every_probe_delay():
    out = place([a(connect=12), a(continent="AS", where=TOKYO, connect=None)],
                lookup_of({"93.184.216.34": FRANKFURT_DB}))
    assert all(p.note == edge.UNCERTAIN and p.reason == edge.SHARED for p in out)


def test_one_continent_but_delay_too_short_for_the_database_place_is_uncertain():
    out = place([a(where=LONDON, connect=1)], lookup_of({"93.184.216.34": (40.71, -74.0)}))
    assert out == [Placement(None, edge.UNCERTAIN, edge.TOO_FAST)]


def test_unknown_continents_count_as_different_ones():
    out = place([a(continent=None), a(continent=None, where=LONDON)], lookup_of({"93.184.216.34": FRANKFURT_DB}))
    assert all(p.reason == edge.SHARED for p in out)


@pytest.mark.parametrize("ip,reason", [("10.0.0.1", edge.PRIVATE), ("192.168.1.2", edge.PRIVATE),
                                       ("::1", edge.PRIVATE), ("not-an-ip", edge.PRIVATE), (None, edge.NO_IP)])
def test_private_or_missing_ip_has_no_position(ip, reason):
    out = place([a(ip=ip)], lookup_of({}))
    assert out == [Placement(None, edge.NOT_AVAILABLE, reason)]


@pytest.mark.parametrize("reason", [edge.NO_DB, edge.NOT_IN_DB, edge.DB_UNREADABLE])
def test_database_reasons_pass_through(reason):
    out = place([a()], lambda ip: reason)
    assert out == [Placement(None, edge.NOT_AVAILABLE, reason)]


def test_not_measured_entries_stay_empty():
    assert place([None, a(), None], lookup_of({"93.184.216.34": FRANKFURT_DB}))[0::2] == [None, None]


def test_lookup_is_called_once_per_public_ip():
    calls = []

    def lookup(ip):
        calls.append(ip)
        return FRANKFURT_DB
    place([a(), a(where=LONDON), a(ip="151.101.1.1"), a(ip="10.0.0.1")], lookup)
    assert sorted(calls) == ["151.101.1.1", "93.184.216.34"]


def test_place_makes_no_network_connection(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    assert place([a(code="NRT", continent="AS", where=TOKYO)], no_db)[0].position is not None


def test_reasons_and_notes_are_fixed_sets():
    assert edge.EDGE_NOTES == ("edge location uncertain", "position not available")
    for r in (edge.SHARED, edge.DISAGREE, edge.TOO_FAST, edge.PRIVATE, edge.NO_IP, edge.NO_DB, edge.NOT_IN_DB,
              edge.DB_UNREADABLE):
        assert r in edge.EDGE_REASONS
