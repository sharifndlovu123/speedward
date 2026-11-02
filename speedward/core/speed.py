"""Is it fast? The speed checks summed up per place and overall, plus the notes they add to the "where
the time goes" diagram. Pure functions over a ScanReport; no framework imports and no sentences
(speedward/reports words them).

Only measured results enter any figure. Blocked runs are counted and named, test failed / not run are
left out. Every overall verdict needs MIN_PLACES places with a result of their own; with fewer it says
"too_few" and concludes nothing. The thresholds are named below and documented in README "Speed checks".

- Consistency: the main test's repeat plus CONSISTENCY_EXTRA runs, each on the cold run's own probe
  (runs from another probe are counted but left out). Server wait (first byte) per run. A place "varies"
  when its slowest run is at least VARIES_RATIO times its median and at least VARIES_MIN_MS above it,
  else "steady"; with fewer than CONSISTENCY_RUNS runs it is not judged and only the count is shown.
- HTTP/2: did the server answer over HTTP/2, and the difference in connect, TLS and first byte against
  the main test's repeat on the same probe (one run each: small differences are noise).
- Compression: the Content-Encoding chosen for "br, gzip", and the download time against the main
  test's repeat on the same probe. Page size on the wire is not available and never estimated.
- DNS trace: the time of each step (local resolver, root, TLD, ...) and the slowest step.
- Path (mtr): a "bad hop" is the first hop from which loss of at least LOSS_PCT carries through every
  later hop to the site itself; loss that stops at a middle hop is a router limiting its own replies.
  A "long link" is the largest rise in round trip at one hop, at least RISE_MS, that stays up to the
  site (the site's own average is within RISE_MS / 2 of that hop's). Jitter is the site's own.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field

from speedward.core.checks import COMPRESSION, CONSISTENCY, CONSISTENCY_EXTRA, DNS_TRACE, HTTP2, MTR, SPEED_CHECKS
from speedward.core.models import SAME_PROBE, LocationResult, ScanReport, SpeedResult

MIN_PLACES = 3
CONSISTENCY_RUNS = CONSISTENCY_EXTRA + 1  # with the main test's own repeat
VARIES_RATIO = 2.0
VARIES_MIN_MS = 50.0
LOSS_PCT = 10.0
RISE_MS = 50.0
NO_RESULT = "this check did not run for this place"

STATES = ("measured", "blocked", "no_data")
VERDICTS = {
    CONSISTENCY: ("too_few", "steady", "varies"),
    HTTP2: ("too_few", "all", "some", "none"),
    COMPRESSION: ("too_few", "all", "some", "none"),
    DNS_TRACE: ("too_few", "judged"),
    MTR: ("too_few", "clean", "bad_hop"),
}


@dataclass
class Gone:
    """Why a place has no figure for a check: the first non-measured result's status and reason."""
    status: str
    reason: str


@dataclass
class Consistency:
    runs: int  # measured runs on the cold run's own probe, the main repeat included
    other_probe: int  # measured runs left out because another probe answered
    blocked: int
    median_ms: float | None
    fastest_ms: float | None
    slowest_ms: float | None
    verdict: str  # "steady", "varies" or "too_few_runs"


@dataclass
class Http2:
    offered: bool
    version: str | None
    connect_diff_ms: float | None  # HTTP/2 minus HTTP/1.1, same probe; None when not comparable
    tls_diff_ms: float | None
    first_byte_diff_ms: float | None


@dataclass
class Compression:
    encoding: str  # "none" when the server did not compress
    download_diff_ms: float | None  # this run minus the main repeat, same probe


@dataclass
class Step:
    kind: str
    server: str
    ms: float | None


@dataclass
class DnsTrace:
    steps: list[Step]
    total_ms: float | None
    slowest: Step | None  # None when every step took under a millisecond or had no time


@dataclass
class Hop:
    n: int
    host: str
    loss_pct: float | None = None
    avg_ms: float | None = None
    rise_ms: float | None = None


@dataclass
class PathLoss:
    reached: bool
    hops: int
    silent: int
    worst_loss: Hop | None  # the answering hop with the most loss (None when no hop lost any)
    jitter_ms: float | None  # at the site itself
    site_loss_pct: float | None
    bad_hop: Hop | None
    long_link: Hop | None


@dataclass
class PlaceSpeed:
    row: int
    place: str
    consistency: Consistency | None = None
    http2: Http2 | None = None
    compression: Compression | None = None
    dns_trace: DnsTrace | None = None
    path: PathLoss | None = None
    tls12: bool = False  # the main test used TLS 1.2 here (one more round trip than TLS 1.3)
    gone: dict[str, Gone] = field(default_factory=dict)  # check -> why there is no figure


@dataclass
class Overall:
    check: str
    verdict: str
    judged: int  # places with a figure of their own for this check
    places: list[str] = field(default_factory=list)  # the places behind the verdict, in row order
    ms: float | None = None  # a median standing for the verdict (see speedward/reports/speed.py)
    low_ms: float | None = None
    high_ms: float | None = None
    kind: str | None = None  # dns trace: the step that is slowest most often
    count: int = 0  # dns trace: in how many places it is the slowest; http2/compression: how many "yes"
    encodings: list[str] = field(default_factory=list)


@dataclass
class SpeedSummary:
    checks: list[str]
    min_places: int
    places: list[PlaceSpeed]
    overall: list[Overall]
    tls12: list[str]  # measured places whose main test used TLS 1.2

    def to_dict(self) -> dict:
        return asdict(self)


def _label(r: LocationResult) -> str:
    return ", ".join(p for p in (r.location.city, r.location.country) if p) or "unknown location"


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 1)


def _baseline(r: LocationResult, x: SpeedResult):
    """The main test's repeat, when it and this result both came from the cold run's own probe."""
    if r.status == "measured" and r.repeat_stages is not None and r.repeat_probe == SAME_PROBE \
            and x.probe == SAME_PROBE:
        return r.repeat_stages
    return None


def _gone(results: list[SpeedResult]) -> Gone:
    first = next((x for x in results if x.status == "blocked"), None) or (results[0] if results else None)
    return Gone("not_run", NO_RESULT) if first is None else Gone(first.status, first.reason)


def _consistency(r: LocationResult, runs: list[SpeedResult]) -> Consistency | None:
    samples, other, blocked = [], 0, 0
    if r.status == "measured" and r.repeat_stages is not None and r.repeat_stages.first_byte is not None:
        if r.repeat_probe == SAME_PROBE:
            samples.append(r.repeat_stages.first_byte)
        else:
            other += 1
    for x in runs:
        if x.status == "blocked":
            blocked += 1
        elif x.status == "measured" and x.stages is not None and x.stages.first_byte is not None:
            if x.probe == SAME_PROBE:
                samples.append(x.stages.first_byte)
            else:
                other += 1
    if not samples:
        return None
    median, slowest = statistics.median(samples), max(samples)
    if len(samples) < CONSISTENCY_RUNS:
        verdict = "too_few_runs"
    elif slowest - median >= VARIES_MIN_MS and slowest >= median * VARIES_RATIO:
        verdict = "varies"
    else:
        verdict = "steady"
    return Consistency(len(samples), other, blocked, _r(median), _r(min(samples)), _r(slowest), verdict)


def _http2(r: LocationResult, x: SpeedResult) -> Http2:
    if not x.h2_offered:
        return Http2(False, x.http_version, None, None, None)
    base, now = _baseline(r, x), x.stages
    if base is None or now is None:
        return Http2(True, x.http_version, None, None, None)
    return Http2(True, x.http_version, _diff(now.connect, base.connect), _diff(now.tls, base.tls),
                 _diff(now.first_byte, base.first_byte))


def _compression(r: LocationResult, x: SpeedResult) -> Compression:
    base = _baseline(r, x)
    diff = _diff(x.stages.download, base.download) if base is not None and x.stages is not None else None
    return Compression(x.encoding, diff)


def _dns_trace(x: SpeedResult) -> DnsTrace:
    steps = [Step(s.kind, s.server, _r(s.ms)) for s in x.trace]
    times = [s.ms for s in steps if s.ms is not None]
    slowest = None
    for s in steps:  # ties go to the earlier step
        if s.ms is not None and s.ms >= 1 and (slowest is None or s.ms > slowest.ms):
            slowest = s
    return DnsTrace(steps, _r(sum(times)) if times else None, slowest)


def _path(x: SpeedResult) -> PathLoss:
    hops = x.hops
    answering = [h for h in hops if h.host != "*"]
    silent = len(hops) - len(answering)
    lossy = [h for h in answering if h.loss_pct]
    worst = max(lossy, key=lambda h: h.loss_pct) if lossy else None
    site = hops[-1] if x.reached and hops else None
    bad = long = None
    if site is not None:
        for k, h in enumerate(answering):
            if h.loss_pct is not None and h.loss_pct >= LOSS_PCT and all(
                    g.loss_pct is not None and g.loss_pct >= LOSS_PCT for g in answering[k:]):
                bad = Hop(h.n, h.host, loss_pct=h.loss_pct)
                break
        prev = None
        for h in answering:
            if h.avg_ms is None:
                continue
            if prev is not None and site.avg_ms is not None:
                rise = h.avg_ms - prev
                if rise >= RISE_MS and site.avg_ms >= h.avg_ms - RISE_MS / 2 \
                        and (long is None or rise > long.rise_ms):
                    long = Hop(h.n, h.host, avg_ms=_r(h.avg_ms), rise_ms=_r(rise))
            prev = h.avg_ms
    return PathLoss(bool(x.reached), len(hops), silent,
                    Hop(worst.n, worst.host, loss_pct=worst.loss_pct) if worst else None,
                    _r(site.jitter_ms) if site is not None else None,
                    site.loss_pct if site is not None else None, bad, long)


def _median(values: list[float]) -> float | None:
    return _r(statistics.median(values)) if values else None


def _overall(check: str, places: list[PlaceSpeed]) -> Overall:
    have = [p for p in places if getattr(p, _ATTR[check]) is not None]
    if check == CONSISTENCY:
        have = [p for p in have if p.consistency.verdict != "too_few_runs"]
    if check == MTR:
        have = [p for p in have if p.path.reached]  # loss to the site needs the site to answer
    if len(have) < MIN_PLACES:
        return Overall(check, "too_few", len(have))
    if check == CONSISTENCY:
        varies = [p for p in have if p.consistency.verdict == "varies"]
        shown = varies or have
        return Overall(check, "varies" if varies else "steady", len(have), [p.place for p in varies],
                       _median([p.consistency.median_ms for p in have]),
                       min(p.consistency.fastest_ms for p in shown), max(p.consistency.slowest_ms for p in shown))
    if check == HTTP2:
        yes = [p for p in have if p.http2.offered]
        verdict = "all" if len(yes) == len(have) else "none" if not yes else "some"
        diffs = [p.http2.first_byte_diff_ms for p in yes if p.http2.first_byte_diff_ms is not None]
        return Overall(check, verdict, len(have), [p.place for p in have if not p.http2.offered],
                       _median(diffs) if len(diffs) >= MIN_PLACES else None, count=len(yes))
    if check == COMPRESSION:
        yes = [p for p in have if p.compression.encoding != "none"]
        verdict = "all" if len(yes) == len(have) else "none" if not yes else "some"
        return Overall(check, verdict, len(have), [p.place for p in have if p.compression.encoding == "none"],
                       count=len(yes), encodings=sorted({p.compression.encoding for p in yes}))
    if check == DNS_TRACE:
        kinds = Counter(p.dns_trace.slowest.kind for p in have if p.dns_trace.slowest is not None)
        if not kinds:
            return Overall(check, "judged", len(have))
        kind, count = max(kinds.items(), key=lambda kv: kv[1])  # ties: the first kind seen in row order
        times = [s.ms for p in have for s in p.dns_trace.steps if s.kind == kind and s.ms is not None]
        return Overall(check, "judged", len(have),
                       [p.place for p in have if p.dns_trace.slowest and p.dns_trace.slowest.kind == kind],
                       _median(times), kind=kind, count=count)
    bad = [p for p in have if p.path.bad_hop is not None]
    long = [p for p in have if p.path.long_link is not None]
    return Overall(check, "bad_hop" if bad else "clean", len(have), [p.place for p in (bad or long)],
                   count=len(bad))


_ATTR = {CONSISTENCY: "consistency", HTTP2: "http2", COMPRESSION: "compression", DNS_TRACE: "dns_trace",
         MTR: "path"}


def _figure(check: str, r: LocationResult, x: SpeedResult):
    if check == HTTP2:
        return _http2(r, x)
    if check == COMPRESSION:
        return _compression(r, x)
    return _dns_trace(x) if check == DNS_TRACE else _path(x)


def speed_summary(report: ScanReport) -> SpeedSummary:
    results = report.speed or []
    checks = [c for c in SPEED_CHECKS if any(x.check == c for x in results)]
    grouped: dict[tuple[int, str], list[SpeedResult]] = {}  # one pass: work grows with rows + results only
    for x in results:
        grouped.setdefault((x.row, x.check), []).append(x)
    places = []
    for i, r in enumerate(report.locations):
        p = PlaceSpeed(i, _label(r), tls12=r.status == "measured" and r.tls_version == "TLSv1.2")
        for c in checks:
            mine = grouped.get((i, c), [])
            measured = next((x for x in mine if x.status == "measured"), None)
            figure = None
            if c == CONSISTENCY:
                figure = p.consistency = _consistency(r, mine)
            elif measured is not None:
                figure = _figure(c, r, measured)
                setattr(p, _ATTR[c], figure)
            if figure is None:
                p.gone[c] = _gone(mine)
        places.append(p)
    return SpeedSummary(checks, MIN_PLACES, places, [_overall(c, places) for c in checks],
                        [p.place for p in places if p.tls12] if checks else [])


def summary_from_dict(value, report: ScanReport) -> SpeedSummary:
    """A saved summary, accepted only if it is exactly what speed_summary gives for the report's own
    results. Anything else raises ValueError: a summary is never taken on trust."""
    from speedward.core.bottleneck import _same

    fresh = speed_summary(report)
    if not isinstance(value, dict) or not _same(value, fresh.to_dict()):
        raise ValueError("speed summary does not follow from the report's own results")
    return fresh
