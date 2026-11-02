"""The words and the block diagram for "where the time goes" (speedward/reports/bottleneck.py), and the
panel in every export. The panel never states a cause: it says what the timings indicate."""
import json
import re

import pytest

from speedward.core.bottleneck import diagnose
from speedward.reports import render
from speedward.reports.bottleneck import cli_lines, pct, render_bottleneck, view
from tests.test_bottleneck import FRANKFURT, bad, ok, report


def diagnosed(*locations, server=FRANKFURT):
    r = report(*locations, server=server)
    r.diagnosis = diagnose(r)
    return r


def server_heavy():
    return diagnosed(ok("Frankfurt", 5, 3, 6, 140, 6, cdn="HIT"), ok("London", 6, 12, 14, 150, 8, cdn="MISS"),
                     ok("Tokyo", 20, 190, 190, 250, 20), ok("Sydney", 20, 200, 200, 260, 20),
                     bad("Lagos"))


def test_pct_never_rounds_up_across_a_threshold():
    assert pct(0.3996) == "39%"
    assert pct(0.4) == "40%"
    assert pct(0.29) == "29%"
    assert pct(0.625) == "63%"
    assert pct(0.2497) == "24%"
    assert pct(0.0297) == "3%"


def test_headline_names_the_biggest_cost_with_its_share():
    v = view(server_heavy().diagnosis)
    assert v["headline"] == "Waiting for the server is the biggest cost."
    assert v["detail"].startswith("The timings indicate the server wait averages 200 ms: 47% of the 430 ms total")
    assert "DNS 13 + connect 101 + TLS 102 + server wait 200 + download 14" in v["detail"]
    assert v["kicker"] == "Where the time goes · average of 4 measured places"


def test_blocks_carry_ms_share_and_a_text_mark():
    v = view(server_heavy().diagnosis)
    blocks = {b["stage"]: b for b in v["overall"]["blocks"]}
    assert blocks["server_wait"]["flag"] == "biggest block"
    assert blocks["server_wait"]["ms"] == "200 ms"
    assert "47% of the total" in blocks["server_wait"]["note"]
    assert "Frankfurt" in blocks["server_wait"]["note"] and "not just distance" in blocks["server_wait"]["note"]
    assert blocks["connect"]["flag"] == "possible block"
    assert "grows with distance" in blocks["connect"]["note"]
    assert blocks["dns"]["flag"] == "" and blocks["dns"]["note"] == "3% · fine"
    assert [b["n"] for b in v["overall"]["blocks"]] == [1, 2, 3, 4, 5]
    assert v["overall"]["edge"]["title"] == "HIT 1 · MISS 1"
    widths = [s["pct"] for s in v["overall"]["bar"]]
    assert abs(sum(widths) - 100) < 0.5


def test_two_what_to_check_notes_for_the_flagged_blocks():
    checks = view(server_heavy().diagnosis)["checks"]
    assert [c["title"] for c in checks] == ["Block 4 · what to check first", "Block 3 · what to check next"]
    assert "origin response time" in checks[0]["text"]


def test_cards_biggest_caching_distance():
    cards = view(server_heavy().diagnosis)["cards"]
    assert [c["kicker"] for c in cards] == ["01 · Biggest cost", "02 · Caching", "03 · Distance"]
    assert cards[0]["title"] == "Server wait"
    assert "Biggest stage in 4 of 4 places" in cards[0]["text"]
    assert "1 HIT, 1 MISS" in cards[1]["text"]
    assert cards[2]["title"] == "Far places pay more"
    assert "Sydney" in cards[2]["text"] and "Frankfurt" in cards[2]["text"]
    assert all(c["check"].startswith("What to check: ") for c in cards)


def test_too_few_places_marks_nothing_and_says_why():
    v = view(diagnosed(ok("London", wait=500), ok("Tokyo", wait=500), bad("Lagos")).diagnosis)
    assert v["headline"] == "Not enough places for a conclusion."
    assert "at least 3" in v["detail"]
    assert all(b["flag"] == "" for b in v["overall"]["blocks"])
    assert v["overall"]["reason"]
    assert v["checks"] == []


def test_nothing_measured():
    v = view(diagnosed(bad("London"), bad("Tokyo", "test_failed", "probe error")).diagnosis)
    assert v["headline"] == "No timings to show where the time goes."
    assert v["overall"] is None


def test_blocked_places_show_no_diagram_only_the_reason():
    v = view(server_heavy().diagnosis)
    lagos = next(p for p in v["places"] if p["name"].startswith("Lagos"))
    assert lagos["diagram"] is None
    assert lagos["reason"] == "blocked: timed out. No timings, left out of every figure."
    assert "1 blocked" in v["footnote"] and "left out of every figure" in v["footnote"]


def test_each_measured_place_has_its_own_diagram_and_tag():
    v = view(diagnosed(ok("London", 10, 5, 5, 150, 10), ok("Tokyo", 10, 190, 60, 50, 10),
                       ok("Lagos", 72, 20, 20, 100, 28)).diagnosis)
    tags = {p["name"].split(",")[0]: p["tag"] for p in v["places"]}
    assert tags == {"London": "server wait", "Tokyo": "connect", "Lagos": "server wait"}
    lagos = next(p for p in v["places"] if p["name"].startswith("Lagos"))
    dns = lagos["diagram"]["blocks"][0]
    assert dns["flag"] == "possible block" and dns["note"].startswith("30%")


def test_never_claims_a_cause():
    for r in (server_heavy(), diagnosed(ok("London", 72, 20, 20, 100, 28), ok("Tokyo"), ok("Lagos"))):
        text = json.dumps(view(r.diagnosis)).lower()
        assert "because" not in text and "is slow" not in text


# --- the panel in the exports and the CLI ---------------------------------------------------------

def test_html_panel_lists_the_path_for_screen_readers():
    html = render_bottleneck(server_heavy())
    assert '<ol class="bn-path"' in html and 'aria-label="Request path' in html
    overall = html.split("</ol>")[0]
    assert overall.count('class="bn-block') == 7  # visitor, five stages, edge
    assert "bn-edge" in html and "seen, not timed" in html
    assert "biggest block" in html and "possible block" in html
    assert 'aria-hidden="true"' in html  # the to-scale bar repeats the list
    assert "<details" in html  # each place opens its own diagram without script


def test_interactive_panel_has_one_view_per_place():
    html = render_bottleneck(server_heavy(), interactive=True)
    assert 'data-bn-view="all"' in html
    for row in range(5):
        assert f'data-bn-view="{row}"' in html and f'data-bn-show="{row}"' in html
    assert "<details" not in html


def test_text_from_a_place_name_is_escaped():
    r = diagnosed(ok("<script>alert(1)</script>"), ok("London"), ok("Tokyo"))
    html = render_bottleneck(r, interactive=True)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_no_panel_without_a_diagnosis():
    r = server_heavy()
    r.diagnosis = None
    assert render_bottleneck(r) == ""
    assert "Where the time goes" not in render(r, "html")
    assert "Where the time goes" not in render(r, "md")
    assert "diagnosis" not in json.loads(render(r, "json"))
    assert cli_lines(r) == []


def test_exports_carry_the_panel():
    r = server_heavy()
    html, md, js = render(r, "html"), render(r, "md"), json.loads(render(r, "json"))
    assert "Where the time goes" in html and "Waiting for the server is the biggest cost." in html
    assert "## Where the time goes" in md and "Waiting for the server is the biggest cost." in md
    assert re.search(r"^1\. DNS: 13 ms \(3%\), fine$", md, re.M)
    assert re.search(r"^4\. Server wait: 200 ms \(47%\), biggest block$", md, re.M)
    assert "Lagos, XX: blocked: timed out. No timings, left out of every figure." in md
    assert js["diagnosis"]["biggest"] == "server_wait"
    assert [s["mark"] for s in js["diagnosis"]["stages"]] == ["fine", "possible", "possible", "biggest", "fine"]


def test_cli_lines_are_short():
    lines = cli_lines(server_heavy())
    assert lines[0] == "Where the time goes: Waiting for the server is the biggest cost."
    assert any(line.startswith("Check first: ") for line in lines)
    assert len(lines) <= 5


@pytest.mark.parametrize("stage_args,phrase", [
    (dict(dns=200), "The DNS lookup is the biggest cost."),
    (dict(connect=200), "Connecting is the biggest cost."),
    (dict(tls=200), "The TLS handshake is the biggest cost."),
    (dict(download=200), "The download is the biggest cost."),
])
def test_each_stage_has_a_headline(stage_args, phrase):
    v = view(diagnosed(ok("London", **stage_args), ok("Tokyo", **stage_args), ok("Lagos", **stage_args),
                       server=None).diagnosis)
    assert v["headline"] == phrase
