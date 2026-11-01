import pytest

from speedward.core.locations import CONTINENT_OF, DEFAULT_LOCATIONS, parse_locations
from speedward.core.target import normalise_target


def test_normalise_adds_https():
    assert normalise_target("Example.com") == "https://example.com/"


def test_normalise_idn_and_trailing_dot_give_same_string_as_ascii_form():
    a = normalise_target("https://Bücher.example.")
    b = normalise_target("https://xn--bcher-kva.example/")
    assert a == b == "https://xn--bcher-kva.example/"


def test_normalise_rejects_ftp():
    with pytest.raises(ValueError):
        normalise_target("ftp://example.com/")


@pytest.mark.parametrize("bad", ["", "   ", "https://user:pass@example.com/", "https://u@example.com/",
                                 "https://exa mple.com/", "https://example.com/\x00", "https://exa\nmple.com",
                                 "http://"])
def test_normalise_rejects_hostile_input(bad):
    with pytest.raises(ValueError):
        normalise_target(bad)


def test_normalise_drops_fragment_keeps_path_and_query():
    assert normalise_target("http://example.com/a?b=1#frag") == "http://example.com/a?b=1"


def test_default_locations_cover_six_continents():
    assert 10 <= len(DEFAULT_LOCATIONS) <= 12
    for loc in DEFAULT_LOCATIONS:
        assert set(loc) == {"magic"}
    continents = {CONTINENT_OF[loc["magic"]] for loc in DEFAULT_LOCATIONS}
    assert continents == {"Europe", "North America", "South America", "Africa", "Asia", "Oceania"}


def test_parse_locations_default_is_a_copy():
    got = parse_locations(None)
    assert got == DEFAULT_LOCATIONS and got is not DEFAULT_LOCATIONS
    got.append({"magic": "x"})
    assert len(DEFAULT_LOCATIONS) != len(got)
    assert parse_locations("  ") == DEFAULT_LOCATIONS


def test_parse_locations_override_with_filters():
    assert parse_locations(" cogent+europe+datacenter-network , Berlin") == [
        {"magic": "cogent+europe+datacenter-network"}, {"magic": "Berlin"}]


@pytest.mark.parametrize("bad", ["Berlin,,Tokyo", "Berlin,", ",Berlin"])
def test_parse_locations_rejects_empty_part(bad):
    with pytest.raises(ValueError):
        parse_locations(bad)
