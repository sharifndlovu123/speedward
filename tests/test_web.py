"""Web UI: Django views, in-flight scan state, stateless render/export. Globalping is FakeGlobalping."""
import copy
import json
import logging
import re
import threading
import time
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from speedward.core.runner import ScanError
from speedward.web import state, views
from tests.fake_globalping import FakeGlobalping, fixture

TOKEN = "tok-web-secret-123"
TARGET = "https://secret-target.example/page"
HOSTILE = "<script>alert(1)</script>"
START_FAILED = "The scan could not start. Check the address and your Globalping key."
STATIC_JS = Path(__file__).resolve().parent.parent / "speedward" / "web" / "static" / "js" / "results.js"


@pytest.fixture(autouse=True)
def fake(settings):
    f = FakeGlobalping()
    f.by_type = {"http": fixture("http_ok"), "ping": fixture("ping_ok"),
                 "mtr": fixture("mtr_ok"), "dns": fixture("dns_ok")}
    f.start()
    # Never reach the real API from a test, whatever the developer's .env holds.
    settings.GLOBALPING_BASE_URL = f.base_url
    settings.GLOBALPING_TOKEN = None
    settings.GEOIP_DB = None
    settings.SERVER_LAT = None
    settings.SERVER_LON = None
    settings.MAX_CONCURRENT_SCANS = 2
    settings.GLOBALPING_POLL_INTERVAL = 0.01
    state.reset()
    yield f
    f.stop()
    state.reset()


def form(**extra):
    return {"url": TARGET, "locations": "Berlin,Tokyo", **extra}


def start(client, **extra):
    return client.post(reverse("scan"), form(**extra))


def wait_done(client, scan_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(reverse("scan_status", args=[scan_id]))
        body = r.json()
        if body["status"] != "running":
            return r
        time.sleep(0.02)
    raise AssertionError("scan did not finish")


def run_full_scan(client, **extra):
    r = start(client, **extra)
    assert r.status_code == 202, r.content
    done = wait_done(client, r.json()["id"])
    assert done.json()["status"] == "done", done.content
    return done.json()["result"]


def report_dict(**overrides):
    loc = {"country": "JP", "city": "Tokyo", "lat": 35.7, "lon": 139.7, "asn": 1, "network": "Net",
           "network_type": "isp"}
    stages = {"dns": 1, "connect": 2, "tls": 3, "first_byte": 40, "download": 5, "total": 51}
    d = {
        "schema_version": 1, "target": "https://example.com/", "started": "2026-11-01T12:00:00+00:00",
        "finished": "2026-11-01T12:01:00+00:00", "version": "0.1.0",
        "options": {"locations": [{"magic": "Tokyo"}], "traceroute": False, "dns": False, "browser": False},
        "server": None,
        "locations": [
            {"location": loc, "status": "measured", "reason": "", "stages": stages, "repeat_stages": None,
             "ping_ms": 9.0, "http_status": 200, "cdn": None, "server_ip": "203.0.113.9", "tls_version": None,
             "http_version": None, "redirects": [], "repeat_same_probe": False},
            {"location": dict(loc, city="Lagos", country="NG", lat=6.5, lon=3.4), "status": "blocked",
             "reason": "timed out the connection", "stages": None, "repeat_stages": None, "ping_ms": None,
             "http_status": None, "cdn": None, "server_ip": None, "tls_version": None, "http_version": None,
             "redirects": [], "repeat_same_probe": False},
        ],
        "paths": [], "dns": [], "browser": None,
    }
    d.update(overrides)
    return d


def post_json(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


# --- the start page ---------------------------------------------------------------------------

def test_index_shows_key_free_up_to_a_limit_wording(client):
    body = client.get(reverse("index")).content.decode()
    assert "A key is free up to a limit" in body
    assert "https://globalping.io" in body
    assert not re.search(r"\bfree\b(?! up to a limit)", body.replace("free up to a limit", ""), re.I)
    assert "Only test sites you own or are authorised to test." in body


def test_status_line_without_token_shows_250_and_50(client):
    body = client.get(reverse("index")).content.decode()
    assert ("No Globalping key set: 250 tests/hour, 50 locations. A key is free up to a limit: "
            "https://globalping.io") in re.sub(r"<[^>]+>", "", body)


def test_status_line_with_token_shows_remaining(client, settings, fake):
    settings.GLOBALPING_TOKEN = TOKEN
    fake.remaining = 321
    body = re.sub(r"<[^>]+>", "", client.get(reverse("index")).content.decode())
    assert "Globalping key active: 500 tests/hour, up to 500 locations. Remaining this hour: 321" in body


def test_no_token_input_anywhere(client, settings, fake):
    settings.GLOBALPING_TOKEN = TOKEN
    fake.valid_token = TOKEN
    index = client.get(reverse("index"))
    page = index.content.decode()
    names = re.findall(r'<(?:input|textarea|select)\b[^>]*\bname="([^"]+)"', page)
    assert "url" in names
    assert not [n for n in names if re.search(r"token|key|globalping", n, re.I)], names
    assert 'type="password"' not in page

    responses = [index, client.post(reverse("estimate"), form())]
    r = start(client)
    responses.append(r)
    done = wait_done(client, r.json()["id"])
    responses.append(done)
    result = done.json()["result"]
    responses.append(post_json(client, reverse("render"), {"result": result, "limits": {}}))
    for fmt in ("html", "json", "md"):
        responses.append(post_json(client, reverse("export", args=[fmt]), {"result": result}))
    assert fake.tokens_seen() and all(t == f"Bearer {TOKEN}" for t in fake.tokens_seen())
    for resp in responses:
        assert resp.status_code < 400, resp.content
        assert TOKEN not in resp.content.decode()
        assert TOKEN not in str(resp.headers)


def test_page_has_no_inline_script_and_carries_ttl(client, settings):
    settings.RESULT_TTL_SECONDS = 60
    page = client.get(reverse("index")).content.decode()
    scripts = re.findall(r"<script[^>]*>", page)
    assert scripts and all("src=" in s for s in scripts)
    assert 'data-ttl="60"' in page
    assert "no-store" in client.get(reverse("index")).headers["Cache-Control"]


def test_security_headers_and_csp(client):
    r = client.get(reverse("index"))
    assert r["Content-Security-Policy"] == ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                            "img-src 'self' data:; script-src 'self'; frame-ancestors 'none'")
    assert r["X-Content-Type-Options"] == "nosniff"
    assert r["Referrer-Policy"] == "no-referrer"
    assert r["X-Frame-Options"] == "DENY"


def test_csrf_required_on_post(fake):
    strict = Client(enforce_csrf_checks=True)
    body = json.dumps({"result": report_dict()})
    for name, args, data, ctype in (("estimate", [], form(), None), ("scan", [], form(), None),
                                    ("render", [], body, "application/json"),
                                    ("export", ["json"], body, "application/json")):
        kw = {"content_type": ctype} if ctype else {}
        assert strict.post(reverse(name, args=args), data, **kw).status_code == 403, name
    assert fake.posts() == []
    page = strict.get(reverse("index")).content.decode()
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
    r = strict.post(reverse("estimate"), form(), HTTP_X_CSRFTOKEN=csrf)
    assert r.status_code == 200


# --- estimate and budget ----------------------------------------------------------------------

def test_estimate_endpoint_returns_cost(client):
    r = client.post(reverse("estimate"), form())
    assert r.status_code == 200
    body = r.json()
    assert body["cost"] == 6 and body["fits"] is True and body["reason"] == ""
    assert body["allowance"] == {"tests_per_hour": 250, "max_probes": 50, "remaining": 250,
                                 "authenticated": False, "credits": None}
    assert "about 6 tests" in body["message"] and "250 left this hour" in body["message"]
    more = client.post(reverse("estimate"), form(traceroute="on", dns="on")).json()
    assert more["cost"] == 10


def test_estimate_rejects_bad_locations(client):
    r = client.post(reverse("estimate"), form(locations="Berlin,,Tokyo"))
    assert r.status_code == 400 and "empty entry" in r.json()["error"]


def test_scan_requires_confirm_when_over_allowance(client, fake):
    fake.remaining = 4
    r = start(client)
    assert r.status_code == 409
    body = r.json()
    assert body["needs_confirm"] is True and "only 4 are left" in body["reason"] and body["cost"] == 6
    assert fake.posts() == []
    r = start(client, confirm="1")
    assert r.status_code == 202
    wait_done(client, r.json()["id"])
    assert fake.posts()


def test_scan_refused_when_too_many_locations_even_with_confirm(client, fake):
    many = ",".join(f"City{i}" for i in range(51))
    r = start(client, locations=many, confirm="1")
    assert r.status_code == 400 and "at most 50" in r.json()["error"]
    assert fake.posts() == []


def test_scan_refused_when_credits_zero_even_with_confirm(client, settings, fake):
    settings.GLOBALPING_TOKEN = TOKEN
    fake.remaining = 4
    fake.credits = 0
    r = start(client, confirm="1")
    assert r.status_code == 400 and "no Globalping credits" in r.json()["error"]
    assert fake.posts() == []


def test_scan_rejects_bad_url(client, fake):
    r = client.post(reverse("scan"), form(url="ftp://x"))
    assert r.status_code == 400 and r.json()["error"] == "Only http and https addresses can be tested."
    assert fake.posts() == []


# --- in-flight state --------------------------------------------------------------------------

def test_finished_result_removed_from_server_after_first_fetch(client):
    r = start(client)
    scan_id = r.json()["id"]
    done = wait_done(client, scan_id)
    assert done.json()["status"] == "done"
    assert done.json()["result"]["schema_version"] == 1
    assert len(state.get()) == 0
    again = client.get(reverse("scan_status", args=[scan_id]))
    assert again.status_code == 404 and "result" not in again.json()


def test_status_reports_progress_lines(client, monkeypatch):
    gate = threading.Event()

    def slow(url, plan, client_, on_progress=None, geo=None):
        on_progress("Testing the first visit from 2 locations")
        gate.wait(5)
        raise ScanError("stop")
    monkeypatch.setattr(views, "run_scan", slow)
    scan_id = start(client).json()["id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        body = client.get(reverse("scan_status", args=[scan_id])).json()
        if body["progress"]:
            break
        time.sleep(0.01)
    assert body["status"] == "running" and body["progress"] == ["Testing the first visit from 2 locations"]
    gate.set()
    assert wait_done(client, scan_id).json()["status"] == "error"


def test_unknown_scan_id_is_404(client):
    assert client.get(reverse("scan_status", args=["nope"])).status_code == 404


def test_state_discards_finished_entries_after_120_seconds():
    now = [1000.0]
    st = state.ScanState(max_concurrent=2, clock=lambda: now[0])
    entry = st.create()
    st.finish(entry.id, {"x": 1})
    now[0] += 119
    assert st.get(entry.id) is not None
    now[0] += 2
    assert st.get(entry.id) is None and len(st) == 0


def test_state_discards_stuck_scans():
    now = [0.0]
    st = state.ScanState(max_concurrent=1, clock=lambda: now[0])
    entry = st.create()
    assert st.create() is None  # busy
    now[0] += state.STUCK_AFTER + 1
    assert st.get(entry.id) is None
    assert st.create() is not None


def test_state_take_is_one_shot():
    st = state.ScanState(max_concurrent=1)
    entry = st.create()
    st.finish(entry.id, {"x": 1})
    assert st.take(entry.id).result == {"x": 1}
    assert st.take(entry.id) is None


def test_concurrent_scan_limit(client, settings, monkeypatch):
    settings.MAX_CONCURRENT_SCANS = 1
    state.reset()
    gate = threading.Event()

    def slow(url, plan, client_, on_progress=None, geo=None):
        gate.wait(5)
        raise ScanError("stop")
    monkeypatch.setattr(views, "run_scan", slow)
    first = start(client)
    assert first.status_code == 202
    second = start(client)
    assert second.status_code == 503
    assert second.json()["error"] == "The scanner is busy with other scans. Please try again in a minute."
    gate.set()
    wait_done(client, first.json()["id"])
    assert start(client).status_code == 202
    gate.set()


def test_scan_error_text_not_echoed(client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def boom(*a, **k):
        raise ScanError("The test could not be started: secret-site.example not allowed")
    monkeypatch.setattr(views, "run_scan", boom)
    done = wait_done(client, start(client).json()["id"])
    assert done.json() == {"status": "error", "error": START_FAILED, "progress": []}
    assert "secret-site" not in done.content.decode() and "secret-site" not in caplog.text


def test_unexpected_error_fixed_message(client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def boom(*a, **k):
        raise KeyError("secret-target.example " + TOKEN)
    monkeypatch.setattr(views, "run_scan", boom)
    done = wait_done(client, start(client).json()["id"])
    assert done.json()["error"] == "The scan failed unexpectedly."
    assert "secret-target" not in caplog.text and TOKEN not in caplog.text
    assert "KeyError" in caplog.text


def test_unsafe_endpoint_refused_before_any_request(client, settings, fake):
    settings.GLOBALPING_TOKEN = TOKEN
    settings.GLOBALPING_BASE_URL = "https://evil.example/v1"
    page = client.get(reverse("index")).content.decode()
    assert "Refusing to send a Globalping key to a non-default, non-local endpoint." in page
    r = start(client)
    assert r.status_code == 503 and TOKEN not in r.content.decode()
    assert client.post(reverse("estimate"), form()).status_code == 503


def test_logs_contain_no_target(client, caplog):
    caplog.set_level(logging.DEBUG)
    result = run_full_scan(client, traceroute="on", dns="on")
    post_json(client, reverse("render"), {"result": result, "limits": {"max_ttfb": 1}})
    for fmt in ("html", "json", "md"):
        post_json(client, reverse("export", args=[fmt]), {"result": result})
    post_json(client, reverse("export", args=["json"]), {"result": {"schema_version": 2}})
    assert caplog.records  # logging was really captured (httpx logs each request)
    assert "secret-target" not in caplog.text
    assert "172.66.147.243" not in caplog.text  # a result value (answering IP)


# --- render, pass/fail and the map ------------------------------------------------------------

def test_render_builds_fragment_from_posted_json(client):
    result = run_full_scan(client)
    r = post_json(client, reverse("render"), {"result": result, "limits": {}})
    assert r.status_code == 200 and r["Content-Type"].startswith("text/html")
    html = r.content.decode()
    assert '<svg class="sw-map"' in html and "Berlin" in html and "Tokyo" in html
    assert 'data-row="0"' in html
    assert "No limits set" in html
    assert len(state.get()) == 0


def test_pass_fail_line_mirrors_cli(client):
    body = {"result": report_dict(), "limits": {"max_ttfb": 10, "max_blocked": 0}}
    html = post_json(client, reverse("render"), body).content.decode()
    assert "Pass/fail: FAILED (limits: max first byte 10 ms, max blocked 0)" in html
    assert "first byte 40 ms from Tokyo, JP is over the limit of 10 ms" in html
    ok = post_json(client, reverse("render"), {"result": report_dict(), "limits": {"max_ttfb": 1000}})
    assert "Pass/fail: passed (limits: max first byte 1000 ms)" in ok.content.decode()
    nothing = report_dict()
    nothing["locations"] = nothing["locations"][1:]
    html = post_json(client, reverse("render"), {"result": nothing, "limits": {"max_ttfb": 5}}).content.decode()
    assert "Pass/fail: could not test, so no limit was judged" in html


@pytest.mark.parametrize("limits", [{"max_ttfb": 0}, {"max_load": -1}, {"max_blocked": -1},
                                    {"max_ttfb": "fast"}, {"max_blocked": 1.5}, "x"])
def test_render_rejects_bad_limits(client, limits):
    assert post_json(client, reverse("render"), {"result": report_dict(), "limits": limits}).status_code == 400


def test_map_uses_fast_and_slow_settings(client, settings):
    settings.FAST_MS, settings.SLOW_MS = 1, 2
    html = post_json(client, reverse("render"), {"result": report_dict(), "limits": {}}).content.decode()
    assert 'fill="var(--slow)"' in html and 'fill="var(--fast)"' not in html
    settings.FAST_MS, settings.SLOW_MS = 1000, 2000
    html = post_json(client, reverse("render"), {"result": report_dict(), "limits": {}}).content.decode()
    assert 'fill="var(--fast)"' in html


def test_hostile_reason_is_escaped_in_page(client, fake):
    bad = fixture("http_badcert")
    bad["results"][0]["result"]["tls"]["error"] = HOSTILE
    bad["results"][0]["probe"]["city"] = "<img src=x onerror=alert(2)>"
    fake.by_type["http"] = bad
    result = run_full_scan(client, locations="Berlin")
    assert result["locations"][0]["status"] == "blocked"
    html = post_json(client, reverse("render"), {"result": result, "limits": {}}).content.decode()
    assert "<script>alert(1)" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)" in html
    hostile = report_dict()
    hostile["target"] = "https://example.com/" + HOSTILE
    hostile["locations"][1]["reason"] = HOSTILE
    html = post_json(client, reverse("render"), {"result": hostile, "limits": {}}).content.decode()
    assert "<script>" not in html


# --- export -----------------------------------------------------------------------------------

def test_export_does_not_keep_posted_result(client):
    result = report_dict(target="https://only-in-the-browser.example/")
    for fmt, ctype in (("json", "application/json"), ("md", "text/markdown"), ("html", "text/html")):
        r = post_json(client, reverse("export", args=[fmt]), {"result": result})
        assert r.status_code == 200 and r["Content-Type"].startswith(ctype)
        assert f'filename="speedward-report.{fmt}"' in r["Content-Disposition"]
        assert "no-store" in r["Cache-Control"]
        assert "only-in-the-browser.example" in r.content.decode()
    assert len(state.get()) == 0
    assert client.get(reverse("export", args=["json"])).status_code == 405
    assert not [v for v in vars(views).values() if isinstance(v, (dict, list)) and "only-in-the-browser" in str(v)]


def test_export_json_round_trips(client):
    result = report_dict()
    r = post_json(client, reverse("export", args=["json"]), {"result": result})
    assert json.loads(r.content) == result


@pytest.mark.parametrize("body", [
    b"not json", b"[]", b'{"result": 5}', b'{"result": {"schema_version": 2}}', b"{}",
    b'{"result": {"schema_version": 1, "target": NaN}}',
])
def test_malformed_export_body_returns_400(client, body):
    for url in (reverse("export", args=["json"]), reverse("render")):
        r = client.post(url, data=body, content_type="application/json")
        assert r.status_code == 400, (url, body)
        text = r.content.decode()
        assert "Traceback" not in text and "File \"" not in text
        assert r.json()["error"] == "That result could not be read. Run the scan again or load a valid export."


def test_malformed_location_field_returns_400(client):
    bad = copy.deepcopy(report_dict())
    bad["locations"][0]["stages"]["first_byte"] = "fast"
    assert post_json(client, reverse("export", args=["md"]), {"result": bad}).status_code == 400


def test_unknown_export_format_404(client):
    assert post_json(client, "/export/pdf", {"result": report_dict()}).status_code == 404


# --- static script ----------------------------------------------------------------------------

def test_results_js_never_uses_inner_html():
    js = STATIC_JS.read_text()
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js
    assert "speedward:result" in js and "sessionStorage" in js and "beforeunload" in js


def test_wsgi_requires_secret_key_outside_debug():
    from django.core.exceptions import ImproperlyConfigured

    from speedward.web.settings import require_secret_key
    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        require_secret_key({"DEBUG": "0"})
    require_secret_key({"DEBUG": "0", "SECRET_KEY": "x"})
    require_secret_key({"DEBUG": "1"})


def test_oversized_number_returns_400_without_traceback(client, caplog):
    caplog.set_level(logging.DEBUG)
    big = "1" + "0" * 400
    for path in (("locations", 0, "location", "lat"), ("locations", 0, "stages", "first_byte")):
        d = report_dict()
        d[path[0]][path[1]][path[2]][path[3]] = "BIG"
        body = json.dumps({"result": d}).replace('"BIG"', big)
        for url in (reverse("render"), reverse("export", args=["json"])):
            r = client.post(url, data=body, content_type="application/json")
            assert r.status_code == 400, (url, path)
            assert "Traceback" not in r.content.decode()
    assert not [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert "Traceback" not in caplog.text


def test_x_frame_options_setting_is_deny(client):
    from django.conf import settings as dj_settings
    assert dj_settings.X_FRAME_OPTIONS == "DENY"
    assert client.get(reverse("index"))["X-Frame-Options"] == "DENY"


BAD_LIMIT_VALUES = [10**400, -5, "abc"]
LIMIT_KEYS = ["max_ttfb", "max_load", "max_blocked", "max_page_weight"]


@pytest.mark.parametrize("key", LIMIT_KEYS)
@pytest.mark.parametrize("value", BAD_LIMIT_VALUES, ids=["huge", "negative", "text"])
def test_bad_limit_on_render_is_400(client, caplog, key, value):
    caplog.set_level(logging.DEBUG)
    body = json.dumps({"result": report_dict(), "limits": {key: "V"}}).replace('"V"', str(value) if value != "abc" else '"abc"')
    r = client.post(reverse("render"), data=body, content_type="application/json")
    assert r.status_code == 400 and "Traceback" not in r.content.decode()
    assert r.json()["error"] == views.BAD_LIMITS
    assert not [x for x in caplog.records if x.levelno >= logging.ERROR]


@pytest.mark.parametrize("route", ["scan", "estimate"])
@pytest.mark.parametrize("key", LIMIT_KEYS)
@pytest.mark.parametrize("value", BAD_LIMIT_VALUES, ids=["huge", "negative", "text"])
def test_bad_limit_on_form_routes_is_400(client, caplog, fake, route, key, value):
    caplog.set_level(logging.DEBUG)
    r = client.post(reverse(route), form(**{key: str(value)}))
    assert r.status_code == 400 and "Traceback" not in r.content.decode()
    assert r.json()["error"] == views.BAD_LIMITS
    assert fake.posts() == []
    assert not [x for x in caplog.records if x.levelno >= logging.ERROR]


@pytest.mark.parametrize("limits", [{"max_ttfb": 0.5}, {"max_load": 0}, {"max_page_weight": 0},
                                    {"max_blocked": True}, {"max_ttfb": [1]}, {"max_blocked": 10**10}])
def test_render_rejects_out_of_range_limits(client, limits):
    r = post_json(client, reverse("render"), {"result": report_dict(), "limits": limits})
    assert r.status_code == 400


@pytest.mark.parametrize("data", [{"max_ttfb": "NaN"}, {"max_load": "inf"}, {"max_blocked": "1.5"},
                                  {"max_ttfb": "1" + "0" * 5000}])
def test_scan_rejects_non_finite_form_limits(client, fake, data):
    assert client.post(reverse("scan"), form(**data)).status_code == 400
    assert fake.posts() == []


def test_valid_form_limits_accepted(client):
    r = client.post(reverse("estimate"), form(max_ttfb="800", max_load="", max_blocked="0"))
    assert r.status_code == 200


@pytest.mark.parametrize("key, label, unit", [("max_ttfb", "max first byte", " ms"), ("max_load", "max load", " ms"),
                                              ("max_page_weight", "max page weight", " bytes"),
                                              ("max_blocked", "max blocked", "")])
@pytest.mark.parametrize("value", [1234567, 1000000000])  # 7 digits; 10 digits (the 10**9 cap)
def test_pass_fail_line_prints_large_limits_exactly(client, key, label, unit, value):
    html = post_json(client, reverse("render"), {"result": report_dict(), "limits": {key: value}}).content.decode()
    assert f"(limits: {label} {value}{unit})" in html
    assert "e+" not in re.sub(r"<svg.*?</svg>", "", html, flags=re.S)


def test_pass_fail_line_prints_fractional_limit(client):
    html = post_json(client, reverse("render"), {"result": report_dict(), "limits": {"max_ttfb": 250.5}}).content.decode()
    assert "Pass/fail: passed (limits: max first byte 250.5 ms)" in html


def test_map_legend_prints_thresholds_exactly(client, settings):
    settings.FAST_MS, settings.SLOW_MS = 1234567.0, 5000000.0
    html = post_json(client, reverse("render"), {"result": report_dict(), "limits": {}}).content.decode()
    assert "green up to 1234567 ms" in html and "red from 5000000 ms" in html


# --- allowance timer ---------------------------------------------------------------------------

def test_web_estimate_returns_reset_and_bar_parts(client, fake):
    fake.reset = 2220
    fake.remaining = 212
    body = client.post(reverse("estimate"), form()).json()
    assert body["cost"] == 6 and body["view"]["reset_seconds"] == 2220
    view = body["view"]
    assert view["reset_in_minutes"] == 37 and view["state"] == "ok" and view["keyless"] is True
    assert (view["used_before"], view["this_scan"], view["left_after"]) == (38, 6, 206)
    assert view["caption"] == "38 used already · 6 for this scan · 206 left afterwards"
    assert view["text"].startswith("212 of 250 tests left this hour (no key) · resets in 37 min")
    # existing keys stay
    assert {"fits", "reason", "refused", "message"} <= set(body)


def test_web_estimate_unknown_reset_has_no_number(client, fake):
    fake.omit_reset = True
    view = client.post(reverse("estimate"), form()).json()["view"]
    assert view["reset_in_minutes"] is None and view["reset_seconds"] is None
    assert "reset time unknown" in view["text"]


def test_web_estimate_ignores_posted_numbers(client, fake):
    fake.remaining = 100
    view = client.post(reverse("estimate"), form(remaining="1", reset="5", cost="999")).json()["view"]
    assert view["tests_left"] == 100 and view["this_scan"] == 6


def test_index_status_strip_shows_countdown(client, fake):
    fake.reset = 2220
    fake.remaining = 212
    body = client.get(reverse("index")).content.decode()
    m = re.search(r'<p[^>]*id="allowance-strip"[^>]*>(.*?)</p>', body, re.S)
    assert m, "status strip missing"
    assert "212 of 250 tests left this hour" in m.group(1) and "resets in 37 min" in m.group(1)
    assert "A key is free up to a limit" in m.group(1)


def test_index_strip_unreachable_has_no_numbers(client, fake):
    fake.limits_status = 500
    body = client.get(reverse("index")).content.decode()
    m = re.search(r'<p[^>]*id="allowance-strip"[^>]*>(.*?)</p>', body, re.S)
    assert "unknown" in m.group(1) and "reset time unknown" in m.group(1)


def test_js_builds_strip_with_text_content_only():
    js = STATIC_JS.read_text()
    assert "allowance-strip" in js and "speedward:result" in js
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
