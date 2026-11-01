"""Views for the local web UI.

Flow: the browser asks for an estimate, starts a scan (a background thread), polls its status and
receives the finished result JSON exactly once; the server then forgets it. The browser keeps the
result in sessionStorage and turns it into HTML through POST /render, which is stateless: it
rebuilds the report from the posted JSON, renders the fragment and keeps nothing. Exports work the
same way through POST /export/<fmt>.

Never log, echo or store: the target, results, exception text (ScanError and GlobalpingError text
can repeat the target) or the Globalping key. Only fixed messages and exception type names.
"""
from __future__ import annotations

import json
import logging
import math
import threading

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.safestring import mark_safe
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from speedward.core.analysis import Limits, evaluate_limits, plain_number
from speedward.core.budget import Plan, check_budget, estimate_cost
from speedward.core.config import EndpointRefused, check_endpoint
from speedward.core.geo import geolocator_from_env
from speedward.core.globalping import Allowance, GlobalpingClient, GlobalpingError
from speedward.core.locations import DEFAULT_LOCATIONS, parse_locations
from speedward.core.models import ScanReport
from speedward.core.runner import ScanError, run_scan
from speedward.core.target import normalise_target
from speedward.reports import render as render_report
from speedward.reports._view import context
from speedward.reports.map import render_map

from . import state

logger = logging.getLogger(__name__)

NOTICE = "Only test sites you own or are authorised to test."
START_FAILED = "The scan could not start. Check the address and your Globalping key."
GLOBALPING_FAILED = "The scan failed because Globalping could not be used."
UNEXPECTED = "The scan failed unexpectedly."
BUSY = "The scanner is busy with other scans. Please try again in a minute."
BAD_RESULT = "That result could not be read. Run the scan again or load a valid export."
BAD_LIMITS = ("Pass/fail limits must be numbers of 1 or more (max blocked: a whole number, 0 or more).")
GONE = "This scan is no longer on the server. Results are kept only in your browser."
UNREACHABLE = ("Globalping could not be reached, or it rejected the key. "
               "Check the server's network and its GLOBALPING_TOKEN setting.")
BAD_SERVER_POSITION = "The server position settings (SERVER_LAT, SERVER_LON) are not valid."
CREDITS_ZERO = ("Refused: this scan is over the tests left and you have no Globalping credits. "
                "Credits are needed to go over the hourly allowance.")
EXPORT_TYPES = {"json": "application/json", "md": "text/markdown; charset=utf-8",
                "html": "text/html; charset=utf-8"}
LIMIT_LABELS = (("max_ttfb", "max first byte", " ms"), ("max_load", "max load", " ms"),
                ("max_blocked", "max blocked", ""), ("max_page_weight", "max page weight", " bytes"))


# --- Globalping access -------------------------------------------------------------------------

def _client() -> GlobalpingClient:
    """Raises EndpointRefused before any request when the key would leave this machine."""
    token = settings.GLOBALPING_TOKEN or None
    custom = check_endpoint(settings.GLOBALPING_BASE_URL, token)
    kwargs = {"base_url": custom} if custom else {}
    return GlobalpingClient(token=token, poll_interval=settings.GLOBALPING_POLL_INTERVAL, **kwargs)


def _status_line(a: Allowance) -> str:
    if not a.authenticated:
        return (f"No Globalping key set: {a.tests_per_hour} tests/hour, {a.max_probes} locations. "
                f"A key is free up to a limit: ")
    left = "unknown" if a.remaining is None else str(a.remaining)
    line = (f"Globalping key active: {a.tests_per_hour} tests/hour, up to {a.max_probes} locations. "
            f"Remaining this hour: {left}")
    if a.credits is not None:
        line += f". Credits: {a.credits}"
    return line


def _allowance_dict(a: Allowance) -> dict:
    return {"tests_per_hour": a.tests_per_hour, "max_probes": a.max_probes, "remaining": a.remaining,
            "authenticated": a.authenticated, "credits": a.credits}


def _error(message: str, status: int, **extra) -> JsonResponse:
    return JsonResponse({"error": message, **extra}, status=status)


def _plan(request) -> Plan:
    """ValueError (with a fixed, target-free message) on a bad locations list."""
    return Plan(locations=parse_locations(request.POST.get("locations")),
                traceroute=bool(request.POST.get("traceroute")), dns=bool(request.POST.get("dns")))


def _budget(plan: Plan, allowance: Allowance) -> dict:
    """The estimate, plus `refused` when no confirmation can make the scan go ahead (as in the CLI)."""
    cost = estimate_cost(plan)
    verdict = check_budget(plan, allowance)
    left = "an unknown number of" if allowance.remaining is None else str(allowance.remaining)
    refused = ""
    if not verdict.fits:
        if len(plan.locations) > allowance.max_probes:
            refused = verdict.reason
        elif allowance.credits is not None and allowance.credits <= 0:
            refused = CREDITS_ZERO
    return {"cost": cost, "fits": verdict.fits, "reason": verdict.reason, "refused": refused,
            "message": f"This scan uses about {cost} tests. You have {left} left this hour.",
            "allowance": _allowance_dict(allowance)}


# --- pages ---------------------------------------------------------------------------------------

@require_GET
@never_cache
def index(request):
    status, link = "", False
    try:
        allowance = _client().allowance()
        status, link = _status_line(allowance), not allowance.authenticated
    except EndpointRefused as exc:
        status = str(exc)
    except GlobalpingError:
        status = UNREACHABLE
    return render(request, "index.html", {
        "notice": NOTICE, "status_line": status, "status_link": link,
        "ttl_seconds": settings.RESULT_TTL_SECONDS, "ttl_minutes": max(1, settings.RESULT_TTL_SECONDS // 60),
        "default_locations": ", ".join(loc["magic"] for loc in DEFAULT_LOCATIONS),
    })


@require_POST
@never_cache
def estimate(request):
    try:
        plan = _plan(request)
    except ValueError as exc:
        return _error(str(exc), 400)
    try:
        _limits(request.POST)
    except ValueError:
        return _error(BAD_LIMITS, 400)
    try:
        allowance = _client().allowance()
    except EndpointRefused as exc:
        return _error(str(exc), 503)
    except GlobalpingError:
        return _error(UNREACHABLE, 502)
    return JsonResponse(_budget(plan, allowance))


def _run(st: state.ScanState, entry: state.Entry, url: str, plan: Plan, client: GlobalpingClient, geo) -> None:
    """Background thread body. Records the outcome in memory; logs only an exception type name."""
    try:
        report = run_scan(url, plan, client, on_progress=entry.add_progress, geo=geo)
        st.finish(entry.id, report.to_dict())
    except ScanError:
        st.fail(entry.id, START_FAILED)
    except GlobalpingError:
        st.fail(entry.id, GLOBALPING_FAILED)
    except Exception as exc:  # noqa: BLE001 - the text may contain the target; never log it
        logger.error("scan failed: %s", type(exc).__name__)
        st.fail(entry.id, UNEXPECTED)


@require_POST
@never_cache
def scan(request):
    url = request.POST.get("url", "")
    try:
        normalise_target(url)
        plan = _plan(request)
    except ValueError as exc:  # fixed messages from target.py / locations.py
        return _error(str(exc), 400)
    try:  # checked before any test is spent, though only /render uses them
        _limits(request.POST)
    except ValueError:
        return _error(BAD_LIMITS, 400)
    try:
        geo = geolocator_from_env({"GEOIP_DB": settings.GEOIP_DB or "", "SERVER_LAT": settings.SERVER_LAT or "",
                                   "SERVER_LON": settings.SERVER_LON or ""})
    except ValueError:
        return _error(BAD_SERVER_POSITION, 500)
    try:
        client = _client()
        allowance = client.allowance()
    except EndpointRefused as exc:
        return _error(str(exc), 503)
    except GlobalpingError:
        return _error(UNREACHABLE, 502)

    budget = _budget(plan, allowance)
    if budget["refused"]:
        return JsonResponse({**budget, "error": budget["refused"]}, status=400)
    if not budget["fits"] and request.POST.get("confirm") != "1":
        return JsonResponse({**budget, "needs_confirm": True}, status=409)

    st = state.get()
    entry = st.create()
    if entry is None:
        return _error(BUSY, 503)
    threading.Thread(target=_run, args=(st, entry, url, plan, client, geo), daemon=True).start()
    return JsonResponse({"id": entry.id}, status=202)


@require_GET
@never_cache
def scan_status(request, scan_id):
    st = state.get()
    entry = st.get(scan_id)
    if entry is not None and entry.status == "running":
        return JsonResponse({"status": "running", "progress": list(entry.progress)})
    entry = st.take(scan_id)  # finished: this is its one and only fetch
    if entry is None:
        return JsonResponse({"status": "gone", "error": GONE}, status=404)
    if entry.status == "error":
        return JsonResponse({"status": "error", "error": entry.error, "progress": list(entry.progress)})
    return JsonResponse({"status": "done", "progress": list(entry.progress), "result": entry.result})


# --- stateless render and export ---------------------------------------------------------------

def _reject_constant(name):
    raise ValueError("non-finite number")


def _posted(request) -> dict:
    """The posted JSON object. ValueError on anything that is not a JSON object."""
    try:
        data = json.loads(request.body.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError("not JSON") from None
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _report(data: dict) -> ScanReport:
    try:
        return ScanReport.from_dict(data.get("result"))
    except (TypeError, RecursionError, ArithmeticError):
        raise ValueError("bad report") from None


# (key, minimum, whole number) as the CLI accepts them: --max-ttfb/--max-load/--max-page-weight >= 1,
# --max-blocked >= 0. The upper bound keeps every value formattable and comparable.
_LIMIT_RULES = (("max_ttfb", 1, False), ("max_load", 1, False), ("max_page_weight", 1, True),
                ("max_blocked", 0, True))
_LIMIT_MAX = 10**9


def _limit_value(value, minimum: int, whole: bool):
    """One limit from JSON (number) or a form (text). ValueError on anything not a finite, in-range number."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            value = int(text)
        except ValueError:
            try:
                value = float(text)
            except ValueError:
                raise ValueError("not a number") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    try:  # a huge integer overflows float conversion
        if not math.isfinite(value):
            raise ValueError("not finite")
    except ArithmeticError:
        raise ValueError("too large") from None
    if not minimum <= value <= _LIMIT_MAX:
        raise ValueError("out of range")
    if whole:
        if not float(value).is_integer():
            raise ValueError("not a whole number")
        return int(value)
    return value


def _limits(raw) -> Limits:
    """Limits from posted JSON (an object) or a form (a QueryDict). ValueError when any is invalid."""
    if raw is None:
        return Limits()
    if not hasattr(raw, "get"):
        raise ValueError("limits must be an object")
    return Limits(**{key: _limit_value(raw.get(key), minimum, whole) for key, minimum, whole in _LIMIT_RULES})


def _pass_fail(report: ScanReport, limits: Limits) -> tuple[str, list[str]]:
    """The same verdict wording as the CLI."""
    set_limits = [(label, getattr(limits, key), unit) for key, label, unit in LIMIT_LABELS
                  if getattr(limits, key) is not None]
    if not set_limits:
        return "No limits set", []
    outcome = evaluate_limits(report, limits)
    if outcome.exit_code == 2:
        return "Pass/fail: could not test, so no limit was judged", []
    word = "FAILED" if outcome.failed else "passed"
    listed = ", ".join(f"{label} {plain_number(value)}{unit}" for label, value, unit in set_limits)
    return f"Pass/fail: {word} (limits: {listed})", outcome.failed


@require_POST
@never_cache
def render_result(request):
    try:
        data = _posted(request)
        report = _report(data)
    except ValueError:
        return _error(BAD_RESULT, 400)
    try:
        limits = _limits(data.get("limits"))
    except ValueError:
        return _error(BAD_LIMITS, 400)
    ctx = context(report)
    for i, row in enumerate(ctx["rows"]):
        row["index"] = i
    verdict, failed = _pass_fail(report, limits)
    # render_map escapes every dynamic string itself (see speedward/reports/map.py).
    ctx.update(map_svg=mark_safe(render_map(report, fast_ms=settings.FAST_MS, slow_ms=settings.SLOW_MS)),
               verdict=verdict, verdict_failed=verdict.startswith("Pass/fail: FAILED"), failed=failed,
               fast_ms=plain_number(settings.FAST_MS), slow_ms=plain_number(settings.SLOW_MS))
    return render(request, "_results.html", ctx)


@require_POST
@never_cache
def export(request, fmt):
    try:
        report = _report(_posted(request))
    except ValueError:
        return _error(BAD_RESULT, 400)
    response = HttpResponse(render_report(report, fmt), content_type=EXPORT_TYPES[fmt])
    response["Content-Disposition"] = f'attachment; filename="speedward-report.{fmt}"'
    return response
