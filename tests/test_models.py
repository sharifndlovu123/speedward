from datetime import UTC, datetime

import pytest

from speedward.core.models import (
    SCHEMA_VERSION,
    DnsResult,
    Hop,
    Location,
    LocationResult,
    PathResult,
    ScanReport,
    Stages,
    clean,
)

LOC = Location("ZA", "Cape Town", -33.9, 18.4, 37457, "Example ISP", "isp")
STAGES = Stages(dns=1.0, connect=2.0, tls=3.0, first_byte=4.0, download=5.0, total=15.0)
HOP = Hop(1, "router.example", 64512, 1.2, 0.0)


def test_non_measured_result_cannot_carry_timings():
    with pytest.raises(ValueError):
        LocationResult(LOC, "blocked", stages=STAGES)
    with pytest.raises(ValueError):
        LocationResult(LOC, "test_failed", ping_ms=3.0)
    with pytest.raises(ValueError):
        LocationResult(LOC, "not_run", repeat_stages=STAGES)


def test_measured_result_keeps_timings():
    r = LocationResult(LOC, "measured", stages=STAGES, ping_ms=3.0)
    assert r.stages.total == 15.0


def test_reason_is_cleaned():
    r = LocationResult(LOC, "blocked", reason="refused \x1b[31mred")
    assert "\x1b" not in r.reason
    assert clean("a\tb\n") == "a\tb\n"


def test_unknown_status_rejected():
    with pytest.raises(ValueError):
        LocationResult(LOC, "great")


def test_non_measured_path_cannot_carry_hops():
    with pytest.raises(ValueError):
        PathResult(LOC, [HOP], "test_failed", "x")
    assert PathResult(LOC, [HOP], "measured", "").hops == [HOP]
    with pytest.raises(ValueError):
        PathResult(LOC, [], "bogus", "")


def test_hop_host_is_cleaned():
    assert "\x1b" not in Hop(1, "h\x1b[0m", None, None, None).host


def test_non_measured_dns_cannot_carry_data():
    with pytest.raises(ValueError):
        DnsResult(LOC, "not_run", "", [], 5.0)
    with pytest.raises(ValueError):
        DnsResult(LOC, "blocked", "", ["1.2.3.4"], None)
    with pytest.raises(ValueError):
        DnsResult(LOC, "bogus", "", [], None)
    d = DnsResult(LOC, "measured", "", ["1.2.3.4\x1b"], 5.0)
    assert "\x1b" not in d.answers[0]


def test_scan_report_to_dict_shape():
    now = datetime(2026, 11, 1, tzinfo=UTC)
    rep = ScanReport("example.com", now, now, "0.1", {}, None,
                     [LocationResult(LOC, "measured", stages=STAGES)], [], [], None)
    d = rep.to_dict()
    assert d["schema_version"] == SCHEMA_VERSION == 1
    for key in ("target", "started", "finished", "version", "options", "server", "locations", "paths", "dns", "browser"):
        assert key in d
    assert d["started"] == now.isoformat()
    assert d["locations"][0]["stages"]["total"] == 15.0


# --- ScanReport.from_dict (the web UI rebuilds reports from the browser's saved JSON) ----------

import copy  # noqa: E402
import json  # noqa: E402

from speedward.core.models import BrowserResult, ServerPosition  # noqa: E402
from speedward.core.models import DnsResult as _Dns  # noqa: E402


def _full_report() -> ScanReport:
    t = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    return ScanReport(
        target="https://example.com/", started=t, finished=t, version="0.1.0",
        options={"locations": [{"magic": "Tokyo"}], "traceroute": True, "dns": True, "browser": False},
        server=ServerPosition(50.1, 8.6, "manual"),
        locations=[
            LocationResult(LOC, "measured", stages=STAGES, repeat_stages=STAGES, ping_ms=3.0, http_status=200,
                           cdn="HIT", server_ip="203.0.113.5", tls_version="TLSv1.3", http_version="HTTP/1.1",
                           redirects=["https://example.com/x"], repeat_same_probe=True),
            LocationResult(Location("", "Lagos", None, None, None, None, None), "blocked", "timed out", http_status=None),
        ],
        paths=[PathResult(LOC, [HOP], "measured", ""), PathResult(LOC, [], "test_failed", "boom")],
        dns=[_Dns(LOC, "measured", "", ["203.0.113.5"], 12.0), _Dns(LOC, "not_run", "none", [], None)],
        browser=BrowserResult(1000, 3, 900.0, [("https://example.com/a.js", 500)]),
    )


def test_from_dict_round_trips():
    r = _full_report()
    assert ScanReport.from_dict(json.loads(json.dumps(r.to_dict()))) == r


def test_from_dict_round_trips_minimal():
    r = _full_report()
    r.server, r.browser, r.paths, r.dns = None, None, [], []
    assert ScanReport.from_dict(r.to_dict()) == r


def _broken(mutate):
    d = json.loads(json.dumps(_full_report().to_dict()))
    mutate(d)
    return d


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=2),
    lambda d: d.pop("schema_version"),
    lambda d: d.update(target=5),
    lambda d: d.update(started="yesterday"),
    lambda d: d.update(options=[]),
    lambda d: d.update(locations="x"),
    lambda d: d["locations"][0].update(status="great"),
    lambda d: d["locations"][1].update(stages={"first_byte": 3}),  # timings on a blocked result
    lambda d: d["locations"][0]["stages"].update(first_byte="fast"),
    lambda d: d["locations"][0]["stages"].update(first_byte=float("inf")),
    lambda d: d["locations"][0]["stages"].update(first_byte=True),
    lambda d: d["locations"][0]["location"].update(lat=500),
    lambda d: d["locations"][0]["location"].pop("city"),
    lambda d: d["locations"][0].update(redirects=[1]),
    lambda d: d["locations"][0].update(http_status="200"),
    lambda d: d["paths"][0]["hops"][0].update(n="1"),
    lambda d: d["dns"][0].update(answers="x"),
    lambda d: d["browser"].update(biggest=[["a"]]),
    lambda d: d["server"].update(lat="north"),
    lambda d: d.update(locations=[None]),
])
def test_from_dict_rejects_malformed(mutate):
    with pytest.raises(ValueError):
        ScanReport.from_dict(_broken(mutate))


@pytest.mark.parametrize("value", [None, [], "x", 3])
def test_from_dict_rejects_non_objects(value):
    with pytest.raises(ValueError):
        ScanReport.from_dict(value)


def test_from_dict_cleans_control_characters():
    d = _broken(lambda d: d["locations"][1].update(reason="bad \x1b[31m"))
    assert "\x1b" not in ScanReport.from_dict(d).locations[1].reason


def test_from_dict_does_not_share_input():
    d = _broken(lambda d: None)
    r = ScanReport.from_dict(d)
    before = copy.deepcopy(r)
    d["options"]["traceroute"] = "changed"
    assert r == before


@pytest.mark.parametrize("mutate", [
    lambda d: d["locations"][0]["location"].update(lat=10**400),
    lambda d: d["locations"][0]["location"].update(lon=10**400),
    lambda d: d["locations"][0]["stages"].update(first_byte=10**400),
])
def test_from_dict_rejects_oversized_integers(mutate):
    with pytest.raises(ValueError):
        ScanReport.from_dict(_broken(mutate))


@pytest.mark.parametrize("source", ["manual", "manual (pin)", "database"])
def test_server_source_known_values_round_trip(source):
    d = {"lat": 1, "lon": 2, "source": source}
    from speedward.core.models import _server
    assert _server(d).source == source


@pytest.mark.parametrize("source", ["trusted by NASA", "", "<b>x</b>", "Manual"])
def test_server_source_unknown_value_rejected(source):
    from speedward.core.models import _server
    with pytest.raises(ValueError):
        _server({"lat": 1, "lon": 2, "source": source})


# --- per-result server position (Task 16) -------------------------------------------------------

def _placed_report() -> ScanReport:
    r = _full_report()
    r.locations[0].server_position = ServerPosition(35.77, 140.39, "cdn header")
    r.locations[0].edge_code = "NRT"
    r.locations.append(LocationResult(LOC, "measured", stages=STAGES, server_ip="104.16.1.1",
                                      edge_note="edge location uncertain", edge_reason="shared address"))
    return r


def test_server_position_fields_round_trip():
    r = _placed_report()
    d = json.loads(json.dumps(r.to_dict()))
    assert d["locations"][0]["server_position"] == {"lat": 35.77, "lon": 140.39, "source": "cdn header"}
    assert d["locations"][0]["edge_code"] == "NRT"
    assert d["locations"][2]["edge_note"] == "edge location uncertain"
    assert ScanReport.from_dict(d) == r


def test_old_report_without_server_position_still_loads():
    d = json.loads(json.dumps(_full_report().to_dict()))
    for loc in d["locations"]:
        for key in ("server_position", "edge_code", "edge_note", "edge_reason"):
            loc.pop(key, None)
    r = ScanReport.from_dict(d)
    assert all(x.server_position is None and x.edge_note is None and x.edge_code is None for x in r.locations)


@pytest.mark.parametrize("mutate", [
    lambda d: d["locations"][0].update(server_position={"lat": 1, "lon": 2, "source": "manual"}),
    lambda d: d["locations"][0].update(server_position={"lat": 1, "lon": 2, "source": "guess"}),
    lambda d: d["locations"][0].update(server_position={"lat": 91, "lon": 2, "source": "database"}),
    lambda d: d["locations"][0].update(server_position={"lat": 1, "lon": None, "source": "database"}),
    lambda d: d["locations"][0].update(server_position={"lat": True, "lon": 2, "source": "database"}),
    lambda d: d["locations"][0].update(server_position="Tokyo"),
    lambda d: d["locations"][0].update(edge_code="nrt"),
    lambda d: d["locations"][0].update(edge_code="NRTX"),
    lambda d: d["locations"][0].update(edge_code=5),
    lambda d: d["locations"][2].update(edge_note="probably Tokyo"),
    lambda d: d["locations"][2].update(edge_reason="<b>x</b>"),
    lambda d: d["locations"][2].update(edge_reason=None),  # a note always says why
    lambda d: d["locations"][2].update(edge_note=None),  # a reason never comes alone
    # a position and an "uncertain" note at once
    lambda d: d["locations"][0].update(edge_note="edge location uncertain", edge_reason="shared address"),
    # only a measured result can say which server answered
    lambda d: d["locations"][1].update(server_position={"lat": 1, "lon": 2, "source": "database"}),
    lambda d: d["locations"][1].update(edge_note="position not available", edge_reason="private address"),
    lambda d: d["locations"][1].update(edge_code="NRT"),
])
def test_server_position_fields_rejected_when_malformed(mutate):
    d = json.loads(json.dumps(_placed_report().to_dict()))
    mutate(d)
    with pytest.raises(ValueError):
        ScanReport.from_dict(d)


def test_model_wording_sets_match_the_edge_module():
    from speedward.core import edge, models
    assert models.EDGE_NOTES == edge.EDGE_NOTES
    assert models.EDGE_REASONS == edge.EDGE_REASONS
    assert models.POSITION_SOURCES == edge.POSITION_SOURCES == ("database", "cdn header")


# --- which probe a later measurement came from (final review item 1) ---------------------------

def test_repeat_probe_is_derived_from_the_old_flag():
    same = LocationResult(LOC, "measured", stages=STAGES, repeat_stages=STAGES, repeat_same_probe=True)
    other = LocationResult(LOC, "measured", stages=STAGES, repeat_stages=STAGES)
    assert same.repeat_probe == "same probe"
    assert other.repeat_probe == "different probe"  # never claimed as the same machine
    assert LocationResult(LOC, "measured", stages=STAGES).repeat_probe is None


@pytest.mark.parametrize("kw", [
    {"repeat_probe": "same probe"},  # no repeat timing to describe
    {"repeat_stages": STAGES, "repeat_probe": "probably the same"},
    {"repeat_stages": STAGES, "repeat_probe": "different probe", "repeat_same_probe": True},
    {"ping_probe": "same probe"},  # no delay to describe
    {"ping_ms": 3.0, "ping_probe": "nearby"},
])
def test_probe_match_values_are_checked(kw):
    with pytest.raises(ValueError):
        LocationResult(LOC, "measured", stages=STAGES, **kw)


def test_path_and_dns_probe_only_on_measured_known_values():
    assert PathResult(LOC, [HOP], "measured", "", "different probe, same city").probe == "different probe, same city"
    with pytest.raises(ValueError):
        PathResult(LOC, [], "test_failed", "x", "different probe")
    with pytest.raises(ValueError):
        PathResult(LOC, [HOP], "measured", "", "elsewhere")
    with pytest.raises(ValueError):
        DnsResult(LOC, "measured", "", ["1.1.1.1"], 3.0, "elsewhere")


def _matched_report() -> ScanReport:
    r = _full_report()
    r.locations[0] = LocationResult(LOC, "measured", stages=STAGES, repeat_stages=STAGES, ping_ms=3.0,
                                    repeat_probe="different probe, same city", ping_probe="different probe")
    r.paths[0].probe = "different probe"
    r.dns[0].probe = "different probe, same city"
    return r


def test_probe_fields_round_trip():
    r = _matched_report()
    d = json.loads(json.dumps(r.to_dict()))
    assert d["locations"][0]["repeat_probe"] == "different probe, same city"
    assert d["locations"][0]["ping_probe"] == "different probe"
    assert d["paths"][0]["probe"] == "different probe"
    assert ScanReport.from_dict(d) == r


def test_old_report_without_probe_fields_never_claims_the_same_probe():
    d = json.loads(json.dumps(_full_report().to_dict()))
    d["locations"][0].update(repeat_same_probe=False)
    for key in ("repeat_probe", "ping_probe"):
        d["locations"][0].pop(key, None)
    r = ScanReport.from_dict(d)
    assert r.locations[0].repeat_probe == "different probe"
    assert r.locations[0].ping_probe is None


@pytest.mark.parametrize("mutate", [
    lambda d: d["locations"][0].update(repeat_probe="same machine"),
    lambda d: d["locations"][0].update(repeat_probe=1),
    lambda d: d["locations"][0].update(repeat_probe="different probe", repeat_same_probe=True),
    lambda d: d["locations"][0].update(ping_probe="<b>x</b>"),
    lambda d: d["paths"][0].update(probe="nearby"),
    lambda d: d["paths"][1].update(probe="different probe"),
    lambda d: d["dns"][1].update(probe="different probe"),
])
def test_probe_fields_rejected_when_malformed(mutate):
    d = json.loads(json.dumps(_matched_report().to_dict()))
    mutate(d)
    with pytest.raises(ValueError):
        ScanReport.from_dict(d)


@pytest.mark.parametrize("value", ["ping: limit", ["ping: evil.example timed out"], [5], "x"])
def test_not_run_must_be_known_fixed_texts(value):
    d = json.loads(json.dumps(_full_report().to_dict()))
    d["options"]["not_run"] = value
    with pytest.raises(ValueError):
        ScanReport.from_dict(d)


def test_known_not_run_texts_load():
    d = json.loads(json.dumps(_full_report().to_dict()))
    d["options"]["not_run"] = ["repeat test: Globalping limit reached", "DNS check: the test failed on the Globalping side"]
    assert ScanReport.from_dict(d).options["not_run"] == d["options"]["not_run"]


# --- final review: bidi controls, cleaned target/version, bounded timings -----------------------

@pytest.mark.parametrize("ch", ["‮", "‪", "⁦", "⁩", "‎", "‏", "؜", "﻿", "​"])
def test_clean_strips_bidi_and_invisible_format_characters(ch):
    assert clean(f"a{ch}b") == "ab"


@pytest.mark.parametrize("ch", [" ", " "])
def test_clean_turns_line_separators_into_spaces(ch):
    assert clean(f"a{ch}b") == "a b"


def test_from_dict_cleans_target_and_version():
    d = _broken(lambda d: d.update(target="https://exa‮mple.com/\x1b[31m", version="0.1 x"))
    r = ScanReport.from_dict(d)
    assert "‮" not in r.target and "\x1b" not in r.target
    assert r.version == "0.1 x"


@pytest.mark.parametrize("mutate", [
    lambda d: d["locations"][0]["stages"].update(first_byte=1e308),
    lambda d: d["locations"][0]["stages"].update(total=1e10),
    lambda d: d["locations"][0].update(ping_ms=1e308),
    lambda d: d["dns"][0].update(lookup_ms=1e308),
    lambda d: d["paths"][0]["hops"][0].update(rtt_ms=1e308),
    lambda d: d["browser"].update(load_ms=1e308),
])
def test_from_dict_rejects_absurd_timings(mutate):
    with pytest.raises(ValueError):
        ScanReport.from_dict(_broken(mutate))


# --- the "where the time goes" diagnosis -------------------------------------------------------

def _diagnosed() -> ScanReport:
    from speedward.core.bottleneck import diagnose
    from speedward.core.models import Stages as _S
    t = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    locs = [LocationResult(Location("XX", c, None, None, None, None, None), "measured",
                           stages=_S(dns=5, connect=c_ms, tls=10, first_byte=200, download=5, total=230))
            for c, c_ms in (("London", 10), ("Tokyo", 150), ("Lagos", 60))]
    locs.append(LocationResult(Location("XX", "Seoul", None, None, None, None, None), "blocked", "timed out"))
    r = ScanReport("https://example.com/", t, t, "0.1.0", {}, None, locs, [], [], None)
    r.diagnosis = diagnose(r)
    return r


def test_diagnosis_round_trips_through_from_dict():
    r = _diagnosed()
    d = json.loads(json.dumps(r.to_dict()))
    assert d["diagnosis"]["verdict"] == "biggest"
    assert ScanReport.from_dict(d) == r


def test_report_without_a_diagnosis_loads_with_none_and_is_never_given_one():
    d = json.loads(json.dumps(_diagnosed().to_dict()))
    del d["diagnosis"]
    assert ScanReport.from_dict(d).diagnosis is None
    d["diagnosis"] = None
    assert ScanReport.from_dict(d).diagnosis is None


@pytest.mark.parametrize("bad", ["biggest", 1, [], {"verdict": "biggest"}])
def test_from_dict_refuses_a_malformed_diagnosis(bad):
    d = json.loads(json.dumps(_diagnosed().to_dict()))
    d["diagnosis"] = bad
    with pytest.raises(ValueError):
        ScanReport.from_dict(d)


def test_from_dict_refuses_a_diagnosis_that_does_not_match_the_timings():
    d = json.loads(json.dumps(_diagnosed().to_dict()))
    d["locations"][0]["stages"]["first_byte"] = 900  # the timings changed, the diagnosis did not
    with pytest.raises(ValueError):
        ScanReport.from_dict(d)


def test_report_without_a_diagnosis_has_no_diagnosis_key():
    r = _diagnosed()
    r.diagnosis = None
    assert "diagnosis" not in r.to_dict()


@pytest.mark.parametrize("rule", ["biggest_share", "min_places", "near_connect_ms"])
def test_from_dict_refuses_a_huge_rule_number_with_valueerror(rule):
    text = json.dumps(_diagnosed().to_dict()).replace(f'"{rule}": ', f'"{rule}": 1{"0" * 400}, "_x": ', 1)
    d = json.loads(text)
    del d["diagnosis"]["rules"]["_x"]
    assert d["diagnosis"]["rules"][rule] == 10**400
    with pytest.raises(ValueError):
        ScanReport.from_dict(d)
