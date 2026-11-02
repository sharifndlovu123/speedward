"""Words and the block diagram for "where the time goes". Pure rendering of a stored Diagnosis.

Every sentence says what the timings indicate; none claims a cause. Each card and each flagged block
ends in a "what to check" prompt, not a promise. A report without a diagnosis (from before it existed)
renders nothing: no panel, no lines, never a diagnosis made up afterwards.
"""
from __future__ import annotations

import math
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from speedward.core.bottleneck import STAGES, Diagnosis, PlaceFigure, StageFigure

from .speed import md_text, notes_for

NAME = {"dns": "DNS", "connect": "Connect", "tls": "TLS", "server_wait": "Server wait", "download": "Download"}
WORD = {"dns": "DNS", "connect": "connect", "tls": "TLS", "server_wait": "server wait", "download": "download"}
NUMBER = {s: i for i, s in enumerate(STAGES, start=1)}
HEADLINE = {"dns": "The DNS lookup", "connect": "Connecting", "tls": "The TLS handshake",
            "server_wait": "Waiting for the server", "download": "The download"}
FLAG = {"biggest": "biggest block", "possible": "possible block"}
MARK_TEXT = {"biggest": "biggest block", "possible": "possible block", "fine": "fine",
             "not_judged": "not judged", "not_available": "not available"}
STATUS_TEXT = {"blocked": "blocked", "test_failed": "test failed", "not_run": "did not run"}
CHECK = {
    "server_wait": "How long the server takes to start answering: origin response time, slow pages or "
                   "database calls, and cache rules that cause a MISS.",
    "dns": "The DNS provider and how long its answers are kept (TTL).",
    "connect": "Whether visitors far away are served by a close edge or region.",
    "tls": "Whether visitors far away reach a close edge (each handshake round trip crosses the distance), "
           "and that TLS 1.3 is offered.",
    "download": "The size of the first response and whether it is compressed.",
}
SCALE = "Bar is to scale: each block's width is its share of the total."
KEY = (("biggest", "biggest block (largest share, at least {big})"),
       ("possible", "possible block (at least {possible}, or grows with distance)"),
       ("fine", "fine"), ("edge", "seen, not timed (edge / CDN)"))

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)), autoescape=True)
# Our own stylesheet, shared by the web UI and the export; inlined so the export stays self-contained.
CSS = (Path(__file__).parent / "bottleneck.css").read_text(encoding="utf-8")


def ms(v: float | None) -> str:
    return "not available" if v is None else f"{v:.0f} ms"


def pct(share: float, thresholds: tuple[float, ...] = (0.25, 0.40)) -> str:
    """A share as a whole percent, rounded half up but never up across a threshold (39.96% is "39%",
    so a block shown under 40% is never shown as 40%)."""
    value = math.floor(share * 100 + 0.5)
    for t in thresholds:
        if share < t and value >= round(t * 100):
            value = round(t * 100) - 1
    return f"{value}%"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _thresholds(d: Diagnosis) -> tuple[float, float]:
    return (d.rules.possible_share, d.rules.biggest_share)


def _p(d: Diagnosis, share: float) -> str:
    return pct(share, _thresholds(d))


def _breakdown(d: Diagnosis, stages: list[StageFigure]) -> str:
    return " + ".join(f"{WORD[s.stage]} {s.ms:.0f}" for s in stages if s.ms is not None)


def _stage(stages: list[StageFigure], name: str) -> StageFigure:
    return next(s for s in stages if s.stage == name)


# --- one diagram -------------------------------------------------------------------------------------

def _note(d: Diagnosis, s: StageFigure, overall: bool) -> str:
    if s.ms is None:
        return "not available"
    share = _p(d, s.share) if s.share is not None else ""
    if s.mark == "biggest":
        text = f"{share} of the total."
        near = d.near_server_wait
        if overall and s.stage == "server_wait" and near is not None:
            text += (f" High even in {near.place} ({near.ms:.0f} ms wait, {near.connect_ms:.0f} ms connect): "
                     "the timings indicate it is not just distance.")
        return text
    if s.mark == "possible" and overall and s.grows_with_distance:
        far, near = d.distance.farthest, d.distance.nearest
        if s.stage == "connect" and far is not None and near is not None and far.connect_ms is not None \
                and near.connect_ms is not None:
            return (f"{share} · grows with distance: {far.place} {far.connect_ms:.0f} ms, "
                    f"{near.place} {near.connect_ms:.0f} ms")
        return f"{share} · grows with distance"
    if s.mark in ("not_judged",):
        return share
    return f"{share} · {MARK_TEXT[s.mark]}"


def _diagram(d: Diagnosis, stages: list[StageFigure], total: float | None, overall: bool,
             place: PlaceFigure | None = None, extra: dict[str, str] | None = None) -> dict:
    """extra: notes from the speed checks (speedward/reports/speed.py diagram_notes), one per stage. They
    sit under a block's own note and never change its mark."""
    blocks, bar = [], []
    extra = extra or {}
    for s in stages:
        blocks.append({"stage": s.stage, "n": NUMBER[s.stage], "name": NAME[s.stage], "ms": ms(s.ms),
                       "mark": s.mark, "flag": FLAG.get(s.mark, ""), "note": _note(d, s, overall),
                       "extra": extra.get(s.stage, "")})
        if s.share:
            bar.append({"stage": s.stage, "mark": s.mark, "pct": round(s.share * 100, 2)})
    if overall:
        c = d.cache
        caption = (f"Path of one request · averages of {_plural(d.measured, 'measured place')} · "
                   f"total {ms(total)}")
        visitor = ("Test places", f"average of {d.measured}")
        edge_title = f"HIT {c.hit} · MISS {c.miss}" if c.hit or c.miss else "no cache header"
        codes = sorted({p.edge for p in d.places if p.edge})
        edge_note = f"answered by {', '.join(codes)}" if codes else "no edge code seen"
    else:
        caption = f"Path of one request · {place.place} · total {ms(total)}"
        visitor = ("Test place", place.place)
        edge_title = place.cdn or "no cache header"
        edge_note = f"answered by {place.edge}" if place.edge else "no edge code seen"
    reason = ""
    if d.verdict == "too_few":
        reason = (f"Not enough places for a conclusion: {_plural(d.measured, 'place')} measured, at least "
                  f"{d.rules.min_places} needed. No block is marked.")
    elif d.verdict == "no_timings":
        reason = "No stage timings were returned, so no block is marked."
    label = "Request path, left to right" + ("" if overall else f", {place.place}")
    return {"caption": caption, "label": label, "visitor": visitor, "blocks": blocks, "bar": bar,
            "edge": {"title": edge_title, "note": edge_note}, "reason": reason, "scale": SCALE}


# --- the whole panel -------------------------------------------------------------------------------

def _headline(d: Diagnosis) -> tuple[str, str]:
    total = d.total_ms
    if d.verdict == "nothing_measured":
        return ("No timings to show where the time goes.",
                "No place was measured, so there is nothing to average. Failed or blocked places are left out.")
    if d.verdict == "too_few":
        return ("Not enough places for a conclusion.",
                f"{_plural(d.measured, 'place')} measured; at least {d.rules.min_places} are needed before any "
                f"block is marked. The averages below are shown as measured.")
    if d.verdict == "no_timings":
        return ("No stage timings were returned.", "The measured places returned no stage timings to compare.")
    largest = _stage(d.stages, d.largest)
    breakdown = _breakdown(d, d.stages)
    if d.verdict == "biggest":
        return (f"{HEADLINE[d.biggest]} is the biggest cost.",
                f"The timings indicate the {WORD[d.biggest]} averages {ms(largest.ms)}: {_p(d, largest.share)} "
                f"of the {ms(total)} total ({breakdown}).")
    return ("No single stage dominates.",
            f"The largest is {WORD[d.largest]} at {ms(largest.ms)}, {_p(d, largest.share)} of the {ms(total)} "
            f"total; no stage reaches {pct(d.rules.biggest_share, ())} ({breakdown}).")


def _checks(d: Diagnosis) -> list[dict]:
    if d.verdict not in ("biggest", "no_single_stage"):
        return []
    flagged = sorted((s for s in d.stages if s.mark in ("biggest", "possible")),
                     key=lambda s: (s.mark != "biggest", -(s.share or 0)))[:2]
    out = []
    for i, s in enumerate(flagged):
        text = CHECK[s.stage]
        far = d.distance.farthest
        if s.grows_with_distance and s.stage == "connect" and far is not None and far.connect_ms is not None:
            text += f" {far.connect_ms:.0f} ms to connect from {far.place}: the timings indicate distance."
        elif s.stage == "dns" and d.dns_example is not None:
            text += f" DNS takes {_p(d, d.dns_example.share)} of the time in {d.dns_example.place}."
        out.append({"title": f"Block {NUMBER[s.stage]} · what to check {'first' if i == 0 else 'next'}",
                    "text": text})
    return out


def _card_biggest(d: Diagnosis) -> dict:
    card = {"kicker": "01 · Biggest cost"}
    if d.verdict not in ("biggest", "no_single_stage"):
        title, _ = _headline(d)
        return {**card, "title": "No conclusion", "text": title,
                "check": "What to check: test from more places, then look again."}
    largest = _stage(d.stages, d.largest)
    if d.verdict == "biggest":
        title = NAME[d.biggest]
        text = f"Biggest stage in {d.largest_in} of {_plural(d.measured, 'place')}."
    else:
        title = "No single stage"
        text = (f"The largest is {WORD[d.largest]} at {_p(d, largest.share)}, the biggest stage in "
                f"{d.largest_in} of {_plural(d.measured, 'place')}.")
    near = d.near_server_wait
    if near is not None and d.largest == "server_wait":
        text += (f" Even close places wait: {near.place} {near.ms:.0f} ms server wait vs {near.connect_ms:.0f} ms "
                 "connect. The timings indicate the delay is not just distance.")
    if d.dns_heavy and d.dns_example is not None and d.largest != "dns":
        text += (f" DNS is large in {_plural(d.dns_heavy, 'place')} ({_p(d, d.dns_example.share)} in "
                 f"{d.dns_example.place}).")
    return {**card, "title": title, "text": text, "check": "What to check: " + CHECK[d.largest]}


def _card_cache(d: Diagnosis) -> dict:
    c = d.cache
    if c.gain_pct is not None and c.gain_pct > 0:
        title = f"Repeat {c.gain_pct:.0f}% faster"
        text = (f"On the same test machine the repeat's first byte was {c.gain_pct:.0f}% faster "
                f"({_plural(c.same_probe_pairs, 'place')}).")
    elif c.gain_pct is not None:
        title = "Repeat not faster"
        text = f"On the same test machine the repeat was not faster ({_plural(c.same_probe_pairs, 'place')})."
    elif c.other_probe_repeats:
        title = "No cache gain worked out"
        text = (f"The repeat ran on a different test machine in {_plural(c.other_probe_repeats, 'place')}, "
                "so no gain is worked out.")
    else:
        title = "No cache gain worked out"
        text = "No repeat request was measured."
    if d.measured:
        counts = f" {c.hit} HIT, {c.miss} MISS"
        if c.unknown:
            counts += f", {c.unknown} without a cache header"
        text += counts + f" of {_plural(d.measured, 'first load')}."
    return {"kicker": "02 · Caching", "title": title, "text": text,
            "check": "What to check: the CDN cache rules. A MISS on a first load means the origin answered."}


def _card_distance(d: Diagnosis) -> dict:
    x = d.distance
    card = {"kicker": "03 · Distance",
            "check": "What to check: the number of regions or edges near your visitors."}
    near, far = x.nearest, x.farthest
    if near is not None and far is not None and near.row != far.row and near.connect_ms is not None \
            and far.connect_ms is not None:
        title = {True: "Far places pay more", False: "No clear distance trend",
                 None: "Connect time by distance"}[x.connect_grows]
        text = (f"{far.place} ({far.km:,.0f} km from the server that answered) takes {far.connect_ms:.0f} ms to "
                f"connect vs {near.connect_ms:.0f} ms in {near.place} ({near.km:,.0f} km).")
        if x.connect_grows is None:
            text += " Too few places to judge a trend."
        return {**card, "title": title, "text": text}
    if x.quickest is not None and x.slowest is not None and x.quickest.row != x.slowest.row:
        return {**card, "title": "Connect time spread",
                "text": (f"Connect takes {x.quickest.connect_ms:.0f} ms in {x.quickest.place} and "
                         f"{x.slowest.connect_ms:.0f} ms in {x.slowest.place}. With no server position the "
                         "timings cannot show whether it grows with distance.")}
    return {**card, "title": "Not enough data", "text": "Distance needs connect times from at least two places."}


def _place(d: Diagnosis, p: PlaceFigure, extra: dict[str, str] | None = None) -> dict:
    if p.status != "measured":
        why = p.reason or "no reason given"
        return {"row": p.row, "name": p.place, "measured": False, "tag": "no data", "total": "",
                "diagram": None, "reason": f"{STATUS_TEXT[p.status]}: {why}. No timings, left out of every figure.",
                "segments": []}
    if p.held_back_by is not None:
        tag = WORD[p.held_back_by]
    elif d.verdict in ("biggest", "no_single_stage"):
        tag = "no single stage"
    else:
        tag = "not judged"
    segments = [{"mark": s.mark, "pct": round(s.share * 100, 2)} for s in p.stages if s.share]
    return {"row": p.row, "name": p.place, "measured": True, "tag": tag, "total": ms(p.total_ms),
            "diagram": _diagram(d, p.stages, p.total_ms, False, p, extra), "reason": "", "segments": segments}


def _footnote(d: Diagnosis) -> str:
    out = "Based on one cold request per place (and the repeat, for the cache gain)."
    left = [(n, w) for n, w in ((d.blocked, "blocked"), (d.test_failed, "test failed"), (d.not_run, "did not run"))
            if n]
    if left:
        total = sum(n for n, _ in left)
        out += (f" {_plural(total, 'place')} left out of every figure above: "
                + ", ".join(f"{n} {w}" for n, w in left) + ".")
    else:
        out += " Failed or blocked places would be left out of every figure; none were."
    return out


def view(d: Diagnosis, notes: dict | None = None) -> dict:
    """notes: speedward/reports/speed.py diagram_notes ({"overall": {...}, "places": {row: {...}}}) or None."""
    headline, detail = _headline(d)
    notes = notes or {"overall": {}, "places": {}}
    overall = None if d.verdict == "nothing_measured" else _diagram(d, d.stages, d.total_ms, True,
                                                                    extra=notes["overall"])
    big, possible = pct(d.rules.biggest_share, ()), pct(d.rules.possible_share, ())
    return {
        "kicker": f"Where the time goes · average of {_plural(d.measured, 'measured place')}",
        "headline": headline, "detail": detail, "overall": overall, "checks": _checks(d),
        "cards": [_card_biggest(d), _card_cache(d), _card_distance(d)],
        "places": [_place(d, p, notes["places"].get(p.row)) for p in d.places], "footnote": _footnote(d),
        "scale": SCALE,
        "key": [(k, text.format(big=big, possible=possible)) for k, text in KEY],
        "average_label": f"average of {_plural(d.measured, 'measured place')}",
    }


def render_bottleneck(report, interactive: bool = False, heading: str = "h2") -> str:
    """The panel as escaped HTML ("" without a diagnosis). interactive: the web UI's version, one view per
    place switched by buttons; otherwise each place opens its own diagram in a <details>."""
    if report.diagnosis is None:
        return ""
    from markupsafe import Markup
    css = Markup(CSS)  # noqa: S704 - a file shipped with this package, not data
    return _env.get_template("bottleneck.html.j2").render(v=view(report.diagnosis, notes_for(report)),
                                                          interactive=interactive, h=heading, css=css)


def markdown_lines(report) -> list[str]:
    if report.diagnosis is None:
        return []
    d = report.diagnosis
    v = view(d, notes_for(report, md_text))  # names in the speed notes are escaped for Markdown
    L = ["## Where the time goes", "", f"**{v['headline']}** {v['detail']}", ""]
    if v["overall"] is not None:
        g = v["overall"]
        L += [g["caption"], ""]
        if g["reason"]:
            L += [g["reason"], ""]
        for s, b in zip(d.stages, g["blocks"], strict=True):
            share = f" ({_p(d, s.share)})" if s.share is not None else ""
            L.append(f"{b['n']}. {b['name']}: {b['ms']}{share}, {MARK_TEXT[s.mark]}"
                     + (f" ({b['extra']})" if b["extra"] else ""))
        L += ["", f"Edge / CDN (seen, not timed): {g['edge']['title']}, {g['edge']['note']}."]
    if v["checks"]:
        L += [""] + [f"- {c['title']}: {c['text']}" for c in v["checks"]]
    L += [""] + [f"- {c['kicker']}: **{c['title']}**. {c['text']} {c['check']}" for c in v["cards"]]
    L += ["", "Each place, by what holds it back:", ""]
    for p in v["places"]:
        L.append(f"- {p['name']}: {p['tag']}, {p['total']}" if p["measured"] else f"- {p['name']}: {p['reason']}")
    L += ["", v["footnote"]]
    return L


def cli_lines(report) -> list[str]:
    """A short text version for the terminal (stderr)."""
    if report.diagnosis is None:
        return []
    v = view(report.diagnosis)
    lines = [f"Where the time goes: {v['headline']}", v["detail"]]
    lines += [f"Check {'first' if i == 0 else 'next'}: {c['text']}" for i, c in enumerate(v["checks"])]
    return lines
