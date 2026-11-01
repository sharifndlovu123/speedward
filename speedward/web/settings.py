"""Django settings for the speedward web UI.

Values come from the real environment, with an allowlisted `.env` filling gaps
(speedward.core.config). GLOBALPING_BASE_URL is read from the real environment only.
"""
import os
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from speedward.core.config import build_environment

BASE_DIR = Path(__file__).resolve().parent
_ENV = build_environment(Path(".env"), on_warning=lambda m: print(m, file=sys.stderr))


def _bool(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = (_ENV.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be a whole number") from None
    if value < minimum:
        raise ImproperlyConfigured(f"{name} must be at least {minimum}")
    return value


def _float(name: str, default: float) -> float:
    raw = (_ENV.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be a number") from None
    if not value > 0:
        raise ImproperlyConfigured(f"{name} must be above 0")
    return value


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in (_ENV.get(name) or default).split(",") if x.strip()]


def require_secret_key(env: Mapping[str, str]) -> None:
    """Called by wsgi.py at server start: outside DEBUG a fixed SECRET_KEY must be set."""
    if not _bool(env.get("DEBUG")) and not (env.get("SECRET_KEY") or "").strip():
        raise ImproperlyConfigured(
            "SECRET_KEY must be set (in the environment or .env) when DEBUG is off. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(50))\"")


DEBUG = _bool(_ENV.get("DEBUG"))
# No sessions or signed data are used. A random per-start key is only a fallback for DEBUG and tests;
# wsgi.py refuses to start without a real key when DEBUG is off.
SECRET_KEY = (_ENV.get("SECRET_KEY") or "").strip() or secrets.token_urlsafe(50)
ALLOWED_HOSTS = _list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = _list("CSRF_TRUSTED_ORIGINS")

# --- speedward ----------------------------------------------------------------------------------
GLOBALPING_TOKEN = (_ENV.get("GLOBALPING_TOKEN") or "").strip() or None  # server-side only
GLOBALPING_BASE_URL = os.environ.get("GLOBALPING_BASE_URL") or None  # tests only; never from .env
GLOBALPING_POLL_INTERVAL = 0.5
GEOIP_DB = _ENV.get("GEOIP_DB") or None
SERVER_LAT = _ENV.get("SERVER_LAT") or None
SERVER_LON = _ENV.get("SERVER_LON") or None
FAST_MS = _float("FAST_MS", 300)
SLOW_MS = _float("SLOW_MS", 800)
RESULT_TTL_SECONDS = _int("RESULT_TTL_SECONDS", 900, minimum=60)
MAX_CONCURRENT_SCANS = _int("MAX_CONCURRENT_SCANS", 2, minimum=1)
if SLOW_MS <= FAST_MS:
    raise ImproperlyConfigured("SLOW_MS must be greater than FAST_MS")

# --- Django (deliberately minimal: no database, sessions, auth or admin) --------------------------
DATABASES = {}
INSTALLED_APPS = ["django.contrib.staticfiles"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "speedward.web.middleware.SecurityHeadersMiddleware",
]
ROOT_URLCONF = "speedward.web.urls"
WSGI_APPLICATION = "speedward.web.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
}]
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", Path.cwd() / "staticfiles"))
USE_TZ = True
CSRF_COOKIE_NAME = "speedward_csrftoken"  # own name: other Django apps on localhost share "csrftoken"
CSRF_COOKIE_HTTPONLY = True  # the page carries the token in a <meta> tag instead
CSRF_COOKIE_SAMESITE = "Strict"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"  # results.js sends its POSTs with referrerPolicy "same-origin"X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # a saved result posted back for render/export
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
# Logs never contain targets or results: our code logs only exception type names, and Django's own
# request logging records paths, which carry only a random scan id.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
