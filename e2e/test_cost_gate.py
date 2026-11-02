"""A scan never starts before its cost is shown: Start is refused while the estimate is stale or failed."""
from playwright.sync_api import expect

TARGET = "https://e2e-target.example/"
WAIT = "Wait for the test cost to be shown"
FAILED = "The test cost could not be estimated, so the scan was not started"


def _scan_posts(page):
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/scan") else None)
    return posts


def test_start_waits_for_a_fresh_estimate(page, live_site, watch):
    page.goto(live_site + "/")
    expect(page.locator("#pick-summary")).to_contain_text("about 36 tests")
    posts = _scan_posts(page)
    held = []
    page.route("**/estimate", lambda route: held.append(route))  # the new cost has not arrived yet
    page.fill("#url", TARGET)
    page.locator("[name=dns]").evaluate("el => el.click()")
    page.click("#start")
    expect(page.locator("#form-error")).to_contain_text(WAIT)
    assert posts == []
    page.unroute("**/estimate")
    for route in held:
        route.continue_()
    expect(page.locator("#pick-summary")).to_contain_text("about 48 tests")
    expect(page.locator("#form-error")).to_be_hidden()
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    assert len(posts) == 1


def test_start_refused_when_the_estimate_failed(page, live_site):  # no `watch`: the 502 is deliberate
    page.goto(live_site + "/")
    expect(page.locator("#pick-summary")).to_contain_text("about 36 tests")
    posts = _scan_posts(page)
    page.route("**/estimate", lambda route: route.fulfill(status=502, content_type="application/json",
                                                         body='{"error": "Globalping could not be reached."}'))
    page.fill("#url", TARGET)
    page.locator("[name=traceroute]").evaluate("el => el.click()")
    expect(page.locator("#estimate")).to_contain_text("Globalping could not be reached.")
    page.click("#start")
    expect(page.locator("#form-error")).to_contain_text(FAILED)
    page.wait_for_timeout(500)
    assert posts == []
