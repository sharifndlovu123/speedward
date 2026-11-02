"""The "More checks" in run_scan, against FakeGlobalping only (results recorded from the real API)."""
import pytest

from speedward.core.budget import Plan
from speedward.core.globalping import GlobalpingClient
from speedward.core.reach import reach_grid
from speedward.core.runner import run_scan
from tests.fake_globalping import FakeGlobalping, fixture, on_probes, recorded

TWO = [{"magic": "Berlin"}, {"magic": "Tokyo"}]
ALL = ("ipv6", "http", "dns", "port")


@pytest.fixture
def fake():
    f = FakeGlobalping()
    f.by_type = {"http": fixture("http_ok"), "ping": fixture("ping_ok"), "dns": fixture("dns_ok")}
    f.by_kind = {"ipv6": on_probes(recorded("http_v6_ok")),
                 "plain_http": on_probes(recorded("http_plain_redirect")),
                 "dns_1.1.1.1": on_probes(recorded("dns_resolver_ok"), type="dns"),
                 "dns_8.8.8.8": on_probes(recorded("dns_resolver_ok"), type="dns"),
                 "port": on_probes(recorded("ping_tcp_ok"), type="ping")}
    f.start()
    yield f
    f.stop()


def scan(fake, url="https://example.com/", checks=ALL, **kw):
    gp = GlobalpingClient(token="t", base_url=fake.base_url, poll_interval=0.01)
    return run_scan(url, Plan([dict(x) for x in TWO], checks=checks, **kw), gp)


def by(report, check, resolver=None):
    return [x for x in report.reach if x.check == check and x.resolver == resolver]


def test_no_check_ticked_runs_nothing_extra_and_has_no_grid(fake):
    report = scan(fake, checks=())
    assert [p["type"] for p in fake.posts()] == ["http", "http", "ping"]
    assert report.reach is None and "reach" not in report.to_dict()


def test_every_check_reuses_the_cold_probes_with_the_observed_options(fake):
    report = scan(fake)
    posts = fake.posts()
    cold_id = "fake0001"
    extra = posts[3:]
    assert [p["type"] for p in extra] == ["http", "http", "dns", "dns", "ping"]
    assert all(p["locations"] == cold_id for p in extra)
    assert extra[0]["measurementOptions"] == {"request": {"method": "GET", "path": "/"}, "protocol": "HTTPS",
                                              "ipVersion": 6}
    assert extra[1]["measurementOptions"] == {"request": {"method": "GET", "path": "/"}, "protocol": "HTTP"}
    assert [p["measurementOptions"] for p in extra[2:4]] == [{"resolver": "1.1.1.1"}, {"resolver": "8.8.8.8"}]
    assert extra[4]["measurementOptions"] == {"protocol": "TCP", "port": 443, "packets": 3}
    assert report.options["checks"] == list(ALL)
    assert len(report.reach) == 2 * 5  # two places; dns has two resolvers


def test_measured_check_fields(fake):
    report = scan(fake)
    v6 = by(report, "ipv6")[0]
    assert (v6.status, v6.ms, v6.http_status, v6.probe) == ("measured", 31, 200, "same probe")
    plain = by(report, "http")[0]
    assert (plain.status, plain.redirect, plain.http_status) == ("measured", "https", 301)
    dns = by(report, "dns", "1.1.1.1")[0]
    assert (dns.status, dns.ms) == ("measured", 2)
    port = by(report, "port")[0]
    assert (port.status, port.ms, port.loss_pct, port.port) == ("measured", 1.242, 0, 443)


def test_no_aaaa_record_is_blocked_with_a_plain_reason(fake):
    fake.by_kind["ipv6"] = on_probes(recorded("http_v6_no_aaaa"))
    v6 = by(scan(fake), "ipv6")[0]
    assert (v6.status, v6.reason, v6.ms) == ("blocked", "no IPv6 address (AAAA record) for the name", None)


def test_closed_port_is_blocked_though_the_test_finished(fake):
    fake.by_kind["port"] = on_probes(recorded("ping_tcp_closed"), type="ping")
    port = by(scan(fake), "port")[0]
    assert (port.status, port.reason, port.ms, port.loss_pct) == (
        "blocked", "no answer on port 443 (closed or filtered)", None, None)


def test_some_lost_attempts_still_reach_with_the_loss(fake):
    res = recorded("ping_tcp_ok")
    res["stats"].update(rcv=2, loss=33.3, avg=2.0)
    fake.by_kind["port"] = on_probes(res, type="ping")
    port = by(scan(fake), "port")[0]
    assert (port.status, port.ms, port.loss_pct) == ("measured", 2.0, 33.3)


def test_resolver_with_no_address_is_blocked(fake):
    fake.by_kind["dns_8.8.8.8"] = on_probes(recorded("dns_resolver_no_address"), type="dns")
    report = scan(fake)
    assert by(report, "dns", "8.8.8.8")[0].status == "blocked"
    assert by(report, "dns", "8.8.8.8")[0].reason == "the name has no IPv4 address (A record) on this resolver"
    assert by(report, "dns", "1.1.1.1")[0].status == "measured"
    assert reach_grid(report).rows[0].cells[3].state == "blocked"


@pytest.mark.parametrize("code, status, reason", [
    ("NXDOMAIN", "blocked", "the name was not found (NXDOMAIN)"),
    ("SERVFAIL", "test_failed", "the resolver could not answer (SERVFAIL)"),
    ("REFUSED", "test_failed", "the resolver could not answer (REFUSED)"),
    ("<b>x", "test_failed", "the resolver could not answer"),
])
def test_resolver_answer_codes(fake, code, status, reason):
    res = recorded("dns_resolver_no_address")
    res["statusCodeName"] = code
    fake.by_kind["dns_1.1.1.1"] = on_probes(res, type="dns")
    got = by(scan(fake), "dns", "1.1.1.1")[0]
    assert (got.status, got.reason) == (status, reason)


def test_resolver_side_failure_is_test_failed_not_blocked(fake):
    fake.by_kind["dns_1.1.1.1"] = on_probes({"status": "failed", "failureSource": "resolver",
                                             "rawOutput": "connection timed out; no servers could be reached"},
                                            type="dns")
    assert by(scan(fake), "dns", "1.1.1.1")[0].status == "test_failed"


def test_plain_http_answering_itself_has_no_redirect(fake):
    fake.by_kind["plain_http"] = fixture("http_ok")
    plain = by(scan(fake), "http")[0]
    assert (plain.status, plain.redirect) == ("measured", None)


def test_plain_http_redirect_elsewhere_is_other(fake):
    res = recorded("http_plain_redirect")
    res["headers"]["location"] = "/login"
    fake.by_kind["plain_http"] = on_probes(res)
    assert by(scan(fake), "http")[0].redirect == "other"


def test_a_check_that_fails_on_globalping_side_is_test_failed_for_every_place(fake):
    fake.fail_kind = {"ipv6"}
    report = scan(fake)
    v6 = by(report, "ipv6")
    assert [x.status for x in v6] == ["test_failed", "test_failed"]
    assert "IPv6 check: the test failed on the Globalping side" in report.options["not_run"]
    assert all(x.status == "measured" for x in by(report, "port"))  # the others still ran


def test_limit_reached_marks_later_checks_not_run_and_runs_nothing_more(fake):
    fake.rate_limit_after = 4  # cold, repeat, ping, ipv6
    report = scan(fake)
    assert [x.status for x in by(report, "ipv6")] == ["measured", "measured"]
    for check in ("http", "port"):
        assert {x.status for x in by(report, check)} == {"not_run"}
    assert len(fake.posts()) == 5  # the plain HTTP POST got the 429; nothing after it


def test_an_ip_address_target_skips_ipv6_and_dns_without_spending(fake):
    report = scan(fake, url="https://93.184.215.14/")
    kinds = [p["type"] for p in fake.posts()]
    assert kinds == ["http", "http", "ping", "http", "ping"]  # plain HTTP and port only
    assert {x.status for x in by(report, "ipv6")} == {"not_run"}
    assert {x.status for x in by(report, "dns", "1.1.1.1")} == {"not_run"}


def test_port_check_uses_the_address_port(fake):
    report = scan(fake, url="https://example.com:8443/")
    assert fake.posts("ping")[-1]["measurementOptions"]["port"] == 8443
    assert by(report, "port")[0].port == 8443


def test_check_results_are_not_assumed_from_a_different_probe(fake):
    doc = on_probes(recorded("http_v6_ok"))
    doc["results"][0]["probe"]["tags"] = ["datacenter-network", "u-someone-else"]
    fake.by_kind["ipv6"] = doc
    assert by(scan(fake), "ipv6")[0].probe == "different probe, same city"


def test_failed_main_location_still_gets_its_checks(fake):
    doc = fixture("http_ok")
    doc["results"][1]["result"] = fixture("http_timeout")["results"][0]["result"]
    fake.by_type["http"] = doc
    report = scan(fake)
    assert report.locations[1].status == "blocked"
    assert by(report, "port")[1].status == "measured"


def test_round_trip_of_a_scan_with_checks(fake):
    from speedward.core.models import ScanReport
    report = scan(fake)
    assert ScanReport.from_dict(report.to_dict()).to_dict() == report.to_dict()


# --- review fixes -----------------------------------------------------------------------------------

def test_dns_check_failure_is_never_blocked_even_if_source_says_target(fake):
    fake.by_kind["dns_1.1.1.1"] = on_probes({"status": "failed", "failureSource": "target",
                                             "rawOutput": "connection timed out; no servers could be reached"},
                                            type="dns")
    got = by(scan(fake), "dns", "1.1.1.1")[0]
    assert got.status == "test_failed" and got.reason.startswith("the resolver lookup failed")


@pytest.mark.parametrize("stats", [None, {}, {"rcv": "x", "loss": None}, {"total": 3}, {"rcv": 2, "avg": None}])
def test_port_with_missing_or_malformed_stats_is_test_failed(fake, stats):
    res = recorded("ping_tcp_ok")
    if stats is None:
        res.pop("stats")
    else:
        res["stats"] = stats
    fake.by_kind["port"] = on_probes(res, type="ping")
    got = by(scan(fake), "port")[0]
    assert (got.status, got.reason) == ("test_failed", "the reply counts were not available")


@pytest.mark.parametrize("stats", [{"rcv": 0, "loss": None, "avg": None}, {"loss": 100, "avg": None}])
def test_port_closed_only_on_explicit_zero_replies_or_full_loss(fake, stats):
    res = recorded("ping_tcp_ok")
    res["stats"] = stats
    fake.by_kind["port"] = on_probes(res, type="ping")
    assert by(scan(fake), "port")[0].status == "blocked"
