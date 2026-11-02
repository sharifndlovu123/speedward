"""Is it fast? rendered: cell words (symbol plus word), findings with "Check:" prompts, the notes on the
"where the time goes" diagram, and the HTML / Markdown / JSON exports. Never a figure from a failed test."""
import json

from speedward.core.bottleneck import diagnose
from speedward.core.models import ScanReport, TraceStep
from speedward.core.speed import speed_summary
from speedward.reports import render
from speedward.reports.bottleneck import render_bottleneck
from speedward.reports.bottleneck import view as bn_view
from speedward.reports.speed import NO_SIZE, cli_lines, diagram_notes, finding, render_speed, view
from tests.test_speed import blocked, hop, ok, rep, runs, sr


def three(speed, **kw):
    r = rep([ok("Berlin", **kw), ok("Tokyo", **kw), ok("Lagos", **kw)], speed)
    r.diagnosis = diagnose(r)
    return r


def full(varies=False):
    waits = [100, 100, 100, 900] if varies else [100, 110, 120, 130]
    speed = [x for i in range(3) for x in runs(i, waits)]
    speed += [sr("http2", 0), sr("http2", 1), sr("http2", 2, h2_offered=False)]
    speed += [sr("compression", 0), sr("compression", 1, encoding="none"), sr("compression", 2)]
    speed += [sr("dnstrace", i) for i in range(3)]
    bad = [hop(1, 1), hop(2, 5, loss=20, host="edge.example.net"), hop(3, 9, loss=20)]
    speed += [sr("mtr", 0), sr("mtr", 1, hops=bad), sr("mtr", 2)]
    return three(speed)


def cells(report, row):
    v = view(speed_summary(report))
    return {h: c for h, c in zip(v["headers"], v["rows"][row]["cells"], strict=True)}


def test_cells_are_symbol_and_word():
    c = cells(full(), 1)
    assert (c["Consistency"]["symbol"], c["Consistency"]["word"]) == ("●", "steady")
    assert c["Compression"]["word"] == "not compressed"
    assert c["Path"]["word"] == "bad hop 2"
    assert c["Path"]["detail"].startswith("20% loss from edge.example.net to the site")
    assert c["DNS trace"]["word"] == "slowest: TLD"
    assert c["DNS trace"]["detail"] == ("local resolver under 1 ms · root under 1 ms · TLD 100 ms · authoritative 3 ms"
                                        " · slowest server asked: a.gtld-servers.net")
    c = cells(full(), 2)
    assert (c["HTTP/2"]["word"], c["HTTP/2"]["detail"]) == ("not offered", "HTTP/1.1 only")
    assert c["HTTP/2"]["state"] == "measured"  # not offered is an answer, not blocked


def test_blocked_and_no_data_cells_carry_only_a_reason():
    r = three([sr("http2", 0, "blocked", "connection timed out"), sr("http2", 1, "test_failed", "boom"), sr("http2", 2)])
    v = view(speed_summary(r))
    states = [(row["cells"][0]["symbol"], row["cells"][0]["word"], row["cells"][0]["detail"]) for row in v["rows"]]
    assert states[0] == ("○", "blocked", "connection timed out")
    assert states[1] == ("◌", "no data", "test failed: boom")


def test_fewer_than_five_runs_shows_the_count_not_a_verdict():
    r = three(runs(0, [100, 900]))
    c = cells(r, 0)["Consistency"]
    assert c["word"] == "3 of 5 runs" and "not judged" in c["detail"]
    assert "steady" not in c["detail"] and "varies" not in c["detail"]


def test_findings_each_verdict():
    s = speed_summary(full(varies=True))
    texts = {o.check: finding(o, s) for o in s.overall}
    assert texts["consistency"]["text"].startswith("The timings indicate the server wait varies in 3 of 3 places")
    assert texts["consistency"]["prompt"].startswith("Check: origin load")
    assert texts["http2"]["text"] == "HTTP/2 is offered in 2 of 3 places; not in Lagos, XX."
    assert texts["compression"]["text"] == "The first response is not compressed in 1 of 3 places (Tokyo, XX)."
    assert texts["dnstrace"]["text"] == ("The timings indicate the slowest DNS step is the TLD in 3 of 3 places "
                                         "(median 100 ms).")
    assert texts["mtr"]["text"].startswith("The timings indicate a bad hop in 1 of 3 places (Tokyo, XX)")
    for f in texts.values():
        assert "because" not in f["text"]  # what the timings indicate, never a cause


def test_too_few_places_concludes_nothing():
    r = rep([ok("A"), ok("B")], [sr("http2", 0), sr("http2", 1)])
    s = speed_summary(r)
    f = finding(s.overall[0], s)
    assert f["text"] == "HTTP/2: not enough places for a conclusion (2 places with an answer, at least 3 needed)."
    assert f["prompt"] == "" and diagram_notes(s)["overall"] == {}


def test_page_size_is_never_shown_only_said_to_be_unavailable():
    r = three([sr("compression", i) for i in range(3)])
    v = view(speed_summary(r))
    assert NO_SIZE in v["notes"]
    html = render_speed(r)
    assert "not available" in html and " KB " not in html.replace("10 KB", "")
    assert "bytes" not in html


def test_diagram_notes_overall_and_per_place_never_change_the_marks():
    r = full(varies=True)
    marks = [s.mark for s in r.diagnosis.stages]
    plain = bn_view(r.diagnosis)
    v = bn_view(r.diagnosis, diagram_notes(speed_summary(r)))
    assert [b["mark"] for b in v["overall"]["blocks"]] == [b["mark"] for b in plain["overall"]["blocks"]]
    assert [s.mark for s in r.diagnosis.stages] == marks
    extra = {b["stage"]: b["extra"] for b in v["overall"]["blocks"]}
    assert extra["server_wait"] == "varies 100 to 900 ms (3 of 3 places)"
    assert extra["dns"] == "slowest step: TLD in 3 of 3 places (median 100 ms)"
    assert extra["download"] == "not compressed in 1 of 3 places"
    assert extra["connect"] == "bad hop in 1 of 3 places"
    assert extra["tls"] == ""  # every place used TLS 1.3
    tokyo = {b["stage"]: b["extra"] for b in v["places"][1]["diagram"]["blocks"]}
    assert tokyo["connect"] == "bad hop 2: 20% loss to the site"
    assert tokyo["download"] == "not compressed"
    assert tokyo["server_wait"] == "varies 100 to 900 ms"


def test_tls12_note_only_where_measured():
    r = three([sr("http2", i) for i in range(3)], tls="TLSv1.2")
    v = bn_view(r.diagnosis, diagram_notes(speed_summary(r)))
    assert {b["stage"]: b["extra"] for b in v["overall"]["blocks"]}["tls"].startswith(
        "TLS 1.2 costs one more round trip (Berlin, XX, Tokyo, XX, Lagos, XX)")
    no_checks = three(None, tls="TLSv1.2")
    assert "one more round trip" not in render_bottleneck(no_checks)


def test_steady_and_clean_notes():
    speed = [x for i in range(3) for x in runs(i, [100, 110, 120, 130])] + [sr("mtr", i) for i in range(3)]
    notes = diagram_notes(speed_summary(three(speed)))
    assert notes["overall"]["server_wait"] == "steady over 5 runs in 3 places"
    assert notes["overall"]["connect"] == "no bad hop"
    assert notes["places"][0]["server_wait"] == "steady over 5 runs"


def test_a_place_that_was_blocked_gets_no_notes():
    r = rep([ok("A"), blocked("B"), ok("C"), ok("D")],
            [sr("compression", 0), sr("compression", 1, "blocked", "connection timed out"), sr("compression", 2),
             sr("compression", 3)])
    r.diagnosis = diagnose(r)
    notes = diagram_notes(speed_summary(r))
    assert notes["places"][1] == {}
    assert notes["overall"]["download"] == "compressed (br)"
    v = bn_view(r.diagnosis, notes)
    assert v["places"][1]["diagram"] is None


def test_html_is_escaped():
    trace = [TraceStep("local", "<script>x</script>", 2)]
    r = three([sr("dnstrace", 0, trace=trace), sr("dnstrace", 1), sr("dnstrace", 2)])
    r.locations[0].location.city = "<img src=x>"
    html = render_speed(r)
    assert "<script>x" not in html and "&lt;script&gt;x&lt;/script&gt;" in html
    assert "<img src=x>" not in html


def test_exports_carry_the_panel_and_notes():
    r = full()
    html = render(r, "html")
    assert "Is it fast? Speed checks" in html and "bn-extra" in html
    md = render(r, "md")
    assert "## Is it fast? Speed checks" in md
    assert "| Place | Consistency | HTTP/2 | Compression | DNS trace | Path |" in md
    assert "(not compressed in 1 of 3 places)" in md  # the diagram note on the Download block
    d = json.loads(render(r, "json"))
    assert d["speed_summary"]["checks"] == ["consistency", "http2", "compression", "dnstrace", "mtr"]
    assert ScanReport.from_dict(d).to_dict() == r.to_dict()


def test_markdown_cells_escape_pipes():
    r = three([sr("mtr", 0, hops=[hop(1, 1), hop(2, 3, loss=20, host="a|b"), hop(3, 5, loss=20)]), sr("mtr", 1),
               sr("mtr", 2)])
    md = render(r, "md")
    assert "a\\|b" in md and "a|b" not in md.replace("a\\|b", "")


def test_a_report_without_speed_checks_renders_nothing():
    r = three(None)
    assert render_speed(r) == "" and cli_lines(r) == []
    assert "Is it fast?" not in render(r, "md") and "Is it fast?" not in render(r, "html")
    assert "bn-extra\"" not in render(r, "html")


def test_cli_lines():
    lines = cli_lines(full())
    assert len(lines) == 5 and all(line.startswith("Is it fast? ") for line in lines)


def test_markdown_escapes_untrusted_names():
    evil = "[x](http://evil)"
    r = full()
    r.locations[1].location.city = evil
    r.speed = [x for x in r.speed if x.check != "dnstrace"] + [
        sr("dnstrace", i, trace=[TraceStep("local", "l", 0), TraceStep("tld", "*a*_b_ `c` <i>", 9)]) for i in range(3)]
    r.speed = [x if not (x.check == "mtr" and x.row == 1) else
               sr("mtr", 1, hops=[hop(1, 1), hop(2, 5, loss=20, host="# [h](x)"), hop(3, 9, loss=20)]) for x in r.speed]
    for x in r.speed:
        if x.check == "http2" and x.row == 0:
            x.status, x.reason, x.h2_offered, x.stages, x.http_version, x.probe = (
                "test_failed", "a|b [c](d)", None, None, None, None)
    md = render(r, "md")
    # The speed panel (the main tables and the diagnosis cards are outside this task).
    md = md[md.index("## Is it fast?"):md.index("## Where the time goes")]
    assert evil not in md and "\\[x\\]\\(http://evil\\)" in md
    assert "\\*a\\*\\_b\\_ \\`c\\` \\<i\\>" in md
    assert "\\# \\[h\\]\\(x\\)" in md
    assert "a\\|b \\[c\\]\\(d\\)" in md
    html = render(r, "html")  # HTML keeps the plain text, escaped by the template
    assert "[x](http://evil)" in html and "&lt;i&gt;" in html


def test_markdown_escapes_names_in_the_notes_on_the_diagram():
    r = three([sr("http2", i) for i in range(3)], tls="TLSv1.2")
    r.locations[0].location.city = "[x](http://evil)"
    md = render(r, "md")
    tls = next(line for line in md.splitlines() if line.startswith("3. TLS"))
    assert "TLS 1.2 costs one more round trip (\\[x\\]\\(http://evil\\), XX" in tls
