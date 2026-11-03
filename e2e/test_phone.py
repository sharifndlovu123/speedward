"""Phone layout (Task 24, approved mockup page-20): one thin top bar with a menu, one bottom sheet with
three heights (peek, half, full) over the map. Sheet heights by button, keyboard and pointer, focus
order and focus management, the cost before Start in peek, the map visible at peek, wide tables with a
fixed place column, the server pin's tap area and Move pin button, reduced motion, axe in light and dark
at every height, and reflow at 320 px and 200% zoom. FakeGlobalping only."""
import pytest
from playwright.sync_api import expect

from e2e.helpers import pick, raise_sheet, start
from e2e.test_a11y import CLIPPED, check
from e2e.test_session_storage import TARGET

PHONES = {"390": {"width": 390, "height": 844}, "320": {"width": 320, "height": 640}}
ZOOM = {"width": 640, "height": 400}  # 1280x800 at 200%
SHEETS = ["peek", "half", "full"]

# How much of the drawn map (meet inside the stage) shows between the top bar and the sheet.
MAP_SHOWN = """(sel) => {
  const svg = document.querySelector(sel), r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
  const k = Math.min(r.width / vb.width, r.height / vb.height), h = vb.height * k;
  const top = r.top + (r.height - h) / 2, bottom = top + h;
  const bar = document.querySelector('#topbar').getBoundingClientRect().bottom;
  const sheet = document.querySelector('#sheet').getBoundingClientRect().top;
  return Math.max(0, Math.min(bottom, sheet) - Math.max(top, bar)) / h;
}"""


def sheet(page):
    return page.evaluate("document.body.dataset.sheet")


def set_sheet(page, height):
    while sheet(page) != height:
        now = SHEETS.index(sheet(page))
        page.click("#sheet-more" if SHEETS.index(height) > now else "#sheet-less")


def phone(page, size="390", scheme="light"):
    page.set_viewport_size(size if isinstance(size, dict) else PHONES[size])
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")


def scan(page, base, *places):
    page.goto(base + "/")
    page.fill("#url", TARGET)
    pick(page, *(places or ("Berlin", "Tokyo", "London", "Seoul")))
    start(page)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    expect(page.locator("body")).to_have_attribute("data-sheet", "peek")


@pytest.mark.parametrize("size", list(PHONES))
def test_peek_shows_address_cost_and_start_over_a_visible_map(page, live_site, watch, size):
    phone(page, size)
    page.goto(live_site + "/")
    expect(page.locator("#estimate")).to_contain_text("about")
    for sel in ("#url", "#pick-summary", "#estimate", "#start"):
        expect(page.locator(sel)).to_be_in_viewport()
    expect(page.locator("#picker-strip")).to_be_hidden()
    expect(page.locator(".more-checks")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate(MAP_SHOWN, "#picker-map svg") >= 0.6
    assert page.locator("#topbar").bounding_box()["height"] <= 64  # one line
    expect(page.get_by_role("region", name="Settings and results")).to_be_visible()


def test_the_cost_sits_straight_above_start_at_every_height(page, live_site, watch):
    phone(page)
    page.goto(live_site + "/")
    expect(page.locator("#pick-summary")).to_contain_text("about")
    for h in SHEETS:
        set_sheet(page, h)
        page.locator("#start").scroll_into_view_if_needed()
        expect(page.locator("#start")).to_be_in_viewport()
        expect(page.locator("#estimate")).to_be_in_viewport()
        cost, start = page.locator("#estimate").bounding_box(), page.locator("#start").bounding_box()
        assert cost["y"] < start["y"]
        assert page.locator("#start").evaluate("e => getComputedStyle(e.parentElement).position") == "static"


def test_buttons_change_the_height_announce_it_and_keep_focus_on_screen(page, live_site, watch):
    phone(page)
    page.goto(live_site + "/")
    expect(page.locator("#sheet-less")).to_be_hidden()
    page.click("#sheet-more")
    expect(page.locator("body")).to_have_attribute("data-sheet", "half")
    expect(page.locator("#sheet-state")).to_have_text("Panel at half height: places and More checks.")
    expect(page.locator("#picker-strip")).to_be_visible()
    expect(page.locator(".more-checks")).to_be_visible()
    page.locator("#sheet-more").focus()
    page.keyboard.press("Enter")
    expect(page.locator("body")).to_have_attribute("data-sheet", "full")
    expect(page.locator("#sheet-more")).to_be_hidden()
    expect(page.locator("#sheet-less")).to_be_focused()  # the pressed button went away: focus moved, not lost
    expect(page.locator(".server-pin")).to_be_visible()
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    expect(page.locator("body")).to_have_attribute("data-sheet", "peek")
    expect(page.locator("#sheet-more")).to_be_focused()


def test_hidden_content_never_keeps_focus(page, live_site, watch):
    phone(page)
    page.goto(live_site + "/")
    set_sheet(page, "half")
    page.locator('#picker-strip [data-preset="all"]').focus()
    page.click("#sheet-less")
    expect(page.locator("#picker-strip")).to_be_hidden()
    assert page.evaluate("document.activeElement.id") in ("sheet-more", "sheet-less")


def test_the_handle_by_tap_and_drag(page, live_site, watch):
    phone(page)
    page.goto(live_site + "/")
    page.locator("#sheet-handle").click()
    expect(page.locator("body")).to_have_attribute("data-sheet", "half")
    box = page.locator("#sheet-handle").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, 60, steps=6)
    page.mouse.up()
    expect(page.locator("body")).to_have_attribute("data-sheet", "full")
    box = page.locator("#sheet-handle").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, 820, steps=6)
    page.mouse.up()
    expect(page.locator("body")).to_have_attribute("data-sheet", "peek")


def test_keyboard_order_follows_the_screen(page, live_site, watch):
    phone(page)
    page.goto(live_site + "/")
    seen = []
    for _ in range(3):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("document.activeElement.id"))
    assert seen == ["menu-toggle", "sheet-more", "url"]


def test_menu_holds_the_actions_and_the_theme_and_escape_closes_it(page, live_site, watch):
    phone(page)
    scan(page, live_site)
    expect(page.locator("#topbar .brand")).to_be_hidden()
    expect(page.locator("#results-title")).to_be_visible()  # the address in the one-line bar
    assert page.locator("#topbar").bounding_box()["height"] <= 64
    for sel in ("[data-export=html]", "#results-run-again", "#results-clear", "#locations-toggle"):
        expect(page.locator(sel)).to_be_hidden()
    page.click("#menu-toggle")
    expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "true")
    for sel in ("[data-export=html]", "[data-export=json]", "[data-export=md]", "#results-run-again",
                "#results-clear", "#locations-toggle"):
        expect(page.locator(sel)).to_be_visible()
    group = page.get_by_role("radiogroup", name="Colour theme")
    expect(group).to_be_visible()
    for word in ("Light", "Dark", "Auto"):
        assert group.locator("label", has_text=word).bounding_box()["height"] >= 44
    page.locator("#topbar .theme-switch label", has_text="Dark").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "true")  # the theme keeps it open
    page.keyboard.press("Escape")
    expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#menu-toggle")).to_be_focused()
    page.click("#menu-toggle")
    page.click("#locations-toggle")  # an action closes the menu
    expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("body")).to_have_attribute("data-sheet", "half")
    expect(page.locator("#picker-strip")).to_be_visible()


def test_results_peek_half_full(page, live_site, watch):
    phone(page)
    scan(page, live_site)
    expect(page.locator("#results-summary .figures")).to_be_in_viewport()
    expect(page.locator("#results-summary .bn-headline")).to_be_in_viewport()
    assert page.evaluate(MAP_SHOWN, "#results-map svg.sw-map") >= 0.6
    expect(page.locator("#drawer")).to_be_hidden()
    page.click("#sheet-more")
    expect(page.locator("#results-summary .rc-head, #results-summary .legend").first).to_be_attached()
    expect(page.locator("#sheet-state")).to_have_text("Panel at half height: the summary.")
    page.click("#sheet-more")
    expect(page.locator("#drawer")).to_be_visible()
    expect(page.locator("#drawer-close")).to_be_focused()  # the details came in: focus goes to them
    expect(page.locator("#results-summary")).to_be_hidden()
    page.keyboard.press("Escape")
    expect(page.locator("body")).to_have_attribute("data-sheet", "half")
    expect(page.locator("#drawer")).to_be_hidden()
    expect(page.locator("#sheet-more")).to_be_focused()


def test_a_dot_opens_the_details_at_full_height(page, live_site, watch):
    phone(page)
    scan(page, live_site)
    dot = page.locator("#results-map circle.sw-dot[data-row='0']")
    dot.focus()
    page.keyboard.press("Enter")
    expect(page.locator("body")).to_have_attribute("data-sheet", "full")
    expect(page.locator("#results-body tr.hl[data-row='0']").first).to_be_attached()
    page.click("#drawer-close")
    expect(page.locator("body")).to_have_attribute("data-sheet", "half")
    expect(dot).to_be_focused()


def test_the_path_is_a_vertical_list_and_tables_keep_the_place_column(page, live_site, watch):
    phone(page)
    scan(page, live_site)
    set_sheet(page, "full")
    path = page.locator("#results-body .bn-view:not([hidden]) .bn-path")
    assert path.evaluate("e => getComputedStyle(e).flexDirection") == "column"
    region = page.get_by_role("region", name="Locations table")
    assert region.evaluate("e => e.tabIndex") == 0
    assert region.evaluate("e => e.scrollWidth > e.clientWidth")
    first = region.locator("tbody tr").first.locator("td").first
    before = first.bounding_box()["x"]
    region.evaluate("e => { e.scrollLeft = 200; }")
    assert abs(first.bounding_box()["x"] - before) < 1  # the place name stays put
    assert first.evaluate("e => getComputedStyle(e).position") == "sticky"


def test_server_pin_has_a_44_px_tap_area_and_a_move_pin_button(page, live_site, watch):
    phone(page)
    page.goto(live_site + "/")
    hit = page.locator("#picker-map .sw-pin .sw-hit")
    box = hit.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    set_sheet(page, "full")
    page.click("#pin-move")
    expect(page.locator("body")).to_have_attribute("data-sheet", "half")  # the map comes back on screen
    expect(page.locator("#pin-move")).to_have_text("Cancel moving")
    expect(page.locator("#pin-move-hint")).to_be_visible()
    svg = page.locator("#picker-map svg").bounding_box()
    page.mouse.click(svg["x"] + svg["width"] * 0.55, page.evaluate(
        "(() => { const r = document.querySelector('#picker-map svg').getBoundingClientRect(),"
        " vb = document.querySelector('#picker-map svg').viewBox.baseVal,"
        " k = Math.min(r.width / vb.width, r.height / vb.height);"
        " return r.top + (r.height - vb.height * k) / 2 + vb.height * k * 0.7; })()"))
    expect(page.locator("#pin-move")).to_have_text("Move pin")
    expect(page.locator("#pin-move")).to_be_focused()
    expect(page.locator("#pin-status")).to_contain_text("south")
    raw = page.evaluate("sessionStorage.getItem('speedward:pin')")
    assert raw and '"lat":-' in raw
    page.click("#pin-move")
    page.keyboard.press("Escape")
    expect(page.locator("#pin-move")).to_have_text("Move pin")


def test_move_pin_is_not_on_the_desktop(page, live_site, watch):
    page.goto(live_site + "/")
    expect(page.locator("#pin-move")).to_be_hidden()
    expect(page.locator(".sheet-head")).to_be_hidden()
    expect(page.locator("#menu-toggle")).to_be_hidden()
    assert page.locator("#picker-map .sw-pin .sw-hit").get_attribute("r") == "0"


def test_reduced_motion_removes_the_slide(page, live_site, watch):
    page.set_viewport_size(PHONES["390"])
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live_site + "/")
    s = page.locator("#sheet")
    assert s.evaluate("e => getComputedStyle(e).animationName") == "sw-rise"
    page.emulate_media(reduced_motion="reduce")
    assert s.evaluate("e => getComputedStyle(e).animationName") == "none"
    assert s.evaluate("e => getComputedStyle(e).transitionDuration") == "0s"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_at_every_height(page, live_site, watch, scheme):
    phone(page, "390", scheme)
    page.goto(live_site + "/")
    expect(page.locator("#pick-summary")).to_contain_text("about")
    for h in SHEETS:
        set_sheet(page, h)
        check(page, f"phone picker {h} {scheme}")
    set_sheet(page, "peek")
    page.click("#menu-toggle")
    check(page, f"phone menu {scheme}")
    page.click("#menu-toggle")
    scan(page, live_site)
    for h in SHEETS:
        set_sheet(page, h)
        check(page, f"phone results {h} {scheme}")
    set_sheet(page, "peek")
    page.click("#menu-toggle")
    check(page, f"phone results menu {scheme}")


@pytest.mark.parametrize("size", ["320", "zoom200"])
def test_reflow_at_every_height(page, live_site, watch, size):
    phone(page, ZOOM if size == "zoom200" else size)
    page.goto(live_site + "/")
    expect(page.locator("#pick-summary")).to_contain_text("about")
    if size == "320":
        expect(page.locator("#estimate")).to_be_in_viewport()
        expect(page.locator("#start")).to_be_in_viewport()
    for h in SHEETS:
        set_sheet(page, h)
        assert page.evaluate(CLIPPED) == [], f"picker {h}"
    page.click("#menu-toggle")
    assert page.evaluate(CLIPPED) == [], "menu"
    page.click("#menu-toggle")
    scan(page, live_site)
    for h in SHEETS:
        set_sheet(page, h)
        assert page.evaluate(CLIPPED) == [], f"results {h}"


def test_sheet_content_stays_reachable_by_keyboard(page, live_site, watch):
    phone(page)
    scan(page, live_site)
    raise_sheet(page, "full")
    # Tabbing through the details reaches the place table region and the last controls.
    seen = set()
    for _ in range(80):
        page.keyboard.press("Tab")
        seen.add(page.evaluate("document.activeElement.getAttribute('aria-label') || document.activeElement.id"))
    assert "Locations table" in seen
