from __future__ import annotations

import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import typer

from speedward.core import config
from speedward.core.allowance_view import describe_allowance
from speedward.core.analysis import Limits, evaluate_limits, plain_number
from speedward.core.budget import Plan, check_budget, estimate_cost
from speedward.core.geo import geolocator_from_env
from speedward.core.globalping import DEFAULT_BASE_URL, GlobalpingClient, GlobalpingError
from speedward.core.locations import parse_locations
from speedward.core.models import clean
from speedward.core.runner import ScanError, run_scan, start_message
from speedward.core.target import normalise_target
from speedward.reports import FORMATS, render
from speedward.reports._view import skipped

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False, pretty_exceptions_show_locals=False,
                  help="Test a website's speed from locations around the world.")
# The same upper bound as the web form: every limit stays a finite, printable number.
LIMIT_MAX = 10**9
NO_BROWSER_LIMIT = ("Error: --max-page-weight needs the browser test, which is not available in this build yet, "
                    "so that limit could never pass.")
NOTICE = "Only test sites you own or are authorised to test."
NO_KEY = ("No Globalping key set (limits: 250 tests/hour, 50 locations). "
          "A key is free up to a limit: https://globalping.io")


@app.callback()
def main():
    """speedward: measure how fast a site loads from around the world."""


def _err(message: str):
    typer.echo(message, err=True)


def load_dotenv(path: Path) -> dict[str, str]:
    """Kept for callers of the CLI module; the reader lives in speedward.core.config."""
    return config.load_dotenv(path, on_warning=_err)


def _environment() -> dict[str, str]:
    """The real environment, with allowlisted `.env` keys filling gaps (never overriding)."""
    return config.build_environment(Path(".env"), on_warning=_err)


def _scan_notes(report) -> None:
    """Fixed-wording notes about what the report could not measure like-for-like. No target, no results."""
    for _, _, note in skipped(report):
        _err(f"Not run: {note}")
    other = sum(1 for r in report.locations if r.status == "measured" and r.repeat_stages is not None
                and not r.repeat_same_probe)
    if other:
        _err(f"Note: {other} location{'' if other == 1 else 's'} {'was' if other == 1 else 'were'} repeated on a "
             "different test machine; they are left out of the cache gain.")


@app.command()
def scan(
    url: str = typer.Argument(..., help="Site to test, e.g. https://example.com"),
    locations: str | None = typer.Option(None, "--locations", help="Comma-separated places, e.g. Berlin,Tokyo,Brazil."),
    traceroute: bool = typer.Option(False, "--traceroute", help="Also trace the network path (more tests)."),
    dns: bool = typer.Option(False, "--dns", help="Also check DNS answers (more tests)."),
    browser: bool = typer.Option(False, "--browser", help="Also run the browser test."),
    fmt: str = typer.Option("md", "--format", help="Report format: json, md or html."),
    out: Path | None = typer.Option(None, "--out", help="Write the report to this file instead of stdout."),
    max_ttfb: float | None = typer.Option(None, "--max-ttfb", min=1, max=LIMIT_MAX, help="Fail if first byte takes longer than this (ms)."),
    max_load: float | None = typer.Option(None, "--max-load", min=1, max=LIMIT_MAX, help="Fail if load takes longer than this (ms)."),
    max_blocked: int | None = typer.Option(None, "--max-blocked", min=0, max=LIMIT_MAX, help="Fail if more locations than this were blocked."),
    max_page_weight: int | None = typer.Option(None, "--max-page-weight", min=1, max=LIMIT_MAX,
                                               help="Not available yet: needs the browser test (refused if set)."),
    yes: bool = typer.Option(False, "--yes", help="Go ahead even if the scan costs more than the tests left."),
    estimate: bool = typer.Option(False, "--estimate", help="Print the cost and allowance, then stop."),
    verbose: bool = typer.Option(False, "--verbose", help="Show more detail when something goes wrong."),
):
    try:
        _run(**locals())
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - never let a traceback (with locals) reach the terminal
        _err("The scan failed unexpectedly.")
        if verbose:
            _err(f"Detail: {clean(str(exc))}")
        raise typer.Exit(2) from None


def _run(url, locations, traceroute, dns, browser, fmt, out, max_ttfb, max_load, max_blocked, max_page_weight, yes, estimate, verbose):
    _err(NOTICE)
    if fmt not in FORMATS:
        _err(f"Error: --format must be one of {', '.join(FORMATS)}")
        raise typer.Exit(2)
    try:
        normalise_target(url)
        specs = parse_locations(locations)
    except ValueError as exc:
        _err(f"Error: {clean(str(exc))}")
        raise typer.Exit(2) from None

    if any(v is not None and not math.isfinite(v) for v in (max_ttfb, max_load)):
        _err("Error: --max-ttfb and --max-load must be numbers from 1 to 1000000000.")
        raise typer.Exit(2)
    if max_page_weight is not None:
        _err(NO_BROWSER_LIMIT)
        raise typer.Exit(2)
    if browser:
        _err("Note: the browser test is not available in this build yet; continuing without it.")
    plan = Plan(locations=specs, traceroute=traceroute, dns=dns, browser=False)

    env = _environment()
    try:  # checked before any test is spent
        fast_ms, slow_ms = config.speed_thresholds(env)
    except ValueError as exc:
        _err(f"Error: {exc}")
        raise typer.Exit(2) from None
    # GLOBALPING_BASE_URL is for tests only (points at a local fake); deliberately not in --help.
    # Read from the real process environment only, never from `.env`.
    base_url = os.environ.get("GLOBALPING_BASE_URL") or None
    kwargs = {}
    token = env.get("GLOBALPING_TOKEN") or None
    if base_url and base_url.rstrip("/") != DEFAULT_BASE_URL:
        _err(f"Using a custom Globalping endpoint: {clean(urlsplit(base_url).hostname or '')}")
    try:
        custom = config.check_endpoint(base_url, token)
    except config.EndpointRefused as exc:
        _err(str(exc))
        raise typer.Exit(2) from None
    if custom:
        kwargs["base_url"] = custom
    client = GlobalpingClient(token=token, **kwargs)

    try:
        allowance = client.allowance()
    except GlobalpingError as exc:
        _err(f"Error: {clean(str(exc))}")
        raise typer.Exit(2) from None

    cost = estimate_cost(plan)
    if not allowance.authenticated:
        _err(NO_KEY)
    left = "an unknown number of" if allowance.remaining is None else str(allowance.remaining)
    reset = describe_allowance(allowance, cost, time.time()).reset_phrase
    _err(f"This scan uses about {cost} tests. You have {left} left this hour ({reset}).")
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
        _err(start_message(exc.kind))
        if verbose:
            _err(f"Detail: {clean(str(exc))}")
        raise typer.Exit(2) from None
    except GlobalpingError as exc:
        _err("The scan failed because Globalping could not be used.")
        if verbose:
            _err(f"Detail: {clean(str(exc))}")
        raise typer.Exit(2) from None

    text = render(report, fmt, fast_ms=fast_ms, slow_ms=slow_ms)
    if out:
        try:
            out.write_text(text, encoding="utf-8")
            _err(f"Report written to {out}")
        except OSError:
            _err("Could not write the report file.")
            typer.echo(text)
            raise typer.Exit(2) from None
    else:
        typer.echo(text)

    _scan_notes(report)
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
        _err("Pass/fail: " + verdict_word + " (limits: " + ", ".join(f"{n} {plain_number(v)}" for n, v in set_limits) + ")")
    raise typer.Exit(outcome.exit_code)
