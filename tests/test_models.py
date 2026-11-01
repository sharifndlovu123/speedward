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
