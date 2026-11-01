"""Settings shared by the CLI and the web app: the `.env` reader and the Globalping endpoint guard.

Framework-free. A `.env` in the working directory may be untrusted (for example a cloned repo), so
only the keys in DOTENV_ALLOWED are taken from it, and never anything that could move the
Globalping endpoint: GLOBALPING_BASE_URL is read from the real process environment only.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from speedward.core.globalping import DEFAULT_BASE_URL

DOTENV_ALLOWED = (
    # core / CLI
    "GLOBALPING_TOKEN", "GEOIP_DB", "SERVER_LAT", "SERVER_LON", "FAST_MS", "SLOW_MS",
    # web
    "RESULT_TTL_SECONDS", "SECRET_KEY", "ALLOWED_HOSTS", "DEBUG", "CSRF_TRUSTED_ORIGINS",
    "MAX_CONCURRENT_SCANS",
)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
BAD_DOTENV = "Note: the .env file could not be read as text and was ignored."


class EndpointRefused(Exception):
    """A Globalping key would be sent to a non-default, non-local endpoint. Never holds the key."""

    def __init__(self, host: str):
        super().__init__("Refusing to send a Globalping key to a non-default, non-local endpoint.")
        self.host = host


def load_dotenv(path: Path, on_warning: Callable[[str], None] | None = None) -> dict[str, str]:
    """Tiny .env reader: KEY=VALUE lines, # comments, blank lines, simple quotes. No expansion."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    except UnicodeDecodeError:
        if on_warning is not None:
            on_warning(BAD_DOTENV)
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value.find(value[0], 1) > 0:
            value = value[1:value.find(value[0], 1)]
        else:
            value = re.split(r"\s#", value, maxsplit=1)[0].strip()
        if key:
            out[key] = value
    return out


def build_environment(dotenv_path: Path = Path(".env"), environ: Mapping[str, str] | None = None,
                      on_warning: Callable[[str], None] | None = None) -> dict[str, str]:
    """The real environment, with allowlisted `.env` keys filling gaps (never overriding)."""
    real = os.environ if environ is None else environ
    file_env = {k: v for k, v in load_dotenv(dotenv_path, on_warning).items() if k in DOTENV_ALLOWED}
    return {**file_env, **real}


def check_endpoint(base_url: str | None, token: str | None) -> str | None:
    """Return the custom endpoint to use, or None for the default one.

    Raises EndpointRefused when a token would go to a host that is neither the default API nor
    this machine. Call it before any request is made.
    """
    if not base_url or base_url.rstrip("/") == DEFAULT_BASE_URL:
        return None
    host = urlsplit(base_url).hostname or ""
    if token and host not in LOOPBACK_HOSTS:
        raise EndpointRefused(host)
    return base_url
