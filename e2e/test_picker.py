"""The map-first location picker: dots and chips share one selection that feeds /estimate and /scan."""
import json

from playwright.sync_api import expect

KEY = "speedward:result"
TARGET = "https://e2e-target.example/"


def dot(page, name):
    return page.locator(f'#picker-map .sw-pick[data-place="{name}"]')


def chip(page, name):
    return page.locator(f'#chips .chip[data-place="{name}"]')


def test_default_twelve_selected_and_summary(page, live_site, watch):
    page.goto(live_site + "/")
    expect(page.locator("#chips .chip[aria-pressed=true]")).to_have_count(12)
    value = page.locator("#locations").input_value()
    assert sorted(value.split(",")) == sorted(["London", "Frankfurt", "New York", "Los Angeles", "Sao Paulo",
                                               "Johannesburg", "Lagos", "Mumbai", "Singapore", "Tokyo",
                                               "Sydney", "Dubai"])
    expect(page.locator("#pick-summary")).to_have_text("12 locations · about 36 tests")


def test_clicking_a_dot_toggles_chip_and_estimate(page, live_site, watch):
    page.goto(live_site + "/")
    expect(page.locator("#pick-summary")).to_contain_text("about 36 tests")
    dot(page, "Paris").click()
    expect(chip(page, "Paris")).to_have_attribute("aria-pressed", "true")
    expect(dot(page, "Paris")).to_have_attribute("aria-checked", "true")
    expect(page.locator("#pick-summary")).to_have_text("13 locations · about 39 tests")
    chip(page, "Paris").click()
    expect(dot(page, "Paris")).to_have_attribute("aria-checked", "false")
    expect(page.locator("#pick-summary")).to_have_text("12 locations · about 36 tests")


def test_keyboard_toggles_dots_and_chips(page, live_site, watch):
    page.goto(live_site + "/")
    dot(page, "Paris").focus()
    page.keyboard.press("Space")
    expect(chip(page, "Paris")).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("Enter")
    expect(chip(page, "Paris")).to_have_attribute("aria-pressed", "false")
    chip(page, "Madrid").focus()
    page.keyboard.press("Enter")
    expect(dot(page, "Madrid")).to_have_attribute("aria-checked", "true")


def test_presets(page, live_site, watch):
    page.goto(live_site + "/")
    page.click("[data-preset=continent]")
    expect(page.locator("#chips .chip[aria-pressed=true]")).to_have_count(6)
    page.click("[data-preset=all]")
    expect(page.locator("#chips .chip[aria-pressed=true]")).to_have_count(
        page.locator("#picker-map .sw-pick").count())
    page.click("[data-preset=default]")
    expect(page.locator("#chips .chip[aria-pressed=true]")).to_have_count(12)
    page.click("[data-preset=clear]")
    expect(page.locator("#chips .chip[aria-pressed=true]")).to_have_count(0)


def test_add_custom_place(page, live_site, watch):
    page.goto(live_site + "/")
    page.click("[data-preset=clear]")
    page.fill("#add-name", "  Reykjavik ")
    page.click("#add-btn")
    expect(page.locator('#custom-group .chip[data-place="Reykjavik"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#locations")).to_have_value("Reykjavik")
    page.fill("#add-name", "a,b")
    page.click("#add-btn")
    expect(page.locator("#add-error")).to_be_visible()
    page.fill("#add-name", "x" * 41)  # maxlength stops typing at 40 but a paste may not
    page.evaluate("document.getElementById('add-name').maxLength = 100")
    page.fill("#add-name", "x" * 41)
    page.click("#add-btn")
    expect(page.locator("#add-error")).to_contain_text("40")
    expect(page.locator("#custom-group .chip")).to_have_count(1)
    page.fill("#add-name", "paris")  # an existing place is selected, not duplicated
    page.click("#add-btn")
    expect(chip(page, "Paris")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#custom-group .chip")).to_have_count(1)


def test_zero_selection_is_an_error_not_the_default(page, live_site, watch):
    page.goto(live_site + "/")
    page.click("[data-preset=clear]")
    expect(page.locator("#form-error")).to_have_text("Pick at least one place")
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.fill("#url", TARGET)
    page.click("#start")
    expect(page.locator("#form-error")).to_have_text("Pick at least one place")
    assert not [u for u in requests if u.endswith("/scan") or u.endswith("/estimate")]
    chip(page, "London").click()
    expect(page.locator("#form-error")).to_be_hidden()


def test_picks_restored_after_refresh_and_picker_reopens(page, live_site, watch):
    page.goto(live_site + "/")
    page.click("[data-preset=clear]")
    chip(page, "Paris").click()
    page.fill("#add-name", "Berlin")
    page.click("#add-btn")
    page.fill("#url", TARGET)
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    expect(page.locator("#picker-map")).to_be_hidden()
    expect(page.locator("#locations-toggle")).to_have_text("Locations (2)")
    saved = json.loads(page.evaluate(f"sessionStorage.getItem({KEY!r})"))
    assert saved["form"]["locations"] == "Paris,Berlin"
    page.reload()
    expect(page.locator("#results-map svg.sw-map")).to_be_visible()
    expect(page.locator("#locations-toggle")).to_have_text("Locations (2)")
    page.click("#locations-toggle")
    expect(page.locator("#picker-map")).to_be_visible()
    expect(chip(page, "Paris")).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#custom-group .chip[data-place="Berlin"]')).to_have_attribute("aria-pressed", "true")
    expect(chip(page, "London")).to_have_attribute("aria-pressed", "false")


def test_picker_is_locked_while_scanning(page, live_site, watch):
    page.goto(live_site + "/")
    page.route("**/scan/*/status", lambda route: route.fulfill(
        json={"status": "running", "progress": ["Testing"]}))
    page.fill("#url", TARGET)
    page.click("#start")
    expect(page.locator("#progress")).to_be_visible()
    expect(page.locator("#picker-map svg")).to_be_visible()
    dot(page, "Paris").click(force=True)
    expect(chip(page, "Paris")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#picker-strip")).to_have_attribute("inert", "")


def test_phone_width_has_no_horizontal_scroll(page, live_site, watch):
    page.set_viewport_size({"width": 360, "height": 740})
    page.goto(live_site + "/")
    expect(page.locator("#picker-map svg")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.locator("#url")).to_be_in_viewport()
    expect(page.locator("#start")).to_be_in_viewport()


def test_start_button_stays_reachable_on_a_short_screen(page, live_site, watch):
    page.goto(live_site + "/")
    expect(page.locator("#start")).to_be_in_viewport()
    page.click("[data-collapse=picker-strip]")  # a collapsed panel frees the dots under it
    expect(page.locator("#chips")).to_be_hidden()
    expect(dot(page, "Paris")).to_be_in_viewport()


def test_first_tab_stops_are_panel_controls_not_dots(page, live_site, watch):
    page.goto(live_site + "/")
    seen = []
    for _ in range(4):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("document.activeElement.id || document.activeElement.className"))
    assert "url" in seen
    assert not any("sw-pick" in str(x) for x in seen)
    for _ in range(60):  # the dots are still reachable, after the panels
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.classList.contains('sw-pick')"):
            break
    else:
        raise AssertionError("dots are not reachable by keyboard")


def _first_scan(page, live_site):
    page.goto(live_site + "/")
    page.fill("#url", TARGET)
    page.click("[data-preset=clear]")
    chip(page, "Paris").click()
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)


def test_failed_rescan_keeps_old_results_and_shows_the_error(page, live_site, watch):
    _first_scan(page, live_site)
    page.route("**/scan/*/status", lambda route: route.fulfill(
        json={"status": "error", "error": "The scan failed because Globalping could not be used."}))
    page.click("#results-run-again")
    expect(page.locator("#form-error")).to_be_visible()
    expect(page.locator("#form-error")).to_have_text("The scan failed because Globalping could not be used.")
    expect(page.locator("#start")).to_be_enabled()
    page.click("#locations-toggle")  # back to the earlier results, still saved
    expect(page.locator("#results-map svg.sw-map")).to_be_visible()


def test_lost_contact_keeps_old_results_and_shows_the_error(page, live_site):  # no `watch`: the abort logs a console error
    _first_scan(page, live_site)
    page.route("**/scan/*/status", lambda route: route.abort())
    page.click("#results-run-again")
    expect(page.locator("#form-error")).to_have_text(
        "Lost contact with the server while waiting for the results.")
    page.click("#locations-toggle")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible()
    assert page.evaluate("sessionStorage.getItem('speedward:result') !== null")


def test_phone_results_mode_keeps_export_and_run_again_reachable(page, live_site, watch):
    page.set_viewport_size({"width": 360, "height": 740})
    _first_scan(page, live_site)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for sel in ("[data-export=html]", "#results-run-again", "#locations-toggle"):
        page.locator(sel).scroll_into_view_if_needed()
        expect(page.locator(sel)).to_be_in_viewport()
