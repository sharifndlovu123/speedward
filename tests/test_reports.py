import json
import re
from datetime import datetime, timezone

from speedward.core.models import (BrowserResult, DnsResult, Hop, Location, LocationResult, PathResult,
                                   ScanReport, ServerPosition, Stages)
from speedward.reports import FORMATS, render

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def loc(city="Tokyo"):
    return Location("JP", city, 35.7, 139.7, 1, "Net", "isp")


def ok(city="Tokyo", fb=920, **kw):
    return LocationResult(loc(city), "measured",
                          stages=Stages(dns=10, connect=20, tls=None, first_byte=fb, download=30, total=fb + 30),
                          **kw)


def rep(locations=None, **kw):
    base = dict(target="https://example.com/", started=NOW, finished=NOW, version="0.1.0", options={},
                server=ServerPosition(52.5, 13.4, "t"), locations=locations or [ok()], paths=[], dns=[],
                browser=None)
    base.update(kw)
    return ScanReport(**base)


def test_formats():
    assert FORMATS == ("json", "md", "html")


def test_json_has_schema_version_and_no_token(monkeypatch):
    monkeypatch.setenv("GLOBALPING_TOKEN", "sekrit-token-123")
    out = render(rep(), "json")
    data = json.loads(out)
    assert data["schema_version"] == 1
    assert "sekrit-token-123" not in out
    for fmt in FORMATS:
        assert "sekrit-token-123" not in render(rep(), fmt)


def test_html_report_has_no_external_urls():
    html = render(rep(locations=[ok(), LocationResult(loc("Beijing"), "blocked", "timed out")]), "html")
    urls = re.findall(r"https?://[^\s\"'<>)]+", html)
    allowed = {"https://globalping.io"}
    assert [u for u in urls if u not in allowed] == [], urls
    assert 'src="http' not in html and 'href="http' not in html.replace('href="https://globalping.io"', "")
    assert "https://globalping.io" in html


def test_excluded_count_is_stated():
    locs = [ok(), LocationResult(loc("Beijing"), "blocked", "timed out"),
            LocationResult(loc("Lagos"), "test_failed", "probe error")]
    html = render(rep(locations=locs), "html")
    md = render(rep(locations=locs), "md")
    for text in (html, md):
        assert "2 locations excluded" in text
        assert "1 blocked" in text


def test_failed_row_shows_no_data_and_reason_no_numbers():
    html = render(rep(locations=[LocationResult(loc("Beijing"), "blocked", "timed out")]), "html")
    row = re.search(r"<tr class=\"excluded\".*?</tr>", html, re.S).group(0)
    assert "no data" in row and "timed out" in row
    assert "ms" not in row.replace("no data", "")


def test_html_escapes_hostile_text():
    html = render(rep(locations=[LocationResult(loc("<b>x</b>"), "blocked", "<script>alert(1)</script>")]), "html")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_stage_none_is_omitted_not_zero():
    html = render(rep(), "html")
    assert "TLS" not in html.split("Stage breakdown")[1].split("</section>")[0]
    assert "DNS" in html


def test_cache_gain_na_text():
    html = render(rep(), "html")
    assert "n/a" in html and "needs the same test machine for both runs" in html
    assert "Repeat request from the same test machine; not a browser cache test." in html
    md = render(rep(), "md")
    assert "Repeat request from the same test machine; not a browser cache test." in md
    assert "n/a" in md


def test_cache_gain_shown_when_available():
    r = rep(locations=[ok(fb=1000, repeat_stages=Stages(first_byte=500), repeat_same_probe=True)])
    assert "50%" in render(r, "html")


def test_dns_differing_answers_flagged():
    dns = [DnsResult(loc("A"), "measured", "", ["1.1.1.1"], 5), DnsResult(loc("B"), "measured", "", ["2.2.2.2"], 6)]
    assert "Answers differ" in render(rep(dns=dns), "html")
    same = [DnsResult(loc("A"), "measured", "", ["1.1.1.1"], 5), DnsResult(loc("B"), "measured", "", ["1.1.1.1"], 6)]
    assert "Answers differ" not in render(rep(dns=same), "html")


def test_paths_hops_with_loss():
    p = PathResult(loc("A"), [Hop(1, "router.local", 1, 1.5, 20.0)], "measured", "")
    html = render(rep(paths=[p]), "html")
    assert "router.local" in html and "20" in html


def test_server_identity_not_available():
    html = render(rep(), "html")
    assert "not available" in html


def test_browser_panel():
    b = BrowserResult(123456, 12, 800.0, [("https://x/y.js", 5000)])
    html = render(rep(browser=b), "html")
    assert "Page weight" in html and "12 requests" in html
    assert "Page weight" not in render(rep(), "html")


def test_markdown_report_has_the_table():
    md = render(rep(locations=[ok(), LocationResult(loc("Beijing"), "blocked", "timed out")]), "md")
    assert "| Location |" in md and "| --- |" in md
    assert "920 ms" in md and "no data" in md and "timed out" in md
