"""'Where the time goes' in the real page: the headline in the summary, the block diagram in the drawer,
per-place redraw from a map dot, a table row or the place list, no diagram for a blocked place, and
WCAG 2.2 AA (axe) in light and dark with the panel on screen. Uses FakeGlobalping only."""
import copy

import pytest
from playwright.sync_api import expect

from e2e.helpers import open_details, pick, start
from e2e.test_a11y import CLIPPED, check, setup
from e2e.test_session_storage import TARGET
from tests.fake_globalping import three_places


@pytest.fixture
def four_places(fake_globalping):
    """Berlin, Tokyo and London measured (London waits on the server), Seoul blocked (timed out)."""
    before = fake_globalping.by_type["http"]
    doc = three_places()
    seoul = copy.deepcopy(doc["results"][1])
    seoul["probe"].update(city="Seoul", country="KR", latitude=37.57, longitude=126.98,
                          tags=["datacenter-network", "u-test-seoul"])
    seoul["result"] = {"status": "failed", "failureSource": "target",
                       "rawOutput": "Request timed out while establishing the TCP connection.",
                       "timings": {"total": 15000, "dns": 0, "tcp": None, "tls": None, "firstByte": None,
                                   "download": None},
                       "statusCode": None, "resolvedAddress": None, "headers": {}, "tls": None}
    doc["results"].append(seoul)
    fake_globalping.by_type["http"] = doc
    yield doc
    fake_globalping.by_type["http"] = before


def scan(page, base):
    page.goto(base + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo", "London", "Seoul")
    start(page)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)


def visible_view(page):
    return page.locator("#results-body .bn-view:not([hidden])")


def test_headline_in_summary_and_diagram_in_drawer(page, live_site, four_places, watch):
    scan(page, live_site)
    head = page.locator("#results-summary .bn-head")
    expect(head).to_be_visible()
    expect(head).to_contain_text("Waiting for the server is the biggest cost.")
    expect(head).to_contain_text("The timings indicate")
    # The headline comes straight after the figures.
    order = page.evaluate("[...document.querySelectorAll('#results-summary > *')].map(e => e.className)")
    assert order[:2] == ["figures", "bn-head"]

    page.get_by_role("button", name="See the request path").click()
    expect(page.locator("#drawer")).to_be_visible()
    expect(page.locator("#results-body .bn-title")).to_be_focused()
    view = visible_view(page)
    expect(view).to_have_count(1)
    expect(view).to_have_attribute("data-bn-view", "all")
    blocks = view.locator(".bn-path > li")
    expect(blocks).to_have_count(7)
    expect(view.locator(".bn-block.bn-biggest")).to_contain_text("4 · Server wait · biggest block")
    expect(view.locator(".bn-block.bn-edge")).to_contain_text("seen, not timed")
    expect(page.locator("#results-body .bn-foot")).to_contain_text("1 place left out of every figure above: 1 blocked")


def test_a_dot_a_row_and_the_place_list_redraw_the_diagram(page, live_site, four_places, watch):
    scan(page, live_site)
    page.locator("#results-map circle.sw-dot[data-row='1']").click()  # Tokyo
    expect(visible_view(page)).to_have_attribute("data-bn-view", "1")
    expect(visible_view(page).locator(".bn-cap")).to_contain_text("Tokyo")
    expect(page.locator("[data-bn-showing]")).to_have_text("Tokyo, JP")
    expect(page.locator(".bn-showing [data-bn-show=all]")).to_be_visible()

    page.locator("#results-body tr[data-row='2']").click()  # London, from the table
    expect(visible_view(page)).to_have_attribute("data-bn-view", "2")
    expect(visible_view(page).locator(".bn-block.bn-biggest")).to_contain_text("Server wait")

    page.locator(".bn-pick[data-bn-show='0']").click()  # Berlin, from the place list
    expect(visible_view(page)).to_have_attribute("data-bn-view", "0")
    expect(page.locator(".bn-pick[data-bn-show='0']")).to_have_attribute("aria-current", "true")

    page.locator(".bn-showing [data-bn-show=all]").click()
    expect(visible_view(page)).to_have_attribute("data-bn-view", "all")
    expect(page.locator("[data-bn-showing]")).to_have_text("average of 3 measured places")


def test_a_blocked_place_shows_no_diagram_only_its_reason(page, live_site, four_places, watch):
    scan(page, live_site)
    page.locator("#results-map circle.sw-dot[data-row='3']").click()  # Seoul, blocked
    view = visible_view(page)
    expect(view).to_have_attribute("data-bn-view", "3")
    expect(view.locator(".bn-path")).to_have_count(0)
    expect(view).to_contain_text("blocked: ")
    expect(view).to_contain_text("No timings, left out of every figure.")


def test_two_places_say_there_is_no_conclusion(page, live_site, watch):
    page.goto(live_site + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    start(page)
    expect(page.locator("#results-summary .bn-head")).to_contain_text("Not enough places for a conclusion.")
    page.click("#drawer-toggle")
    view = visible_view(page)
    expect(view.locator(".bn-biggest, .bn-possible")).to_have_count(0)
    expect(view.locator(".bn-reason")).to_contain_text("No block is marked.")


def test_reduced_motion_stops_the_bar_wipe(page, live_site, four_places, watch):
    page.emulate_media(reduced_motion="no-preference")
    scan(page, live_site)
    page.click("#drawer-toggle")
    bar = visible_view(page).locator(".bn-bar")
    assert bar.evaluate("e => getComputedStyle(e).animationName") == "bn-wipe"
    page.emulate_media(reduced_motion="reduce")
    assert bar.evaluate("e => getComputedStyle(e).animationName") == "none"


@pytest.mark.parametrize("size", ["desktop", "phone"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_with_the_panel(page, live_site, four_places, watch, scheme, size):
    setup(page, scheme, size)
    scan(page, live_site)
    expect(page.locator("#results-summary .bn-head")).to_be_visible()
    check(page, f"results with panel {scheme} {size}")
    page.get_by_role("button", name="See the request path").click()
    expect(visible_view(page)).to_have_attribute("data-bn-view", "all")
    check(page, f"diagram {scheme} {size}")
    page.locator(".bn-pick[data-bn-show='2']").click()
    check(page, f"place diagram {scheme} {size}")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_on_the_exported_panel(page, scheme):
    """The HTML export carries the same panel, read on its own: same AA rules, details opened."""
    from speedward.core.bottleneck import diagnose
    from speedward.reports import render
    from tests.test_bottleneck import FRANKFURT, bad, ok, report
    r = report(ok("Frankfurt", 5, 3, 6, 140, 6, cdn="HIT"), ok("London", 6, 12, 14, 150, 8, cdn="MISS"),
               ok("Tokyo", 20, 190, 190, 250, 20), ok("Sydney", 20, 200, 200, 260, 20), bad("Lagos"),
               server=FRANKFURT)
    r.diagnosis = diagnose(r)
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.set_content(render(r, "html"))
    page.evaluate("document.querySelectorAll('.bn-place details').forEach(d => d.open = true)")
    check(page, f"export panel {scheme}")


@pytest.mark.parametrize("size", [{"width": 320, "height": 640}, {"width": 640, "height": 400}],
                         ids=["320", "zoom200"])
def test_reflow_with_the_diagram(page, live_site, four_places, watch, size):
    page.set_viewport_size(size)
    page.emulate_media(reduced_motion="reduce")
    scan(page, live_site)
    assert page.evaluate(CLIPPED) == []
    open_details(page)  # phones: the sheet at full height (Task 24)
    assert page.evaluate(CLIPPED) == []
    direction = visible_view(page).locator(".bn-path").evaluate("e => getComputedStyle(e).flexDirection")
    assert direction == "column"


def test_see_the_request_path_shows_the_heading_below_the_sticky_bar(page, live_site, four_places, watch):
    scan(page, live_site)
    page.get_by_role("button", name="See the request path").click()
    title = page.locator("#results-body .bn-title").bounding_box()
    bar = page.locator("#drawer .panel-head").bounding_box()
    assert title["y"] >= bar["y"] + bar["height"] - 1
