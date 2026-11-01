import re

from speedward.core.locations import CATALOG, CONTINENTS, DEFAULT_LOCATIONS, per_continent
from speedward.reports.map import render_picker_map


def test_catalog_names_unique_and_sized():
    names = [p.name for p in CATALOG]
    assert len(names) == len(set(names)) and 30 <= len(names) <= 50


def test_defaults_are_a_subset_of_the_catalog():
    assert {d["magic"] for d in DEFAULT_LOCATIONS} <= {p.name for p in CATALOG}
    assert len(DEFAULT_LOCATIONS) == 12


def test_continents_known_and_all_covered():
    assert {p.continent for p in CATALOG} == set(CONTINENTS)


def test_coordinates_in_range_and_no_commas_in_names():
    for p in CATALOG:
        assert -90 <= p.lat <= 90 and -180 <= p.lon <= 180
        assert "," not in p.name and len(p.name) <= 40


def test_per_continent_one_each_from_defaults():
    picks = per_continent()
    assert len(picks) == len(CONTINENTS)
    assert set(picks) <= {d["magic"] for d in DEFAULT_LOCATIONS}


def test_picker_has_one_dot_per_place_with_state():
    svg = render_picker_map(["London", "Tokyo"])
    assert svg.count('class="sw-pick"') == len(CATALOG)
    assert re.search(r'data-place="London"[^>]*aria-checked="true"', svg)
    assert re.search(r'data-place="Paris"[^>]*aria-checked="false"', svg)
    assert len(re.findall(r'<circle[^>]*aria-checked="true"', svg)) == 2
    assert 'role="checkbox"' in svg and 'tabindex="0"' in svg and "<title>London" in svg


def test_picker_has_no_external_resources_or_scripts():
    svg = render_picker_map([])
    assert "http://" not in svg.replace("http://www.w3.org", "") and "https://" not in svg
    assert "<script" not in svg and "<image" not in svg and "url(" not in svg


def test_picker_escapes_dynamic_text(monkeypatch):
    from speedward.core.locations import Place
    from speedward.reports import map as m
    monkeypatch.setattr(m, "CATALOG", [Place('<b>"x"', "<i>", "Asia", 0, 0)])
    svg = render_picker_map([])
    assert "<b>" not in svg and "<i>" not in svg and "&lt;b&gt;" in svg and "&quot;x&quot;" in svg


def test_picker_has_a_focusable_labelled_server_pin_and_a_lines_layer():
    svg = render_picker_map([])
    assert svg.count('class="sw-pin sw-unset"') == 1 and 'role="application"' in svg
    assert 'aria-label="Server position pin' in svg and 'tabindex="0"' in svg
    assert 'aria-describedby="pin-help"' in svg
    assert 'class="sw-preview"' in svg
    assert "<script" not in svg and "http" not in svg.replace("http://www.w3.org", "")
