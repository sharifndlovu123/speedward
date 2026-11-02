"""The rendered map's labels (Task 23): web results map and HTML export share render_map."""
import html
import json
import re

import pytest
from django.urls import reverse

from speedward.core.models import Location, LocationResult, ScanReport, Stages
from speedward.reports import render
from speedward.reports._labels import box_hits, text_box
from speedward.reports.map import _STYLE, LABEL_TIERS, render_map
from tests.map_sets import BLOCKED, NOW, SETS, report

TEXT = re.compile(r"<text ([^>]*)>([^<]*)</text>")
DOT = re.compile(r'<circle class="sw-dot[^"]*" data-row="(\d+)"[^>]*cx="([^"]+)" cy="([^"]+)" r="([^"]+)"')


def layers(svg):
    """{tier: [(text, box)]} from the label groups."""
    out = {}
    for tier, size, body in re.findall(r'<g class="sw-labels sw-lt(\d)" font-size="([^"]+)">(.*?)</g>', svg):
        items = []
        for attrs, text in TEXT.findall(body):
            a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
            t = html.unescape(text)
            items.append((t, text_box(t, float(size), float(a["x"]), float(a["y"]), a.get("text-anchor", "start"))))
        out[int(tier)] = items
    return out


def view_height(svg):
    return float(re.search(r'viewBox="0 0 [^ ]+ ([^"]+)"', svg).group(1))


@pytest.mark.parametrize("blocked", [(), BLOCKED], ids=["all-measured", "two-blocked"])
@pytest.mark.parametrize("name", list(SETS))
def test_no_label_overlaps_another_covers_a_dot_or_runs_off_the_map(name, blocked):
    svg = render_map(report(SETS[name], blocked))
    dots = [(float(x), float(y), float(r)) for _, x, y, r in DOT.findall(svg)]
    assert len(dots) == len(SETS[name])  # every place keeps its dot, labelled or not
    found = layers(svg)
    assert sorted(found) == list(range(len(LABEL_TIERS)))
    for tier, items in found.items():
        assert items, tier
        for i, (text, box) in enumerate(items):
            assert box[0] >= 0 and box[1] >= 0 and box[2] <= 1000 and box[3] <= view_height(svg), (tier, text)
            for other, obox in items[i + 1:]:
                assert not box_hits(box, obox), (tier, text, other)
            for x, y, r in dots:
                assert not box_hits(box, (x - r, y - r, x + r, y + r)), (tier, text)


def test_layers_are_switched_by_screen_width_only_one_at_a_time():
    assert ".sw-lt1,.sw-lt2{display:none}" in _STYLE
    assert "@media (max-width:1000px){.sw-lt0{display:none}.sw-lt1{display:inline}}" in _STYLE
    assert "@media (max-width:700px){.sw-lt1{display:none}.sw-lt2{display:inline}}" in _STYLE
    sizes = [float(s) for s in re.findall(r'class="sw-labels sw-lt\d" font-size="([^"]+)"',
                                          render_map(report(SETS["default-12"])))]
    assert sizes == [11, 15, 30]


def test_close_places_share_one_label_with_leader_lines_and_keep_their_own_titles():
    svg = render_map(report(SETS["default-12"]))
    base = [t for t, _ in layers(svg)[0]]
    assert "London 38ms · Frankfurt 120ms" in base
    assert "London 38ms" not in base and "Frankfurt 120ms" not in base
    lead = re.search(r'<g class="sw-labels sw-lt0"[^>]*><path class="sw-lead" aria-hidden="true" d="([^"]+)"/>', svg)
    assert lead and lead.group(1).count("M") >= 2
    assert "<title>London 38ms</title>" in svg and "<title>Frankfurt 120ms</title>" in svg


def test_server_label_is_one_of_the_placed_labels_and_avoids_frankfurt():
    svg = render_map(report(SETS["default-12"]))
    for tier, items in layers(svg).items():
        assert [t for t, _ in items].count("Server (manual setting)") == 1, tier
    assert re.search(r'<text [^>]*font-weight="600">Server \(manual setting\)</text>', svg)


def test_a_large_cluster_shows_a_count_and_every_place_stays_in_titles():
    svg = render_map(report(SETS["europe-7"]))
    phone = [t for t, _ in layers(svg)[2]]
    assert any(re.fullmatch(r"\d places", t) for t in phone)
    for name in SETS["europe-7"]:
        assert re.search(rf"<title>{name} \d+ms</title>", svg)


def test_phone_layer_shows_fewer_labels_ranked_blocked_first():
    svg = render_map(report(SETS["all-32"], BLOCKED))
    found = layers(svg)
    assert len(found[2]) < len(found[0])
    phone = " ".join(t for t, _ in found[2])
    assert "blocked" in phone  # a blocked place is labelled before measured ones


def test_blocked_places_keep_their_marks():
    svg = render_map(report(SETS["all-32"], BLOCKED))
    for row in (SETS["all-32"].index("Frankfurt"), SETS["all-32"].index("Lagos")):
        assert f'class="sw-dot sw-none" data-row="{row}"' in svg
    assert "<title>Frankfurt blocked: connection timed out</title>" in svg


def test_rendering_is_deterministic():
    assert render_map(report(SETS["all-32"], BLOCKED)) == render_map(report(SETS["all-32"], BLOCKED))


def test_labels_add_no_style_attribute_and_no_red():
    svg = render_map(report(SETS["all-32"], BLOCKED))
    assert " style=" not in svg
    lead = _STYLE.split(".sw-lead{")[1].split("}")[0]
    assert "var(--ink)" in lead and "--pin" not in lead and "--sq" not in lead


def test_merged_and_count_labels_escape_every_name():
    def place(i, city, lat, lon):
        return LocationResult(Location("XX", city, lat, lon, 1, "Net", "isp"), "measured",
                              stages=Stages(first_byte=100 + i))
    evil = ["<b>A&B</b>", 'Q"x', "C'd"]
    r = ScanReport("https://e.com/", NOW, NOW, "0", {}, None,
                   [place(i, c, 10, 10 + i * 0.5) for i, c in enumerate(evil)], [], [], None)
    svg = render_map(r)
    assert "<b>" not in svg and "&lt;b&gt;A&amp;B&lt;/b&gt;" in svg
    merged = [t for t, _ in layers(svg)[0] if "·" in t]
    assert merged and all(e in merged[0] for e in evil)


def test_html_export_carries_the_placed_labels():
    out = render(report(SETS["europe-7"]), "html")
    assert out.count('class="sw-labels sw-lt') == 3 and "sw-lead" in out


def test_web_results_map_carries_the_placed_labels(client):
    body = {"result": json.loads(render(report(SETS["default-12"]), "json")), "limits": {}}
    r = client.post(reverse("render"), data=json.dumps(body), content_type="application/json")
    assert r.status_code == 200
    page = r.content.decode()
    assert page.count('class="sw-labels sw-lt') == 3
    assert "London 38ms · Frankfurt 120ms" in page


@pytest.mark.parametrize("stacked", [False, True], ids=["spread", "stacked"])
def test_a_500_place_report_renders_quickly_with_every_place_in_titles_and_table(stacked):
    import random
    import time
    rng = random.Random(3)
    locs = []
    for i in range(500):
        lat, lon = (50 + i * 1e-4, 8.0) if stacked else (rng.uniform(-60, 70), rng.uniform(-170, 170))
        locs.append(LocationResult(Location("XX", f"Place{i}", lat, lon, 1, "Net", "isp"), "measured",
                                   stages=Stages(first_byte=100 + i)))
    r = ScanReport("https://e.com/", NOW, NOW, "0", {}, None, locs, [], [], None)
    t = time.perf_counter()
    svg = render_map(r)
    assert time.perf_counter() - t < 2  # generous; it takes a few milliseconds
    titles = re.findall(r'<circle class="sw-dot[^>]*><title>(Place\d+) \d+ms</title>', svg)
    assert sorted(titles) == sorted(f"Place{i}" for i in range(500))
    assert all(items == [] for items in layers(svg).values())  # above the limit: dots and titles only
    out = render(r, "html")
    assert all(f">Place{i}" in out or f"Place{i}," in out or f"Place{i}<" in out for i in range(500))
