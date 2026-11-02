"""The "Is it fast?" checks in run_scan, against FakeGlobalping only (results recorded from the real API,
tests/fixtures/globalping/speed_recorded.json)."""
import copy

import pytest

from speedward.core.budget import Plan, cost_breakdown, estimate_cost
from speedward.core.globalping import GlobalpingClient
from speedward.core.models import ScanReport
from speedward.core.runner import NOT_A_NAME, PLAIN_ADDRESS, run_scan
from speedward.core.speed import speed_summary
from tests.fake_globalping import FakeGlobalping, fixture, on_probes

TWO = [{"magic": "Berlin"}, {"magic": "Tokyo"}]
ALL = ("consistency", "http2", "compression", "dnstrace", "mtr")


def speed_recorded(name):
    return copy.deepcopy(fixture("speed_recorded")["results"][name]["result"])


@pytest.fixture
def fake():
    f = FakeGlobalping()
    f.by_type = {"http": fixture("http_ok"), "ping": fixture("ping_ok"),
                 "dns": on_probes(speed_recorded("dns_trace_ok"), type="dns"),
                 "mtr": on_probes(speed_recorded("mtr_icmp_far"), type="mtr")}
    f.by_kind = {"http2": on_probes(speed_recorded("http2_ok"))}
    f.start()
    yield f
    f.stop()


def scan(fake, url="https://example.com/", checks=ALL, **kw):
    gp = GlobalpingClient(token="t", base_url=fake.base_url, poll_interval=0.01)
    return run_scan(url, Plan([dict(x) for x in TWO], checks=checks, **kw), gp)


def by(report, check):
    return [x for x in report.speed if x.check == check]


def test_no_speed_check_ticked_runs_nothing_extra(fake):
    report = scan(fake, checks=())
    assert [p["type"] for p in fake.posts()] == ["http", "http", "ping"]
    assert report.speed is None and "speed" not in report.to_dict()


def test_speed_only_has_no_reach_grid_and_reach_only_has_no_speed(fake):
    report = scan(fake, checks=("http2",))
    assert report.reach is None and report.speed is not None
    report = scan(fake, checks=("ipv6",))
    assert report.reach is not None and report.speed is None


def test_every_check_reuses_the_cold_probes_with_the_observed_options(fake):
    report = scan(fake)
    extra = fake.posts()[3:]
    assert [p["type"] for p in extra] == ["http"] * 4 + ["http", "http", "dns", "mtr"]
    assert all(p["locations"] == "fake0001" for p in extra)
    main = {"request": {"method": "GET", "path": "/"}, "protocol": "HTTPS"}
    assert all(p["measurementOptions"] == main for p in extra[:4])
    assert extra[4]["measurementOptions"] == {**main, "protocol": "HTTP2"}
    assert extra[5]["measurementOptions"] == {"request": {"method": "GET", "path": "/",
                                                          "headers": {"Accept-Encoding": "br, gzip"}},
                                              "protocol": "HTTPS"}
    assert extra[6]["measurementOptions"] == {"trace": True}
    assert extra[7]["measurementOptions"] == {"packets": 5}
    assert report.options["checks"] == list(ALL)
    assert len(report.speed) == 2 * 8


def test_cost_matches_what_was_spent(fake):
    plan = Plan([dict(x) for x in TWO], checks=ALL)
    assert [(x.key, x.per_place) for x in cost_breakdown(plan)] == [
        ("base", 3), ("consistency", 4), ("http2", 1), ("compression", 1), ("dnstrace", 1), ("mtr", 1)]
    assert estimate_cost(plan) == 2 * 11
    scan(fake)
    assert sum(len(fake.measurements[f"fake{i:04d}"]["results"]) for i in range(1, len(fake.posts()) + 1)) == 22


def test_measured_fields_from_the_recorded_answers(fake):
    report = scan(fake)
    run = by(report, "consistency")[0]
    assert (run.status, run.run, run.stages.first_byte, run.probe) == ("measured", 1, 5, "same probe")
    h2 = by(report, "http2")[0]
    assert (h2.status, h2.http_version, h2.h2_offered, h2.stages.first_byte) == ("measured", "HTTP/2.0", True, 12)
    comp = by(report, "compression")[0]
    assert (comp.status, comp.encoding) == ("measured", "br")
    trace = by(report, "dnstrace")[0]
    assert [(s.kind, s.server, s.ms) for s in trace.trace] == [
        ("local", "127.0.0.53", 0), ("root", "d.root-servers.net", 0), ("tld", "a.gtld-servers.net", 100),
        ("authoritative", "elliott.ns.cloudflare.com", 0)]
    path = by(report, "mtr")[0]
    assert (path.status, path.reached, len(path.hops)) == ("measured", True, 20)
    silent = path.hops[3]
    assert (silent.host, silent.avg_ms, silent.loss_pct, silent.jitter_ms) == ("*", None, 100, None)
    assert path.hops[-1].host == "www.gnu.org" and path.hops[-1].jitter_ms is not None


def test_recorded_path_has_a_long_link_and_no_bad_hop(fake):
    s = speed_summary(scan(fake, checks=("mtr",)))
    p = s.places[0].path
    assert p.bad_hop is None  # 20% at middle hops did not carry to the site
    assert (p.long_link.n, p.long_link.host, p.long_link.rise_ms) == (9, "be1.core1.sea1.he.net", 82.2)
    # jitter from the per-packet times, never Globalping's jAvg (48.7 on the recording)
    assert p.jitter_ms < 2


def test_http2_not_offered_is_measured_not_blocked(fake):
    fake.by_kind["http2"] = on_probes(speed_recorded("http2_alpn_refused"))
    h2 = by(scan(fake, checks=("http2",)), "http2")[0]
    assert (h2.status, h2.h2_offered, h2.stages, h2.http_version) == ("measured", False, None, None)


def test_http2_other_failures_keep_the_usual_status(fake):
    timeout = {"status": "failed", "failureSource": "target", "rawOutput": "Request timed out while establishing "
               "the TCP connection.", "timings": {"total": 15000}}
    fake.by_kind["http2"] = on_probes(timeout)
    h2 = by(scan(fake, checks=("http2",)), "http2")[0]
    assert (h2.status, h2.reason) == ("blocked", "connection timed out")


def test_http2_is_not_tried_on_a_plain_http_address(fake):
    report = scan(fake, url="http://example.com/", checks=("http2",))
    assert [p["type"] for p in fake.posts()] == ["http", "http", "ping"]
    assert {(x.status, x.reason) for x in by(report, "http2")} == {("not_run", PLAIN_ADDRESS)}


def test_dns_trace_of_an_ip_address_does_not_run(fake):
    report = scan(fake, url="https://192.0.2.10/", checks=("dnstrace",))
    assert {(x.status, x.reason) for x in by(report, "dnstrace")} == {("not_run", NOT_A_NAME)}
    assert not fake.posts("dns")


def test_a_failed_trace_or_path_is_never_blocked(fake):
    failed = {"status": "failed", "failureSource": "target", "rawOutput": "connection refused"}
    fake.by_type["dns"] = on_probes(failed, type="dns")
    fake.by_type["mtr"] = on_probes(failed, type="mtr")
    report = scan(fake, checks=("dnstrace", "mtr"))
    assert {x.status for x in report.speed} == {"test_failed"}
    assert by(report, "dnstrace")[0].reason == "the DNS trace failed: connection refused"
    assert by(report, "mtr")[0].reason == "the path test failed: connection refused"


def test_no_content_encoding_is_not_compressed_and_odd_values_are_not_shown(fake):
    plain = copy.deepcopy(speed_recorded("compress_br"))
    del plain["headers"]["content-encoding"]
    odd = copy.deepcopy(speed_recorded("compress_br"))
    odd["headers"]["Content-Encoding"] = "<script>"
    del odd["headers"]["content-encoding"]
    fake.queue("http", "http_ok")
    fake.queue("http", "http_ok")
    fake.queue("http", on_probes([plain, odd]))
    comp = by(scan(fake, checks=("compression",)), "compression")
    assert [c.encoding for c in comp] == ["none", "unrecognised"]


def test_a_blocked_consistency_run_is_kept_as_blocked(fake):
    timeout = {"status": "failed", "failureSource": "target", "rawOutput": "Request timed out while establishing "
               "the TCP connection.", "timings": {"total": 15000}}
    for _ in range(2):
        fake.queue("http", "http_ok")
    fake.queue("http", on_probes([timeout, speed_recorded("compress_br")]))
    runs = by(scan(fake, checks=("consistency",)), "consistency")
    first = [x for x in runs if x.row == 0]
    assert (first[0].status, first[0].stages, first[1].status) == ("blocked", None, "measured")


def test_globalping_side_failures_and_the_limit(fake):
    fake.fail_kind = {"http2"}
    report = scan(fake, checks=("http2", "mtr"))
    assert {(x.status, x.reason) for x in by(report, "http2")} == {
        ("test_failed", "the test failed on the Globalping side")}
    assert "HTTP/2 check: the test failed on the Globalping side" in report.options["not_run"]
    fake.fail_kind = set()
    fake.rate_limit_after = fake._accepted + 4  # cold, repeat, ping and one consistency run
    report = scan(fake, checks=("consistency", "mtr"))
    assert {x.status for x in by(report, "mtr")} == {"not_run"}
    assert "path loss check: Globalping limit reached" in report.options["not_run"]
    assert ScanReport.from_dict(report.to_dict()).to_dict() == report.to_dict()


def test_a_scan_with_speed_checks_round_trips(fake):
    report = scan(fake)
    assert ScanReport.from_dict(report.to_dict()).to_dict() == report.to_dict()


def test_cold_run_that_cannot_be_fetched_keeps_an_empty_speed_list(fake):
    fake.in_progress_polls = 10 ** 6  # never finishes: the client gives up waiting
    gp = GlobalpingClient(token="t", base_url=fake.base_url, poll_interval=0.001, wait_timeout=0.05)
    report = run_scan("https://example.com/", Plan([dict(x) for x in TWO], checks=("mtr",)), gp)
    assert report.speed == [] and report.reach is None


def _answer(code, content_type="text/html; charset=utf-8", encoding=None):
    res = speed_recorded("compress_br")
    res["statusCode"] = code
    res["rawOutput"] = f"HTTP/1.1 {code}\n..."
    res["headers"].pop("content-encoding", None)
    res["headers"].pop("content-type", None)
    if content_type:
        res["headers"]["content-type"] = content_type
    if encoding:
        res["headers"]["content-encoding"] = encoding
    return res


@pytest.mark.parametrize(("code", "content_type", "reason"), [
    (301, "text/html", "the first response was a redirect or an error (HTTP 301), so compression was not judged"),
    (404, "text/html", "the first response was a redirect or an error (HTTP 404), so compression was not judged"),
    (500, "text/html", "the first response was a redirect or an error (HTTP 500), so compression was not judged"),
    (204, None, "nothing to compress: the first response has no body (HTTP 204)"),
    (200, "image/png", "nothing to compress: the first response is not text (image/png)"),
    (200, None, "nothing to compress: the first response has no content type"),
])
def test_compression_is_judged_only_on_a_2xx_text_answer(fake, code, content_type, reason):
    fake.by_kind["compression"] = on_probes(_answer(code, content_type))
    comp = by(scan(fake, checks=("compression",)), "compression")[0]
    assert (comp.status, comp.reason, comp.encoding, comp.stages) == ("not_run", reason, None, None)


@pytest.mark.parametrize("content_type", ["text/html", "application/json", "application/javascript",
                                          "image/svg+xml", "application/ld+json", "text/css"])
def test_compressible_answers_are_judged(fake, content_type):
    fake.by_kind["compression"] = on_probes(_answer(200, content_type))
    comp = by(scan(fake, checks=("compression",)), "compression")[0]
    assert (comp.status, comp.encoding) == ("measured", "none")


def test_not_judged_places_never_make_the_finding_or_the_download_note():
    from speedward.core.models import Location, SpeedResult
    from speedward.core.speed import speed_summary
    from speedward.reports.speed import diagram_notes
    from tests.test_speed import ok, rep

    gone = [SpeedResult("compression", i, Location("XX", "x", None, None, None, None, None), "not_run",
                        "the first response was a redirect or an error (HTTP 301), so compression was not judged")
            for i in range(3)]
    s = speed_summary(rep([ok("A"), ok("B"), ok("C")], gone))
    assert s.overall[0].verdict == "too_few"
    assert "download" not in diagram_notes(s)["overall"]
    assert all("download" not in diagram_notes(s)["places"][i] for i in range(3))


def test_an_odd_silent_hop_never_breaks_the_scan(fake):
    res = speed_recorded("mtr_icmp_far")
    res["hops"][3]["stats"]["loss"] = 40  # no address but partial loss: not observed, kept as unknown
    fake.by_type["mtr"] = on_probes(res, type="mtr")
    hop = by(scan(fake, checks=("mtr",)), "mtr")[0].hops[3]
    assert (hop.host, hop.loss_pct) == ("*", None)
