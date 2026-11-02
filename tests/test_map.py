import re
from datetime import UTC, datetime

from speedward.core.models import Location, LocationResult, ScanReport, ServerPosition, Stages
from speedward.reports.map import render_map

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def loc(city="Tokyo", lat=35.7, lon=139.7):
    return Location("JP", city, lat, lon, 1, "Net", "isp")


def ok(city, fb, **kw):
    return LocationResult(loc(city, **kw), "measured", stages=Stages(first_byte=fb))


def report(locations, server=ServerPosition(52.5, 13.4, "test")):
    return ScanReport("https://example.com/", NOW, NOW, "0.1.0", {}, server, locations, [], [], None)


def test_blocked_location_is_dimmed_dashed_with_reason():
    svg = render_map(report([LocationResult(loc("Beijing"), "blocked", "the site timed out")]))
    assert "stroke-dasharray" in svg
    assert "opacity" in svg
    assert "Beijing" in svg and "blocked" in svg and "the site timed out" in svg


def test_failed_location_has_no_timing_text():
    svg = render_map(report([LocationResult(loc("Lagos"), "test_failed", "probe error")]))
    assert "Lagos" in svg and "probe error" in svg
    assert "ms" not in svg.replace("transform", "")


def test_map_escapes_hostile_reason():
    svg = render_map(report([LocationResult(loc("Evil<script>"), "blocked", "<script>alert(1)</script>")]))
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_unknown_server_position_is_labelled():
    assert "Server position unknown" in render_map(report([ok("Tokyo", 100)], server=None))


def test_known_server_has_no_unknown_label():
    assert "Server position unknown" not in render_map(report([ok("Tokyo", 100)]))


def test_colour_by_first_byte_thresholds():
    r = report([ok("A", 100), ok("B", 500), ok("C", 900)])
    svg = render_map(r)
    assert svg.count("var(--fast)") >= 1 and svg.count("var(--mid)") >= 1 and svg.count("var(--slow)") >= 1
    assert "Tokyo" not in svg
    assert "100ms" in svg and "900ms" in svg


def test_projection_places_point():
    svg = render_map(report([ok("Null", 100, lat=0.0, lon=0.0)], server=ServerPosition(0, 0, "t")), width=1000)
    assert 'cx="500"' in svg and 'cy="250"' in svg


def test_location_without_coordinates_listed_under_map():
    r = report([ok("Nowhere", 100, lat=None, lon=None)])
    svg = render_map(r)
    assert "Not shown on map: Nowhere" in svg


def test_missing_first_byte_on_measured_is_not_a_number():
    r = report([LocationResult(loc("Oslo"), "measured", stages=Stages())])
    svg = render_map(r)
    assert "no data" in svg and "ms" not in svg.replace("transform", "")


def test_labels_are_escaped_exactly_once():
    svg = render_map(report([LocationResult(loc("Côte d'Ivoire"), "blocked", 'AT&T said "no"')]))
    assert "d&#x27;Ivoire" in svg and "AT&amp;T" in svg and "&quot;no&quot;" in svg
    for bad in ("&amp;amp;", "&amp;#x27;", "&amp;quot;"):
        assert bad not in svg


def test_dots_have_a_stroke_based_keyboard_focus_ring():
    svg = render_map(report([ok("Tokyo", 100)]))
    assert ".sw-dot:focus-visible" in svg and ".sw-pick:focus-visible" in svg
    assert "stroke-width" in svg.split(".sw-dot:focus-visible")[1].split("}")[0]
    assert "outline:2px" not in svg  # no SVG outline, keyboard-only stroke
    assert ".sw-dot.sw-sel" in svg


def test_focus_ring_rule_comes_after_the_checked_rule_for_picks():
    from speedward.reports.map import _PICK_STYLE
    checked = _PICK_STYLE.index('.sw-pick[aria-checked="true"]{')
    ring = _PICK_STYLE.index('[aria-checked="true"]:focus-visible{stroke:var(--ink)')
    assert ring > checked


def test_unknown_server_position_says_how_to_fix_and_draws_no_lines():
    svg = render_map(report([ok("Tokyo", 100)], server=None))
    # A CLI report or exported file cannot drop a pin: only the settings are named there.
    assert "Server position unknown: set GEOIP_DB or SERVER_LAT/SERVER_LON" in svg
    assert "drop the pin" not in svg
    assert not re.search(r'<path d="M[^"]* Q', svg)
    web = render_map(report([ok("Tokyo", 100)], server=None), pin_hint=True)
    assert "Server position unknown: set GEOIP_DB, drop the pin or set SERVER_LAT/SERVER_LON" in web


def test_pinned_server_draws_one_arc_per_place_and_says_pin():
    svg = render_map(report([ok("Tokyo", 100), ok("Lagos", 900)], server=ServerPosition(1, 2, "manual (pin)")))
    assert len(re.findall(r'<path d="M[^"]* Q', svg)) == 2
    assert "Server (pin)" in svg


# --- squares at the server that answered each probe (Task 16) -----------------------------------

ARC = r'<path d="M[^"]* Q[^"]*"'
SQUARE = r'<rect class="sw-sq[^"]*"'


def placed(city, fb, ip, pos=None, source="database", code=None, note=None, reason=None, **kw):
    sp = ServerPosition(*pos, source) if pos else None
    return LocationResult(loc(city, **kw), "measured", stages=Stages(first_byte=fb), server_ip=ip,
                          server_position=sp, edge_code=code, edge_note=note, edge_reason=reason)


def cdn_report():
    return report([
        placed("Tokyo", 38, "104.16.1.1", (35.77, 140.39), "cdn header", "NRT", lat=35.7, lon=139.7),
        placed("London", 22, "151.101.1.1", (51.5, -0.1), lat=51.5, lon=-0.1),
        placed("Lagos", 240, "104.16.9.9", note="edge location uncertain", reason="shared address", lat=6.5, lon=3.4),
        LocationResult(loc("Beijing", lat=39.9, lon=116.4), "blocked", "timed out"),
    ], server=None)


def test_cdn_case_draws_a_square_and_a_line_per_placed_result_only():
    svg = render_map(cdn_report())
    assert len(re.findall(SQUARE, svg)) == 2
    assert len(re.findall(ARC, svg)) == 2  # Lagos (uncertain) and Beijing (blocked): no square, no line
    assert "Server position unknown" not in svg


def test_square_lines_are_coloured_by_speed():
    svg = render_map(cdn_report())
    arcs = re.findall(r'<path d="M[^"]* Q[^"]*" stroke="([^"]+)"', svg)
    assert arcs == ["var(--fast)", "var(--fast)"]


def test_square_tooltip_names_ip_source_and_cdn_edge_warning():
    svg = render_map(cdn_report())
    titles = re.findall(r'<rect class="sw-sq[^>]*aria-label="([^"]*)"[^>]*><title>([^<]*)</title>', svg)
    assert len(titles) == 2
    tokyo = next(t for label, t in titles if "Tokyo" in t)
    assert "104.16.1.1" in tokyo and "CDN header (cf-ray -NRT)" in tokyo and "edge NRT (Tokyo)" in tokyo
    assert "CDN edge, not necessarily your origin" in tokyo
    london = next(t for label, t in titles if "London" in t)
    assert "151.101.1.1" in london and "geo-IP database" in london
    assert "CDN edge, not necessarily your origin" in london  # the IPs differ between probes
    for label, title in titles:
        assert label == title


def test_squares_are_keyboard_reachable_with_an_accessible_name():
    svg = render_map(cdn_report())
    for tag in re.findall(r"<rect class=\"sw-sq[^>]*>", svg):
        assert 'tabindex="0"' in tag and 'role="img"' in tag and "aria-label=" in tag
    assert ".sw-sq:focus-visible" in svg


def test_uncertain_result_says_so_and_draws_no_square():
    svg = render_map(cdn_report())
    assert "Lagos 240ms, edge location uncertain" in svg
    assert "edge location uncertain (shared address), no square drawn" in svg


def test_single_origin_draws_one_server_square_with_lines_from_every_probe():
    r = report([placed(c, fb, "93.184.216.34", (50.1, 8.7), lat=la, lon=lo) for c, fb, la, lo in
                (("Tokyo", 400, 35.7, 139.7), ("New York", 900, 40.7, -74.0), ("London", 30, 51.5, -0.1))]
               + [LocationResult(loc("Beijing", lat=39.9, lon=116.4), "blocked", "timed out")], server=None)
    svg = render_map(r)
    assert len(re.findall(SQUARE, svg)) == 1
    assert 'class="sw-sq sw-server"' in svg and "Server (all probes reached the same IP)" in svg
    assert len(re.findall(ARC, svg)) == 4  # three measured, plus the blocked one dashed
    assert svg.count("stroke-dasharray=") == 1  # the dashed line (the CSS dashes no-data dots)
    assert "CDN edge, not necessarily your origin" not in svg
    assert "93.184.216.34" in svg and "geo-IP database" in svg


def test_same_ip_at_different_cdn_edges_is_not_a_single_origin():
    r = report([placed("Tokyo", 38, "104.16.1.1", (35.77, 140.39), "cdn header", "NRT", lat=35.7, lon=139.7),
                placed("Berlin", 20, "104.16.1.1", (52.56, 13.29), "cdn header", "TXL", lat=52.5, lon=13.4)],
               server=None)
    svg = render_map(r)
    assert len(re.findall(SQUARE, svg)) == 2 and 'class="sw-sq sw-server"' not in svg
    assert svg.count("CDN edge, not necessarily your origin") >= 2


def test_configured_position_wins_for_the_single_server_square():
    r = cdn_report()
    r.server = ServerPosition(1.0, 2.0, "manual")
    svg = render_map(r)
    assert len(re.findall(SQUARE, svg)) == 1 and "Server (manual setting)" in svg
    assert len(re.findall(ARC, svg)) == 4  # every placed probe, the blocked one dashed


def test_pin_square_is_labelled_as_a_claim():
    svg = render_map(report([ok("Tokyo", 100)], server=ServerPosition(1, 2, "manual (pin)")))
    assert "Server (pin)" in svg and "manual (pin), your own claim" in svg


def test_square_text_is_escaped():
    r = report([placed("Evil<b>", 38, "<script>x</script>", (35.77, 140.39), lat=35.7, lon=139.7)], server=None)
    svg = render_map(r)
    assert "<script>" not in svg and "&lt;script&gt;" in svg and "<b>" not in svg


def test_results_at_one_spot_share_one_square():
    r = report([placed("Tokyo", 38, "151.101.1.1", (35.6, 139.8), lat=35.7, lon=139.7),
                placed("Osaka", 40, "151.101.1.1", (35.6, 139.8), lat=34.7, lon=135.5),
                placed("London", 20, "151.101.2.2", (51.5, -0.1), lat=51.5, lon=-0.1)], server=None)
    svg = render_map(r)
    assert len(re.findall(SQUARE, svg)) == 2 and len(re.findall(ARC, svg)) == 3
    assert "Tokyo, Osaka" in svg


def test_edge_in_the_same_city_frames_the_circle_instead_of_hiding_under_it():
    # Tokyo's edge (NRT) is a few pixels from the Tokyo circle: an outline square around it, drawn on top.
    r = report([placed("Tokyo", 38, "104.16.1.1", (35.77, 140.39), "cdn header", "NRT", lat=35.69, lon=139.69),
                placed("London", 22, "151.101.1.1", (40.0, -100.0), lat=51.5, lon=-0.1)], server=None)
    svg = render_map(r)
    assert len(re.findall(r'<rect class="sw-sq sw-here"', svg)) == 1
    assert len(re.findall(r'<rect class="sw-sq"', svg)) == 1  # London's is far away: a filled square
    assert svg.index('class="sw-sq sw-here"') > svg.index('class="sw-dot"')
    assert ".sw-here{fill:none" in svg


def test_no_single_origin_label_when_another_probe_got_a_different_ip():
    r = report([placed("Tokyo", 400, "93.184.216.34", (50.1, 8.7), lat=35.7, lon=139.7),
                placed("London", 30, "93.184.216.34", (50.1, 8.7), lat=51.5, lon=-0.1),
                placed("Lagos", 200, "151.101.1.1", note="position not available",
                       reason="not in the geo-IP database", lat=6.5, lon=3.4)], server=None)
    svg = render_map(r)
    assert "all probes reached the same IP" not in svg and 'class="sw-sq sw-server"' not in svg
    assert "CDN edge, not necessarily your origin" in svg


def test_no_single_origin_label_for_one_placed_probe_among_several():
    r = report([placed("Tokyo", 400, "93.184.216.34", (50.1, 8.7), lat=35.7, lon=139.7),
                placed("London", 30, "151.101.1.1", note="position not available",
                       reason="private address", lat=51.5, lon=-0.1),
                placed("Lagos", 200, "151.101.2.2", note="edge location uncertain",
                       reason="shared address", lat=6.5, lon=3.4)], server=None)
    assert "all probes reached the same IP" not in render_map(r)
