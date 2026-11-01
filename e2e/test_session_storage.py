"""Saved results live only in this tab's sessionStorage; the server keeps nothing."""
from e2e.helpers import pick
import json
from urllib.parse import urlsplit

from playwright.sync_api import expect

TARGET = "https://e2e-target.example/"
KEY = "speedward:result"


def run_scan(page, base):
    page.goto(base + "/")
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)


def scan_requests(requests):
    return [(m, u) for m, u in requests if urlsplit(u).path == "/scan" or urlsplit(u).path.startswith("/scan/")]


def test_refresh_keeps_results_without_a_new_scan(page, live_site, fake_globalping, watch):
    run_scan(page, live_site)
    saved = json.loads(page.evaluate(f"sessionStorage.getItem({KEY!r})"))
    assert saved["result"]["schema_version"] == 1 and isinstance(saved["savedAt"], (int, float))
    measurements = len(fake_globalping.posts())

    requests = []
    page.on("request", lambda r: requests.append((r.method, r.url)))
    page.reload()
    banner = page.locator("#saved-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("Showing results for e2e-target.example from 0 minutes ago. "
                                   "Kept until you close this tab. No tests were used.")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible()
    expect(page.locator("#results-body tr[data-row]")).to_have_count(2)
    expect(page.locator("#banner-run-again")).to_have_text("Run again (~6 tests)")
    expect(page.locator("#confirm-wrap")).to_be_hidden()  # the scan fits: no "go ahead anyway" box
    assert scan_requests(requests) == []
    assert len(fake_globalping.posts()) == measurements  # no tests were used

    # A dot selects its table row.
    page.locator("#results-map circle[data-row='1']").click()
    expect(page.locator("#results-body tr[data-row='1']")).to_have_class("hl")
    expect(page.locator("#map-detail")).not_to_be_empty()

    # Export from the saved copy.
    with page.expect_download() as download:
        page.click("[data-export=json]")
    assert download.value.suggested_filename == "speedward-report.json"
    exported = json.loads(open(download.value.path()).read())
    assert exported["target"] == "https://e2e-target.example/"
    assert json.loads(page.evaluate(f"sessionStorage.getItem({KEY!r})"))["exported"] is True


def test_closing_the_page_and_reopening_shows_the_empty_start_page(context, live_site, watch, page):
    run_scan(page, live_site)
    page.close()
    fresh = context.new_page()
    fresh.goto(live_site + "/")
    expect(fresh.locator("#scan-form")).to_be_visible()
    expect(fresh.locator("#results-bar")).to_be_hidden()
    expect(fresh.locator("#saved-banner")).to_be_hidden()
    expect(fresh.locator("#notice")).to_be_hidden()
    assert fresh.evaluate(f"sessionStorage.getItem({KEY!r})") is None


def test_clear_now_removes_saved_results(page, live_site, watch):
    run_scan(page, live_site)
    page.click("#results-clear")
    expect(page.locator("#results-bar")).to_be_hidden()
    assert page.evaluate(f"sessionStorage.getItem({KEY!r})") is None


def test_expired_results_are_removed(page, live_site, watch):
    page.add_init_script(
        "try { sessionStorage.setItem('speedward:result', JSON.stringify("
        "{v: 1, savedAt: Date.now() - 3600 * 1000, result: {target: 'https://old.example/'}})); } catch (e) {}")
    page.goto(live_site + "/")
    expect(page.locator("#notice")).to_have_text("Saved results expired after 15 minutes and were removed.")
    expect(page.locator("#results-bar")).to_be_hidden()


def test_storage_failure_shows_notice(browser, live_site):
    context = browser.new_context()
    context.add_init_script(
        "Object.defineProperty(window, 'sessionStorage', {configurable: true, get: function () {"
        " throw new DOMException('The operation is insecure.', 'SecurityError'); }});")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(live_site + "/")
    expect(page.locator("#notice")).to_be_visible()
    expect(page.locator("#notice")).to_contain_text("Results cannot be kept in this window")
    # A scan still works and shows its results; they just are not kept.
    page.fill("#url", TARGET)
    pick(page, "Berlin")
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    assert errors == []
    context.close()
