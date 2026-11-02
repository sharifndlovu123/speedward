"""Results drawer: tall, closable by button, Escape or a map click; focus goes in and comes back."""
import pytest
from playwright.sync_api import expect

from e2e.test_session_storage import run_scan

SIZES = [{"width": 1280, "height": 800}, {"width": 360, "height": 740}]


def open_drawer(page, base):
    run_scan(page, base)
    dot = page.locator("#results-map circle.sw-dot[data-row='0']")
    dot.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#drawer")).to_be_visible()
    return dot


def drawer_ratio(page):
    box = page.locator("#drawer").bounding_box()
    return box["height"] / page.viewport_size["height"]


@pytest.mark.parametrize("size,minimum", list(zip(SIZES, (0.6, 0.8))))
def test_drawer_is_tall_and_focus_moves_in(page, live_site, watch, size, minimum):
    page.set_viewport_size(size)
    open_drawer(page, live_site)
    assert drawer_ratio(page) >= minimum
    assert page.locator("#results-map").bounding_box()["y"] < page.locator("#drawer").bounding_box()["y"]
    expect(page.locator("#drawer-close")).to_be_focused()
    expect(page.locator("#results-map circle.sw-dot[data-row='0']")).to_have_class("sw-dot sw-sel")


def test_close_button_returns_focus_to_dot(page, live_site, watch):
    dot = open_drawer(page, live_site)
    page.get_by_role("button", name="Close").click()
    expect(page.locator("#drawer")).to_be_hidden()
    expect(dot).to_be_focused()
    expect(dot).not_to_have_class("sw-dot sw-sel")


def test_escape_closes_and_returns_focus(page, live_site, watch):
    dot = open_drawer(page, live_site)
    page.keyboard.press("Escape")
    expect(page.locator("#drawer")).to_be_hidden()
    expect(dot).to_be_focused()


def test_table_button_opens_and_escape_returns_to_it(page, live_site, watch):
    run_scan(page, live_site)
    page.click("#drawer-toggle")
    expect(page.locator("#drawer-close")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#drawer-toggle")).to_be_focused()


def test_clicking_the_map_outside_closes(page, live_site, watch):
    open_drawer(page, live_site)
    page.mouse.click(5, 120)
    expect(page.locator("#drawer")).to_be_hidden()


def test_focus_ring_is_a_stroke(page, live_site, watch):
    run_scan(page, live_site)
    dot = page.locator("#results-map circle.sw-dot[data-row='0']")
    before = dot.evaluate("e => getComputedStyle(e).strokeWidth")
    page.keyboard.press("Tab")  # keyboard focus so :focus-visible applies
    dot.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    after = dot.evaluate("e => [getComputedStyle(e).strokeWidth, getComputedStyle(e).stroke]")
    assert before != after[0] and float(after[0].replace("px", "")) >= 3
    page.click("#locations-toggle")
    pd = page.locator("#picker-map .sw-pick").first
    pd.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    assert float(pd.evaluate("e => getComputedStyle(e).strokeWidth").replace("px", "")) >= 3


def test_focused_checked_pick_ring_is_ink_not_background(page, live_site, watch):
    page.goto(live_site + "/")
    pd = page.locator('#picker-map .sw-pick[aria-checked="true"]').first
    pd.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    stroke, ink, bg = pd.evaluate(
        "e => { const m = e.closest('svg'); const cs = getComputedStyle(m);"
        " const probe = (v) => { const t = document.createElement('span'); t.style.color = v;"
        " m.appendChild(t); const c = getComputedStyle(t).color; t.remove(); return c; };"
        " return [getComputedStyle(e).stroke, probe(cs.getPropertyValue('--ink')),"
        " probe(cs.getPropertyValue('--bg'))]; }")
    assert stroke == ink and stroke != bg
