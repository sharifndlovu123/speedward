"""More checks and 'Who can reach it' in the real page: the cost per check before any scan, the finding in
the summary, the grid in the drawer, the worst-result map dot, axe (light and dark, desktop and phone),
reflow, reduced motion, and an old saved result with no grid. FakeGlobalping only; results recorded."""
import copy
import json

import pytest
from playwright.sync_api import expect

from e2e.helpers import pick, start
from e2e.test_a11y import CLIPPED, check, setup
from e2e.test_bottleneck import four_places  # noqa: F401 - fixture
from e2e.test_session_storage import KEY, TARGET
from tests.fake_globalping import recorded


def _like(doc, results, type):
    out = copy.deepcopy(doc)
    out["type"] = type
    for item, res in zip(out["results"], results, strict=True):
        item["result"] = copy.deepcopy(res)
    return out


@pytest.fixture
def reach_kinds(fake_globalping, four_places):  # noqa: F811
    """Berlin, Tokyo, London and Seoul (blocked in the main test). Tokyo has no IPv6; Seoul's port is closed."""
    v6, no6 = recorded("http_v6_ok"), recorded("http_v6_no_aaaa")
    port, closed = recorded("ping_tcp_ok"), recorded("ping_tcp_closed")
    plain, dns = recorded("http_plain_redirect"), recorded("dns_resolver_ok")
    fake_globalping.by_kind = {
        "ipv6": _like(four_places, [v6, no6, v6, no6], "http"),
        "plain_http": _like(four_places, [plain] * 4, "http"),
        "dns_1.1.1.1": _like(four_places, [dns] * 4, "dns"),
        "dns_8.8.8.8": _like(four_places, [dns] * 4, "dns"),
        "port": _like(four_places, [port, port, port, closed], "ping"),
    }
    yield
    fake_globalping.by_kind = {}


def tick(page, *names):
    for n in names:
        page.check(f'input[name="check"][value="{n}"]')


def scan(page, base, *checks):
    page.goto(base + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo", "London", "Seoul")
    tick(page, *checks)
    start(page)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)


def test_ticking_checks_updates_the_cost_before_any_scan(page, live_site, fake_globalping, watch):
    page.goto(live_site + "/")
    pick(page, "Berlin", "Tokyo")
    expect(page.locator("#start")).to_have_attribute("data-cost", "ready")
    expect(page.locator("#estimate")).to_contain_text("about 6 tests")
    before = len(fake_globalping.posts())
    tick(page, "ipv6", "dns")
    expect(page.locator("#start")).to_have_attribute("data-cost", "ready")
    expect(page.locator("#estimate")).to_contain_text("about 12 tests")
    total = page.locator("#mc-total")
    expect(total).to_be_visible()
    expect(total).to_contain_text("2 checks on · 2 places")
    expect(total).to_contain_text("Over IPv4 and IPv6 +1 per place = 2")
    expect(total).to_contain_text("Through different DNS resolvers +2 per place = 4")
    expect(total).to_contain_text("about 12 tests")
    assert len(fake_globalping.posts()) == before  # nothing was measured to show the cost


def test_over_the_allowance_with_checks_asks_to_confirm(page, live_site, fake_globalping, watch):
    fake_globalping.remaining = 7
    fake_globalping.credits = 50  # with no credits the scan is refused outright instead
    try:
        page.goto(live_site + "/")
        pick(page, "Berlin", "Tokyo")
        expect(page.locator("#confirm-wrap")).to_be_hidden()
        tick(page, "port")
        expect(page.locator("#confirm-wrap")).to_be_visible()
        expect(page.locator("#over")).to_contain_text("needs about 8 tests but only 7 are left")
    finally:
        fake_globalping.remaining = None
        fake_globalping.credits = None


def test_finding_in_summary_grid_in_drawer_and_worst_dot(page, live_site, reach_kinds, watch):
    scan(page, live_site, "ipv6", "http", "dns", "port")
    head = page.locator("#results-summary .rc-head")
    expect(head).to_be_visible()
    expect(head).to_contain_text("The timings indicate: ")
    expect(head).to_contain_text("2 of 4 places cannot reach the site over IPv6 (Tokyo, JP, Seoul, KR)")
    expect(head).to_contain_text("Check: ")
    order = page.evaluate("[...document.querySelectorAll('#results-summary > *')].map(e => e.className)")
    assert order[:3] == ["figures", "bn-head", "rc-head"]

    page.get_by_role("button", name="See who can reach it").click()
    expect(page.locator("#drawer")).to_be_visible()
    expect(page.locator("#results-body .rc-title")).to_be_focused()
    grid = page.locator("#results-body table.rc-grid")
    expect(grid.locator("thead th")).to_have_text(["Place", "HTTPS v4", "HTTPS v6", "HTTP", "DNS", "Port 443"])
    tokyo = grid.locator("tbody tr[data-row='1']")
    expect(tokyo.locator("td").nth(1)).to_contain_text("○ blocked no IPv6 address (AAAA record) for the name")
    expect(tokyo.locator("td").nth(2)).to_contain_text("● → HTTPS")
    seoul = grid.locator("tbody tr[data-row='3']")
    expect(seoul.locator("td").nth(4)).to_contain_text("○ closed")
    # The grid sits above the diagram.
    assert page.evaluate("""() => { const a = document.querySelector('#results-body .reach'),
        b = document.querySelector('#results-body .bottleneck');
        return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING); }""")

    dot = page.locator("#results-map circle.sw-dot[data-row='1']")
    expect(dot).to_have_class("sw-dot sw-part")
    expect(dot.locator("title")).to_contain_text("blocked: HTTPS v6")
    expect(page.locator("#results-summary .legend")).to_contain_text("each dot shows the worst result")


def test_reduced_motion_stops_the_finding_wipe(page, live_site, reach_kinds, watch):
    page.emulate_media(reduced_motion="no-preference")
    scan(page, live_site, "ipv6")
    box = page.locator("#results-summary .rc-finding")
    assert box.evaluate("e => getComputedStyle(e).animationName") == "rc-wipe"
    page.emulate_media(reduced_motion="reduce")
    assert box.evaluate("e => getComputedStyle(e).animationName") == "none"


@pytest.mark.parametrize("size", ["desktop", "phone"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_with_checks_and_grid(page, live_site, reach_kinds, watch, scheme, size):
    setup(page, scheme, size)
    page.goto(live_site + "/")
    tick(page, "ipv6", "http", "dns", "port")
    expect(page.locator("#mc-total")).to_be_visible()
    check(page, f"picker with checks {scheme} {size}")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo", "London", "Seoul")
    start(page)
    expect(page.locator("#results-summary .rc-head")).to_be_visible(timeout=30_000)
    check(page, f"results with finding {scheme} {size}")
    page.get_by_role("button", name="See who can reach it").click()
    expect(page.locator("#results-body table.rc-grid")).to_be_visible()
    check(page, f"grid {scheme} {size}")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_on_the_exported_grid(page, scheme):
    from speedward.reports import render
    from tests.test_reach_reports import mockup_report
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.set_content(render(mockup_report(), "html"))
    expect(page.locator("table.rc-grid")).to_be_visible()
    check(page, f"export grid {scheme}")


@pytest.mark.parametrize("size", [{"width": 320, "height": 640}, {"width": 640, "height": 400}],
                         ids=["320", "zoom200"])
def test_reflow_with_checks_and_grid(page, live_site, reach_kinds, watch, size):
    page.set_viewport_size(size)
    page.emulate_media(reduced_motion="reduce")
    page.goto(live_site + "/")
    tick(page, "ipv6", "http", "dns", "port")
    expect(page.locator("#mc-total")).to_be_visible()
    assert page.evaluate(CLIPPED) == []
    scan(page, live_site, "ipv6", "http", "dns", "port")
    assert page.evaluate(CLIPPED) == []
    page.click("#drawer-toggle")
    assert page.evaluate(CLIPPED) == []


def test_ticked_checks_come_back_with_saved_results(page, live_site, reach_kinds, watch):
    scan(page, live_site, "port")
    page.reload()
    expect(page.locator("#saved-banner")).to_be_visible()
    expect(page.locator('input[name="check"][value="port"]')).to_be_checked()
    expect(page.locator('input[name="check"][value="ipv6"]')).not_to_be_checked()
    expect(page.locator("#banner-run-again")).to_have_text("Run again (~16 tests)")


def test_an_old_saved_result_loads_with_no_grid(page, live_site, watch):
    page.goto(live_site + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    start(page)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    saved = json.loads(page.evaluate(f"sessionStorage.getItem({KEY!r})"))
    assert "reach" not in saved["result"] and "reach_grid" not in saved["result"]
    page.reload()
    expect(page.locator("#saved-banner")).to_be_visible()
    expect(page.locator("#results-map svg.sw-map")).to_be_visible()
    expect(page.locator(".rc-head, .reach")).to_have_count(0)
    expect(page.locator("#results-summary .legend")).not_to_contain_text("worst result")
