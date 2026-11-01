"""The allowance strip: tests left, a countdown to the reset, and "last run" from this tab only."""
import re

import pytest
from playwright.sync_api import expect

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
    expect(strip).not_to_contain_text("last run")
    expect(page.locator("#allowance-bar")).to_be_visible()
    expect(page.locator("#allowance-caption")).to_contain_text("for this scan")
    page.clock.run_for(330 * 1000)
    expect(strip).to_contain_text("resets in 5 min")
    page.clock.run_for(240 * 1000)
    expect(strip).to_contain_text("resets in 1 min")


def test_last_run_appears_after_a_scan_and_vanishes_with_the_tab(page, context, live_site,
                                                                  reset_in_10_minutes, watch):
    page.goto(live_site + "/")
    expect(page.locator("#allowance-strip")).not_to_contain_text("last run")
    page.fill("#url", TARGET)
    page.fill("#locations", "Berlin,Tokyo")
    page.click("#start")
    expect(page.locator("#results-body svg.sw-map")).to_be_visible(timeout=30_000)
    strip = page.locator("#allowance-strip")
    expect(strip).to_contain_text("last run just now")
    expect(strip).to_contain_text(re.compile(r"\d+ of 500 tests left"))  # refreshed from /estimate after the scan
    page.close()
    fresh = context.new_page()
    fresh.on("dialog", lambda d: d.accept())
    fresh.goto(live_site + "/")
    expect(fresh.locator("#allowance-strip")).to_contain_text("tests left this hour")
    expect(fresh.locator("#allowance-strip")).not_to_contain_text("last run")
