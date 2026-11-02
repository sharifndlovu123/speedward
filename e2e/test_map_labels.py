"""Map labels measured in a real browser (Task 23, the Task 22 labels.py idea): for the default 12, the
Europe 7 and all 32 places, no two shown labels overlap, none covers a dot, none is cut off at the map
edge, and the text stays readable, on the web results map and in the HTML export, desktop and phone.
The reports are synthetic (tests/map_sets.py); no Globalping test is used.
"""
import json

import pytest
from playwright.sync_api import expect

from e2e.test_a11y import check
from speedward.reports import render
from tests.map_sets import BLOCKED, SETS, report

KEY = "speedward:result"
SIZES = {"desktop": {"width": 1280, "height": 800}, "medium": {"width": 820, "height": 900},
         "phone": {"width": 390, "height": 844}}
MIN_PX = 9.5  # the smallest rendered label size accepted at any of these widths

MEASURE = """(root) => {
  const svg = document.querySelector(root + ' svg.sw-map');
  const area = svg.querySelector(':scope > rect').getBoundingClientRect();
  const vb = svg.viewBox.baseVal, scale = area.width / vb.width;
  const shown = [...svg.querySelectorAll('.sw-labels text')].filter(t => t.getClientRects().length > 0);
  const box = e => e.getBoundingClientRect();
  const hit = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
  const dots = [...svg.querySelectorAll('circle.sw-dot')];
  const pairs = [], covered = [], cut = [];
  shown.forEach((t, i) => {
    const b = box(t);
    shown.slice(i + 1).forEach(u => { if (hit(b, box(u))) pairs.push([t.textContent, u.textContent]); });
    dots.forEach(d => { if (hit(b, box(d))) covered.push([t.textContent, d.querySelector('title').textContent]); });
    if (b.left < area.left - 0.5 || b.right > area.right + 0.5 || b.top < area.top - 0.5 || b.bottom > area.bottom + 0.5)
      cut.push(t.textContent);
  });
  const sizes = shown.map(t => parseFloat(getComputedStyle(t).fontSize) * scale);
  return {shown: shown.map(t => t.textContent), pairs, covered, cut, minPx: Math.min(...sizes),
          dots: dots.length, leaders: svg.querySelectorAll('.sw-labels path.sw-lead').length};
}"""


def measure(page, root):
    data = page.evaluate(MEASURE, root)
    assert data["shown"], "no label is shown"
    assert data["pairs"] == [], data["pairs"]
    assert data["covered"] == [], data["covered"]
    assert data["cut"] == [], data["cut"]
    assert data["minPx"] >= MIN_PX, data["minPx"]
    return data


def show_saved(page, base, rep):
    saved = json.dumps({"v": 1, "savedAt": 0, "result": json.loads(render(rep, "json"))})
    page.add_init_script(f"try {{ const s = {saved}; s.savedAt = Date.now(); "
                         f"sessionStorage.setItem({KEY!r}, JSON.stringify(s)); }} catch (e) {{}}")
    page.goto(base + "/")
    expect(page.locator("#results-map svg.sw-map")).to_be_visible(timeout=30_000)


@pytest.mark.parametrize("size", list(SIZES))
@pytest.mark.parametrize("name", list(SETS))
def test_export_labels_do_not_collide(page, name, size):
    page.set_viewport_size(SIZES[size])
    page.emulate_media(reduced_motion="reduce")
    for blocked in ((), BLOCKED):
        page.set_content(render(report(SETS[name], blocked), "html"))
        data = measure(page, "")
        assert data["dots"] == len(SETS[name])  # dropped labels never drop a place


@pytest.mark.parametrize("size", list(SIZES))
@pytest.mark.parametrize("name", list(SETS))
def test_web_results_labels_do_not_collide(page, live_site, watch, name, size):
    page.set_viewport_size(SIZES[size])
    page.emulate_media(reduced_motion="reduce")
    show_saved(page, live_site, report(SETS[name], BLOCKED))
    data = measure(page, "#results-map")
    assert data["dots"] == len(SETS[name])
    expect(page.locator("#results-body tr[data-row]")).to_have_count(len(SETS[name]))


def test_every_dot_stays_reachable_by_keyboard_when_labels_are_dropped(page, live_site, watch):
    page.set_viewport_size(SIZES["phone"])
    page.emulate_media(reduced_motion="reduce")
    show_saved(page, live_site, report(SETS["all-32"], BLOCKED))
    dots = page.locator("#results-map circle.sw-dot")
    expect(dots).to_have_count(32)
    for i in range(32):
        assert dots.nth(i).get_attribute("tabindex") == "0"
        assert dots.nth(i).locator("title").text_content()


@pytest.mark.parametrize("size", ["desktop", "phone"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_crowded_map_passes_axe(page, live_site, watch, scheme, size):
    page.set_viewport_size(SIZES[size])
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    show_saved(page, live_site, report(SETS["all-32"], BLOCKED))
    check(page, f"all-32 results {scheme} {size}")
    page.set_content(render(report(SETS["all-32"], BLOCKED), "html"))
    check(page, f"all-32 export {scheme} {size}")
