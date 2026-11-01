from datetime import datetime, timezone

from speedward.core.models import (Location, LocationResult, ScanReport, ServerPosition, Stages)
from speedward.reports.map import render_map

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


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
    from speedward.reports.map import render_picker_map
    svg = render_map(report([ok("Tokyo", 100)]))
    assert ".sw-dot:focus-visible" in svg and ".sw-pick:focus-visible" in svg
    assert "stroke-width" in svg.split(".sw-dot:focus-visible")[1].split("}")[0]
    assert "outline:2px" not in svg  # no SVG outline, keyboard-only stroke
    assert ".sw-dot.sw-sel" in svg
