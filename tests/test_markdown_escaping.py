"""Every piece of untrusted text in the Markdown export (place names, reasons, hostnames, header values,
the address) is made inert: link, image, emphasis, code, HTML and table syntax are backslash-escaped.
Numbers and fixed words stay readable."""
import re

from speedward.core.bottleneck import diagnose
from speedward.core.models import DnsResult, Hop, LocationResult, PathResult, ServerPosition
from speedward.reports import render
from speedward.reports.markdown_report import to_markdown
from tests.test_reach import rr
from tests.test_speed_reports import full

EVIL = "[x](http://evil)"
SAFE = "\\[x\\]\\(http://evil\\)"


def hostile():
    r = full()
    for loc_result in r.locations:
        loc_result.location.city = EVIL
    tokyo = r.locations[1]
    tokyo.server_ip = "1.2.3.4*b*"
    tokyo.redirects = ["https://e.com/_a_"]
    tokyo.edge_note, tokyo.edge_reason = "edge `x`", "shared <b>"
    r.locations.append(LocationResult(r.locations[0].location, "blocked", "refused [y](z)"))
    r.paths = [PathResult(r.locations[0].location, [Hop(1, "# [h](x)", None, 3.0, 0.0)], "measured", "")]
    r.dns = [DnsResult(r.locations[0].location, "test_failed", "boom *now*", [], None)]
    r.reach = [rr("port", i, "blocked" if i == 0 else "measured", "x | [r](s)" if i == 0 else "") for i in range(4)]
    r.server = ServerPosition(52.5, 13.4, "manual")
    r.diagnosis = diagnose(r)
    return r


def test_no_hostile_text_survives_unescaped_anywhere():
    md = render(hostile(), "md")
    assert EVIL not in md
    for raw in ("*b*", "`x`", "<b>", "[y](z)", "[h](x)", "*now*", "[r](s)", "_a_"):
        assert raw not in md, raw


def test_main_locations_table_escapes_names_and_status():
    md = render(hostile(), "md")
    md = md[md.index("## Locations"):md.index("## Stage breakdown")]
    rows = [line for line in md.splitlines() if line.startswith(f"| {SAFE}")]
    assert rows, md
    assert any("| 1.2.3.4\\*b\\* |" in line for line in rows)
    assert any("edge \\`x\\` (shared \\<b\\>)" in line for line in rows)  # fixed words stay plain
    assert any("blocked: refused \\[y\\]\\(z\\)" in line for line in rows)
    header = next(line for line in md.splitlines() if line.startswith("| Location |"))
    for line in rows:  # escaped pipes never add a column
        assert line.count("|") - line.count("\\|") == header.count("|")


def test_diagnosis_cards_and_place_list_escape_names():
    md = to_markdown(hostile())
    where = md[md.index("## Where the time goes"):md.index("## Locations")]
    assert SAFE in where and EVIL not in where
    assert f"- {SAFE}, XX: blocked: refused \\[y\\]\\(z\\). No timings" in where
    # Fixed words and numbers stay plain.
    assert re.search(r"^4\. Server wait: \d+ ms \(\d+%\), ", where, re.M)


def test_other_sections_escape_hosts_answers_and_the_address():
    r = hostile()
    r.target = "https://example.com/a_b_c"
    md = to_markdown(r)
    assert md.startswith("# Speed report: https://example.com/a\\_b\\_c\n")
    assert "1. \\# \\[h\\]\\(x\\) 3 ms, loss 0%" in md
    assert "boom \\*now\\*" in md
    assert "https://e.com/\\_a\\_" in md
    assert f"## Route from {SAFE}, XX" in md


def test_reach_table_escapes_reasons_and_places():
    md = to_markdown(hostile())
    reach = md[md.index("## Who can reach it"):md.index("## Is it fast?")]
    assert f"| {SAFE}, XX | " in reach
    assert "x \\| \\[r\\]\\(s\\)" in reach
    for line in reach.splitlines():
        if line.startswith("| "):
            assert line.count("|") - line.count("\\|") == 4


def test_plain_names_are_unchanged():
    md = render(full(), "md")
    assert "| Berlin, XX |" in md and "\\" not in md.split("## Locations", 1)[1].split("## Stage", 1)[0]
