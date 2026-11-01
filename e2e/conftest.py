"""Browser tests against the real app: gunicorn + collectstatic + WhiteNoise, pointed at FakeGlobalping.

Run with:  pip install -e '.[dev,e2e]' && playwright install chromium && pytest e2e
The Django test client cannot see sessionStorage, CSP violations or JS errors; a real browser can.
GLOBALPING_BASE_URL is loopback, so the dummy key below is allowed to be sent to the fake.
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.fake_globalping import FakeGlobalping, fixture

DUMMY_TOKEN = "e2e-dummy-token"


def _chromium_missing() -> str:
    """Empty when Chromium can be launched by Playwright, otherwise the reason it cannot."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright is not installed (pip install -e '.[e2e]')"
    try:
        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).exists():
                return "Chromium is not installed (playwright install chromium)"
    except Exception as exc:  # noqa: BLE001 - any failure here means no browser
        return f"Playwright could not start ({type(exc).__name__})"
    return ""


def pytest_collection_modifyitems(config, items):
    reason = _chromium_missing()
    if reason:
        for item in items:
            if "e2e" in str(item.fspath):
                item.add_marker(pytest.mark.skip(reason=reason))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def fake_globalping():
    f = FakeGlobalping(valid_token=DUMMY_TOKEN)
    f.by_type = {"http": fixture("http_ok"), "ping": fixture("ping_ok")}
    f.start()
    yield f
    f.stop()


@pytest.fixture(scope="session")
def live_site(fake_globalping, tmp_path_factory):
    work = tmp_path_factory.mktemp("site")  # empty cwd: no developer .env is read
    port = _free_port()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("GLOBALPING_", "SERVER_", "GEOIP_"))},
        "DJANGO_SETTINGS_MODULE": "speedward.web.settings",
        "STATIC_ROOT": str(work / "staticfiles"),
        "SECRET_KEY": "e2e-only",
        "DEBUG": "0",
        "ALLOWED_HOSTS": "127.0.0.1,localhost",
        "GLOBALPING_BASE_URL": fake_globalping.base_url,
        "GLOBALPING_TOKEN": DUMMY_TOKEN,
    }
    subprocess.run([sys.executable, "-m", "django", "collectstatic", "--noinput"],
                   env=env, cwd=work, check=True, capture_output=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "speedward.web.wsgi:application",
         "--bind", f"127.0.0.1:{port}", "--workers", "1", "--threads", "8"],
        env=env, cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError(proc.stderr.read().decode())
                time.sleep(0.1)
        else:
            raise RuntimeError("gunicorn did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def watch(page):
    """Fail the test on any console error, JS error (including CSP violations) or failed response."""
    problems = []
    page.on("console", lambda m: problems.append(f"console: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"{r.status}: {r.url}") if r.status >= 400 else None)
    page.on("dialog", lambda d: d.accept())  # the "Leave this page?" prompt on reload
    yield problems
    assert not problems, "\n".join(problems)


@pytest.fixture
def no_cf_ray(fake_globalping):
    """The recorded http result without its cf-ray header, so no answering server can be placed
    (the e2e server has no geo-IP database). Restored after the test."""
    import copy
    before = fake_globalping.by_type["http"]
    doc = copy.deepcopy(fixture("http_ok"))
    for item in doc["results"]:
        item["result"]["headers"].pop("cf-ray", None)
    fake_globalping.by_type["http"] = doc
    yield doc
    fake_globalping.by_type["http"] = before
