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
    fake.by_type["http"] = fixture("http_timeout")
    r = scan("--max-blocked", "0")
    assert r.exit_code == 2  # nothing measured at all -> could not test


def test_all_locations_failed_exits_2(fake):
    fake.by_type["http"] = fixture("http_timeout")
    r = scan()
    assert r.exit_code == 2
    assert "Could not test" in r.stderr


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
