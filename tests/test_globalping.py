import copy

import httpx
import pytest

from speedward.core.globalping import (
    Allowance,
    GlobalpingClient,
    GlobalpingError,
    ProbeReuseUnavailable,
    RateLimited,
    classify,
)
from tests.fake_globalping import FakeGlobalping, fixture

TOKEN = "test-token-123"
HTTP_OPTS = {"request": {"method": "GET", "path": "/"}, "protocol": "HTTPS"}


@pytest.fixture
def fake():
    f = FakeGlobalping()
    f.start()
    yield f
    f.stop()


def client(fake, token=None, **kw):
    return GlobalpingClient(token=token, base_url=fake.base_url, poll_interval=0.01, **kw)


def probe_result(name, index=0):
    return fixture(name)["results"][index]


def test_token_sent_as_bearer_and_never_in_repr(fake):
    c = client(fake, token=TOKEN)
    mid = c.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    c.wait(mid)
    c.allowance()
    assert fake.tokens_seen() and all(t == f"Bearer {TOKEN}" for t in fake.tokens_seen())
    assert TOKEN not in repr(c) and TOKEN not in str(c)
    assert "token=<set>" in repr(c)
    assert "token=<unset>" in repr(client(fake))


def test_no_token_sends_no_authorization_header(fake):
    c = client(fake)
    c.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    assert fake.tokens_seen() == [None]


def test_token_not_in_error_messages(fake):
    fake.valid_token = "some-other-token"
    c = client(fake, token=TOKEN)
    with pytest.raises(GlobalpingError) as e:
        c.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    assert TOKEN not in str(e.value) and TOKEN not in repr(e.value)
    # connection failure message must not leak it either
    dead = GlobalpingClient(token=TOKEN, base_url="http://127.0.0.1:1/v1", poll_interval=0.01)
    with pytest.raises(GlobalpingError) as e:
        dead.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    assert TOKEN not in str(e.value)


def test_create_and_wait_returns_finished_measurement(fake):
    fake.in_progress_polls = 2
    c = client(fake)
    mid = c.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    doc = c.wait(mid)
    assert doc["status"] == "finished" and doc["results"][0]["result"]["statusCode"] == 200
    method, path, _, body = [r for r in fake.requests if r[0] == "POST"][0]
    assert body == {"type": "http", "target": "example.com",
                    "locations": [{"magic": "Berlin"}], "measurementOptions": HTTP_OPTS}
    assert c.last_rate["remaining"] == 248 and c.last_rate["cost"] == 2


def test_wait_times_out(fake):
    fake.in_progress_polls = 10**6
    c = client(fake, wait_timeout=0.1)
    mid = c.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    with pytest.raises(GlobalpingError, match="did not finish"):
        c.wait(mid)


def test_reuse_probes_by_measurement_id(fake):
    c = client(fake)
    first = c.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    c.wait(first)
    c.create("http", "example.com", first, HTTP_OPTS)
    assert [r[3] for r in fake.requests if r[0] == "POST"][1]["locations"] == first


def test_reuse_of_expired_id_raises_probe_reuse_unavailable(fake):
    c = client(fake)
    with pytest.raises(ProbeReuseUnavailable):
        c.create("http", "example.com", "expired-id", HTTP_OPTS)
    assert issubclass(ProbeReuseUnavailable, GlobalpingError)


def test_429_raises_rate_limited_with_remaining(fake):
    fake.rate_limit({"retry-after": "120", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "3000"},
                    '{"error":{"type":"too_many_requests"}}')
    with pytest.raises(RateLimited) as e:
        client(fake).create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    assert e.value.remaining == 0
    assert e.value.retry_after == 120


def test_429_with_no_headers_and_garbage_body_still_rate_limited(fake):
    fake.rate_limit({}, "<html>nope</html>")
    with pytest.raises(RateLimited) as e:
        client(fake).create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    assert e.value.remaining is None and e.value.retry_after is None


def test_429_falls_back_to_reset_header_for_retry_after(fake):
    fake.rate_limit({"x-ratelimit-reset": "42"})
    with pytest.raises(RateLimited) as e:
        client(fake).create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    assert e.value.retry_after == 42


def test_allowance_without_token_is_250_and_50(fake):
    a = client(fake).allowance()
    assert isinstance(a, Allowance)
    assert (a.tests_per_hour, a.max_probes, a.authenticated) == (250, 50, False)
    assert a.remaining == 250 and a.credits is None


def test_allowance_with_token_is_500_and_500(fake):
    fake.credits = 7
    a = client(fake, token=TOKEN).allowance()
    assert (a.tests_per_hour, a.max_probes, a.authenticated) == (500, 500, True)
    assert a.remaining == 500 and a.credits == 7


def test_allowance_reflects_remaining_after_a_post(fake):
    c = client(fake)
    c.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    assert c.allowance().remaining == 248  # fixture has 2 probes, cost 2


def test_allowance_falls_back_when_limits_unreachable(fake):
    fake.limits_status = 500
    assert client(fake).allowance() == Allowance(250, 50, None, False, None)
    assert client(fake, token=TOKEN).allowance() == Allowance(500, 500, None, True, None)


def test_allowance_bad_token_is_an_error(fake):
    fake.valid_token = "other"
    with pytest.raises(GlobalpingError):
        client(fake, token=TOKEN).allowance()


def test_injected_http_client_is_used(fake):
    h = httpx.Client()
    c = GlobalpingClient(base_url=fake.base_url, http=h)
    assert c.allowance().tests_per_hour == 250
    assert not h.is_closed


# --- classify ---------------------------------------------------------

def test_classify_measured_ok():
    assert classify(probe_result("http_ok")) == ("measured", "")


def test_classify_timeout_is_blocked():
    status, reason = classify(probe_result("http_timeout"))
    assert status == "blocked" and "timed out" in reason.lower()


def test_classify_dns_failure_is_blocked():
    status, reason = classify(probe_result("http_dns_failure"))
    assert status == "blocked" and "dns" in reason.lower()


def test_classify_certificate_error_is_blocked():
    status, reason = classify(probe_result("http_badcert"))
    assert status == "blocked"
    assert reason.startswith("certificate error") and "CERT_HAS_EXPIRED" in reason


@pytest.mark.parametrize("code", [403, 451])
def test_classify_403_is_blocked(code):
    raw = copy.deepcopy(probe_result("http_ok"))
    raw["result"]["statusCode"] = code
    status, reason = classify(raw)
    assert status == "blocked" and str(code) in reason


def test_classify_connection_refused_is_blocked():
    raw = copy.deepcopy(probe_result("http_timeout"))
    raw["result"]["rawOutput"] = "connect ECONNREFUSED 1.2.3.4:443"
    assert classify(raw)[0] == "blocked"


def test_classify_api_error_is_test_failed_not_blocked():
    raw = {"probe": probe_result("http_ok")["probe"],
           "result": {"status": "failed", "failureSource": "globalping", "rawOutput": "internal error"}}
    assert classify(raw)[0] == "test_failed"
    for odd in ({"status": "failed", "rawOutput": "timed out"},  # no failureSource
                {"status": "failed", "failureSource": None, "rawOutput": "timed out"},
                {"status": "failed", "failureSource": "target", "rawOutput": "something strange"},
                {"status": "failed"}, {"status": "weird"}):
        assert classify({"probe": {}, "result": odd})[0] == "test_failed", odd


def test_classify_offline_probe_is_test_failed():
    status, reason = classify({"probe": {}, "result": {"status": "offline", "rawOutput": "gone"}})
    assert status == "test_failed" and "offline" in reason


def test_classify_no_probe_is_not_run():
    assert classify({"probe": None, "result": None})[0] == "not_run"
    assert classify({})[0] == "not_run"


def test_classify_ignores_timings_on_failure():
    # timings.total is numeric on failure (15000) and must never leak into the answer
    out = classify(probe_result("http_timeout"))
    assert isinstance(out, tuple) and len(out) == 2 and all(isinstance(x, str) for x in out)
    assert "15000" not in out[1]


def test_classify_accepts_bare_result_dict():
    assert classify(probe_result("http_timeout")["result"])[0] == "blocked"


def test_classify_handles_missing_tls_and_status_code():
    raw = {"probe": {}, "result": {"status": "finished", "statusCode": 200, "tls": None}}
    assert classify(raw) == ("measured", "")


def test_classify_unfinished_result_is_test_failed():
    assert classify({"probe": {}, "result": {"status": "in-progress"}})[0] == "test_failed"


def test_create_with_no_options_sends_empty_object_not_null(fake):
    c = client(fake)
    c.create("ping", "example.com", [{"magic": "Berlin"}], None)
    assert [r[3] for r in fake.requests if r[0] == "POST"][0]["measurementOptions"] == {}


def test_strict_fake_rejects_null_options(fake):
    r = httpx.post(fake.base_url + "/measurements", json={
        "type": "ping", "target": "x", "locations": [{"magic": "Berlin"}], "measurementOptions": None})
    assert r.status_code == 400 and r.json()["error"]["message"] == "Parameter validation failed."


def test_allowance_reads_reset_seconds_from_limits(fake):
    fake.reset = 1800
    assert client(fake).allowance().reset_seconds == 1800


def test_allowance_reset_unknown_when_limits_omit_it(fake):
    fake.omit_reset = True
    assert client(fake).allowance().reset_seconds is None


def test_allowance_fallback_uses_reset_header_from_last_post(fake):
    c = client(fake)
    fake.reset = 900
    c.create("http", "example.com", [{"magic": "Berlin"}], HTTP_OPTS)
    fake.limits_status = 500
    assert c.allowance().reset_seconds == 900
