from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import typer

from speedward.core.analysis import Limits, evaluate_limits
from speedward.core.budget import Plan, check_budget, estimate_cost
from speedward.core.geo import geolocator_from_env
from speedward.core.globalping import DEFAULT_BASE_URL, GlobalpingClient, GlobalpingError
from speedward.core.locations import parse_locations
from speedward.core.models import clean
from speedward.core.runner import ScanError, run_scan
from speedward.core.target import normalise_target
from speedward.reports import FORMATS, render

app = typer.Typer(add_completion=False, help="Test a website's speed from locations around the world.")
NOTICE = "Only test sites you own or are authorised to test."
NO_KEY = ("No Globalping key set (limits: 250 tests/hour, 50 locations). "
          "A key is free up to a limit: https://globalping.io")
START_FAILED = "The scan could not start. Check the address and your Globalping key."


@app.callback()
def main():
    """speedward: measure how fast a site loads from around the world."""


def _err(message: str):
    typer.echo(message, err=True)


def load_dotenv(path: Path) -> dict[str, str]:
    """Tiny .env reader: KEY=VALUE lines, # comments, blank lines, simple quotes. No expansion."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


# Only these keys may come from a `.env` in the current directory, which may be untrusted
# (e.g. a cloned repo). Anything that could move an endpoint or path is deliberately absent.
DOTENV_ALLOWED = ("GLOBALPING_TOKEN", "GEOIP_DB", "SERVER_LAT", "SERVER_LON", "FAST_MS", "SLOW_MS")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _environment() -> dict[str, str]:
    """The real environment, with allowlisted `.env` keys filling gaps (never overriding)."""
    file_env = {k: v for k, v in load_dotenv(Path(".env")).items() if k in DOTENV_ALLOWED}
    return {**file_env, **os.environ}


@app.command()
def scan(
    url: str = typer.Argument(..., help="Site to test, e.g. https://example.com"),
    locations: str | None = typer.Option(None, "--locations", help="Comma-separated places, e.g. Berlin,Tokyo,Brazil."),
    traceroute: bool = typer.Option(False, "--traceroute", help="Also trace the network path (more tests)."),
    dns: bool = typer.Option(False, "--dns", help="Also check DNS answers (more tests)."),
    browser: bool = typer.Option(False, "--browser", help="Also run the browser test."),
    fmt: str = typer.Option("md", "--format", help="Report format: json, md or html."),
    out: Path | None = typer.Option(None, "--out", help="Write the report to this file instead of stdout."),
    max_ttfb: float | None = typer.Option(None, "--max-ttfb", help="Fail if first byte takes longer than this (ms)."),
    max_load: float | None = typer.Option(None, "--max-load", help="Fail if load takes longer than this (ms)."),
    max_blocked: int | None = typer.Option(None, "--max-blocked", help="Fail if more locations than this were blocked."),
    max_page_weight: int | None = typer.Option(None, "--max-page-weight", help="Fail if the page is heavier than this (bytes)."),
    yes: bool = typer.Option(False, "--yes", help="Go ahead even if the scan costs more than the tests left."),
    estimate: bool = typer.Option(False, "--estimate", help="Print the cost and allowance, then stop."),
    verbose: bool = typer.Option(False, "--verbose", help="Show more detail when something goes wrong."),
):
    if fmt not in FORMATS:
        _err(f"Error: --format must be one of {', '.join(FORMATS)}")
        raise typer.Exit(2)
    _err(NOTICE)
    try:
        normalise_target(url)
        specs = parse_locations(locations)
    except ValueError as exc:
        _err(f"Error: {clean(str(exc))}")
        raise typer.Exit(2)

    if browser:
        _err("Note: the browser test is not available in this build yet; continuing without it.")
    plan = Plan(locations=specs, traceroute=traceroute, dns=dns, browser=False)

    env = _environment()
    # GLOBALPING_BASE_URL is for tests only (points at a local fake); deliberately not in --help.
    # Read from the real process environment only, never from `.env`.
    base_url = os.environ.get("GLOBALPING_BASE_URL") or None
    kwargs = {}
    token = env.get("GLOBALPING_TOKEN") or None
    if base_url and base_url.rstrip("/") != DEFAULT_BASE_URL:
        host = urlsplit(base_url).hostname or ""
        _err(f"Using a custom Globalping endpoint: {clean(host)}")
        if token and host not in LOOPBACK_HOSTS:
            _err("Refusing to send a Globalping key to a non-default, non-local endpoint.")
            raise typer.Exit(2)
        kwargs["base_url"] = base_url
    client = GlobalpingClient(token=token, **kwargs)

    try:
        allowance = client.allowance()
    except GlobalpingError as exc:
        _err(f"Error: {clean(str(exc))}")
        raise typer.Exit(2)

    cost = estimate_cost(plan)
    if not allowance.authenticated:
        _err(NO_KEY)
    left = "an unknown number of" if allowance.remaining is None else str(allowance.remaining)
    _err(f"This scan uses about {cost} tests. You have {left} left this hour.")
    if allowance.credits is not None:
        _err(f"Globalping credits: {allowance.credits}.")
    if estimate:
        raise typer.Exit(0)

    verdict = check_budget(plan, allowance)
    if not verdict.fits:
        too_many = len(specs) > allowance.max_probes
        if too_many or not yes:
            _err(f"Refused: {verdict.reason}" + ("" if too_many else " Use --yes to go ahead anyway."))
            raise typer.Exit(2)
        if allowance.credits is not None and allowance.credits <= 0:
            _err("Refused: this scan is over the tests left and you have no Globalping credits. "
                 "Credits are needed to go over the hourly allowance.")
            raise typer.Exit(2)

    try:
        report = run_scan(url, plan, client, on_progress=lambda line: _err(f"  {line}"),
                          geo=geolocator_from_env(env))
    except ScanError as exc:
        _err(START_FAILED)
        if verbose:
            _err(f"Detail: {clean(str(exc))}")
        raise typer.Exit(2)
    except GlobalpingError as exc:
        _err("The scan failed because Globalping could not be used.")
        if verbose:
            _err(f"Detail: {clean(str(exc))}")
        raise typer.Exit(2)

    text = render(report, fmt)
    if out:
        out.write_text(text, encoding="utf-8")
        _err(f"Report written to {out}")
    else:
        typer.echo(text)

    limits = Limits(max_ttfb=max_ttfb, max_load=max_load, max_blocked=max_blocked,
                    max_page_weight=max_page_weight)
    outcome = evaluate_limits(report, limits)
    for message in outcome.failed:
        _err(f"Failed: {clean(message)}")
    set_limits = [(n, v) for n, v in (("max-ttfb", max_ttfb), ("max-load", max_load),
                                      ("max-blocked", max_blocked), ("max-page-weight", max_page_weight))
                  if v is not None]
    if not set_limits:
        _err("No limits set")
    elif outcome.exit_code == 2:
        _err("Pass/fail: could not test, so no limit was judged")
    else:
        verdict_word = "FAILED" if outcome.failed else "passed"
        _err("Pass/fail: " + verdict_word + " (limits: " + ", ".join(f"{n} {v:g}" for n, v in set_limits) + ")")
    raise typer.Exit(outcome.exit_code)
