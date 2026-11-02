"""Shared `.env` loader and Globalping endpoint guard (used by the CLI and the web settings)."""
import pytest

from speedward.core.config import (
    DOTENV_ALLOWED,
    EndpointRefused,
    build_environment,
    check_endpoint,
    load_dotenv,
)
from speedward.core.globalping import DEFAULT_BASE_URL

ENV_FILE = ("GLOBALPING_TOKEN=t\nGEOIP_DB=/x\nSERVER_LAT=1\nSERVER_LON=2\nFAST_MS=1\nSLOW_MS=2\n"
            "RESULT_TTL_SECONDS=60\nSECRET_KEY=s\nALLOWED_HOSTS=a\nDEBUG=1\nCSRF_TRUSTED_ORIGINS=https://a\n"
            "MAX_CONCURRENT_SCANS=3\nGLOBALPING_BASE_URL=http://evil.example/\nPATH=/evil\nOTHER=1\n")


def test_load_dotenv_parses_quotes_comments_and_blanks(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\n\nA=1\nB='two'\nC=\"three\"\nexport D=4\nbad line\nE=x # note\n")
    assert load_dotenv(p) == {"A": "1", "B": "two", "C": "three", "D": "4", "E": "x"}


def test_load_dotenv_missing_or_binary_file(tmp_path):
    assert load_dotenv(tmp_path / ".env") == {}
    p = tmp_path / ".env"
    p.write_bytes(b"A=\xff\xfe\n")
    warnings = []
    assert load_dotenv(p, on_warning=warnings.append) == {}
    assert warnings and ".env" in warnings[0]


def test_allowlist_covers_core_and_web_settings_only():
    assert set(DOTENV_ALLOWED) == {
        "GLOBALPING_TOKEN", "GEOIP_DB", "SERVER_LAT", "SERVER_LON", "FAST_MS", "SLOW_MS",
        "RESULT_TTL_SECONDS", "SECRET_KEY", "ALLOWED_HOSTS", "DEBUG", "CSRF_TRUSTED_ORIGINS",
        "MAX_CONCURRENT_SCANS"}
    assert "GLOBALPING_BASE_URL" not in DOTENV_ALLOWED


def test_build_environment_applies_allowlist_and_real_env_wins(tmp_path):
    p = tmp_path / ".env"
    p.write_text(ENV_FILE)
    env = build_environment(p, environ={"PATH": "/usr/bin", "SLOW_MS": "999"})
    assert env["GLOBALPING_TOKEN"] == "t" and env["MAX_CONCURRENT_SCANS"] == "3"
    assert env["SECRET_KEY"] == "s" and env["RESULT_TTL_SECONDS"] == "60"
    assert env["SLOW_MS"] == "999"  # the real environment wins
    assert env["PATH"] == "/usr/bin"
    assert "OTHER" not in env
    assert "GLOBALPING_BASE_URL" not in env  # never from .env


def test_base_url_only_from_real_environment(tmp_path):
    p = tmp_path / ".env"
    p.write_text(ENV_FILE)
    env = build_environment(p, environ={"GLOBALPING_BASE_URL": "http://127.0.0.1:9/v1"})
    assert env["GLOBALPING_BASE_URL"] == "http://127.0.0.1:9/v1"


def test_check_endpoint_default_and_unset():
    assert check_endpoint(None, "tok") is None
    assert check_endpoint("", "tok") is None
    assert check_endpoint(DEFAULT_BASE_URL + "/", "tok") is None


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/v1", "http://localhost/v1", "http://[::1]:5/v1"])
def test_check_endpoint_loopback_allows_token(url):
    assert check_endpoint(url, "tok") == url


def test_check_endpoint_remote_without_token_allowed():
    assert check_endpoint("https://mirror.example/v1", None) == "https://mirror.example/v1"


def test_check_endpoint_refuses_token_to_remote_host():
    with pytest.raises(EndpointRefused) as info:
        check_endpoint("https://evil.example/v1", "tok-secret")
    assert "tok-secret" not in str(info.value)
    assert info.value.host == "evil.example"


# --- FAST_MS / SLOW_MS (final review item 4) ----------------------------------------------------

def test_speed_thresholds_defaults_and_values():
    from speedward.core.config import speed_thresholds
    assert speed_thresholds({}) == (300, 800)
    assert speed_thresholds({"FAST_MS": "50", "SLOW_MS": "100.5"}) == (50, 100.5)


@pytest.mark.parametrize("env", [{"FAST_MS": "x"}, {"FAST_MS": "0"}, {"SLOW_MS": "-1"}, {"FAST_MS": "nan"},
                                 {"FAST_MS": "inf"}, {"FAST_MS": "900"}, {"FAST_MS": "500", "SLOW_MS": "500"}])
def test_speed_thresholds_rejects_bad_values(env):
    from speedward.core.config import speed_thresholds
    with pytest.raises(ValueError):
        speed_thresholds(env)
