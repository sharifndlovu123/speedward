"""The "Who can reach it" grid: ReachResult rules, the grid and finding, strict loading, old reports."""
import json
from datetime import UTC, datetime

import pytest

from speedward.core.models import Location, LocationResult, ReachResult, ScanReport, Stages
from speedward.core.reach import MIN_PLACES, NO_RESULT, reach_grid


def loc(city, country="XX"):
    return Location(country, city, 10.0, 20.0, 1, "Net", "datacenter")


def ok(city, total=30.0):
    return LocationResult(loc(city), "measured", stages=Stages(1, 2, 3, 4, 5, total), http_status=200)


def blocked(city, reason="connection timed out"):
    return LocationResult(loc(city), "blocked", reason)


def failed(city):
    return LocationResult(loc(city), "test_failed", "the test failed on the Globalping side")


def rr(check, row, status="measured", reason="", **kw):
    if status == "measured":
        kw.setdefault("ms", 12.0)
    if check == "dns":
        kw.setdefault("resolver", "1.1.1.1")
    if check == "port":
        kw.setdefault("port", 443)
    return ReachResult(check, row, loc(f"r{row}"), status, reason, **kw)


def rep(locations, reach=None, target="https://example.com/", options=None):
    t = datetime(2026, 11, 2, tzinfo=UTC)
    return ScanReport(target, t, t, "0.1.0", options or {"locations": []}, None, locations, [], [], None,
                      reach=reach)


def cells(grid, row):
    return {c.column: c.state for c in grid.rows[row].cells}


# --- ReachResult --------------------------------------------------------------------------------------

def test_only_a_measured_check_result_carries_figures():
    with pytest.raises(ValueError):
        ReachResult("ipv6", 0, loc("A"), "blocked", "timed out", ms=3.0)
    with pytest.raises(ValueError):
        ReachResult("port", 0, loc("A"), "test_failed", "x", port=443, loss_pct=0.0)
    with pytest.raises(ValueError):
        ReachResult("http", 0, loc("A"), "not_run", "x", redirect="https")
    with pytest.raises(ValueError):
        ReachResult("ipv6", 0, loc("A"), "blocked", "x", probe="same probe")


@pytest.mark.parametrize("kw", [
    {"check": "bogus"}, {"row": -1}, {"row": True}, {"check": "dns"},  # dns needs a resolver
    {"resolver": "1.1.1.1"},  # only for dns
    {"check": "dns", "resolver": "9.9.9.9"},  # not a known resolver
    {"check": "port"},  # needs a port
    {"check": "port", "port": 0}, {"check": "port", "port": 70000},
    {"redirect": "https"},  # only for the http check
    {"check": "http", "redirect": "ftp"},
    {"loss_pct": 10.0}, {"probe": "maybe"}, {"status": "great"},
])
def test_reach_result_rejects_inconsistent_fields(kw):
    base = {"check": "ipv6", "row": 0, "location": loc("A"), "status": "measured", "reason": "", "ms": 1.0}
    with pytest.raises(ValueError):
        ReachResult(**{**base, **kw})


def test_reach_result_reason_is_cleaned():
    assert "\x1b" not in ReachResult("ipv6", 0, loc("A"), "blocked", "x\x1b[31m").reason


# --- the grid -------------------------------------------------------------------------------------------

def test_main_column_comes_from_the_main_scan_and_checks_follow_in_order():
    report = rep([ok("A"), ok("B"), ok("C")], [rr("port", 0), rr("ipv6", 0), rr("ipv6", 1), rr("ipv6", 2)])
    g = reach_grid(report)
    assert g.columns == ["main", "ipv6", "port"]
    assert g.scheme == "https" and g.port == 443
    assert g.rows[0].cells[0].parts[0].ms == 30.0  # the main request's total time


def test_cells_reached_blocked_and_no_data():
    report = rep([ok("A"), blocked("B"), failed("C")],
                 [rr("ipv6", 0), rr("ipv6", 1, "blocked", "no IPv6 address (AAAA record) for the name"),
                  rr("ipv6", 2, "test_failed", "the test failed on the Globalping side")])
    g = reach_grid(report)
    assert cells(g, 0) == {"main": "reached", "ipv6": "reached"}
    assert cells(g, 1) == {"main": "blocked", "ipv6": "blocked"}
    assert cells(g, 2) == {"main": "no_data", "ipv6": "no_data"}
    assert [r.worst for r in g.rows] == ["reached", "blocked", "no_data"]


def test_a_missing_result_is_no_data_not_a_guess():
    g = reach_grid(rep([ok("A"), ok("B")], [rr("ipv6", 0)]))
    cell = g.rows[1].cells[1]
    assert cell.state == "no_data" and cell.parts[0].status == "not_run" and cell.parts[0].reason == NO_RESULT


def test_dns_cell_is_blocked_when_one_resolver_gets_no_address():
    report = rep([ok("A")], [rr("dns", 0, resolver="1.1.1.1"),
                             rr("dns", 0, "blocked", "the name has no IPv4 address (A record)", resolver="8.8.8.8")])
    cell = reach_grid(report).rows[0].cells[1]
    assert cell.state == "blocked" and [p.resolver for p in cell.parts] == ["1.1.1.1", "8.8.8.8"]


def test_dns_cell_with_one_resolver_failed_on_globalping_side_is_still_reached():
    report = rep([ok("A")], [rr("dns", 0), rr("dns", 0, "test_failed", "x", resolver="8.8.8.8")])
    assert reach_grid(report).rows[0].cells[1].state == "reached"


def test_finding_counts_blocked_places_per_column_over_places_with_data_only():
    report = rep([ok("Frankfurt"), ok("Tokyo"), blocked("Seoul", "connection refused"), failed("Lagos")],
                 [rr("ipv6", 0), rr("ipv6", 1, "blocked", "connection timed out"),
                  rr("ipv6", 2, "blocked", "connection refused"), rr("ipv6", 3, "test_failed", "x")])
    g = reach_grid(report)
    assert g.verdict == "some_blocked" and g.judged == 3
    by = {f.column: f for f in g.findings}
    assert (by["ipv6"].blocked, by["ipv6"].judged) == (2, 3)  # Lagos (no data) never counts
    assert by["ipv6"].places == ["Tokyo, XX", "Seoul, XX"]
    assert (by["main"].blocked, by["main"].judged) == (1, 3)
    assert g.nowhere == ["Seoul, XX"]


def test_all_reached_has_no_findings():
    g = reach_grid(rep([ok("A"), ok("B"), ok("C")], [rr("http", i, redirect="https") for i in range(3)]))
    assert g.verdict == "all_reached" and g.findings == [] and g.nowhere == []


def test_fewer_than_three_places_with_data_is_not_enough_and_counts_nothing():
    report = rep([ok("A"), blocked("B"), failed("C"), failed("D")],
                 [rr("ipv6", 0, "blocked", "x"), rr("ipv6", 1, "blocked", "x")])
    g = reach_grid(report)
    assert MIN_PLACES == 3
    assert g.verdict == "too_few" and g.judged == 2 and g.findings == [] and g.nowhere == []


def test_exactly_three_places_is_enough():
    g = reach_grid(rep([ok("A"), ok("B"), blocked("C")], [rr("ipv6", i) for i in range(3)]))
    assert g.verdict == "some_blocked" and g.judged == 3


def test_no_data_cells_never_change_any_count():
    base = [ok("A"), ok("B"), ok("C")]
    reach = [rr("port", 0), rr("port", 1, "blocked", "no answer on port 443 (closed or filtered)"), rr("port", 2)]
    g1 = reach_grid(rep(base, reach))
    g2 = reach_grid(rep(base + [failed("D"), failed("E")],
                        reach + [rr("port", 3, "test_failed", "x"), rr("port", 4, "not_run", "y")]))
    assert [(f.column, f.judged, f.blocked) for f in g1.findings] == [(f.column, f.judged, f.blocked)
                                                                       for f in g2.findings]
    assert g1.judged == g2.judged


def test_plain_http_scheme_names_the_main_column():
    assert reach_grid(rep([ok("A")], [], target="http://example.com/")).scheme == "http"


# --- loading and exporting ------------------------------------------------------------------------------

def _round_trip(report):
    return ScanReport.from_dict(json.loads(json.dumps(report.to_dict())))


def test_reach_round_trips_with_its_grid():
    report = rep([ok("A"), blocked("B"), ok("C")],
                 [rr("ipv6", 0), rr("ipv6", 1, "blocked", "x"), rr("ipv6", 2, probe="different probe"),
                  rr("port", 0, loss_pct=33.3), rr("http", 0, redirect="https", http_status=301),
                  rr("dns", 0), rr("dns", 0, resolver="8.8.8.8")],
                 options={"locations": [], "checks": ["ipv6", "http", "dns", "port"]})
    d = report.to_dict()
    assert d["reach_grid"]["columns"] == ["main", "ipv6", "http", "dns", "port"]
    again = _round_trip(report)
    assert again.reach == report.reach and again.to_dict() == d


def test_old_report_without_checks_has_no_keys_and_loads_with_no_grid():
    report = rep([ok("A")])
    d = report.to_dict()
    assert "reach" not in d and "reach_grid" not in d
    assert ScanReport.from_dict(d).reach is None


def _with(mutate):
    report = rep([ok("A"), ok("B"), ok("C")], [rr("ipv6", 0), rr("ipv6", 1), rr("ipv6", 2)])
    d = json.loads(json.dumps(report.to_dict()))
    mutate(d)
    return d


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(reach="x"),
    lambda d: d["reach"].append(d["reach"][0]),  # the same check twice for one place
    lambda d: d["reach"][0].update(row=9),  # no such place
    lambda d: d["reach"][0].update(row="0"),
    lambda d: d["reach"][0].update(ms=-1),
    lambda d: d["reach"][0].update(ms=1e12),
    lambda d: d["reach"][0].update(status="blocked"),  # blocked with a time
    lambda d: d["reach"][0].update(check="traceroute"),
    lambda d: d["reach_grid"]["rows"][0].update(worst="blocked"),  # a grid that does not follow
    lambda d: d["reach_grid"].update(verdict="too_few"),
    lambda d: d.pop("reach_grid"),
    lambda d: d.pop("reach"),  # a grid with no results
    lambda d: d["options"].update(checks=["ipv6", "ipv6"]),
    lambda d: d["options"].update(checks=["bogus"]),
    lambda d: d["options"].update(not_run=["IPv6 check: whatever"]),
])
def test_from_dict_refuses_malformed_or_forged_reach(mutate):
    with pytest.raises(ValueError):
        ScanReport.from_dict(_with(mutate))


def test_known_not_run_texts_for_checks_load():
    d = _with(lambda d: d["options"].update(not_run=["IPv6 check: Globalping limit reached",
                                                     "port check: the test failed on the Globalping side"]))
    assert ScanReport.from_dict(d).options["not_run"][0] == "IPv6 check: Globalping limit reached"


# --- untrusted size (posted to /render and /export) -----------------------------------------------------

def test_location_cap_matches_globalping_largest_probe_count():
    from speedward.core.globalping import KEY_MAX_PROBES
    from speedward.core.models import MAX_LOCATIONS, REACH_PER_PLACE
    assert MAX_LOCATIONS == KEY_MAX_PROBES and REACH_PER_PLACE == 5


def _big(places, per_place=5):
    report = rep([ok(f"P{i}") for i in range(places)])
    d = json.loads(json.dumps(report.to_dict()))
    item = rr("ipv6", 0).__dict__ | {"location": json.loads(json.dumps(loc("A").__dict__))}
    d["reach"] = [dict(item, row=i % places) for i in range(places * per_place)]
    return d


def test_a_report_at_the_location_cap_loads_and_one_more_is_refused():
    from speedward.core.models import MAX_LOCATIONS
    report = rep([ok(f"P{i}") for i in range(MAX_LOCATIONS)])
    assert len(ScanReport.from_dict(report.to_dict()).locations) == MAX_LOCATIONS
    d = rep([ok(f"P{i}") for i in range(MAX_LOCATIONS + 1)]).to_dict()
    with pytest.raises(ValueError, match="too many locations"):
        ScanReport.from_dict(d)


def test_a_reach_list_longer_than_places_times_checks_is_refused_before_reading_it():
    d = _big(3)
    d["reach"].append(d["reach"][0])  # 3 places x 5 + 1
    with pytest.raises(ValueError, match="more reach results"):
        ScanReport.from_dict(d)
    d["reach"] = [None] * (10**6)  # never iterated
    with pytest.raises(ValueError, match="more reach results"):
        ScanReport.from_dict(d)


def test_a_full_size_grid_is_built_quickly():
    import time

    from speedward.core.models import MAX_LOCATIONS
    report = rep([ok(f"P{i}") for i in range(MAX_LOCATIONS)],
                 [rr(c, i, **({"resolver": ip} if c == "dns" else {}))
                  for i in range(MAX_LOCATIONS) for c, ip in (("ipv6", None), ("http", None), ("dns", "1.1.1.1"),
                                                             ("dns", "8.8.8.8"), ("port", None))])
    began = time.perf_counter()
    again = ScanReport.from_dict(json.loads(json.dumps(report.to_dict())))
    assert len(again.reach) == 5 * MAX_LOCATIONS
    assert time.perf_counter() - began < 5  # linear: well under a second here; quadratic took far longer


def test_a_name_that_resolves_does_not_count_as_reaching_the_site():
    report = rep([ok("A"), ok("B"), blocked("C")],
                 [rr("dns", 2), rr("port", 0), rr("port", 1), rr("port", 2, "blocked", "x")])
    assert reach_grid(report).nowhere == ["C, XX"]


# --- review fixes -----------------------------------------------------------------------------------

def test_nowhere_never_rests_on_a_failed_main_test():
    # main no data (Globalping side), one blocked check: not "cannot reach it at all"
    for check, extra in (("ipv6", {}), ("http", {}), ("port", {})):
        report = rep([ok("A"), ok("B"), ok("C"), failed("D")],
                     [rr(check, 0), rr(check, 1), rr(check, 2), rr(check, 3, "blocked", "x", **extra)])
        assert reach_grid(report).nowhere == [], check


def test_nowhere_with_failed_main_needs_two_blocked_site_checks():
    report = rep([ok("A"), ok("B"), ok("C"), failed("D")],
                 [rr("ipv6", i) for i in range(3)] + [rr("ipv6", 3, "blocked", "x"),
                                                     rr("port", 0), rr("port", 1), rr("port", 2),
                                                     rr("port", 3, "blocked", "y")])
    assert reach_grid(report).nowhere == ["D, XX"]


def test_nowhere_with_blocked_main_and_nothing_reached():
    report = rep([ok("A"), ok("B"), blocked("C")], [rr("ipv6", 0), rr("ipv6", 1), rr("ipv6", 2, "test_failed", "x")])
    assert reach_grid(report).nowhere == ["C, XX"]


def test_a_column_needs_three_judged_places_for_a_finding():
    # ipv6 has data in only 2 places (one blocked): no ipv6 finding, though 3 places have data overall
    report = rep([ok("A"), ok("B"), ok("C")],
                 [rr("ipv6", 0), rr("ipv6", 1, "blocked", "x"), rr("ipv6", 2, "test_failed", "y")])
    g = reach_grid(report)
    assert [f.column for f in g.findings] == []
    assert g.verdict == "unjudged"  # a blocked cell exists, so it never says "all reached"


def test_a_column_with_exactly_three_judged_places_has_a_finding():
    report = rep([ok("A"), ok("B"), ok("C")], [rr("ipv6", 0), rr("ipv6", 1, "blocked", "x"), rr("ipv6", 2)])
    g = reach_grid(report)
    assert [(f.column, f.judged, f.blocked) for f in g.findings] == [("ipv6", 3, 1)]
    assert g.verdict == "some_blocked"


@pytest.mark.parametrize("code", [99, 600, -1])
def test_http_status_must_be_a_real_status_code(code):
    with pytest.raises(ValueError):
        ReachResult("http", 0, loc("A"), "measured", "", ms=1.0, http_status=code)


@pytest.mark.parametrize("status", ["test_failed", "not_run"])
def test_http_status_only_on_measured_or_blocked(status):
    with pytest.raises(ValueError):
        ReachResult("ipv6", 0, loc("A"), status, "x", http_status=500)
    assert ReachResult("ipv6", 0, loc("A"), "blocked", "x", http_status=403).http_status == 403
