"""The logo: one SVG, no outside requests, decorative next to the visible name."""
import re
from pathlib import Path

from django.test import Client
from django.urls import reverse

from speedward.reports import render
from tests.test_reports import rep

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "speedward" / "web" / "static" / "img" / "logo.svg"


def test_logo_svg_is_self_contained_and_matches_the_docs_copy():
    svg = LOGO.read_text()
    assert svg.startswith("<svg") and 'aria-label="speedward"' in svg
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", svg)  # no outside references
    assert "<script" not in svg and "href" not in svg
    assert (ROOT / "docs" / "logo.svg").read_text() == svg  # README copy cannot drift


def test_logo_follows_the_colour_scheme_and_keeps_red_to_the_server_square():
    svg = LOGO.read_text()
    assert "prefers-color-scheme:dark" in svg
    assert svg.count("#C62F17") == 1  # the one light-theme red: the server square only
    assert svg.count('class="a"') == 1


def test_index_links_the_favicon_and_shows_a_decorative_mark_next_to_the_name():
    body = Client().get(reverse("index")).content.decode()
    assert re.search(r'<link rel="icon" type="image/svg\+xml" href="/static/img/logo\.svg">', body)
    brand = re.search(r'<div class="brand">(.*?)</div>', body, re.S).group(1)
    assert 'aria-hidden="true"' in brand and "<h1>speedward</h1>" in brand
    assert "style=" not in brand  # strict CSP: no inline style attributes in the page


def test_the_html_report_carries_the_mark_and_an_inline_favicon_with_no_outside_request():
    html = render(rep(), "html")
    assert 'rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,' in html
    assert re.search(r'<div class="kicker"><svg class="logo"[^>]*aria-hidden="true"', html)
    assert not re.search(r'<(link|img|script|iframe)\b[^>]*(src|href)="https?://', html)  # nothing is fetched


def test_readme_shows_the_logo():
    assert 'src="docs/logo.svg"' in (ROOT / "README.md").read_text()
