import json
import re
from datetime import UTC, datetime

from speedward.core.models import BrowserResult, DnsResult, Hop, Location, LocationResult, PathResult, ScanReport, ServerPosition, Stages
from speedward.reports import FORMATS, render

NOW = datetime(2026, 11, 1, tzinfo=UTC)


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


SAME_NOTE = "Repeat request from the same test machine; not a browser cache test."


def test_cache_gain_na_text():
    html = render(rep(), "html")
    assert "n/a" in html and "no repeat request was measured" in html
    md = render(rep(), "md")
    assert "n/a" in md and "no repeat request was measured" in md
    for text in (html, md):  # no repeat at all: the same-machine note would be untrue
        assert SAME_NOTE not in text
        assert "needs the same test machine" not in text


def test_same_probe_note_only_when_true():
    r = rep(locations=[ok(fb=1000, repeat_stages=Stages(first_byte=500), repeat_same_probe=True)])
    for fmt in ("html", "md"):
        out = render(r, fmt)
        assert SAME_NOTE in out and "different probe" not in out


def test_different_probe_repeat_is_labelled_and_left_out_of_cache_gain():
    r = rep(locations=[ok(fb=1000, repeat_stages=Stages(first_byte=500), repeat_probe="different probe")])
    for fmt in ("html", "md"):
        out = render(r, fmt)
        assert "500 ms" in out and "repeat request (different probe)" in out
        assert "50%" not in out and "n/a" in out
        assert "the repeat ran on a different test machine" in out
        assert SAME_NOTE not in out
    data = json.loads(render(r, "json"))
    assert data["locations"][0]["repeat_probe"] == "different probe"


def test_same_city_repeat_and_ping_labels():
    r = rep(locations=[ok(fb=1000, repeat_stages=Stages(first_byte=500), repeat_probe="different probe, same city",
                          ping_ms=40, ping_probe="different probe"),
                       ok("Osaka", fb=1000, repeat_stages=Stages(first_byte=400), repeat_same_probe=True)])
    for fmt in ("html", "md"):
        out = render(r, fmt)
        assert "repeat request (different probe, same city)" in out
        assert "40 ms" in out and "delay from a different probe" in out
        assert "60%" in out  # only the same-probe pair counts
        assert "rows marked &#34;different probe&#34;" in out or 'rows marked "different probe"' in out


def test_path_and_dns_from_another_probe_are_labelled():
    p = PathResult(loc("A"), [Hop(1, "router.local", 1, 1.5, 0.0)], "measured", "", "different probe")
    d = DnsResult(loc("B"), "measured", "", ["1.1.1.1"], 5, "different probe, same city")
    for fmt in ("html", "md"):
        out = render(rep(paths=[p], dns=[d]), fmt)
        assert "A, JP (different probe)" in out
        assert "B, JP (different probe, same city)" in out


NOT_RUN = {"locations": [], "not_run": ["repeat test: Globalping limit reached",
                                        "ping: the test failed on the Globalping side"]}


def test_skipped_measurements_are_shown_with_their_effect():
    r = rep(options=NOT_RUN)
    for fmt in ("html", "md"):
        out = render(r, fmt)
        assert "Repeat test: Globalping limit reached, so the Cache column and cache gain have no figures." in out
        assert "Ping: the test failed on the Globalping side, so the Delay column has no figures." in out
        assert "repeat test: Globalping limit reached" in out.split("Cache gain")[1].split("Blocked")[0]
        assert "no repeat request was measured" not in out


def test_unknown_skipped_text_is_never_shown():
    r = rep(options={"not_run": ["ping: <b>evil.example</b>"]})
    for fmt in ("html", "md"):
        assert "evil.example" not in render(r, fmt)


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


# --- "Answered by" and "Position source" (Task 16) ----------------------------------------------

def placed_locations():
    return [
        ok("Tokyo", 38, server_ip="104.16.1.1", edge_code="NRT",
           server_position=ServerPosition(35.77, 140.39, "cdn header")),
        ok("London", 22, server_ip="151.101.1.1", server_position=ServerPosition(51.5, -0.1, "database")),
        ok("Lagos", 240, server_ip="104.16.9.9", edge_note="edge location uncertain", edge_reason="shared address"),
        ok("Oslo", 50),  # a report from before Task 16: nothing known
        LocationResult(loc("Beijing"), "blocked", "timed out"),
    ]


def md_row(md, city):
    return next(line for line in md.splitlines() if line.startswith(f"| {city},"))


def test_markdown_has_answered_by_and_position_source_columns():
    md = render(rep(locations=placed_locations(), server=None), "md")
    assert "| Answered by | Position source |" in md
    assert "| edge NRT (Tokyo) | CDN header (cf-ray -NRT) |" in md_row(md, "Tokyo")
    assert "| 151.101.1.1 | geo-IP database |" in md_row(md, "London")
    assert "| 104.16.9.9 | edge location uncertain (shared address) |" in md_row(md, "Lagos")
    assert "| not available | not available |" in md_row(md, "Oslo")
    assert "| no data | no data |" in md_row(md, "Beijing")
    header = next(line for line in md.splitlines() if line.startswith("| Location |"))
    assert header.count("|") == md_row(md, "Tokyo").count("|")


def test_html_report_has_the_new_columns():
    html = render(rep(locations=placed_locations(), server=None), "html")
    assert "<th>Answered by</th><th>Position source</th>" in html
    assert "<td>edge NRT (Tokyo)</td><td>CDN header (cf-ray -NRT)</td>" in html
    assert "<td>104.16.9.9</td><td>edge location uncertain (shared address)</td>" in html
    assert html.count('class="sw-sq') == 2


def test_json_carries_positions_and_sources():
    data = json.loads(render(rep(locations=placed_locations(), server=None), "json"))
    tokyo, london, lagos = data["locations"][:3]
    assert tokyo["server_position"] == {"lat": 35.77, "lon": 140.39, "source": "cdn header"}
    assert tokyo["edge_code"] == "NRT"
    assert london["server_position"]["source"] == "database"
    assert (lagos["server_position"], lagos["edge_note"], lagos["edge_reason"]) == (
        None, "edge location uncertain", "shared address")


def test_markdown_says_where_the_server_square_came_from():
    md = render(rep(server=ServerPosition(1, 2, "manual (pin)")), "md")
    assert "Server position on the map: manual (pin)" in md
    md = render(rep(server=ServerPosition(1, 2, "manual")), "md")
    assert "Server position on the map: manual (setting)" in md


def test_new_columns_are_escaped_in_markdown_and_html():
    hostile = [ok("Tokyo", 38, server_ip="1.2.3.4|<b>x</b>")]
    md = render(rep(locations=hostile, server=None), "md")
    assert "1.2.3.4\\|\\<b\\>x\\</b\\>" in md_row(md, "Tokyo")  # Task 22: full Markdown escaping
    html = render(rep(locations=hostile, server=None), "html")
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_numeral_splits_the_unit_and_keeps_the_text():
    from speedward.reports._view import numeral
    assert numeral("412 ms") == ("412", " ms")
    assert numeral("-38%") == ("-38%", "")
    assert numeral("no data") == ("no data", "")
    assert numeral("n/a") == ("n/a", "")
    assert "".join(numeral("1.5 KB")) == "1.5 KB"


def test_summary_figures_keep_wording_and_never_invent_a_number():
    def text(html):
        return re.sub(r"<[^>]+>", "", html)
    failed = [LocationResult(loc("Beijing"), "blocked", "timed out")]
    html = render(rep(locations=failed), "html")
    figs = text(re.search(r'<section class="figures".*?</section>', html, re.S).group(0))
    assert "Average first byteno datameasured locations only" in figs.replace("\n", "")
    assert "the site refused or timed out" in figs
    assert "Blocked1 / 1" in figs.replace("\n", "")
    ok_html = render(rep(), "html")
    figs = text(re.search(r'<section class="figures".*?</section>', ok_html, re.S).group(0))
    assert "920 ms" in figs


def test_html_export_uses_the_configured_speed_thresholds():
    out = render(rep(), "html", fast_ms=50, slow_ms=100)
    assert "green up to 50 ms first byte, red from 100 ms" in out
    assert "green up to 300 ms first byte, red from 800 ms" in render(rep(), "html")
    assert render(rep(), "md", fast_ms=50, slow_ms=100) == render(rep(), "md")


def test_markdown_path_note_is_escaped():
    p = PathResult(loc("A"), [], "test_failed", "bad | pipe\nnew line")
    md = render(rep(paths=[p]), "md")
    assert "bad \\| pipe new line" in md
