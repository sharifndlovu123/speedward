"""Summaries, CDN cache status and pass/fail limits over a ScanReport.

Only `measured` locations ever contribute numbers. Failed, blocked and not-run locations are
counted as excluded and never pass a limit on anyone's behalf.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from speedward.core.models import LocationResult, ScanReport

STAGE_NAMES = ("dns", "connect", "tls", "first_byte", "download")


def _header(headers: dict, name: str) -> str | None:
    for key, value in (headers or {}).items():
        if isinstance(key, str) and key.lower() == name:
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            return value if isinstance(value, str) else None
    return None


def cdn_status(headers: dict[str, str]) -> str | None:
    """"HIT" or "MISS" from cache headers; None when the headers say nothing either way."""
    cf = _header(headers, "cf-cache-status")
    if cf is not None:
        value = cf.strip().upper()
        return value if value in ("HIT", "MISS") else None
    x_cache = _header(headers, "x-cache")
    if x_cache is not None:
        # Several cache layers may be listed ("MISS, HIT"); the last one is the one that answered.
        last = x_cache.split(",")[-1].upper()
        if "HIT" in last:
            return "HIT"
        if "MISS" in last:
            return "MISS"
    age = _header(headers, "age")
    if age is not None:
        try:
            if int(age.strip()) > 0:
                return "HIT"
        except ValueError:
            pass
    return None


def _label(r: LocationResult) -> str:
    parts = [p for p in (r.location.city, r.location.country) if p]
    return ", ".join(parts) or "unknown location"


def _measured(report: ScanReport) -> list[LocationResult]:
    return [r for r in report.locations if r.status == "measured"]


def _first_byte(r: LocationResult) -> float | None:
    return r.stages.first_byte if r.stages is not None else None


@dataclass
class Summary:
    measured: int
    excluded: int
    avg_first_byte: float | None
    median_first_byte: float | None
    worst: LocationResult | None
    cache_gain_pct: float | None
    blocked: int
    slowest_stage: str | None


def summarise(report: ScanReport) -> Summary:
    measured = _measured(report)
    fbs = [(r, _first_byte(r)) for r in measured]
    fbs = [(r, fb) for r, fb in fbs if fb is not None]
    values = [fb for _, fb in fbs]

    cache_gain = None
    same = [r for r in measured if r.repeat_same_probe]
    pairs = [(_first_byte(r), r.repeat_stages.first_byte) for r in same
             if r.repeat_stages is not None and r.repeat_stages.first_byte is not None
             and _first_byte(r) is not None]
    if pairs:
        cold = statistics.fmean(c for c, _ in pairs)
        warm = statistics.fmean(w for _, w in pairs)
        if cold > 0:
            cache_gain = (cold - warm) / cold * 100

    slowest, slowest_avg = None, None
    for name in STAGE_NAMES:
        vals = [getattr(r.stages, name) for r in measured if r.stages is not None]
        vals = [v for v in vals if v is not None]
        if vals:
            avg = statistics.fmean(vals)
            if slowest_avg is None or avg > slowest_avg:
                slowest, slowest_avg = name, avg

    return Summary(
        measured=len(measured),
        excluded=len(report.locations) - len(measured),
        avg_first_byte=statistics.fmean(values) if values else None,
        median_first_byte=statistics.median(values) if values else None,
        worst=max(fbs, key=lambda p: p[1])[0] if fbs else None,
        cache_gain_pct=cache_gain,
        blocked=sum(1 for r in report.locations if r.status == "blocked"),
        slowest_stage=slowest,
    )


@dataclass
class Limits:
    max_ttfb: float | None = None
    max_load: float | None = None
    max_blocked: int | None = None
    max_page_weight: int | None = None


@dataclass
class LimitOutcome:
    failed: list[str] = field(default_factory=list)
    exit_code: int = 0


def plain_number(value: float) -> str:
    """A number as people read it: whole numbers exactly, fractions to at most 2 places. Never 1e+06."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _check_timing(measured: list[LocationResult], limit: float, stage: str, what: str) -> list[str]:
    out = []
    for r in measured:
        value = getattr(r.stages, stage) if r.stages is not None else None
        if value is None:
            out.append(f"{what} not available for {_label(r)}, so the {what} limit cannot pass there")
        elif value > limit:
            out.append(f"{what} {plain_number(value)} ms from {_label(r)} is over the limit of {plain_number(limit)} ms")
    return out


def evaluate_limits(report: ScanReport, limits: Limits) -> LimitOutcome:
    measured = _measured(report)
    if not measured:
        return LimitOutcome(["Could not test: no location was measured successfully"], 2)
    failed: list[str] = []
    if limits.max_ttfb is not None:
        failed += _check_timing(measured, limits.max_ttfb, "first_byte", "first byte")
    if limits.max_load is not None:
        failed += _check_timing(measured, limits.max_load, "total", "load time")
    if limits.max_blocked is not None:
        blocked = sum(1 for r in report.locations if r.status == "blocked")
        if blocked > limits.max_blocked:
            failed.append(f"{blocked} locations were blocked, over the limit of {limits.max_blocked}")
    if limits.max_page_weight is not None:
        weight = report.browser.page_weight_bytes if report.browser is not None else None
        if weight is None:
            failed.append("page weight was not measured (run the browser test), so its limit cannot pass")
        elif weight > limits.max_page_weight:
            failed.append(f"page weight {weight} bytes is over the limit of {limits.max_page_weight} bytes")
    return LimitOutcome(failed, 1 if failed else 0)
