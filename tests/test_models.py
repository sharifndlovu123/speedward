from datetime import datetime, timezone

import pytest

from speedward.core.models import (
    SCHEMA_VERSION, DnsResult, Hop, Location, LocationResult, PathResult, ScanReport, Stages, clean,
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
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
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

from speedward.core.models import BrowserResult, DnsResult as _Dns, ServerPosition  # noqa: E402


def _full_report() -> ScanReport:
    t = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
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
