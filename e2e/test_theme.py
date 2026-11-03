"""Light / Dark / Auto: the switch sets data-theme, remembers it in this browser only, Auto follows the
device, the saved choice is on <html> before the body exists (no flash), and it all works with
localStorage blocked. Axe runs in every choice. FakeGlobalping only."""
import pytest
from playwright.sync_api import expect

from e2e.test_a11y import check

KEY = "speedward:theme"
PAPER = {"light": "#F0EAE0", "dark": "#141414"}
# Records data-theme the moment <body> is inserted: proof the choice is applied before the first paint.
AT_BODY = """
window.__themeAtBody = 'unset';
new MutationObserver(function (list, obs) {
  if (document.body) { window.__themeAtBody = document.documentElement.getAttribute('data-theme'); obs.disconnect(); }
}).observe(document, {childList: true, subtree: true});
"""
BLOCK_STORAGE = """
Object.defineProperty(window, 'localStorage', {configurable: true,
  get: function () { throw new DOMException('blocked', 'SecurityError'); }});
"""


def paper(page):
    return page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--paper').trim()").upper()


def radio(page, value):
    return page.locator(f'#topbar input[name="theme"][value="{value}"]')


def choose(page, word):
    page.locator("#topbar .theme-switch label", has_text=word).click()


def stored(page):
    return page.evaluate(f"(() => {{ try {{ return localStorage.getItem({KEY!r}); }} catch (e) {{ return 'blocked'; }} }})()")


def test_auto_is_the_default_and_follows_the_device(page, live_site, watch):
    page.emulate_media(color_scheme="dark")
    page.goto(live_site + "/")
    expect(radio(page, "auto")).to_be_checked()
    assert page.evaluate("document.documentElement.hasAttribute('data-theme')") is False
    assert paper(page) == PAPER["dark"]
    page.emulate_media(color_scheme="light")
    assert paper(page) == PAPER["light"]
    expect(page.get_by_role("radiogroup", name="Colour theme")).to_be_visible()


def test_choosing_sets_and_persists_and_auto_forgets(page, live_site, watch):
    page.emulate_media(color_scheme="light")
    page.goto(live_site + "/")
    choose(page, "Dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert paper(page) == PAPER["dark"] and stored(page) == "dark"
    page.reload()
    expect(radio(page, "dark")).to_be_checked()
    assert paper(page) == PAPER["dark"]
    page.emulate_media(color_scheme="dark")
    choose(page, "Light")
    assert paper(page) == PAPER["light"] and stored(page) == "light"
    choose(page, "Auto")
    assert page.evaluate("document.documentElement.hasAttribute('data-theme')") is False
    assert stored(page) is None and paper(page) == PAPER["dark"]
    assert not [c for c in page.context.cookies() if "theme" in c["name"]]


def test_saved_choice_is_on_html_before_the_body_exists(page, live_site, watch):
    page.goto(live_site + "/")
    choose(page, "Dark")
    page.add_init_script(AT_BODY)
    page.reload()
    assert page.evaluate("window.__themeAtBody") == "dark"


def test_arrow_keys_move_the_choice_and_the_focus_ring_shows(page, live_site, watch):
    page.goto(live_site + "/")
    radio(page, "auto").focus()
    page.keyboard.press("ArrowLeft")
    expect(radio(page, "dark")).to_be_checked()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.keyboard.press("ArrowLeft")
    expect(radio(page, "light")).to_be_checked()
    ring = page.locator('#topbar input[value="light"] + span').evaluate("e => getComputedStyle(e).outlineStyle")
    assert ring == "solid"


def test_works_with_local_storage_blocked(page, live_site, watch):
    page.add_init_script(BLOCK_STORAGE)
    page.goto(live_site + "/")
    expect(radio(page, "auto")).to_be_checked()
    choose(page, "Dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert stored(page) == "blocked"
    expect(page.locator("#start")).to_have_attribute("data-cost", "ready")  # the page still works


def test_the_map_follows_the_chosen_theme(page, live_site, watch):
    page.emulate_media(color_scheme="light")
    page.goto(live_site + "/")
    choose(page, "Dark")
    bg = page.evaluate("getComputedStyle(document.querySelector('#picker-map svg')).getPropertyValue('--bg').trim()")
    assert bg.upper() == PAPER["dark"]


@pytest.mark.parametrize("choice", ["Light", "Dark", "Auto"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_in_every_choice(page, live_site, watch, scheme, choice):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.goto(live_site + "/")
    choose(page, choice)
    expect(page.locator("#pick-summary")).to_contain_text("about")
    check(page, f"theme {choice} on {scheme}")


# The switch must not make the desktop top bar taller. The bar's own height depends on the fonts of the
# machine (about 74 px here, about 94 px on the CI runner, where the allowance text wraps further), so the
# check compares the page with the switch against the same page with the switch taken out, never an
# absolute pixel bound.
@pytest.mark.parametrize("width", [1280, 1440, 1600])
def test_switch_does_not_make_the_desktop_top_bar_taller(page, live_site, watch, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(live_site + "/")
    expect(page.get_by_role("radiogroup", name="Colour theme")).to_be_visible()
    with_switch = page.locator("#topbar").bounding_box()["height"]
    page.add_style_tag(content=".theme-switch{display:none !important}")
    without = page.locator("#topbar").bounding_box()["height"]
    assert abs(with_switch - without) <= 1
