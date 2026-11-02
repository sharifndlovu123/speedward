"""WCAG 2.2 AA contrast for the design tokens, computed from the shipped CSS (page and map SVG).

Text needs 4.5:1, large text (the big result numerals) and non-text UI (hairlines, focus rings,
map dots, lines and markers) need 3:1. Both the light and the dark theme are checked.
"""
import re
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
