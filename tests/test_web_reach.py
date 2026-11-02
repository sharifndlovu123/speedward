"""More checks in the web UI: the form, the estimate (per check and in total), the scan, the grid.
Globalping is FakeGlobalping (see tests/test_web.py for the shared fixture)."""
import json

from django.urls import reverse

from tests.fake_globalping import on_probes, recorded
from tests.test_web import fake, form, run_full_scan, start  # noqa: F401 - fake is an autouse fixture


def test_index_lists_the_four_reachability_checks_with_their_cost(client):
    page = client.get(reverse("index")).content.decode()
    for value, label, per in (("ipv6", "Over IPv4 and IPv6", "+1"), ("http", "Over HTTP and HTTPS", "+1"),
                              ("dns", "Through different DNS resolvers", "+2"),
                              ("port", "Port reachable (TCP 443)", "+1")):
        assert f'name="check" value="{value}"' in page
        assert label in page and f"{per} per place" in page
    assert 'name="check" value="ipv6" checked' not in page  # opt-in: nothing ticked by default


def test_estimate_counts_the_checks_per_part_and_in_total(client):
    body = client.post(reverse("estimate"), form(checks="ipv6,dns")).json()
    assert body["cost"] == 2 * (3 + 1 + 2)
    assert body["parts"] == [{"key": "base", "per_place": 3, "cost": 6}, {"key": "ipv6", "per_place": 1, "cost": 2},
                             {"key": "dns", "per_place": 2, "cost": 4}]
    assert body["breakdown"].endswith("2 places: about 12 tests")
    assert body["checks_line"] == "2 checks on · 2 places"
    assert "about 12 tests" in body["message"]


def test_estimate_without_checks_is_unchanged(client):
    body = client.post(reverse("estimate"), form()).json()
    assert body["cost"] == 6 and body["checks_line"] == "no extra checks · 2 places"


def test_unknown_check_is_refused_without_echo(client, fake):  # noqa: F811
    for route in ("estimate", "scan"):
        r = client.post(reverse(route), form(checks="ipv6,<b>x</b>"))
        assert r.status_code == 400 and "<b>" not in r.content.decode()
    assert fake.posts() == []


def test_over_allowance_with_checks_needs_confirmation(client, fake):  # noqa: F811
    fake.remaining = 7
    r = start(client, checks="port")
    assert r.status_code == 409 and r.json()["needs_confirm"] and r.json()["cost"] == 8
    assert fake.posts() == []


def test_scan_with_checks_renders_the_grid_and_legend(client, fake):  # noqa: F811
    fake.by_kind = {"ipv6": on_probes(recorded("http_v6_no_aaaa")),
                    "port": on_probes(recorded("ping_tcp_ok"), type="ping")}
    result = run_full_scan(client, checks="ipv6,port")
    assert result["options"]["checks"] == ["ipv6", "port"]
    assert [x["status"] for x in result["reach"] if x["check"] == "ipv6"] == ["blocked", "blocked"]
    html = client.post(reverse("render"), json.dumps({"result": result}), content_type="application/json")
    page = html.content.decode()
    assert html.status_code == 200
    assert 'class="rc-head"' in page and "data-rc-open" in page
    assert "Not enough places for a conclusion" in page  # two places
    assert "<th scope=\"col\">HTTPS v6</th>" in page and "no IPv6 address (AAAA record) for the name" in page
    assert "each dot shows the worst result" in page
    assert page.index('class="reach"') < page.index('class="bottleneck"')


def test_render_of_a_report_without_checks_has_no_grid(client):
    result = run_full_scan(client)
    page = client.post(reverse("render"), json.dumps({"result": result}),
                       content_type="application/json").content.decode()
    assert "Who can reach it" not in page and "worst result" not in page


def test_render_refuses_a_forged_grid(client, fake):  # noqa: F811
    fake.by_kind = {"port": on_probes(recorded("ping_tcp_ok"), type="ping")}
    result = run_full_scan(client, checks="port")
    result["reach_grid"]["verdict"] = "all_reached"
    r = client.post(reverse("render"), json.dumps({"result": result}), content_type="application/json")
    assert r.status_code == 400


def test_exports_carry_the_grid(client, fake):  # noqa: F811
    fake.by_kind = {"port": on_probes(recorded("ping_tcp_ok"), type="ping")}
    result = run_full_scan(client, checks="port")
    body = json.dumps({"result": result})
    md = client.post(reverse("export", args=["md"]), body, content_type="application/json").content.decode()
    assert "## Who can reach it" in md and "| Place | HTTPS v4 | Port 443 |" in md
    js = json.loads(client.post(reverse("export", args=["json"]), body, content_type="application/json").content)
    assert js["reach_grid"]["columns"] == ["main", "port"]
