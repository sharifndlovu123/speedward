"""The Light / Dark / Auto colour theme switch: markup, the no-flash head script, and the export staying on Auto."""
import re
from pathlib import Path

from django.test import Client
from django.urls import reverse

from speedward.reports import render
from tests.test_reports import rep

ROOT = Path(__file__).resolve().parent.parent
THEME_JS = ROOT / "speedward" / "web" / "static" / "js" / "theme.js"


def page():
    return Client().get(reverse("index")).content.decode()


def test_head_applies_the_saved_theme_with_a_blocking_external_script_before_the_stylesheet():
    head = page().split("</head>")[0]
    tag = re.search(r'<script src="/static/js/theme\.js"[^>]*></script>', head)
    assert tag, "theme.js must load from <head>"
    assert "defer" not in tag.group(0) and "async" not in tag.group(0) and "module" not in tag.group(0)
    assert head.index("theme.js") < head.index('rel="stylesheet"')  # runs before the first paint


def test_switch_is_a_named_radio_group_with_auto_chosen_by_default():
    body = page()
    group = re.search(r'<div class="theme-switch" role="radiogroup" aria-labelledby="theme-name">(.*?)</div>', body, re.S)
    assert group, "no theme radio group"
    inner = group.group(1)
    assert re.search(r'id="theme-name"[^>]*>\s*<span class="visually-hidden">Colour </span>theme', inner)
    values = re.findall(r'<input type="radio" name="theme" value="(\w+)"( checked)?', inner)
    assert [v for v, _ in values] == ["light", "dark", "auto"]
    assert [v for v, c in values if c] == ["auto"]
    for word in ("Light", "Dark", "Auto"):
        assert f"<span>{word}</span>" in inner
    assert "style=" not in inner


def test_switch_sits_in_the_top_bar_outside_the_scan_form():
    body = page()
    top = re.search(r'<header id="topbar".*?</header>', body, re.S).group(0)
    assert 'class="theme-switch"' in top
    form = re.search(r'<form id="scan-form".*?</form>', body, re.S).group(0)
    assert 'name="theme"' not in form  # never posted to the server


def test_theme_script_keeps_the_choice_in_local_storage_only_and_survives_a_blocked_store():
    js = THEME_JS.read_text()
    assert "localStorage" in js and "speedward:theme" in js
    assert js.count("try {") >= 2  # reading and writing are each wrapped
    for banned in ("document.cookie", "fetch(", "XMLHttpRequest", "sendBeacon", "innerHTML", "outerHTML",
                   "insertAdjacentHTML", "sessionStorage", ".style"):
        assert banned not in js, banned
    assert 'setAttribute("data-theme"' in js and 'removeAttribute("data-theme")' in js


def test_the_exported_report_has_no_switch_and_follows_the_device():
    html = render(rep(), "html")
    assert 'name="theme"' not in html and "theme.js" not in html
    assert "<html" in html and "data-theme" not in re.search(r"<html[^>]*>", html).group(0)
