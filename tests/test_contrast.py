"""WCAG 2.2 AA contrast for the design tokens, computed from the shipped CSS (page and map SVG).

Text needs 4.5:1, large text (the big result numerals) and non-text UI (hairlines, focus rings,
map dots, lines and markers) need 3:1. Both the light and the dark theme are checked.
"""
import re
from datetime import UTC
from pathlib import Path

import pytest

from speedward.reports.map import _STYLE

APP_CSS = (Path(__file__).parent.parent / "speedward/web/static/css/app.css").read_text()
REPORT = (Path(__file__).parent.parent / "speedward/reports/report.html.j2").read_text()


def _lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    def chan(v):
        c = int(v, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(h[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _block(css: str, selector: str) -> dict[str, str]:
    """The custom properties set in the first rule whose selector is exactly `selector`."""
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\b", m.group(1)))


def themes(css: str, light_sel: str, dark_sel: str) -> dict[str, dict[str, str]]:
    light = _block(css, light_sel)
    dark = {**light, **_block(css, dark_sel)}
    # The forced dark theme repeats the same values as the system dark theme.
    forced = {**light, **_block(css, dark_sel.replace(':not([data-theme="light"])', '[data-theme="dark"]'))}
    assert forced == dark, "the data-theme=dark block must match the prefers-color-scheme dark block"
    return {"light": light, "dark": dark}


PAGE = themes(APP_CSS, ":root", ':root:not([data-theme="light"])')
MAP = themes(_STYLE, ".sw-map", ':root:not([data-theme="light"]) .sw-map')
EXPORT = themes(REPORT, ":root", ':root:not([data-theme="light"])')

TEXT, LARGE, UI = 4.5, 3.0, 3.0

PAGE_PAIRS = [
    ("ink", "paper", TEXT),          # body text
    ("mute", "paper", TEXT),         # small uppercase labels, captions
    ("paper", "ink", TEXT),          # primary buttons and selected chips (paper text on ink)
    ("accent-ink", "accent", TEXT),  # Start test: white on red
    ("warn", "paper", TEXT),
    ("err", "paper", TEXT),
    ("ink", "hl", TEXT),             # highlighted table row
    ("faint", "paper", LARGE),       # the light secondary numerals (large text only)
    ("hair", "paper", UI),           # panel hairlines, input borders, chip outlines
    ("focus", "paper", UI),          # focus ring
    ("focus", "accent", UI),         # focus ring next to the red Start button
]

MAP_PAIRS = [
    ("ink", "bg", TEXT), ("ink", "land", TEXT),             # map labels
    ("fast", "land", UI), ("mid", "land", UI), ("slow", "land", UI),
    ("fast", "bg", UI), ("mid", "bg", UI), ("slow", "bg", UI),
    ("pin", "land", UI), ("pin", "bg", UI),                 # the server marker and pin
    ("sq", "land", UI), ("sq", "bg", UI),                   # servers that answered
    ("off", "bg", UI), ("off", "land", UI),                 # unreachable dots
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg,need", PAGE_PAIRS)
def test_page_tokens(theme, fg, bg, need):
    t = PAGE[theme]
    assert ratio(t[fg], t[bg]) >= need, f"{theme} --{fg} on --{bg}: {ratio(t[fg], t[bg]):.2f}"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg,need", MAP_PAIRS)
def test_map_tokens(theme, fg, bg, need):
    t = MAP[theme]
    assert ratio(t[fg], t[bg]) >= need, f"{theme} map --{fg} on --{bg}: {ratio(t[fg], t[bg]):.2f}"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg,need", [p for p in PAGE_PAIRS if "accent" not in p[:2] and "hl" not in p[:2]
                                        and "focus" not in p[:2]])
def test_export_tokens(theme, fg, bg, need):
    t = EXPORT[theme]
    assert ratio(t[fg], t[bg]) >= need, f"export {theme} --{fg} on --{bg}: {ratio(t[fg], t[bg]):.2f}"


def test_page_and_map_share_paper_and_ink():
    for theme in ("light", "dark"):
        assert PAGE[theme]["paper"].lower() == MAP[theme]["bg"].lower()
        assert PAGE[theme]["ink"].lower() == MAP[theme]["ink"].lower()
        assert EXPORT[theme]["paper"].lower() == PAGE[theme]["paper"].lower()


def test_ratio_matches_known_values():
    assert round(ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(ratio("#ffffff", "#c62f17"), 1) == 5.5


def test_panels_are_flat_and_motion_can_be_switched_off():
    css = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
    assert "box-shadow" not in css and "backdrop-filter" not in css
    assert "@import" not in css and "url(" not in css  # no web fonts or outside requests
    assert re.search(r"@media \(prefers-reduced-motion:\s*reduce\)", css)
    assert re.search(r"\.panel\{[^}]*border-radius:0", css)


def _blend(fg: str, bg: str, alpha: float) -> str:
    f, b = (fg.lstrip("#"), bg.lstrip("#"))
    mix = [round(alpha * int(f[i:i + 2], 16) + (1 - alpha) * int(b[i:i + 2], 16)) for i in (0, 2, 4)]
    return "#" + "".join(f"{c:02x}" for c in mix)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_speed_lines_pass_3_to_1_as_drawn(theme):
    """Lines are drawn with an opacity: the colour that reaches the screen must still be 3:1 on land."""
    from datetime import datetime

    from speedward.core.models import Location, LocationResult, ScanReport, Stages
    from speedward.reports.map import render_map
    now = datetime(2026, 11, 1, tzinfo=UTC)
    locs = [LocationResult(Location("JP", "Tokyo", 35.7, 139.7, 1, "n", "isp"), "measured",
                           stages=Stages(first_byte=fb, total=fb), server_position=None) for fb in (100, 500, 900)]
    from speedward.core.models import ServerPosition
    svg = render_map(ScanReport(target="https://e.com/", started=now, finished=now, version="0", options={},
                                server=ServerPosition(52.5, 13.4, "manual"), locations=locs, paths=[], dns=[],
                                browser=None))
    lines = re.findall(r'<path d="M[^"]*" stroke="var\(--(fast|mid|slow)\)"[^>]*?(?:opacity="([\d.]+)")?/>', svg)
    assert {name for name, _ in lines} == {"fast", "mid", "slow"}
    t = MAP[theme]
    for name, op in lines:
        drawn = _blend(t[name], t["land"], float(op or 1))
        assert ratio(drawn, t["land"]) >= UI, f"{theme} {name} line as drawn: {ratio(drawn, t['land']):.2f}"


# --- "Where the time goes" block diagram (speedward/reports/bottleneck.css, shared by page and export) ---

BOTTLENECK_CSS = (Path(__file__).parent.parent / "speedward/reports/bottleneck.css").read_text()


def test_diagram_has_no_red():
    """CLAUDE.md: the red accent is only for the server marker and Start. The diagram is ink on paper."""
    css = re.sub(r"/\*.*?\*/", "", BOTTLENECK_CSS, flags=re.S)
    assert "--accent" not in css and "rgba(198" not in css.replace(" ", "")
    assert "--accent" not in REPORT  # the export has no red at all


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("tokens", [PAGE, EXPORT], ids=["page", "export"])
def test_biggest_block_outline_and_text(tokens, theme):
    rule = re.search(r"\.bn-block\.bn-biggest\{([^}]*)\}", BOTTLENECK_CSS).group(1)
    assert "outline:3px solid var(--ink)" in rule and "background:var(--ink)" in rule
    t = tokens[theme]
    assert ratio(t["ink"], t["paper"]) >= UI, "the biggest block's thick ink frame on the page"
    assert ratio(t["paper"], t["ink"]) >= TEXT, "paper text inside the filled block"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("tokens", [PAGE, EXPORT], ids=["page", "export"])
def test_text_on_the_hatched_possible_block(tokens, theme):
    """The ink hatch lines sit behind ink and muted text: the text must still pass on the hatch colour."""
    m = re.search(r"\.bn-block\.bn-possible\{[^}]*color-mix\(in srgb,var\(--ink\) (\d+)%,transparent\)",
                  BOTTLENECK_CSS)
    assert m, "possible block ink hatch not found"
    t = tokens[theme]
    line = _blend(t["ink"], t["paper"], int(m.group(1)) / 100)
    for fg in ("ink", "mute"):
        assert ratio(t[fg], line) >= TEXT, f"{theme} --{fg} on the hatch: {ratio(t[fg], line):.2f}"


def test_diagram_css_is_flat_self_contained_and_uses_known_tokens():
    css = re.sub(r"/\*.*?\*/", "", BOTTLENECK_CSS, flags=re.S)
    assert "box-shadow" not in css and "url(" not in css and "@import" not in css
    assert "border-radius:0" in css  # the place buttons drop the pill shape
    assert re.search(r"@media \(prefers-reduced-motion:\s*reduce\)\{\.bn-bar\{animation:none\}\}", css)
    used = set(re.findall(r"var\(--([\w-]+)\)", css))
    for name, tokens in (("page", PAGE), ("export", EXPORT)):
        for theme in ("light", "dark"):
            missing = used - set(tokens[theme]) - {"hl"}  # --hl (hover) has a fallback and is web-only
            assert not missing, f"{name} {theme} lacks {missing}"


# --- "Who can reach it" (speedward/reports/reach.css, shared by page and export) and More checks -------

REACH_CSS = (Path(__file__).parent.parent / "speedward/reports/reach.css").read_text()


def test_reach_panel_has_no_red_and_is_flat_and_self_contained():
    css = re.sub(r"/\*.*?\*/", "", REACH_CSS, flags=re.S)
    assert "--accent" not in css and "#c62f17" not in css.lower()
    assert "box-shadow" not in css and "url(" not in css and "@import" not in css and "border-radius" not in css
    assert re.search(r"@media \(prefers-reduced-motion:\s*reduce\)\{\.rc-finding\{animation:none\}\}", css)
    used = set(re.findall(r"var\(--([\w-]+)\)", css))
    for name, tokens in (("page", PAGE), ("export", EXPORT)):
        for theme in ("light", "dark"):
            assert not used - set(tokens[theme]), f"{name} {theme} lacks {used - set(tokens[theme])}"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("tokens", [PAGE, EXPORT], ids=["page", "export"])
def test_finding_text_on_its_tinted_box(tokens, theme):
    m = re.search(r"\.rc-finding\{[^}]*color-mix\(in srgb,var\(--ink\) (\d+)%,transparent\)", REACH_CSS)
    assert m, "finding tint not found"
    t = tokens[theme]
    box = _blend(t["ink"], t["paper"], int(m.group(1)) / 100)
    assert ratio(t["ink"], box) >= TEXT
    assert ratio(t["ink"], t["paper"]) >= UI  # the 3px ink rule beside it, and the grid hairlines


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_more_checks_total_bar_is_paper_on_ink(theme):
    rule = re.search(r"\.mc-total\{([^}]*)\}", APP_CSS).group(1)
    assert "background:var(--ink)" in rule and "color:var(--paper)" in rule
    t = PAGE[theme]
    assert ratio(t["paper"], t["ink"]) >= TEXT
