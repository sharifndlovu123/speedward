"""Is it fast? in the real page: the cost per check before any scan, the findings in the summary, the table in
the drawer, the notes on the "where the time goes" diagram (overall and per place), axe (light and dark,
desktop and phone), reflow, reduced motion, and an old saved result with no panel. FakeGlobalping only;
the answers are trimmed real recordings (tests/fixtures/globalping/speed_recorded.json)."""
import copy
import json

import pytest
from playwright.sync_api import expect

from e2e.helpers import pick, start
from e2e.test_a11y import CLIPPED, check, setup
from e2e.test_bottleneck import four_places  # noqa: F401 - fixture
from e2e.test_reach import _like, tick
from e2e.test_session_storage import KEY, TARGET
from tests.test_runner_speed import speed_recorded

ALL = ("consistency", "http2", "compression", "dnstrace", "mtr")


@pytest.fixture
def speed_kinds(fake_globalping, four_places):  # noqa: F811
    """Berlin, Tokyo, London measured; Seoul blocked in the main test. London's server has no HTTP/2 and
    does not compress; the path and DNS trace answers are the recorded ones."""
    h2, no_h2 = speed_recorded("http2_ok"), speed_recorded("http2_alpn_refused")
    br = speed_recorded("compress_br")
    plain = copy.deepcopy(br)
    del plain["headers"]["content-encoding"]
    seoul = four_places["results"][3]["result"]  # timed out, as in the main test
    fake_globalping.by_kind = {"http2": _like(four_places, [h2, h2, no_h2, seoul], "http"),
                               "compression": _like(four_places, [br, br, plain, seoul], "http")}
    before = dict(fake_globalping.by_type)
    # A scan with every speed check costs 11 tests per place; earlier scans in the session draw down the
    # fake's allowance, so start each test with a full one (no confirmation prompt in the way).
    fake_globalping.remaining = None
    fake_globalping.by_type["dns"] = _like(four_places, [speed_recorded("dns_trace_ok")] * 4, "dns")
    fake_globalping.by_type["mtr"] = _like(four_places, [speed_recorded("mtr_icmp_far")] * 4, "mtr")
    yield
    fake_globalping.by_kind = {}
    fake_globalping.by_type = before
    fake_globalping.remaining = None


def scan(page, base, *checks):
    page.goto(base + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo", "London", "Seoul")
    tick(page, *checks)
    start(page)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)


def test_ticking_speed_checks_updates_the_cost_before_any_scan(page, live_site, fake_globalping, watch):
    page.goto(live_site + "/")
    pick(page, "Berlin", "Tokyo")
    expect(page.locator("#start")).to_have_attribute("data-cost", "ready")
    before = len(fake_globalping.posts())
    expect(page.get_by_text("Is it fast? (where the time goes)")).to_be_visible()
    tick(page, "consistency", "mtr")
    expect(page.locator("#start")).to_have_attribute("data-cost", "ready")
    total = page.locator("#mc-total")
    expect(total).to_contain_text("2 checks on · 2 places")
    expect(total).to_contain_text("Repeat 5 times (consistency) +4 per place = 8")
    expect(total).to_contain_text("Path loss and jitter (mtr) +1 per place = 2")
    expect(page.locator("#estimate")).to_contain_text("about 16 tests")
    assert len(fake_globalping.posts()) == before  # nothing was measured to show the cost


def test_findings_in_summary_table_in_drawer_notes_on_the_diagram(page, live_site, speed_kinds, watch):
    scan(page, live_site, *ALL)
    head = page.locator("#results-summary .sp-head")
    expect(head).to_contain_text("The timings indicate the server wait is steady: 5 runs each in 3 places, no "
                                 "slowest run at 2 times its place's median and 50 ms above it.")
    expect(head).to_contain_text("HTTP/2 is offered in 2 of 3 places; not in London, GB.")
    expect(head).to_contain_text("The first response is not compressed in 1 of 3 places (London, GB).")
    expect(head).to_contain_text("Check: ")
    order = page.evaluate("[...document.querySelectorAll('#results-summary > *')].map(e => e.className)")
    assert order[:3] == ["figures", "bn-head", "sp-head"]

    page.get_by_role("button", name="See the speed checks").click()
    expect(page.locator("#drawer")).to_be_visible()
    expect(page.locator("#results-body .sp-title")).to_be_focused()
    table = page.locator("#results-body table.sp-grid")
    expect(table.locator("thead th")).to_have_text(["Place", "Consistency", "HTTP/2", "Compression", "DNS trace",
                                                    "Path"])
    london = table.locator("tbody tr[data-row='2']")
    expect(london.locator("td").nth(1)).to_contain_text("● not offered HTTP/1.1 only")
    expect(london.locator("td").nth(2)).to_contain_text("● not compressed")
    seoul = table.locator("tbody tr[data-row='3']")
    expect(seoul.locator("td").nth(0)).to_contain_text("○ blocked connection timed out")
    expect(seoul.locator("td").nth(1)).to_contain_text("○ blocked")
    expect(page.locator("#results-body .speedchecks")).to_contain_text("Page size on the wire is not available")

    overall = page.locator('#results-body .bn-view[data-bn-view="all"]')
    expect(overall.locator(".bn-extra")).to_contain_text(["slowest step: TLD", "no bad hop",
                                                          "steady over 5 runs in 3 places",
                                                          "not compressed in 1 of 3 places"])
    london.click()  # a row of the table shows that place's own diagram with its notes
    mine = page.locator('#results-body .bn-view[data-bn-view="2"]')
    expect(mine).to_be_visible()
    expect(mine.locator(".bn-extra")).to_contain_text(["no bad hop; +82 ms at hop 9 (long link)",
                                                       "steady over 5 runs", "not compressed"])


def test_reduced_motion_stops_the_findings_wipe(page, live_site, speed_kinds, watch):
    page.emulate_media(reduced_motion="no-preference")
    scan(page, live_site, "http2")
    box = page.locator("#results-summary .sp-findings")
    assert box.evaluate("e => getComputedStyle(e).animationName") == "sp-wipe"
    page.emulate_media(reduced_motion="reduce")
    assert box.evaluate("e => getComputedStyle(e).animationName") == "none"


@pytest.mark.parametrize("size", ["desktop", "phone"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_with_speed_checks(page, live_site, speed_kinds, watch, scheme, size):
    setup(page, scheme, size)
    page.goto(live_site + "/")
    tick(page, *ALL)
    expect(page.locator("#mc-total")).to_be_visible()
    check(page, f"picker with speed checks {scheme} {size}")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo", "London", "Seoul")
    start(page)
    expect(page.locator("#results-summary .sp-head")).to_be_visible(timeout=30_000)
    check(page, f"results with findings {scheme} {size}")
    page.get_by_role("button", name="See the speed checks").click()
    expect(page.locator("#results-body table.sp-grid")).to_be_visible()
    check(page, f"speed table {scheme} {size}")
    page.locator("#results-body table.sp-grid tbody tr[data-row='1']").click()
    expect(page.locator('#results-body .bn-view[data-bn-view="1"] .bn-extra').first).to_be_visible()
    check(page, f"place diagram with notes {scheme} {size}")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_on_the_exported_panel(page, scheme):
    from speedward.reports import render
    from tests.test_speed_reports import full
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.set_content(render(full(), "html"))
    expect(page.locator("table.sp-grid")).to_be_visible()
    expect(page.locator(".bn-extra").first).to_be_visible()
    check(page, f"export speed panel {scheme}")


@pytest.mark.parametrize("size", [{"width": 320, "height": 640}, {"width": 640, "height": 400}],
                         ids=["320", "zoom200"])
def test_reflow_with_speed_checks(page, live_site, speed_kinds, watch, size):
    page.set_viewport_size(size)
    page.emulate_media(reduced_motion="reduce")
    page.goto(live_site + "/")
    tick(page, *ALL)
    expect(page.locator("#mc-total")).to_be_visible()
    assert page.evaluate(CLIPPED) == []
    scan(page, live_site, *ALL)
    assert page.evaluate(CLIPPED) == []
    page.click("#drawer-toggle")
    assert page.evaluate(CLIPPED) == []


def test_ticked_speed_checks_come_back_with_saved_results(page, live_site, speed_kinds, watch):
    scan(page, live_site, "http2")
    page.reload()
    expect(page.locator("#saved-banner")).to_be_visible()
    expect(page.locator('input[name="check"][value="http2"]')).to_be_checked()
    expect(page.locator('input[name="check"][value="mtr"]')).not_to_be_checked()
    expect(page.locator("#banner-run-again")).to_have_text("Run again (~16 tests)")
    expect(page.locator("#results-summary .sp-head")).to_be_visible()


def test_an_old_saved_result_loads_with_no_speed_panel(page, live_site, watch):
    page.goto(live_site + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    start(page)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    saved = json.loads(page.evaluate(f"sessionStorage.getItem({KEY!r})"))
    assert "speed" not in saved["result"] and "speed_summary" not in saved["result"]
    page.reload()
    expect(page.locator("#saved-banner")).to_be_visible()
    expect(page.locator("#results-map svg.sw-map")).to_be_visible()
    expect(page.locator(".sp-head, .speedchecks, .bn-extra")).to_have_count(0)
