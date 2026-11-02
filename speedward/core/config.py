"""Settings shared by the CLI and the web app: the `.env` reader and the Globalping endpoint guard.

Framework-free. A `.env` in the working directory may be untrusted (for example a cloned repo), so
only the keys in DOTENV_ALLOWED are taken from it, and never anything that could move the
Globalping endpoint: GLOBALPING_BASE_URL is read from the real process environment only.
"""
from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from speedward.core.bottleneck import RULE_BOUNDS, Rules
from speedward.core.globalping import DEFAULT_BASE_URL
from speedward.core.rules import parse
from speedward.core.speed import SPEED_RULE_BOUNDS, SpeedRules

# The "where the time goes" and "Is it fast?" thresholds (README "Where the time goes", "Speed checks"):
# setting -> [(rules, field)]. MIN_PLACES sets both panels' minimum.
RULE_SETTINGS = {
    "BIGGEST_SHARE": [(Rules, "biggest_share")], "POSSIBLE_SHARE": [(Rules, "possible_share")],
    "MIN_PLACES": [(Rules, "min_places"), (SpeedRules, "min_places")],
    "NEAR_CONNECT_MS": [(Rules, "near_connect_ms")], "DISTANCE_CORRELATION": [(Rules, "distance_correlation")],
    "DISTANCE_SPREAD_MS": [(Rules, "distance_spread_ms")], "DNS_SHARE": [(Rules, "dns_share")],
    "VARIES_RATIO": [(SpeedRules, "varies_ratio")], "VARIES_MIN_MS": [(SpeedRules, "varies_min_ms")],
    "BAD_HOP_LOSS_PCT": [(SpeedRules, "loss_pct")], "LONG_LINK_MS": [(SpeedRules, "rise_ms")],
}
_BOUNDS = {Rules: RULE_BOUNDS, SpeedRules: SPEED_RULE_BOUNDS}

DOTENV_ALLOWED = (
    # core / CLI
    "GLOBALPING_TOKEN", "GEOIP_DB", "SERVER_LAT", "SERVER_LON", "FAST_MS", "SLOW_MS",
    # web
    "RESULT_TTL_SECONDS", "SECRET_KEY", "ALLOWED_HOSTS", "DEBUG", "CSRF_TRUSTED_ORIGINS",
    "MAX_CONCURRENT_SCANS",
    # analysis thresholds
    *RULE_SETTINGS,
)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# Line colours on the map: green up to FAST_MS first byte, red from SLOW_MS.
DEFAULT_FAST_MS, DEFAULT_SLOW_MS = 300, 800
BAD_THRESHOLDS = ("FAST_MS and SLOW_MS must be numbers above 0, with SLOW_MS greater than FAST_MS "
                  "(defaults 300 and 800).")
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


def speed_thresholds(env: Mapping[str, str]) -> tuple[float, float]:
    """(FAST_MS, SLOW_MS) from the environment, with the defaults. ValueError(BAD_THRESHOLDS) otherwise."""
    out = []
    for name, default in (("FAST_MS", DEFAULT_FAST_MS), ("SLOW_MS", DEFAULT_SLOW_MS)):
        raw = (env.get(name) or "").strip()
        if not raw:
            out.append(default)
            continue
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(BAD_THRESHOLDS) from None
        if not math.isfinite(value) or value <= 0 or value > 10**9:
            raise ValueError(BAD_THRESHOLDS)
        out.append(int(value) if value.is_integer() else value)
    if out[1] <= out[0]:
        raise ValueError(BAD_THRESHOLDS)
    return out[0], out[1]


def analysis_rules(env: Mapping[str, str]) -> tuple[Rules, SpeedRules]:
    """(Rules, SpeedRules) from the environment, defaults for anything unset. A bad value raises
    ValueError naming the setting and its bounds, never the value given (so nothing posted or secret
    can reach a log through it)."""
    values: dict[type, dict] = {Rules: {}, SpeedRules: {}}
    for name, targets in RULE_SETTINGS.items():
        raw = (env.get(name) or "").strip()
        if not raw:
            continue
        kind, attr = targets[0]
        value = parse(name, raw, _BOUNDS[kind][attr], kind.__dataclass_fields__[attr].default)
        for kind, attr in targets:
            values[kind][attr] = value
    rules, speed = Rules(**values[Rules]), SpeedRules(**values[SpeedRules])
    if rules.possible_share > rules.biggest_share:
        raise ValueError("POSSIBLE_SHARE must not be above BIGGEST_SHARE "
                         f"(defaults {Rules().possible_share:g} and {Rules().biggest_share:g}).")
    return rules, speed


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
