from datetime import UTC, datetime

import pytest

from speedward.core.analysis import Limits, cdn_status, evaluate_limits, summarise
from speedward.core.models import BrowserResult, Location, LocationResult, ScanReport, Stages

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def loc(city="Berlin", country="DE"):
    return Location(country, city, 52.5, 13.4, 8560, "IONOS", "datacenter")


def measured(city, first_byte, total=None, repeat_fb=None, same=False, **stages):
    return LocationResult(
        loc(city), "measured",
        stages=Stages(first_byte=first_byte, total=total, **stages),
        repeat_stages=Stages(first_byte=repeat_fb) if repeat_fb is not None else None,
        repeat_same_probe=same,
    )


def failed(city, status="blocked", reason="timed out the connection"):
    return LocationResult(loc(city), status, reason)


def report(locations, browser=None):
    return ScanReport("https://example.com/", NOW, NOW, "0.1.0", {}, None, locations, [], [], browser)


# --- summarise ---------------------------------------------------------

def test_failed_locations_excluded_from_average():
    s = summarise(report([measured("Berlin", 100), measured("Tokyo", 300), failed("Lagos")]))
    assert s.avg_first_byte == 200
    assert s.measured == 2 and s.excluded == 1 and s.blocked == 1


def test_median_and_worst_use_measured_only():
    r = report([measured("A", 100), measured("B", 500), measured("C", 200), failed("D", "test_failed")])
    s = summarise(r)
    assert s.median_first_byte == 200
    assert s.worst is r.locations[1]
    assert s.blocked == 0 and s.excluded == 1


def test_summary_of_nothing_measured_is_empty_not_zero():
    s = summarise(report([failed("A"), failed("B", "not_run", "Globalping limit reached")]))
    assert s.measured == 0 and s.excluded == 2
    assert s.avg_first_byte is None and s.median_first_byte is None and s.worst is None
    assert s.cache_gain_pct is None and s.slowest_stage is None


def test_cache_gain_none_when_no_location_reused_its_probe():
    s = summarise(report([measured("A", 200, repeat_fb=100, same=False)]))
    assert s.cache_gain_pct is None


def test_cache_gain_only_over_same_probe_locations():
    r = report([measured("A", 200, repeat_fb=100, same=True), measured("B", 100, repeat_fb=1000, same=False)])
    assert summarise(r).cache_gain_pct == pytest.approx(50.0)


def test_cache_gain_none_when_same_probe_has_no_repeat_timing():
    s = summarise(report([measured("A", 200, repeat_fb=None, same=True)]))
    assert s.cache_gain_pct is None


def test_slowest_stage_is_largest_average():
    r = report([measured("A", 10, dns=5, connect=50, tls=20), measured("B", 30, dns=5, connect=40, tls=20),
                failed("C")])
    assert summarise(r).slowest_stage == "connect"


# --- limits ------------------------------------------------------------

def test_all_failed_gives_exit_code_2():
    out = evaluate_limits(report([failed("A"), failed("B", "test_failed", "x")]), Limits(max_ttfb=1000))
    assert out.exit_code == 2
    assert out.failed and "could not test" in out.failed[0].lower()


def test_no_limits_and_nothing_measured_is_still_exit_2():
    assert evaluate_limits(report([failed("A")]), Limits()).exit_code == 2


def test_one_location_over_max_ttfb_fails():
    out = evaluate_limits(report([measured("Berlin", 100), measured("Tokyo", 900)]), Limits(max_ttfb=500))
    assert out.exit_code == 1
    assert len(out.failed) == 1 and "Tokyo" in out.failed[0]


def test_all_under_limits_pass():
    out = evaluate_limits(report([measured("A", 100, total=300), failed("B")]),
                          Limits(max_ttfb=500, max_load=400, max_blocked=1))
    assert out.exit_code == 0 and out.failed == []


def test_max_load_uses_total():
    out = evaluate_limits(report([measured("A", 100, total=3000)]), Limits(max_load=2000))
    assert out.exit_code == 1 and "A" in out.failed[0]


def test_measured_location_without_the_timing_does_not_silently_pass():
    out = evaluate_limits(report([measured("A", None, total=100)]), Limits(max_ttfb=500))
    assert out.exit_code == 1 and "not available" in out.failed[0]


def test_max_blocked_counts_blocked_locations():
    r = report([measured("A", 100), failed("B"), failed("C"), failed("D", "test_failed", "x")])
    assert evaluate_limits(r, Limits(max_blocked=1)).exit_code == 1
    assert evaluate_limits(r, Limits(max_blocked=2)).exit_code == 0


def test_max_page_weight_without_browser_result_fails_as_not_measured():
    out = evaluate_limits(report([measured("A", 100)]), Limits(max_page_weight=1_000_000))
    assert out.exit_code == 1 and "not measured" in out.failed[0]


def test_max_page_weight_with_browser_result():
    heavy = report([measured("A", 100)], BrowserResult(2_000_000, 40, 1500.0))
    light = report([measured("A", 100)], BrowserResult(500_000, 10, 800.0))
    assert evaluate_limits(heavy, Limits(max_page_weight=1_000_000)).exit_code == 1
    assert evaluate_limits(light, Limits(max_page_weight=1_000_000)).exit_code == 0


# --- CDN status --------------------------------------------------------

def test_cdn_status_cf_hit():
    assert cdn_status({"cf-cache-status": "HIT"}) == "HIT"


def test_cdn_status_cf_miss_and_other_values():
    assert cdn_status({"CF-Cache-Status": "miss"}) == "MISS"
    assert cdn_status({"cf-cache-status": "DYNAMIC", "age": "30"}) is None
    assert cdn_status({"cf-cache-status": "EXPIRED"}) is None


def test_cdn_status_x_cache_and_age():
    assert cdn_status({"X-Cache": "Hit from cloudfront"}) == "HIT"
    assert cdn_status({"x-cache": "TCP_MISS"}) == "MISS"
    assert cdn_status({"x-cache": "MISS, HIT"}) == "HIT"  # last layer is the one that answered
    assert cdn_status({"Age": "120"}) == "HIT"


def test_cdn_status_none_without_signal():
    assert cdn_status({"server": "nginx", "content-type": "text/html"}) is None
    assert cdn_status({"age": "0"}) is None
    assert cdn_status({"age": "soon"}) is None
    assert cdn_status({}) is None


# --- exact number text (never scientific notation) ---------------------------------------------

from speedward.core.analysis import plain_number  # noqa: E402


@pytest.mark.parametrize("value, text", [(1234567, "1234567"), (1000000000, "1000000000"), (5000000.0, "5000000"),
                                         (250.5, "250.5"), (40.125, "40.12"), (10, "10"), (0, "0"),
                                         (1e20, "100000000000000000000")])
def test_plain_number(value, text):
    assert plain_number(value) == text


def test_failure_message_prints_large_numbers_exactly():
    from speedward.core.models import ScanReport
    from tests.test_web import report_dict
    d = report_dict()
    d["locations"][0]["stages"]["first_byte"] = 9876543.0
    out = evaluate_limits(ScanReport.from_dict(d), Limits(max_ttfb=1234567))
    assert out.failed == ["first byte 9876543 ms from Tokyo, JP is over the limit of 1234567 ms"]
