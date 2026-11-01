"""Target normalisation: one canonical URL string used for every measurement."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_BAD_CHARS = re.compile(r"[\x00-\x20\x7f-\x9f\\]")


def normalise_target(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Enter a website address.")
    text = url.strip()
    if _BAD_CHARS.search(text):
        raise ValueError("The address contains spaces or control characters.")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
        host = parts.hostname
    except ValueError as exc:
        raise ValueError("That address is not valid.") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("Only http and https addresses can be tested.")
    if "@" in parts.netloc:
        raise ValueError("Addresses with a username or password are not accepted.")
    if not host:
        raise ValueError("The address has no host name.")
    host = host.rstrip(".").lower()
    if not host:
        raise ValueError("The address has no host name.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("The host name is not valid.") from exc
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))
