"""Is it fast?: SpeedResult rules, the per-place and overall summary, every threshold, strict loading."""
import json
from datetime import UTC, datetime

import pytest

from speedward.core.models import (
    SAME_PROBE,
    Location,
    LocationResult,
    PathHop,
    ScanReport,
    SpeedResult,
    Stages,
    TraceStep,
)
from speedward.core.speed import (
    CONSISTENCY_RUNS,
    LOSS_PCT,
    MIN_PLACES,
    NO_RESULT,
    RISE_MS,
    VARIES_MIN_MS,
    VARIES_RATIO,
    speed_summary,
)


def loc(city, country="XX"):
    return Location(country, city, 10.0, 20.0, 1, "Net", "datacenter")


def ok(city, wait=100.0, repeat_wait=None, same=True, tls="TLSv1.3", download=5.0):
    rw = wait if repeat_wait is None else repeat_wait
    return LocationResult(loc(city), "measured", stages=Stages(1, 10, 10, wait, download, 121 + wait),
                          repeat_stages=Stages(1, 10, 10, rw, download, 121 + rw), repeat_same_probe=same,
                          http_status=200, tls_version=tls)


def blocked(city, reason="connection timed out"):
    return LocationResult(loc(city), "blocked", reason)


def st(wait=100.0, connect=10.0, tls=10.0, download=5.0):
    return Stages(1, connect, tls, wait, download, 1 + connect + tls + wait + download)


def sr(check, row, status="measured", reason="", **kw):
    if check == "consistency":
        kw.setdefault("run", 1)
    if status == "measured":
        kw.setdefault("probe", SAME_PROBE)
        if check in ("consistency", "compression"):
            kw.setdefault("stages", st())
        if check == "http2":
            kw.setdefault("h2_offered", True)
            if kw["h2_offered"]:
                kw.setdefault("stages", st())
                kw.setdefault("http_version", "HTTP/2.0")
        if check == "compression":
            kw.setdefault("encoding", "br")
        if check == "dnstrace":
            kw.setdefault("trace", [TraceStep("local", "127.0.0.53", 0), TraceStep("root", "d.root-servers.net", 0),
                                    TraceStep("tld", "a.gtld-servers.net", 100),
                                    TraceStep("authoritative", "ns.example.net", 3)])
        if check == "mtr":
            kw.setdefault("hops", [hop(1, 1.0), hop(2, 5.0), hop(3, 20.0)])
            kw.setdefault("reached", True)
    return SpeedResult(check, row, loc(f"r{row}"), status, reason, **kw)


def hop(n, avg, loss=0.0, host=None, jitter=0.5):
    if host == "*":
        return PathHop(n, "*", None, None, 100.0, None)
    return PathHop(n, host or f"h{n}.example.net", 64500, avg, loss, jitter)


def rep(locations, speed=None, target="https://example.com/"):
    t = datetime(2026, 11, 2, tzinfo=UTC)
    return ScanReport(target, t, t, "0.1.0", {"locations": []}, None, locations, [], [], None, speed=speed)


def runs(row, waits, **kw):
    return [sr("consistency", row, run=k, stages=st(w), **kw) for k, w in enumerate(waits, start=1)]


def overall(summary, check):
    return next(o for o in summary.overall if o.check == check)


# --- SpeedResult --------------------------------------------------------------------------------------

def test_only_a_measured_result_carries_figures():
    for kw in ({"stages": st()}, {"probe": SAME_PROBE}):
        with pytest.raises(ValueError):
            SpeedResult("compression", 0, loc("A"), "blocked", "timed out", **kw)
    with pytest.raises(ValueError):
        SpeedResult("mtr", 0, loc("A"), "test_failed", "x", hops=[hop(1, 1.0)])
    with pytest.raises(ValueError):
        SpeedResult("dnstrace", 0, loc("A"), "not_run", "x", trace=[TraceStep("local", "x", 0)])
    with pytest.raises(ValueError):
        SpeedResult("http2", 0, loc("A"), "test_failed", "x", h2_offered=False)


@pytest.mark.parametrize("kw", [
    {"check": "ipv6"},  # a reach check is not a speed check
    {"check": "consistency", "run": None},
    {"check": "consistency", "run": 5},
    {"check": "consistency", "run": 0},
    {"check": "consistency", "run": True},
    {"check": "http2", "run": 1},
    {"check": "mtr", "stages": st(), "hops": [], "reached": True},
    {"check": "compression", "http_version": "HTTP/2.0"},
    {"check": "http2", "http_version": "<b>2</b>"},
    {"check": "http2", "h2_offered": False, "http_version": None, "stages": st()},
    {"check": "compression", "encoding": "BR"},
    {"check": "compression", "encoding": "x" * 41},
    {"check": "compression", "encoding": "br‮"},
    {"check": "http2", "encoding": "br"},
    {"check": "compression", "trace": [TraceStep("root", "x", 1)]},
    {"check": "dnstrace", "hops": [hop(1, 1.0)]},
    {"check": "dnstrace", "reached": True},
    {"check": "dnstrace", "trace": []},  # measured needs its figures
    {"check": "mtr", "reached": None},
    {"check": "consistency", "stages": None},
    {"check": "http2", "h2_offered": None},
    {"check": "compression", "encoding": None},
    {"check": "consistency", "row": -1},
    {"check": "consistency", "row": True},
    {"check": "mtr", "probe": "maybe"},
])
def test_bad_speed_results_are_refused(kw):
    check = kw.pop("check")
    row = kw.pop("row", 0)
    with pytest.raises(ValueError):
        sr(check, row, **kw)


def test_trace_kinds_hop_numbers_and_text_are_checked_and_cleaned():
    with pytest.raises(ValueError):
        TraceStep("isp", "x", 1)
    with pytest.raises(ValueError):
        PathHop(0, "x", None, 1.0, 0, None)
    assert PathHop(1, "a\x1b[31mb", None, 1.0, 0, None).host == "a�[31mb"
    assert TraceStep("root", "a‮b", 1).server == "ab"


# --- consistency ----------------------------------------------------------------------------------

def test_five_steady_runs_are_steady_with_median_and_slowest():
    report = rep([ok("A", repeat_wait=100)], runs(0, [110, 120, 90, 130]))
    c = speed_summary(report).places[0].consistency
    assert (c.runs, c.median_ms, c.fastest_ms, c.slowest_ms, c.verdict) == (5, 110, 90, 130, "steady")


def test_varies_needs_both_the_ratio_and_the_ms_boundary():
    # median 100: slowest exactly 2x and exactly 50 ms above is not possible together; check each edge
    at_edge = rep([ok("A", repeat_wait=100)], runs(0, [100, 100, 100, 200]))
    assert speed_summary(at_edge).places[0].consistency.verdict == "varies"  # 200 >= 2*100 and +100
    ratio_short = rep([ok("A", repeat_wait=100)], runs(0, [100, 100, 100, 199.9]))
    assert speed_summary(ratio_short).places[0].consistency.verdict == "steady"
    small = rep([ok("A", repeat_wait=40)], runs(0, [40, 40, 40, 89.9]))  # 2.2x but only 49.9 ms above
    assert speed_summary(small).places[0].consistency.verdict == "steady"
    small_edge = rep([ok("A", repeat_wait=40)], runs(0, [40, 40, 40, 40 + VARIES_MIN_MS]))
    assert speed_summary(small_edge).places[0].consistency.verdict == "varies"
    assert VARIES_RATIO == 2.0 and VARIES_MIN_MS == 50.0


def test_fewer_than_five_runs_shows_the_count_and_no_verdict():
    report = rep([ok("A")], runs(0, [100, 900]) + [sr("consistency", 0, "test_failed", "boom", run=3),
                                                  sr("consistency", 0, "blocked", "connection timed out", run=4)])
    c = speed_summary(report).places[0].consistency
    assert (c.runs, c.blocked, c.verdict) == (3, 1, "too_few_runs")
    assert CONSISTENCY_RUNS == 5


def test_runs_from_another_probe_are_counted_but_left_out():
    report = rep([ok("A", same=False)], runs(0, [100, 100]) + [sr("consistency", 0, run=3, stages=st(5000), probe="different probe")])
    c = speed_summary(report).places[0].consistency
    assert (c.runs, c.other_probe, c.slowest_ms) == (2, 2, 100)  # the repeat and run 3 are left out


def test_overall_consistency_needs_three_judged_places():
    two = rep([ok("A"), ok("B")], runs(0, [100] * 4) + runs(1, [100] * 4))
    o = overall(speed_summary(two), "consistency")
    assert (o.verdict, o.judged) == ("too_few", 2)
    three = rep([ok("A"), ok("B"), ok("C")], runs(0, [100] * 4) + runs(1, [100] * 4) + runs(2, [100, 100, 100, 900]))
    o = overall(speed_summary(three), "consistency")
    assert (o.verdict, o.judged, o.places, o.low_ms, o.high_ms) == ("varies", 3, ["C, XX"], 100, 900)
    assert MIN_PLACES == 3


def test_blocked_and_failed_runs_never_change_a_figure():
    base = speed_summary(rep([ok("A")], runs(0, [100, 110, 120]))).places[0].consistency
    noisy = speed_summary(rep([ok("A")], runs(0, [100, 110, 120]) + [
        sr("consistency", 0, "blocked", "connection timed out", run=4)])).places[0].consistency
    assert (noisy.median_ms, noisy.slowest_ms, noisy.runs) == (base.median_ms, base.slowest_ms, base.runs)
    assert (noisy.blocked, base.blocked) == (1, 0)
    gone = rep([blocked("A")], [sr("consistency", 0, "blocked", "connection timed out", run=k) for k in range(1, 5)])
    p = speed_summary(gone).places[0]
    assert p.consistency is None and (p.gone["consistency"].status, p.gone["consistency"].reason) == (
        "blocked", "connection timed out")


# --- http2 --------------------------------------------------------------------------------------------

def test_http2_differences_only_against_the_same_probe():
    x = sr("http2", 0, stages=st(80, connect=12, tls=7))
    p = speed_summary(rep([ok("A", wait=100)], [x])).places[0].http2
    assert (p.offered, p.version, p.connect_diff_ms, p.tls_diff_ms, p.first_byte_diff_ms) == (
        True, "HTTP/2.0", 2, -3, -20)
    other = sr("http2", 0, stages=st(80), probe="different probe")
    assert speed_summary(rep([ok("A")], [other])).places[0].http2.first_byte_diff_ms is None
    not_same_repeat = speed_summary(rep([ok("A", same=False)], [x])).places[0].http2
    assert not_same_repeat.first_byte_diff_ms is None


def test_http2_not_offered_is_an_answer_without_timings():
    p = speed_summary(rep([ok("A")], [sr("http2", 0, h2_offered=False)])).places[0]
    assert (p.http2.offered, p.http2.version, p.http2.first_byte_diff_ms) == (False, None, None)


def test_http2_overall():
    places = [ok("A"), ok("B"), ok("C")]
    some = [sr("http2", 0), sr("http2", 1), sr("http2", 2, h2_offered=False)]
    o = overall(speed_summary(rep(places, some)), "http2")
    assert (o.verdict, o.count, o.places, o.ms) == ("some", 2, ["C, XX"], None)  # 2 diffs < 3 places
    every = [sr("http2", i, stages=st(100 - 10 * i)) for i in range(3)]
    o = overall(speed_summary(rep(places, every)), "http2")
    assert (o.verdict, o.ms) == ("all", -10)
    two = overall(speed_summary(rep(places[:2], every[:2])), "http2")
    assert two.verdict == "too_few"


# --- compression ----------------------------------------------------------------------------------

def test_compression_encoding_and_download_difference():
    p = speed_summary(rep([ok("A", download=5)], [sr("compression", 0, stages=st(download=2))])).places[0]
    assert (p.compression.encoding, p.compression.download_diff_ms) == ("br", -3)
    none = speed_summary(rep([ok("A")], [sr("compression", 0, encoding="none", probe="different probe")]))
    assert (none.places[0].compression.encoding, none.places[0].compression.download_diff_ms) == ("none", None)


def test_compression_overall():
    places = [ok("A"), ok("B"), ok("C")]
    o = overall(speed_summary(rep(places, [sr("compression", i, encoding="none") for i in range(3)])), "compression")
    assert (o.verdict, o.count, o.places) == ("none", 0, ["A, XX", "B, XX", "C, XX"])
    mixed = [sr("compression", 0, encoding="gzip"), sr("compression", 1), sr("compression", 2, encoding="none")]
    o = overall(speed_summary(rep(places, mixed)), "compression")
    assert (o.verdict, o.count, o.encodings) == ("some", 2, ["br", "gzip"])


# --- dns trace ------------------------------------------------------------------------------------

def test_dns_trace_slowest_step_and_total():
    t = speed_summary(rep([ok("A")], [sr("dnstrace", 0)])).places[0].dns_trace
    assert (t.total_ms, t.slowest.kind, t.slowest.ms) == (103, "tld", 100)


def test_dns_trace_under_a_millisecond_has_no_slowest_and_ties_go_to_the_earlier_step():
    zero = [TraceStep("local", "l", 0), TraceStep("root", "r", 0.4)]
    assert speed_summary(rep([ok("A")], [sr("dnstrace", 0, trace=zero)])).places[0].dns_trace.slowest is None
    tie = [TraceStep("local", "l", 0), TraceStep("root", "r", 7), TraceStep("tld", "t", 7)]
    assert speed_summary(rep([ok("A")], [sr("dnstrace", 0, trace=tie)])).places[0].dns_trace.slowest.kind == "root"


def test_dns_trace_overall_names_the_most_common_slowest_step():
    places = [ok("A"), ok("B"), ok("C")]
    auth = [TraceStep("local", "l", 0), TraceStep("tld", "t", 5), TraceStep("authoritative", "a", 40)]
    o = overall(speed_summary(rep(places, [sr("dnstrace", 0), sr("dnstrace", 1), sr("dnstrace", 2, trace=auth)])),
                "dnstrace")
    assert (o.kind, o.count, o.judged, o.places, o.ms) == ("tld", 2, 3, ["A, XX", "B, XX"], 100)  # median of 100, 100 and 5
    assert overall(speed_summary(rep(places[:2], [sr("dnstrace", 0), sr("dnstrace", 1)])), "dnstrace").verdict == "too_few"


# --- path (mtr) -----------------------------------------------------------------------------------

def test_loss_that_stops_at_a_middle_hop_is_not_a_bad_hop():
    hops = [hop(1, 1), hop(2, 5, loss=40), hop(3, 6, host="*"), hop(4, 20)]
    p = speed_summary(rep([ok("A")], [sr("mtr", 0, hops=hops)])).places[0].path
    assert p.bad_hop is None and (p.worst_loss.n, p.worst_loss.loss_pct) == (2, 40)
    assert (p.silent, p.hops, p.site_loss_pct) == (1, 4, 0)


def test_loss_from_a_hop_to_the_site_is_a_bad_hop_at_the_boundary():
    hops = [hop(1, 1), hop(2, 5, loss=LOSS_PCT), hop(3, 6, host="*"), hop(4, 20, loss=LOSS_PCT)]
    p = speed_summary(rep([ok("A")], [sr("mtr", 0, hops=hops)])).places[0].path
    assert (p.bad_hop.n, p.bad_hop.loss_pct) == (2, LOSS_PCT)
    under = [hop(1, 1), hop(2, 5, loss=9.9), hop(3, 20, loss=9.9)]
    assert speed_summary(rep([ok("A")], [sr("mtr", 0, hops=under)])).places[0].path.bad_hop is None


def test_a_long_link_is_a_rise_that_stays_up_to_the_site():
    hops = [hop(1, 2), hop(2, 3.6), hop(3, 3.6 + RISE_MS), hop(4, 175), hop(5, 144)]
    p = speed_summary(rep([ok("A")], [sr("mtr", 0, hops=hops)])).places[0].path
    # hop 4 rises 121.4 but the site (144) is below 175 - 25: a slow-answering router, not a long link
    assert (p.long_link.n, p.long_link.rise_ms) == (3, RISE_MS)
    short = [hop(1, 2), hop(2, 2 + RISE_MS - 0.1)]
    assert speed_summary(rep([ok("A")], [sr("mtr", 0, hops=short)])).places[0].path.long_link is None


def test_a_path_that_does_not_reach_the_site_judges_no_loss_and_is_left_out_overall():
    hops = [hop(1, 1), hop(2, 5, loss=50), hop(3, None, host="*")]
    p = speed_summary(rep([ok("A")], [sr("mtr", 0, hops=hops, reached=False)])).places[0].path
    assert (p.reached, p.bad_hop, p.jitter_ms, p.site_loss_pct, p.long_link) == (False, None, None, None, None)
    places = [ok("A"), ok("B"), ok("C")]
    o = overall(speed_summary(rep(places, [sr("mtr", 0), sr("mtr", 1), sr("mtr", 2, hops=hops, reached=False)])), "mtr")
    assert (o.verdict, o.judged) == ("too_few", 2)


def test_path_overall_bad_hop_and_clean():
    places = [ok("A"), ok("B"), ok("C")]
    bad = [hop(1, 1), hop(2, 5, loss=20), hop(3, 9, loss=20)]
    o = overall(speed_summary(rep(places, [sr("mtr", 0), sr("mtr", 1), sr("mtr", 2, hops=bad)])), "mtr")
    assert (o.verdict, o.count, o.places) == ("bad_hop", 1, ["C, XX"])
    o = overall(speed_summary(rep(places, [sr("mtr", i) for i in range(3)])), "mtr")
    assert (o.verdict, o.places) == ("clean", [])


# --- the summary as a whole ---------------------------------------------------------------------------

def test_tls12_is_named_from_the_main_test_only_when_measured():
    s = speed_summary(rep([ok("A", tls="TLSv1.2"), ok("B"), blocked("C")], [sr("http2", 0)]))
    assert s.tls12 == ["A, XX"]
    assert speed_summary(rep([ok("A", tls="TLSv1.2")], [])).tls12 == []  # no check ran: nothing to note


def test_a_place_without_a_result_says_it_did_not_run():
    s = speed_summary(rep([ok("A"), ok("B")], [sr("http2", 0)]))
    assert s.places[1].http2 is None and (s.places[1].gone["http2"].status, s.places[1].gone["http2"].reason) == (
        "not_run", NO_RESULT)


def test_columns_follow_the_checks_that_ran():
    s = speed_summary(rep([ok("A")], [sr("mtr", 0), sr("http2", 0)]))
    assert s.checks == ["http2", "mtr"]


# --- strict loading -------------------------------------------------------------------------------------

def full():
    places = [ok("A"), ok("B"), ok("C")]
    speed = [x for i in range(3) for x in runs(i, [100, 110, 120, 130])]
    speed += [sr("http2", i) for i in range(3)] + [sr("compression", i) for i in range(3)]
    speed += [sr("dnstrace", i) for i in range(3)] + [sr("mtr", i) for i in range(3)]
    return rep(places, speed)


def roundtrip(d):
    return ScanReport.from_dict(json.loads(json.dumps(d)))


def test_a_report_with_speed_round_trips_with_its_summary():
    d = full().to_dict()
    assert "speed" in d and d["speed_summary"]["checks"] == ["consistency", "http2", "compression", "dnstrace", "mtr"]
    again = roundtrip(d)
    assert again.to_dict() == d


def test_an_old_report_without_speed_loads_and_shows_nothing():
    d = full().to_dict()
    del d["speed"], d["speed_summary"]
    assert roundtrip(d).speed is None
    assert "speed" not in rep([ok("A")]).to_dict()


@pytest.mark.parametrize("change", [
    lambda d: d.pop("speed_summary"),
    lambda d: d.pop("speed") and None,
    lambda d: d["speed_summary"]["overall"][0].update(verdict="varies"),
    lambda d: d["speed_summary"].update(min_places=1),
    lambda d: d["speed"][0].update(row=3),
    lambda d: d["speed"][0].update(row=True),
    lambda d: d["speed"].append(dict(d["speed"][0])),  # a duplicate run
    lambda d: d["speed"][0]["stages"].update(first_byte=float("nan")),
    lambda d: d["speed"][0]["stages"].update(first_byte=10 ** 400),
    lambda d: d["speed"][0].update(status="blocked"),
    lambda d: d["speed"][-1]["hops"][0].update(n=0),
    lambda d: d["speed"][-1]["hops"][0].update(loss_pct=101),
    lambda d: d["speed"][-1].update(hops=[d["speed"][-1]["hops"][0]] * 65),
    lambda d: d["speed"][-4]["trace"][0].update(kind="isp"),
    lambda d: d["speed"][-4].update(trace=d["speed"][-4]["trace"] * 5),
    lambda d: d["speed"][-1].update(reached="yes"),
    lambda d: d["speed"].extend([d["speed"][0]] * (3 * 8)),  # more than places times checks allow
    lambda d: d.update(speed={}),
    lambda d: d["options"].update(checks=["consistency", "teleport"]),
])
def test_tampered_speed_data_is_refused(change):
    d = json.loads(json.dumps(full().to_dict()))
    change(d)
    with pytest.raises(ValueError):
        ScanReport.from_dict(d)


def test_a_huge_posted_speed_list_is_refused_before_reading_items():
    d = json.loads(json.dumps(full().to_dict()))
    d["speed"] = [None] * (3 * 8 + 1)
    with pytest.raises(ValueError, match="more speed results"):
        ScanReport.from_dict(d)


# --- silent hops and a path that reached the site ------------------------------------------------------

@pytest.mark.parametrize("kw", [{"asn": 64500}, {"avg_ms": 3.0}, {"jitter_ms": 0.2}, {"loss_pct": 50.0}])
def test_a_silent_hop_has_no_figures(kw):
    fields = {"n": 2, "host": "*", "asn": None, "avg_ms": None, "loss_pct": 100.0, "jitter_ms": None, **kw}
    with pytest.raises(ValueError):
        PathHop(**fields)
    assert PathHop(2, "*", None, None, None, None).loss_pct is None  # loss not given is fine


def test_a_path_that_reached_the_site_ends_on_an_answering_hop():
    with pytest.raises(ValueError):
        sr("mtr", 0, hops=[hop(1, 1.0), hop(2, None, host="*")], reached=True)
    with pytest.raises(ValueError):
        sr("mtr", 0, hops=[], reached=True)
    assert sr("mtr", 0, hops=[hop(1, 1.0), hop(2, None, host="*")], reached=False).reached is False


@pytest.mark.parametrize("change", [
    lambda d: d["speed"][-1]["hops"][-1].update(host="*"),  # reached, but ends silent
    lambda d: d["speed"][-1]["hops"][0].update(host="*"),  # silent with a time and an ASN
])
def test_tampered_paths_are_refused(change):
    d = json.loads(json.dumps(full().to_dict()))
    change(d)
    with pytest.raises(ValueError):
        ScanReport.from_dict(d)
