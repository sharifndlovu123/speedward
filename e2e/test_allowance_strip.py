"""The allowance strip: tests left, a countdown to the reset, and "last run" from this tab only."""
import re

import pytest
from playwright.sync_api import expect

from e2e.helpers import pick

TARGET = "https://e2e-target.example/"


@pytest.fixture
def reset_in_10_minutes(fake_globalping):
    fake_globalping.reset = 600
    yield
    fake_globalping.reset = None


def test_strip_counts_down_when_the_clock_advances(page, live_site, reset_in_10_minutes, watch):
    page.clock.install()
    page.goto(live_site + "/")
    strip = page.locator("#allowance-strip")
    expect(strip).to_contain_text("tests left this hour")
    expect(strip).to_contain_text("resets in 10 min")
    expect(page.locator("#allowance-last-run")).to_be_hidden()
    expect(page.locator("#allowance-bar")).to_be_visible()
    expect(page.locator("#allowance-caption")).to_contain_text("for this scan")
    page.clock.run_for(330 * 1000)
    expect(strip).to_contain_text("resets in 5 min")
    page.clock.run_for(240 * 1000)
    expect(strip).to_contain_text("resets in 1 min")


def test_last_run_appears_after_a_scan_and_vanishes_with_the_tab(page, context, live_site,
                                                                  reset_in_10_minutes, watch):
    page.goto(live_site + "/")
    expect(page.locator("#allowance-last-run")).to_be_hidden()
    page.fill("#url", TARGET)
    pick(page, "Berlin", "Tokyo")
    page.click("#start")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)
    strip = page.locator("#allowance-strip")
    expect(page.locator("#allowance-last-run")).to_be_visible()
    expect(page.locator("#allowance-last-run")).to_contain_text("last run just now")
    expect(strip).to_contain_text(re.compile(r"\d+ of 500 tests left"))  # refreshed from /estimate after the scan
    page.close()
    fresh = context.new_page()
    fresh.on("dialog", lambda d: d.accept())
    fresh.goto(live_site + "/")
    expect(fresh.locator("#allowance-strip")).to_contain_text("tests left this hour")
    expect(fresh.locator("#allowance-last-run")).to_be_hidden()


def test_countdown_reaching_zero_shows_resets_now_and_refetches_once(page, live_site, fake_globalping, watch):
    fake_globalping.reset = 90
    try:
        estimates = []
        page.on("request", lambda r: estimates.append(r.url) if r.url.endswith("/estimate") else None)
        page.clock.install()
        page.goto(live_site + "/")
        strip = page.locator("#allowance-strip")
        expect(strip).to_contain_text("resets in 2 min")
        assert len(estimates) == 1
        fake_globalping.reset = 0  # what the server will report on the refetch
        fake_globalping.remaining = 400
        page.clock.run_for(120 * 1000)
        expect(strip).to_contain_text("resets now")
        page.clock.run_for(5 * 60 * 1000)  # no further polling while the reset is 0
        assert len(estimates) == 2
    finally:
        fake_globalping.reset = None
        fake_globalping.remaining = None


def test_estimate_failure_marks_the_strip_stale(page, live_site, reset_in_10_minutes, watch):
    page.goto(live_site + "/")
    strip = page.locator("#allowance-strip")
    expect(strip).to_contain_text("resets in 10 min")
    expect(page.locator("#allowance-stale")).to_be_hidden()
    watch.clear()  # the 500 below is on purpose
    page.route("**/estimate", lambda route: route.fulfill(status=500, body="{}", content_type="application/json"))
    pick(page, "Berlin")
    expect(page.locator("#allowance-stale")).to_be_visible()
    expect(page.locator("#allowance-stale")).to_contain_text("could not refresh")
    watch.clear()


def test_low_wording_comes_from_the_server_both_times(page, live_site, fake_globalping, watch):
    fake_globalping.reset = 520
    fake_globalping.remaining = 14
    try:
        served = page.request.get(live_site + "/").text()  # before any script runs
        assert "14 of 500 tests left this hour. Resets in 9 min." in served  # no cost yet: nothing to wait for
        with page.expect_response("**/estimate") as info:
            page.goto(live_site + "/")
        view = info.value.json()["view"]
        assert view["state"] == "low" and "Next full allowance in 9 min." in view["text"]
        expect(page.locator("#allowance-caption")).to_contain_text("for this scan")
        # The page shows exactly the server's wording, with the countdown number filled in.
        assert page.locator("#allowance-text").inner_text() == view["text"]
    finally:
        fake_globalping.reset = None
        fake_globalping.remaining = None
