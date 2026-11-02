"""'Who can reach it' in the exports, map and CLI text: wording, escaping, nothing for old reports."""
import json

from speedward.core.models import ScanReport
from speedward.core.reach import reach_grid
from speedward.reports import render
from speedward.reports.map import render_map
from speedward.reports.reach import cell_text, cli_lines, finding, markdown_lines, render_reach
from tests.test_reach import blocked, failed, ok, rep, rr


def mockup_report():
    """The approved mockup's four places: Frankfurt fine, Tokyo blocked over IPv6, Seoul blocked
    everywhere (port closed), Lagos no data (test failed)."""
    locs = [ok("Frankfurt", 31), ok("Tokyo", 190), blocked("Seoul", "connection refused"), failed("Lagos")]
    reach = [rr("ipv6", 0, ms=37), rr("ipv6", 1, "blocked", "connection timed out"),
             rr("ipv6", 2, "blocked", "connection refused"), rr("ipv6", 3, "test_failed", "x"),
             rr("http", 0, redirect="https", http_status=301), rr("http", 1, redirect="https", http_status=301),
             rr("http", 2, "blocked", "connection refused"), rr("http", 3, "test_failed", "x"),
             rr("dns", 0, ms=5), rr("dns", 0, ms=7, resolver="8.8.8.8"), rr("dns", 1, ms=12),
             rr("dns", 1, ms=14, resolver="8.8.8.8"), rr("dns", 2, ms=9), rr("dns", 2, ms=9, resolver="8.8.8.8"),
             rr("dns", 3, "test_failed", "x"), rr("dns", 3, "test_failed", "x", resolver="8.8.8.8"),
             rr("port", 0, ms=1.2, loss_pct=0), rr("port", 1, ms=98, loss_pct=0),
             rr("port", 2, "blocked", "no answer on port 443 (closed or filtered)"), rr("port", 3, "not_run", "y")]
    return rep(locs, reach, options={"locations": [], "checks": ["ipv6", "http", "dns", "port"]})


def texts(report, row):
    g = reach_grid(report)
    return [(c["symbol"], c["word"], c["detail"]) for c in (cell_text(g, x) for x in g.rows[row].cells)]


def test_cells_are_symbol_and_word():
    r = mockup_report()
    assert texts(r, 0) == [("●", "reached", "31 ms"), ("●", "reached", "37 ms"), ("●", "→ HTTPS", "HTTP 301"),
                           ("●", "resolves", "1.1.1.1 5 ms · 8.8.8.8 7 ms"), ("●", "open", "1 ms")]
    assert texts(r, 2)[4] == ("○", "closed", "no answer on port 443 (closed or filtered)")
    assert texts(r, 2)[1] == ("○", "blocked", "connection refused")
    assert texts(r, 3)[0] == ("◌", "no data", "test failed: the test failed on the Globalping side")
    assert texts(r, 3)[4] == ("◌", "no data", "did not run: y")


def test_finding_matches_the_mockup_wording():
    text, check = finding(reach_grid(mockup_report()))
    assert text.startswith("The timings indicate: ")
    assert "2 of 3 places cannot reach the site over IPv6 (Tokyo, XX, Seoul, XX)" in text
    assert "and Seoul, XX cannot reach it at all" in text
    assert "1 of 3 places find port 443 closed (Seoul, XX)" in text
    assert check.startswith("Check: ") and "AAAA record and IPv6 firewall" in check
    assert check.endswith("whether a regional block applies.")


def test_finding_with_too_few_places_draws_no_conclusion():
    text, check = finding(reach_grid(rep([ok("A"), blocked("B")], [rr("ipv6", 0), rr("ipv6", 1, "blocked", "x")])))
    assert text.startswith("Not enough places for a conclusion: 2 places with a result, at least 3 needed")
    assert check == ""


def test_long_place_lists_are_shortened():
    locs = [blocked(f"P{i}") for i in range(8)]
    text, _ = finding(reach_grid(rep(locs, [rr("ipv6", i, "blocked", "x") for i in range(8)])))
    assert "and 3 more" in text


def test_html_export_has_the_grid_above_the_diagram_and_a_legend():
    from speedward.core.bottleneck import diagnose
    r = mockup_report()
    r.diagnosis = diagnose(r)
    html = render(r, "html")
    assert html.index("Who can reach it") < html.index("Where the time goes")
    assert 'class="rc-grid"' in html and "<th scope=\"col\">HTTPS v6</th>" in html
    assert "each dot shows the worst result for its place" in html
    assert "#C62F17" not in render_reach(mockup_report()).upper()  # no red in the panel


def test_html_escapes_hostile_place_names_and_reasons():
    r = rep([ok("<img src=x>"), ok("B"), ok("C")], [rr("ipv6", 0, "blocked", "<script>x</script>"),
                                                    rr("ipv6", 1), rr("ipv6", 2)])
    html = render(r, "html")
    assert "<script>x" not in html and "<img src=x>" not in html
    assert "&lt;script&gt;" in html


def test_markdown_has_the_table_and_escapes_pipes():
    r = rep([ok("A|B"), ok("C"), ok("D")], [rr("port", 0, "blocked", "x | y"), rr("port", 1), rr("port", 2)])
    md = render(r, "md")
    assert "## Who can reach it" in md
    assert "| Place | HTTPS v4 | Port 443 |" in md
    assert "| A\\|B, XX | ● reached 30 ms | ○ closed x \\| y |" in md
    lines = markdown_lines(r)
    assert all(line.count("|") - line.count("\\|") in (0, 4) for line in lines)


def test_json_carries_results_and_grid():
    data = json.loads(render(mockup_report(), "json"))
    assert len(data["reach"]) == 20
    assert data["reach_grid"]["verdict"] == "some_blocked"
    assert ScanReport.from_dict(data).reach is not None


def test_old_report_renders_no_panel_no_legend_no_cli_lines():
    r = rep([ok("A")])
    assert render_reach(r) == "" and markdown_lines(r) == [] and cli_lines(r) == []
    html = render(r, "html")
    assert "Who can reach it" not in html and "worst result" not in html


def test_map_dot_uses_the_worst_result():
    svg = render_map(mockup_report())
    assert 'class="sw-dot sw-part" data-row="1"' in svg  # Tokyo: measured, blocked over IPv6
    assert "Tokyo 4ms, blocked: HTTPS v6" in svg
    assert 'class="sw-dot" data-row="0"' in svg  # Frankfurt: nothing blocked
    assert 'class="sw-dot sw-none" data-row="2"' in svg  # Seoul's main test was blocked already
    assert "also blocked: HTTPS v6, HTTP, Port 443" in svg


def test_no_data_cells_never_mark_a_dot():
    r = rep([ok("A")], [rr("ipv6", 0, "test_failed", "x")])
    assert 'class="sw-dot" data-row="0"' in render_map(r)


def test_cli_lines():
    lines = cli_lines(mockup_report())
    assert lines[0].startswith("Who can reach it: The timings indicate:")
    assert lines[1].startswith("Check: ")
