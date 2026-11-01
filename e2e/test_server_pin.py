"""The server pin: drag or arrow keys move it, the city select sets it, lines draw from it."""
import json

from playwright.sync_api import expect

from e2e.helpers import pick

TARGET = "https://e2e-target.example/"
PIN_KEY = "speedward:pin"
PIN = "#picker-map .sw-pin"


def stored(page):
    raw = page.evaluate(f"sessionStorage.getItem({PIN_KEY!r})")
    return json.loads(raw) if raw else None


def centre(page, selector):
    box = page.locator(selector).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def previews(page):
    return page.locator("#picker-map .sw-preview path")


def test_no_pin_no_lines_and_pin_is_labelled(page, live_site, watch):
    page.goto(live_site + "/")
    expect(previews(page)).to_have_count(0)
    expect(page.locator(PIN)).to_have_attribute("role", "application")
    expect(page.locator(PIN)).to_have_attribute("aria-label", "Server position pin, not set")
    expect(page.locator("#pin-help")).to_contain_text("arrow keys")
    expect(page.locator("#pin-help")).to_contain_text("Backspace")
    expect(page.locator(PIN)).to_have_class("sw-pin sw-unset")
    expect(page.locator(PIN + " text")).to_have_text("Server (drag me)")
    assert stored(page) is None


def test_select_sets_pin_and_preview_lines_for_selected_places(page, live_site, watch):
    page.goto(live_site + "/")
    page.select_option("#server-city", "Johannesburg")
    expect(previews(page)).to_have_count(12)
    data = stored(page)
    assert abs(data["lat"] + 26.2) < 1 and abs(data["lon"] - 28.0) < 1
    expect(page.locator("#pin-status")).to_contain_text("south")
    page.locator('#chips .chip[data-place="London"]').click()
    expect(previews(page)).to_have_count(11)
    page.select_option("#server-city", "")
    expect(previews(page)).to_have_count(0)
    assert stored(page) is None


def test_keyboard_moves_pin_and_focus_ring_is_visible(page, live_site, watch):
    page.goto(live_site + "/")
    page.locator(PIN).focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator(PIN)).to_have_attribute("aria-label", "Server position pin, 0.00 north, 167.00 east")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("Shift+ArrowUp")
    expect(page.locator(PIN)).to_have_attribute("aria-label", "Server position pin, 12.00 north, 167.00 east")
    assert stored(page)["lat"] == 12
    expect(page.locator("#server-city")).to_have_value("custom")
    assert page.locator(PIN + " .sw-ring").evaluate("e => getComputedStyle(e).stroke") != "none"
    page.keyboard.press("Delete")
    expect(page.locator(PIN)).to_have_attribute("aria-label", "Server position pin, not set")
    assert stored(page) is None


def test_drag_moves_pin_and_draws_lines(page, live_site, watch):
    page.goto(live_site + "/")
    x, y = centre(page, PIN + " .sw-body")
    box = page.locator("#picker-map svg").bounding_box()
    tx, ty = box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.7  # lon ~18, lat ~ -36
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(tx, ty, steps=8)
    page.mouse.up()
    data = stored(page)
    assert data is not None and abs(data["lon"] - 18) < 12 and data["lat"] < -20
    expect(previews(page)).to_have_count(12)


def test_pin_survives_refresh_and_is_cleared_with_results(page, live_site, watch):
    page.goto(live_site + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    page.select_option("#server-city", "Frankfurt")
    page.reload()
    expect(page.locator("#server-city")).to_have_value("Frankfurt")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    arcs = page.locator("#results-map svg.sw-map path[stroke]")
    expect(arcs).to_have_count(2)
    expect(page.locator("#results-map svg.sw-map")).to_contain_text("Server (pin)")
    saved = json.loads(page.evaluate("sessionStorage.getItem('speedward:result')"))
    assert saved["result"]["server"]["source"] == "manual (pin)"
    page.reload()
    expect(page.locator("#results-map svg.sw-map path[stroke]")).to_have_count(2)
    assert stored(page) is not None
    page.click("#results-clear")
    assert stored(page) is None
    expect(page.locator("#server-city")).to_have_value("")
    expect(previews(page)).to_have_count(0)


def test_scan_without_pin_says_position_unknown_and_has_no_lines(page, live_site, watch):
    page.goto(live_site + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    expect(page.locator("#results-map svg.sw-map")).to_contain_text(
        "Server position unknown: drop the pin or set SERVER_LAT/SERVER_LON")
    expect(page.locator("#results-map svg.sw-map path[stroke]")).to_have_count(0)


def test_plain_click_on_unset_pin_does_not_place_it_or_send_a_position(page, live_site, watch):
    bodies = []
    page.on("request", lambda r: bodies.append(r.post_data or "") if r.url.endswith("/scan") else None)
    page.goto(live_site + "/")
    x, y = centre(page, PIN + " .sw-body")
    page.mouse.click(x, y)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 2, y + 1)  # under the 4px threshold
    page.mouse.up()
    assert stored(page) is None
    expect(page.locator(PIN)).to_have_attribute("aria-label", "Server position pin, not set")
    expect(previews(page)).to_have_count(0)
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    assert len(bodies) == 1 and "server_lat" not in bodies[0] and "server_lon" not in bodies[0]


def test_placed_pin_is_solid_and_labelled_server(page, live_site, watch):
    page.goto(live_site + "/")
    page.select_option("#server-city", "Frankfurt")
    expect(page.locator(PIN)).not_to_have_class("sw-pin sw-unset")
    expect(page.locator(PIN + " text")).to_have_text("Server")


def test_status_announces_on_drop_not_during_drag(page, live_site, watch):
    page.goto(live_site + "/")
    x, y = centre(page, PIN + " .sw-body")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 200, y + 50, steps=6)
    expect(page.locator("#pin-status")).to_have_text("")
    page.mouse.up()
    expect(page.locator("#pin-status")).to_contain_text("Server pin:")
