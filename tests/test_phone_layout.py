"""Phone layout markup (Task 24): the sheet, its buttons, the menu button and Move pin. Behaviour is in e2e/test_phone.py."""
import re
from pathlib import Path

from django.test import Client
from django.urls import reverse

ROOT = Path(__file__).resolve().parent.parent
APP_CSS = (ROOT / "speedward/web/static/css/app.css").read_text()


def page():
    return Client().get(reverse("index")).content.decode()


def test_sheet_is_a_labelled_region_holding_the_form_and_the_details():
    body = page()
    m = re.search(r'<div id="sheet" class="sheet" role="region" aria-label="Settings and results">(.*)<div id="stage"',
                  body, re.S)
    assert m, "no sheet region"
    inner = m.group(1)
    assert inner.index('id="sheet-less"') < inner.index('id="sheet-more"') < inner.index('id="scan-form"')
    assert inner.index('id="scan-form"') < inner.index('id="drawer"')
    assert re.search(r'<div id="sheet-handle" class="sheet-handle" aria-hidden="true">', inner)  # pointer only
    assert 'id="sheet-state" class="visually-hidden" aria-live="polite"' in inner
    assert 'data-sheet="peek"' in re.search(r"<body[^>]*>", body).group(0)


def test_menu_button_sits_first_in_the_top_bar_and_the_old_more_toggle_is_gone():
    body = page()
    top = re.search(r'<header id="topbar".*?</header>', body, re.S).group(0)
    assert re.search(r'<button type="button" id="menu-toggle"[^>]*aria-expanded="false">Menu</button>', top)
    assert top.index('id="menu-toggle"') < top.index('class="theme-switch"') < top.index('id="results-bar"')
    assert "sheet-toggle" not in body


def test_move_pin_button_and_the_cost_straight_before_start():
    body = page()
    assert '<button type="button" id="pin-move" class="secondary pin-move">Move pin</button>' in body
    assert re.search(r'<p id="pin-move-hint" class="pin-move-hint" hidden>', body)
    assert body.index('id="pick-summary"') < body.index('id="estimate"') < body.index('id="start"')
    foot = re.search(r'<div class="form-foot">(.*?)</div>', body, re.S).group(1)
    assert '<p id="start-cost" class="start-cost" aria-hidden="true"></p>' in foot  # beside Start on phones
    assert "style=" not in body.split("<div id=\"stage\"")[0].split("<body")[1]


def test_wider_screens_get_no_phone_boxes():
    css = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
    before_phone = css.split("@media (max-width:700px){\n  :root{--bar-h")[0]
    assert ".sheet,.sheet-body{display:contents}" in before_phone
    assert re.search(r"\.sheet-head,\.menu-toggle,\.pin-move,\.pin-move-hint,\.start-cost\{display:none\}", before_phone)
