import copy
import json

import pytest

from speedward.core.analysis import Limits, evaluate_limits, summarise
from speedward.core.budget import Plan
from speedward.core.geo import Geolocator
from speedward.core.globalping import GlobalpingClient, GlobalpingError
from speedward.core.runner import ScanError, run_scan
from tests.fake_globalping import FakeGlobalping, fixture

TOKEN = "secret-token-xyz"
# The recorded http_ok fixture holds two probe results (Berlin, Tokyo).
TWO = [{"magic": "Berlin"}, {"magic": "Tokyo"}]


@pytest.fixture
def fake():
    f = FakeGlobalping()
    f.by_type = {"http": fixture("http_ok"), "ping": fixture("ping_ok"),
                 "mtr": fixture("mtr_ok"), "dns": fixture("dns_ok")}
    f.start()
    yield f
    f.stop()


def client(fake):
    return GlobalpingClient(token=TOKEN, base_url=fake.base_url, poll_interval=0.01)


def plan(**kw):
    return Plan(locations=[dict(x) for x in TWO], **kw)


def http_with(index_to_result: dict) -> dict:
    """http_ok with some probes' `result` replaced (or patched when given a callable)."""
    doc = copy.deepcopy(fixture("http_ok"))
    for i, res in index_to_result.items():
        if callable(res):
            res(doc["results"][i]["result"])
        else:
            doc["results"][i]["result"] = copy.deepcopy(res)
    return doc


def failure(name):
    return fixture(name)["results"][0]["result"]


# --- order and shape --------------------------------------------------

def test_default_scan_runs_cold_warm_ping_with_one_target(fake):
    rep = run_scan("Example.com.", plan(), client(fake))
    posts = fake.posts()
    assert [p["type"] for p in posts] == ["http", "http", "ping"]
    assert {p["target"] for p in posts} == {"example.com"}
    assert posts[0]["measurementOptions"] == {"request": {"method": "GET", "path": "/"}, "protocol": "HTTPS"}
    assert rep.target == "https://example.com/"
    assert len(rep.locations) == 2 and all(r.status == "measured" for r in rep.locations)
    assert [r.location.city for r in rep.locations] == [p["probe"]["city"] for p in fixture("http_ok")["results"]]
    assert rep.paths == [] and rep.dns == [] and rep.browser is None


def test_path_query_and_port_go_into_http_options(fake):
    run_scan("http://Example.com:8080/a/b?x=1", plan(), client(fake))
    http = fake.posts("http")[0]
    assert http["target"] == "example.com"
    assert http["measurementOptions"] == {"request": {"method": "GET", "path": "/a/b", "query": "x=1"},
                                          "protocol": "HTTP", "port": 8080}
    assert {p["target"] for p in fake.posts()} == {"example.com"}


def test_measured_fields_from_http_result(fake):
    rep = run_scan("example.com", plan(), client(fake))
    first = rep.locations[0]
    raw = fixture("http_ok")["results"][0]["result"]
    assert first.stages.first_byte == raw["timings"]["firstByte"]
    assert first.stages.connect == raw["timings"]["tcp"]
    assert first.stages.total == raw["timings"]["total"]
    assert first.http_status == 200
    assert first.http_version == "HTTP/1.1"
    assert first.tls_version == "TLSv1.3"
    assert first.server_ip == "172.66.147.243"
    assert first.cdn == "HIT"
    assert first.redirects == []
    assert first.location.network_type == "datacenter"
    assert first.ping_ms == pytest.approx(0.744)


# --- warm run ---------------------------------------------------------

def test_warm_run_reuses_probes_flag(fake):
    rep = run_scan("example.com", plan(), client(fake))
    cold, warm = fake.posts("http")
    assert isinstance(cold["locations"], list)
    assert isinstance(warm["locations"], str)  # the cold measurement id
    assert all(r.repeat_same_probe for r in rep.locations)
    assert all(r.repeat_stages is not None for r in rep.locations)
    assert summarise(rep).cache_gain_pct is not None


def test_cache_gain_none_when_probes_not_reused(fake):
    fake.reuse_expired = True
    rep = run_scan("example.com", plan(), client(fake))
    warm = [p for p in fake.posts("http") if isinstance(p["locations"], list)]
    assert len(warm) == 2  # cold, then the warm fallback with the original location list
    assert not any(r.repeat_same_probe for r in rep.locations)
    assert summarise(rep).cache_gain_pct is None


def test_warm_probe_differs_for_one_location_is_not_claimed_as_reuse(fake):
    fake.queue("http", fixture("http_ok"))  # cold
    other = copy.deepcopy(fixture("http_ok"))
    other["results"][1]["probe"]["tags"] = ["datacenter-network", "u-someoneelse"]
    other["results"][1]["probe"]["asn"] = 9999
    fake.queue("http", other)  # warm
    rep = run_scan("example.com", plan(), client(fake))
    flags = [r.repeat_same_probe for r in rep.locations]
    assert flags == [True, False]


def test_warm_results_matched_by_probe_not_by_position(fake):
    fake.queue("http", fixture("http_ok"))
    swapped = copy.deepcopy(fixture("http_ok"))
    swapped["results"].reverse()
    swapped["results"][0]["result"]["timings"]["firstByte"] = 777  # now the Tokyo probe
    fake.queue("http", swapped)
    rep = run_scan("example.com", plan(), client(fake))
    assert rep.locations[1].repeat_stages.first_byte == 777
    assert rep.locations[0].repeat_stages.first_byte != 777
    assert all(r.repeat_same_probe for r in rep.locations)


def test_cold_passes_warm_fails_keeps_measured_without_repeat(fake):
    fake.queue("http", fixture("http_ok"))
    fake.queue("http", http_with({0: failure("http_timeout")}))
    rep = run_scan("example.com", plan(), client(fake))
    berlin = rep.locations[0]
    assert berlin.status == "measured"
    assert berlin.stages is not None and berlin.repeat_stages is None
    assert berlin.repeat_same_probe is False
    assert rep.locations[1].repeat_stages is not None


# --- failures and classification --------------------------------------

def test_mixed_success_and_timeout_across_probes(fake):
    fake.by_type["http"] = http_with({1: failure("http_timeout")})
    rep = run_scan("example.com", plan(), client(fake))
    tokyo = rep.locations[1]
    assert tokyo.status == "blocked" and "timed out" in tokyo.reason
    assert tokyo.stages is None and tokyo.ping_ms is None
    s = summarise(rep)
    assert s.measured == 1 and s.excluded == 1
    others = [fixture("http_ok")["results"][i]["result"]["timings"]["firstByte"] for i in (0,)]
    assert s.avg_first_byte == pytest.approx(sum(others) / len(others))  # the 15000 ms total never enters


def test_403_is_blocked(fake):
    def forbid(res):
        res["statusCode"], res["statusCodeName"] = 403, "Forbidden"
        res["rawOutput"] = "HTTP/1.1 403\nServer: cloudflare\n\nnope"
    fake.by_type["http"] = http_with({0: forbid})
    rep = run_scan("example.com", plan(), client(fake))
    assert rep.locations[0].status == "blocked"
    assert "403" in rep.locations[0].reason
    assert rep.locations[0].stages is None
    assert rep.locations[0].http_status == 403


def test_5xx_is_measured_with_status(fake):
    fake.by_type["http"] = http_with({0: lambda r: r.update(statusCode=503, rawOutput="HTTP/1.1 503\n\n")})
    rep = run_scan("example.com", plan(), client(fake))
    assert rep.locations[0].status == "measured" and rep.locations[0].http_status == 503


def test_certificate_error_is_blocked(fake):
    fake.by_type["http"] = http_with({0: failure("http_badcert")})
    rep = run_scan("example.com", plan(), client(fake))
    assert rep.locations[0].status == "blocked"
    assert "certificate" in rep.locations[0].reason and rep.locations[0].stages is None


def test_all_probes_failed_gives_exit_code_2(fake):
    fake.by_type["http"] = http_with({i: failure("http_dns_failure") for i in range(2)})
    rep = run_scan("example.com", plan(), client(fake))
    assert all(r.status == "blocked" for r in rep.locations)
    assert evaluate_limits(rep, Limits(max_ttfb=1000)).exit_code == 2


def test_missing_optional_fields_are_none(fake):
    def strip(res):
        for key in ("tls", "resolvedAddress", "headers", "statusCode"):
            res.pop(key, None)
        res["rawOutput"] = "something unexpected"
        res["timings"] = {"total": 40, "firstByte": 12}
    fake.by_type["http"] = http_with({0: strip})
    rep = run_scan("example.com", plan(), client(fake))
    r = rep.locations[0]
    assert r.status == "measured"
    assert r.stages.first_byte == 12 and r.stages.dns is None and r.stages.tls is None
    assert r.tls_version is None and r.http_version is None and r.server_ip is None
    assert r.http_status is None and r.cdn is None and r.redirects == []


def test_redirect_first_response_recorded_and_not_followed(fake):
    def redirect(res):
        res["statusCode"], res["statusCodeName"] = 301, "Moved Permanently"
        res["headers"] = {"location": "https://www.example.com/"}
        res["rawOutput"] = "HTTP/1.1 301\nLocation: https://www.example.com/\n\n"
    fake.by_type["http"] = http_with({0: redirect})
    rep = run_scan("example.com", plan(), client(fake))
    assert rep.locations[0].status == "measured"
    assert rep.locations[0].http_status == 301
    assert rep.locations[0].redirects == ["https://www.example.com/"]
    assert [p["type"] for p in fake.posts()] == ["http", "http", "ping"]
    assert {p["target"] for p in fake.posts()} == {"example.com"}


def test_hostile_header_value_is_cleaned(fake):
    hostile = "evil\x1b]0;x\x07"

    def poison(res):
        res["statusCode"] = 302
        res["headers"] = {"server": hostile, "location": "https://x.test/\x1b[2J", "x-cache": hostile}
        res["rawOutput"] = "HTTP/1.1 302\nServer: " + hostile + "\n\n"
    fake.by_type["http"] = http_with({0: poison})
    rep = run_scan("example.com", plan(), client(fake))
    dumped = json.dumps(rep.to_dict())
    assert "\x1b" not in dumped and "\x07" not in dumped
    assert "\\u001b" not in dumped and "\\u0007" not in dumped
    assert rep.locations[0].redirects == ["https://x.test/�[2J"]


def test_fewer_probes_than_locations_marks_missing_not_run(fake):
    p = Plan(locations=TWO + [{"magic": "Antarctica"}])
    rep = run_scan("example.com", p, client(fake))
    assert len(rep.locations) == 3
    missing = rep.locations[2]
    assert missing.status == "not_run" and missing.location.city == "Antarctica"
    assert missing.stages is None


# --- rate limits and errors ---------------------------------------------

def test_rate_limit_midscan_keeps_completed_and_marks_rest_not_run(fake):
    fake.rate_limit_after = 2  # cold and warm accepted, ping and later refused
    rep = run_scan("example.com", plan(traceroute=True, dns=True), client(fake))
    assert len(fake.posts()) == 3  # nothing tried after the first 429
    assert all(r.status == "measured" and r.repeat_stages is not None for r in rep.locations)
    assert all(r.ping_ms is None for r in rep.locations)
    assert len(rep.paths) == 2 and len(rep.dns) == 2
    for item in rep.paths + rep.dns:
        assert item.status == "not_run" and item.reason == "Globalping limit reached"
    assert "Globalping limit reached" in json.dumps(rep.options)


def test_rate_limit_on_warm_run_keeps_cold_results(fake):
    fake.rate_limit_after = 1
    rep = run_scan("example.com", plan(), client(fake))
    assert len(fake.posts()) == 2
    assert all(r.status == "measured" and r.repeat_stages is None and not r.repeat_same_probe
               for r in rep.locations)


def test_scan_error_when_first_measurement_cannot_be_created(fake):
    fake.rate_limit_after = 0
    with pytest.raises(ScanError) as e:
        run_scan("example.com", plan(), client(fake))
    assert TOKEN not in str(e.value)


def test_bad_url_is_scan_error(fake):
    with pytest.raises(ScanError):
        run_scan("ftp://example.com", plan(), client(fake))
    assert fake.posts() == []


# --- optional measurements ------------------------------------------------

def test_traceroute_and_dns_when_asked(fake):
    rep = run_scan("example.com", plan(traceroute=True, dns=True), client(fake))
    assert [p["type"] for p in fake.posts()] == ["http", "http", "ping", "mtr", "dns"]
    assert len(rep.paths) == 2 and all(p.status == "measured" for p in rep.paths)
    hops = rep.paths[0].hops
    assert [h.n for h in hops] == [1, 2, 3]
    assert hops[0].asn is None and hops[1].asn == 8560
    assert hops[2].loss_pct == pytest.approx(33.3) and hops[1].rtt_ms == pytest.approx(0.6)
    assert rep.paths[0].location.city == "Berlin"
    assert len(rep.dns) == 2 and rep.dns[0].answers == ["104.20.23.154", "172.66.147.243"]
    assert rep.dns[0].lookup_ms == 0


def test_browser_flag_is_ignored_here(fake):
    rep = run_scan("example.com", plan(browser=True), client(fake))
    assert rep.browser is None and len(fake.posts()) == 3


# --- server position and progress ---------------------------------------

class RecordingGeo:
    def __init__(self):
        self.seen = []

    def locate(self, ip):
        self.seen.append(ip)
        return Geolocator(None, (1.0, 2.0)).locate(ip)


def test_server_position_from_first_measured_ip(fake):
    fake.by_type["http"] = http_with({0: failure("http_timeout")})
    geo = RecordingGeo()
    rep = run_scan("example.com", plan(), client(fake), geo=geo)
    assert geo.seen == [fixture("http_ok")["results"][1]["result"]["resolvedAddress"]]
    assert rep.server is not None and (rep.server.lat, rep.server.lon) == (1.0, 2.0)


def test_server_position_none_without_geo(fake):
    assert run_scan("example.com", plan(), client(fake)).server is None


def test_progress_callback_receives_no_token_or_target(fake):
    lines = []
    run_scan("example.com/secret-path", plan(traceroute=True, dns=True), client(fake), on_progress=lines.append)
    assert len(lines) >= 5
    for line in lines:
        assert isinstance(line, str)
        assert TOKEN not in line and "example.com" not in line and "secret-path" not in line


def test_cold_results_unreadable_marks_every_location_test_failed(fake, monkeypatch):
    c = client(fake)

    def broken(_id):
        raise GlobalpingError("Globalping could not return the results (500)")
    monkeypatch.setattr(c, "wait", broken)
    rep = run_scan("example.com", plan(), c)
    assert [r.status for r in rep.locations] == ["test_failed", "test_failed"]
    assert all(r.stages is None for r in rep.locations)
    assert evaluate_limits(rep, Limits()).exit_code == 2
