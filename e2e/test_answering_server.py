"""Lines to the server that answered each probe: squares, the two new columns, and the fallback note.

The e2e server has no geo-IP database. The recorded results carry Cloudflare cf-ray headers
(Berlin -TXL, Tokyo -NRT), so their edges are placed from the airport table; without the header
nothing can be placed and the fallback note shows.
"""
from playwright.sync_api import expect

from e2e.helpers import pick, start

TARGET = "https://e2e-target.example/"
NOTE = "No server positions yet. Results still show every number; lines need the free offline location file."


def scan(page, live_site):
    page.goto(live_site + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    start(page)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)


def row(page, city):
    return page.locator("#results-body tr[data-row]", has_text=city)


def test_cdn_edges_get_squares_lines_and_columns(page, live_site, watch):
    scan(page, live_site)
    svg = page.locator("#results-map svg.sw-map")
    expect(svg.locator("rect.sw-sq")).to_have_count(2)
    expect(svg.locator("path[stroke]")).to_have_count(2)
    expect(page.locator(".position-note")).to_have_count(0)
    page.click("#drawer-toggle")
    head = page.locator("#results-body table").first.locator("th")
    expect(head.nth(6)).to_have_text("Answered by")
    expect(head.nth(7)).to_have_text("Position source")
    expect(row(page, "Berlin").locator("td").nth(6)).to_have_text("edge TXL (Berlin)")
    expect(row(page, "Berlin").locator("td").nth(7)).to_have_text("CDN header (cf-ray -TXL)")
    expect(row(page, "Tokyo").locator("td").nth(6)).to_have_text("edge NRT (Tokyo)")


def test_square_is_keyboard_reachable_and_describes_itself(page, live_site, watch):
    scan(page, live_site)
    sq = page.locator("#results-map rect.sw-sq").first
    expect(sq).to_have_attribute("role", "img")
    label = sq.get_attribute("aria-label")
    assert "172.66.147.243" in label and "CDN edge, not necessarily your origin" in label
    sq.focus()
    expect(page.locator("#map-detail")).to_have_text(label)
    page.keyboard.press("Shift+Tab")  # keyboard focus so :focus-visible applies
    page.keyboard.press("Tab")
    expect(sq).to_be_focused()
    assert sq.evaluate("e => getComputedStyle(e).strokeWidth") == "3px"


def test_no_database_shows_the_fallback_note_and_the_manual_pin(page, live_site, watch, no_cf_ray):
    scan(page, live_site)
    note = page.locator("#results-summary .position-note")
    expect(note).to_be_visible()
    expect(note).to_contain_text(NOTE)
    expect(page.locator("#results-map rect.sw-sq")).to_have_count(0)
    note.locator("summary").click()
    expect(note.locator("ol")).to_contain_text("GEOIP_DB")
    page.click("#drawer-toggle")
    expect(row(page, "Tokyo").locator("td").nth(6)).to_have_text("172.66.147.243")
    expect(row(page, "Tokyo").locator("td").nth(7)).to_have_text("position not available (no geo-IP database set)")
    page.click("#drawer-close")
    note.get_by_role("button", name="Place a server manually (your own claim)").click()
    expect(page.locator("#picker-map")).to_be_visible()
    expect(page.locator("#picker-map .sw-pin")).to_be_focused()
    expect(page.locator("#pin-status")).to_contain_text("your own claim")
