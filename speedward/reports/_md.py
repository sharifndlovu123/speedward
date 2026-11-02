"""Markdown escaping for untrusted text (place names, reasons, hostnames, header values, the address).

Shared by every Markdown section so the main table, the diagnosis, "Who can reach it" and "Is it fast?"
treat untrusted text the same way. Fixed words and numbers are written as they are.
"""
from __future__ import annotations

import re

_MD_SPECIAL = re.compile(r"([\\`*_\[\]()<>|!~])")
_MD_LEADING = re.compile(r"^(\s*)([#+-])")


def md_text(text) -> str:
    """Untrusted text made inert in Markdown: link, image, emphasis, strike-through, code, HTML and table
    syntax are backslash-escaped, and so is a heading or list mark at the start. Line breaks are folded."""
    text = _MD_SPECIAL.sub(r"\\\1", str(text).replace("\n", " ").replace("\r", " "))
    return _MD_LEADING.sub(r"\1\\\2", text)


def md_fold(text) -> str:
    """For text already made safe (fixed words plus md_text names): only line breaks are folded."""
    return str(text).replace("\n", " ").replace("\r", " ")
