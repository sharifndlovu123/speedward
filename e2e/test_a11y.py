"""WCAG 2.2 AA: axe-core (via axe-playwright-python, MPL-2.0) on every main state, light and dark.

States: the picker (start page), scanning (progress shown, picks locked), results, and the open
details drawer, plus the error state. Motion is reduced so axe sees the settled page, not a wipe
half way through. Any waived rule must be listed in WAIVED with a reason; there are none now.
"""
import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from e2e.helpers import pick, start
from e2e.test_session_storage import TARGET

AXE = Axe()
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"]
WAIVED: dict[str, str] = {}
SIZES = {"desktop": {"width": 1280, "height": 800}, "phone": {"width": 360, "height": 740}}


def check(page, state):
    options = {"runOnly": {"type": "tag", "values": TAGS}, "resultTypes": ["violations"],
               "rules": {rule: {"enabled": False} for rule in WAIVED}}
    results = AXE.run(page, options=options)
    assert results.violations_count == 0, f"{state}:\n{results.generate_report()}"


def setup(page, scheme, size):
    page.set_viewport_size(SIZES[size])
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")


def start_scan(page, base):
    page.goto(base + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    start(page)


@pytest.mark.parametrize("size", list(SIZES))
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_picker(page, live_site, watch, scheme, size):
    setup(page, scheme, size)
    page.goto(live_site + "/")
    expect(page.locator("#pick-summary")).to_contain_text("about")
    check(page, f"picker {scheme} {size}")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_scanning(page, live_site, fake_globalping, watch, scheme):
    setup(page, scheme, "desktop")
    fake_globalping.in_progress_polls = 10_000
    try:
        start_scan(page, live_site)
        expect(page.locator("#progress-lines li")).not_to_have_count(0, timeout=10_000)
        check(page, f"scanning {scheme}")
    finally:
        fake_globalping.in_progress_polls = 0
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)


@pytest.mark.parametrize("size", list(SIZES))
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_results_and_drawer(page, live_site, watch, scheme, size):
    setup(page, scheme, size)
    start_scan(page, live_site)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    check(page, f"results {scheme} {size}")
    page.click("#drawer-toggle")
    expect(page.locator("#drawer")).to_be_visible()
    check(page, f"drawer {scheme} {size}")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_error_state(page, live_site, fake_globalping, watch, scheme):
    setup(page, scheme, "desktop")
    page.goto(live_site + "/")
    page.click("[data-preset=clear]")  # no places: the error is shown at once, no test is spent
    expect(page.locator("#form-error")).to_be_visible()
    expect(page.locator("#form-error")).to_be_in_viewport()
    check(page, f"error {scheme}")


# 320 px wide (WCAG reflow), and 1280x800 at 200% zoom, which lays out like a 640x400 window.
REFLOW = [{"width": 320, "height": 640}, {"width": 640, "height": 400}]
CLIPPED = """() => {
  const w = document.documentElement.clientWidth, out = [];
  if (document.documentElement.scrollWidth > w) out.push('page scrolls sideways');
  // Only the export buttons row and the wide tables scroll sideways on purpose.
  const sideways = '.results-bar .actions, .scroll';
  for (const el of document.querySelectorAll('.panel, .left-col, .panel-body')) {
    if (el.closest('[hidden]') || el.closest(sideways)) continue;
    if (el.scrollWidth > el.clientWidth + 1) out.push('sideways overflow in #' + (el.id || el.className));
  }
  for (const el of document.querySelectorAll('button, input:not([type=hidden]), select, summary, a')) {
    const r = el.getBoundingClientRect();
    if (!r.width || el.closest('[hidden]') || el.closest(sideways)) continue;
    if (r.left < -1 || r.right > w + 1) out.push(el.outerHTML.slice(0, 80));
  }
  return out;
}"""


@pytest.mark.parametrize("size", REFLOW, ids=["320", "zoom200"])
def test_reflow_no_sideways_scroll_or_clipped_controls(page, live_site, watch, size):
    page.set_viewport_size(size)
    page.emulate_media(reduced_motion="reduce")
    start_scan(page, live_site)
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    assert page.evaluate(CLIPPED) == []
    page.click("#drawer-toggle")
    assert page.evaluate(CLIPPED) == []
    page.goto(live_site + "/")
    assert page.evaluate(CLIPPED) == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_html_export(page, scheme):
    """The downloadable HTML report is read on its own: same AA rules, light and dark."""
    from speedward.core.models import LocationResult
    from speedward.reports import render
    from tests.test_reports import loc, ok, rep
    report = rep(locations=[ok("Tokyo", 412), ok("London", 120),
                            LocationResult(loc("Lagos"), "blocked", "timed out")])
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.set_content(render(report, "html"))
    check(page, f"export {scheme}")
