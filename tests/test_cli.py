import pytest
from typer.testing import CliRunner

from speedward.cli.main import app, load_dotenv
from speedward.core.runner import ScanError
from tests.fake_globalping import FakeGlobalping, fixture

TOKEN = "secret-token-xyz"
TWO = "Berlin,Tokyo"
runner = CliRunner()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeGlobalping()
    f.by_type = {"http": fixture("http_ok"), "ping": fixture("ping_ok"),
                 "mtr": fixture("mtr_ok"), "dns": fixture("dns_ok")}
    f.start()
    monkeypatch.setenv("GLOBALPING_BASE_URL", f.base_url)
    monkeypatch.delenv("GLOBALPING_TOKEN", raising=False)
    monkeypatch.delenv("SPEEDWARD_GEOIP_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    yield f
    f.stop()


def scan(*args):
    return runner.invoke(app, ["scan", "example.com", "--locations", TWO, *args])


def test_estimate_prints_cost_and_makes_no_measurements(fake):
    r = scan("--estimate")
    assert r.exit_code == 0
    assert "about 6 tests" in r.stderr and "250 left this hour" in r.stderr
    assert "Only test sites you own or are authorised to test." in r.stderr
    assert fake.posts() == []


def test_scan_refused_when_over_allowance_without_yes(fake):
    fake.remaining = 4
    r = scan()
    assert r.exit_code == 2
    assert "only 4 are left" in r.stderr
    assert fake.posts() == []


def test_yes_accepts_cost_over_remaining(fake):
    fake.remaining = 4
    r = scan("--yes")
    assert r.exit_code == 0
    assert fake.posts()


def test_yes_still_refuses_when_credits_are_zero(fake, monkeypatch):
    monkeypatch.setenv("GLOBALPING_TOKEN", TOKEN)
    fake.remaining = 4
    fake.credits = 0
    r = scan("--yes")
    assert r.exit_code == 2
    assert "credits" in r.stderr
    assert fake.posts() == []


def test_scan_passes_and_writes_report_to_stdout(fake):
    r = scan("--format", "json")
    assert r.exit_code == 0
    assert r.stdout.lstrip().startswith("{")
    assert "No limits set" in r.stderr


def test_failed_limit_exits_1(fake):
    r = scan("--max-ttfb", "1")
    assert r.exit_code == 1
    assert "over the limit" in r.stderr
    assert "Pass/fail" in r.stderr


def test_passing_limit_listed(fake):
    r = scan("--max-ttfb", "100000")
    assert r.exit_code == 0
    assert "Pass/fail" in r.stderr and "max-ttfb" in r.stderr


def test_max_blocked(fake):
    doc = fixture("http_ok")
    doc["results"][1]["result"] = fixture("http_timeout")["results"][0]["result"]
    fake.by_type["http"] = doc
    assert scan("--max-blocked", "0").exit_code == 1
    assert scan("--max-blocked", "1").exit_code == 0


def test_all_locations_failed_exits_2(fake):
    fake.by_type["http"] = fixture("http_timeout")
    r = scan()
    assert r.exit_code == 2
    assert "Could not test" in r.stderr
    assert r.stdout.strip()


def test_token_never_in_output(fake, monkeypatch):
    monkeypatch.setenv("GLOBALPING_TOKEN", TOKEN)
    for args in ([], ["--estimate"], ["--verbose", "--max-ttfb", "1"], ["--format", "html"]):
        r = scan(*args)
        assert TOKEN not in r.stdout and TOKEN not in r.stderr
    assert fake.tokens_seen()[0] == f"Bearer {TOKEN}"


def test_bad_format_exits_2(fake):
    r = scan("--format", "pdf")
    assert r.exit_code == 2
    assert "json" in r.stderr and "md" in r.stderr and "html" in r.stderr


def test_bad_url_exits_2_without_control_chars(fake):
    r = runner.invoke(app, ["scan", "exa\x1b[31mmple.com"])
    assert r.exit_code == 2
    assert "\x1b" not in r.stderr


def test_missing_key_wording(fake):
    r = scan("--estimate")
    assert "No Globalping key set (limits: 250 tests/hour, 50 locations)." in r.stderr
    assert "free up to a limit" in r.stderr
    assert " free " not in r.stderr.replace("free up to a limit", "")


def test_out_writes_file(fake, tmp_path):
    out = tmp_path / "report.md"
    r = scan("--out", str(out))
    assert r.exit_code == 0
    assert out.read_text().strip()
    assert r.stdout.strip() == ""


def test_browser_notice_and_continues(fake):
    r = scan("--browser")
    assert r.exit_code == 0
    assert "the browser test is not available in this build yet" in r.stderr
    assert not fake.posts("mtr")


def test_scan_error_text_not_echoed(fake, monkeypatch):
    def boom(*a, **k):
        raise ScanError("The test could not be started: secret-site.example not allowed\x1b[0m")
    monkeypatch.setattr("speedward.cli.main.run_scan", boom)
    r = scan()
    assert r.exit_code == 2
    assert "The scan could not start. Check the address and your Globalping key." in r.stderr
    assert "secret-site.example" not in r.stderr
    r = scan("--verbose")
    assert "secret-site.example" in r.stderr and "\x1b" not in r.stderr


# --- .env loader ------------------------------------------------------

def test_dotenv_parses_quotes_comments_and_blanks(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\n\nA=1\nB='two'\nC=\"three\"\nexport D=4\nbad line\n")
    assert load_dotenv(p) == {"A": "1", "B": "two", "C": "three", "D": "4"}


def test_dotenv_missing_file(tmp_path):
    assert load_dotenv(tmp_path / ".env") == {}


def test_env_file_token_used_but_real_env_wins(fake, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text('GLOBALPING_TOKEN="from-file"\n')
    scan("--estimate")
    assert fake.tokens_seen()[0] == "Bearer from-file"
    fake.requests.clear()
    monkeypatch.setenv("GLOBALPING_TOKEN", "from-env")
    scan("--estimate")
    assert fake.tokens_seen()[0] == "Bearer from-env"


# --- endpoint safety --------------------------------------------------

def test_dotenv_cannot_redirect_endpoint(fake, tmp_path):
    (tmp_path / ".env").write_text("GLOBALPING_BASE_URL=http://evil.example/\nGLOBALPING_TOKEN=tok-file\n")
    r = scan("--estimate")
    assert r.exit_code == 0
    assert "evil.example" not in r.stderr
    assert fake.tokens_seen()[0] == "Bearer tok-file"


def test_remote_override_with_token_refused(fake, monkeypatch):
    monkeypatch.setenv("GLOBALPING_BASE_URL", "http://evil.example/v1")
    monkeypatch.setenv("GLOBALPING_TOKEN", TOKEN)
    r = scan("--estimate")
    assert r.exit_code == 2
    assert "Refusing to send a Globalping key to a non-default, non-local endpoint." in r.stderr
    assert "Using a custom Globalping endpoint: evil.example" in r.stderr
    assert TOKEN not in r.stdout + r.stderr
    assert fake.requests == []


def test_dotenv_allowlist(tmp_path, monkeypatch):
    from speedward.cli.main import _environment
    (tmp_path / ".env").write_text("GLOBALPING_TOKEN=t\nGEOIP_DB=/x\nSERVER_LAT=1\nSERVER_LON=2\n"
                                   "FAST_MS=1\nSLOW_MS=2\nGLOBALPING_BASE_URL=http://e/\nPATH=/evil\nOTHER=1\n")
    monkeypatch.chdir(tmp_path)
    for k in ("GLOBALPING_TOKEN", "GEOIP_DB", "SERVER_LAT", "SERVER_LON", "FAST_MS", "SLOW_MS", "OTHER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("GLOBALPING_BASE_URL", raising=False)
    env = _environment()
    assert env["GLOBALPING_TOKEN"] == "t" and env["SLOW_MS"] == "2" and env["GEOIP_DB"] == "/x"
    assert "OTHER" not in env and "GLOBALPING_BASE_URL" not in env
    assert env["PATH"] != "/evil"


# --- robustness -------------------------------------------------------

def test_app_hides_locals():
    assert app.pretty_exceptions_show_locals is False


def test_unwritable_out_falls_back_to_stdout(fake, tmp_path):
    r = scan("--out", str(tmp_path / "no" / "such" / "dir" / "r.md"))
    assert r.exit_code == 2
    assert "Could not write the report file." in r.stderr
    assert r.stdout.strip()
    assert "Traceback" not in r.stdout + r.stderr


def test_non_utf8_dotenv_ignored(fake, tmp_path):
    (tmp_path / ".env").write_bytes(b"GLOBALPING_TOKEN=\xff\xfe\x80\n")
    r = scan("--estimate")
    assert r.exit_code == 0
    assert ".env file could not be read" in r.stderr
    assert "Traceback" not in r.stderr


@pytest.mark.parametrize("exc", [KeyError("tok-xyz-secret"), __import__("httpx").ConnectError("boom tok-xyz-secret")])
def test_unexpected_error_fixed_message(fake, monkeypatch, exc):
    monkeypatch.setenv("GLOBALPING_TOKEN", "tok-xyz-secret")
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr("speedward.cli.main.run_scan", boom)
    r = scan()
    assert r.exit_code == 2
    assert "The scan failed unexpectedly." in r.stderr
    assert "tok-xyz-secret" not in r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr


@pytest.mark.parametrize("args", [["--max-ttfb", "0"], ["--max-load", "-5"], ["--max-page-weight", "0"],
                                  ["--max-blocked", "-1"]])
def test_invalid_limits_exit_2(fake, args):
    r = scan(*args)
    assert r.exit_code == 2
    assert fake.posts() == []


def test_notice_before_bad_format(fake):
    r = scan("--format", "pdf")
    assert r.stderr.index("Only test sites") < r.stderr.index("--format")


def test_dotenv_inline_comments_and_empty(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=abc # note\nB=\"x # y\" # c\nC=\nD='q'\nE=a#b\n")
    assert load_dotenv(p) == {"A": "abc", "B": "x # y", "C": "", "D": "q", "E": "a#b"}


@pytest.mark.parametrize("args, text", [
    (["--max-ttfb", "1234567"], "max-ttfb 1234567"),
    (["--max-load", "5000000"], "max-load 5000000"),
    (["--max-blocked", "1234567890"], "max-blocked 1234567890"),
    (["--max-ttfb", "250.5"], "max-ttfb 250.5"),
])
def test_pass_fail_line_prints_limits_exactly(fake, args, text):
    r = scan(*args)
    assert f"(limits: {text})" in r.stderr
    assert "e+" not in r.stderr


def test_cli_estimate_prints_reset_countdown(fake):
    fake.reset = 2220
    r = scan("--estimate")
    assert "You have 250 left this hour (resets in 37 min)." in r.stderr
    assert "last run" not in r.stderr.lower()


def test_cli_estimate_unknown_reset_is_not_guessed(fake):
    fake.omit_reset = True
    r = scan("--estimate")
    assert "(reset time unknown)" in r.stderr


# --- final review: skipped measurements and other-probe repeats on stderr ------------------------

def test_cli_lists_skipped_measurements_on_stderr(fake):
    fake.rate_limit_after = 1  # cold accepted; the repeat test and ping are refused
    r = scan()
    assert r.exit_code == 0
    assert "Not run: Repeat test: Globalping limit reached, so the Cache column and cache gain have no figures." in r.stderr
    assert "Not run: Ping: Globalping limit reached, so the Delay column has no figures." in r.stderr
    assert "example.com" not in r.stderr.replace("Only test sites", "")


def test_cli_says_when_repeats_ran_on_other_probes(fake):
    fake.reuse_expired = True
    r = scan("--format", "md")
    assert r.exit_code == 0
    assert "repeat request (different probe)" in r.stdout
    assert ("Note: 2 locations were repeated on a different test machine; "
            "they are left out of the cache gain.") in r.stderr
