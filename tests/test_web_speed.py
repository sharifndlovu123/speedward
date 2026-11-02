"""Is it fast? in the web UI: the form, the estimate (per check and in total), the scan, the panel and the
notes on the diagram. Globalping is FakeGlobalping (see tests/test_web.py for the shared fixture)."""
import json

from django.urls import reverse

from tests.fake_globalping import on_probes
from tests.test_runner_speed import speed_recorded
from tests.test_web import fake, form, run_full_scan, start  # noqa: F401 - fake is an autouse fixture


def test_index_lists_the_five_speed_checks_with_their_cost(client):
    page = client.get(reverse("index")).content.decode()
    assert "Is it fast? (where the time goes)" in page
    for value, label, per in (("consistency", "Repeat 5 times (consistency)", "+4"),
                              ("http2", "HTTP/2 against HTTP/1.1", "+1"),
                              ("compression", "Compression (gzip, brotli)", "+1"),
                              ("dnstrace", "DNS delegation trace", "+1"),
                              ("mtr", "Path loss and jitter (mtr)", "+1")):
        assert f'name="check" value="{value}"> <span><b>{label}</b> <span class="mute">{per} per place' in page
    assert "+1 per place · size on the wire is not available" in page
    assert "+1 per place · where a slow lookup spends its time" in page
    assert "checked" not in page.split('value="consistency"')[1][:20]  # opt-in


def test_estimate_counts_speed_checks_per_part_and_in_total(client):
    body = client.post(reverse("estimate"), form(checks="consistency,mtr,ipv6")).json()
    assert body["cost"] == 2 * (3 + 1 + 4 + 1)
    assert [(p["key"], p["per_place"], p["cost"]) for p in body["parts"]] == [
        ("base", 3, 6), ("ipv6", 1, 2), ("consistency", 4, 8), ("mtr", 1, 2)]
    assert "Repeat 5 times (consistency) +4 per place = 8" in body["breakdown"]
    assert body["breakdown"].endswith("2 places: about 18 tests")
    assert body["checks_line"] == "3 checks on · 2 places"


def test_over_allowance_with_speed_checks_needs_confirmation(client, fake):  # noqa: F811
    fake.remaining = 13
    r = start(client, checks="consistency,http2")
    assert r.status_code == 409 and r.json()["needs_confirm"] and r.json()["cost"] == 16
    assert fake.posts() == []


def test_scan_with_speed_checks_renders_the_panel(client, fake):  # noqa: F811
    fake.by_kind = {"http2": on_probes(speed_recorded("http2_alpn_refused"))}
    fake.by_type["mtr"] = on_probes(speed_recorded("mtr_icmp_far"), type="mtr")
    result = run_full_scan(client, checks="http2,mtr")
    assert result["options"]["checks"] == ["http2", "mtr"]
    assert "reach" not in result and result["speed_summary"]["checks"] == ["http2", "mtr"]
    r = client.post(reverse("render"), json.dumps({"result": result}), content_type="application/json")
    page = r.content.decode()
    assert r.status_code == 200
    assert 'class="sp-head"' in page and "data-sp-open" in page
    assert "HTTP/2: not enough places for a conclusion" in page  # two places
    assert "<b>not offered</b>" in page and "<b>no bad hop</b>" in page
    assert "+82 ms at hop 9 (long link)" in page  # the per-place note on the Connect block
    assert page.index('class="speedchecks"') < page.index('class="bottleneck"')


def test_render_refuses_a_forged_speed_summary(client, fake):  # noqa: F811
    result = run_full_scan(client, checks="compression")
    result["speed_summary"]["overall"][0]["verdict"] = "all"
    r = client.post(reverse("render"), json.dumps({"result": result}), content_type="application/json")
    assert r.status_code == 400


def test_render_of_an_old_report_has_no_speed_panel(client):
    result = run_full_scan(client)
    page = client.post(reverse("render"), json.dumps({"result": result}),
                       content_type="application/json").content.decode()
    assert "Is it fast?" not in page
    assert "bn-n bn-extra" not in page


def test_exports_carry_the_speed_checks(client, fake):  # noqa: F811
    result = run_full_scan(client, checks="compression")
    body = json.dumps({"result": result})
    md = client.post(reverse("export", args=["md"]), body, content_type="application/json").content.decode()
    assert "## Is it fast? Speed checks" in md and "| Place | Compression |" in md
    assert "Page size on the wire is not available" in md
    js = json.loads(client.post(reverse("export", args=["json"]), body, content_type="application/json").content)
    assert js["speed_summary"]["checks"] == ["compression"]
    html = client.post(reverse("export", args=["html"]), body, content_type="application/json").content.decode()
    assert "Is it fast? Speed checks" in html


def test_estimate_leaves_out_checks_the_address_cannot_use(client):
    # The page never sends the address to /estimate, only what it is.
    full = client.post(reverse("estimate"), form(url="", checks="http2,dnstrace,ipv6")).json()
    assert full["cost"] == 2 * (3 + 3)
    plain = client.post(reverse("estimate"), form(url="", checks="http2,dnstrace,ipv6", plain="1")).json()
    assert plain["cost"] == 2 * (3 + 2)
    assert "HTTP/2 against HTTP/1.1: not run for this address = 0" in plain["breakdown"]
    ip = client.post(reverse("estimate"), form(url="", checks="http2,dnstrace,ipv6", ip="1")).json()
    assert ip["cost"] == 2 * (3 + 1)


def test_scan_cost_follows_the_posted_address_and_matches_what_is_spent(client, fake):  # noqa: F811
    fake.remaining = 7  # the default scan costs 6; HTTP/2 would make it 8, but http:// skips it
    result = run_full_scan(client, url="http://example.com/", checks="http2")
    assert {x["status"] for x in result["speed"]} == {"not_run"}
    assert [p["type"] for p in fake.posts()] == ["http", "http", "ping"]


def test_a_posted_address_wins_over_the_flags(client):
    body = client.post(reverse("estimate"), form(url="https://example.com/", checks="http2", plain="1")).json()
    assert body["cost"] == 2 * (3 + 1)


def test_estimate_and_hints_count_one_mtr_for_network_path_and_the_mtr_check(client):
    body = client.post(reverse("estimate"), form(checks="mtr", traceroute="on")).json()
    assert body["cost"] == 2 * (3 + 1)
    assert [(p["key"], p["cost"]) for p in body["parts"]] == [("base", 6), ("traceroute", 2), ("mtr", 0)]
    assert "Path loss and jitter (mtr): shares the Network path test = 0" in body["breakdown"]
    page = client.get(reverse("index")).content.decode()
    assert "ticking both runs two" not in page
    assert page.count("shares one test with") == 2
