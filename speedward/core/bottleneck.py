"""Where the time goes: which stage of a request costs the most, from measured results only.

Pure functions over a ScanReport; no framework imports and no wording (speedward/reports words it).
Only results with status "measured" enter any figure. Blocked, failed and not-run places are counted
and listed with their reason, never averaged, compared or marked.

Stages, in the order a request goes through them: DNS lookup, connect (TCP), TLS handshake, server
wait, download. Server wait is Globalping's `timings.firstByte` as stored (Stages.first_byte): it is
already the time from the request being sent to the first byte, after DNS, connect and TLS (observed:
total 22 = dns 3 + tcp 0 + tls 11 + firstByte 5 + download 3, docs/globalping-api-notes.md), so nothing
is subtracted. A stage the API did not return is "not available", never zero.

Marks (the block diagram):
- "biggest": the largest share of the total, and at least BIGGEST_SHARE of it.
- "possible": any other stage with at least POSSIBLE_SHARE, or connect / TLS when they grow with the
  distance to the server that answered (a positive correlation of at least DISTANCE_CORRELATION and a
  spread of at least DISTANCE_SPREAD_MS between the quickest and slowest place).
- "fine": everything else that was measured.
- "not_judged": fewer than MIN_PLACES measured places, so nothing is marked.
- "not_available": the stage was not returned by any measured place.

Each rule can be set from the environment (speedward/core/config.py analysis_rules, README "Where the
time goes"), within RULE_BOUNDS. The diagnosis records the rule values it used, and a saved copy is
accepted only when those values are within the same bounds (MIN_PLACES never below 3) and its figures are
exactly what its own timings give under them.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

from speedward.core.edge import distance_km
from speedward.core.models import MAX_MS, SAME_PROBE, LocationResult, ScanReport
from speedward.core.rules import in_bounds

STAGES = ("dns", "connect", "tls", "server_wait", "download")
_FIELD = {"dns": "dns", "connect": "connect", "tls": "tls", "server_wait": "first_byte", "download": "download"}
# Stages that take network round trips, so can grow with distance.
_DISTANCE_STAGES = ("connect", "tls")

BIGGEST_SHARE = 0.40       # a stage "dominates" (biggest block) at this share of the total or more
POSSIBLE_SHARE = 0.25      # a "possible block" at this share or more; also "DNS is large" in a place
MIN_PLACES = 3             # fewer measured places than this: no conclusion, no marks
NEAR_CONNECT_MS = 40.0     # a place is "close" to its server when connect takes less than this
DISTANCE_CORRELATION = 0.7  # connect / TLS "grow with distance" at this correlation with km or more
DISTANCE_SPREAD_MS = 50.0  # ... and when the slowest place takes at least this much longer than the quickest
DNS_SHARE = 0.25           # "DNS is large" in a place at this share of its own total or more

MARKS = ("biggest", "possible", "fine", "not_judged", "not_available")
VERDICTS = ("nothing_measured", "too_few", "no_timings", "biggest", "no_single_stage")
LEFT_OUT = ("blocked", "test_failed", "not_run")


@dataclass(frozen=True)
class Rules:
    biggest_share: float = BIGGEST_SHARE
    possible_share: float = POSSIBLE_SHARE
    min_places: int = MIN_PLACES
    near_connect_ms: float = NEAR_CONNECT_MS
    distance_correlation: float = DISTANCE_CORRELATION
    distance_spread_ms: float = DISTANCE_SPREAD_MS
    dns_share: float = DNS_SHARE


# (lowest, highest, whole number) for each rule: the settings and saved copies are held to the same bounds.
RULE_BOUNDS = {"biggest_share": (0.01, 1, False), "possible_share": (0.01, 1, False),
               "min_places": (3, 1000, True), "near_connect_ms": (0, MAX_MS, False),
               "distance_correlation": (0, 1, False), "distance_spread_ms": (0, MAX_MS, False),
               "dns_share": (0.01, 1, False)}


@dataclass
class StageFigure:
    stage: str
    ms: float | None
    share: float | None
    mark: str
    grows_with_distance: bool = False


@dataclass
class Spot:
    """One measured place standing for a fact (e.g. "London: 150 ms server wait, 12 ms connect")."""
    place: str
    row: int
    ms: float | None = None
    connect_ms: float | None = None
    km: float | None = None
    share: float | None = None


@dataclass
class PlaceFigure:
    place: str
    row: int  # index in report.locations (the map dot's data-row)
    status: str
    reason: str
    total_ms: float | None
    stages: list[StageFigure]
    largest: str | None
    held_back_by: str | None  # the place's biggest block, else its largest possible block, else None
    cdn: str | None
    edge: str | None


@dataclass
class CacheFacts:
    hit: int
    miss: int
    unknown: int
    same_probe_pairs: int
    other_probe_repeats: int
    gain_pct: float | None  # first byte, cold vs repeat, same-probe pairs only


@dataclass
class DistanceFacts:
    positioned: int  # measured places with a known distance to the server that answered
    quickest: Spot | None  # lowest connect time (no position needed)
    slowest: Spot | None
    nearest: Spot | None   # by distance to the answering server
    farthest: Spot | None
    connect_grows: bool | None  # None: not enough positioned places to judge
    tls_grows: bool | None


@dataclass
class Diagnosis:
    rules: Rules
    measured: int
    blocked: int
    test_failed: int
    not_run: int
    verdict: str
    total_ms: float | None
    stages: list[StageFigure]
    largest: str | None
    biggest: str | None
    largest_in: int  # measured places whose own largest stage is the overall largest
    near_server_wait: Spot | None
    dns_heavy: int
    dns_example: Spot | None
    cache: CacheFacts
    distance: DistanceFacts
    places: list[PlaceFigure] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --- helpers ---------------------------------------------------------------------------------------

def _label(r: LocationResult) -> str:
    return ", ".join(p for p in (r.location.city, r.location.country) if p) or "unknown location"


def _ms(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def _share(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


def _value(r: LocationResult, name: str) -> float | None:
    return getattr(r.stages, _FIELD[name]) if r.stages is not None else None


def _mark(name: str, share: float | None, largest: str | None, rules: Rules, grows: bool = False) -> str:
    if share is None:
        return "not_available"
    if name == largest and share >= rules.biggest_share:
        return "biggest"
    if share >= rules.possible_share or grows:
        return "possible"
    return "fine"


def _largest(values: dict[str, float | None]) -> str | None:
    best = None
    for name in STAGES:  # ties go to the earlier stage in the path
        v = values[name]
        if v is not None and (best is None or v > values[best]):
            best = name
    return best


def _figures(values: dict[str, float | None], judged: bool, rules: Rules,
             grows: dict[str, bool] | None = None) -> tuple[float | None, list[StageFigure], str | None]:
    """(total, per-stage figures, largest stage) for one set of stage values."""
    present = [v for v in values.values() if v is not None]
    total = sum(present) if present else None
    shares = {n: (values[n] / total if values[n] is not None and total else None) for n in STAGES}
    largest = _largest(values) if total else None
    grows = grows or {}
    out = []
    for n in STAGES:
        if values[n] is None:
            mark = "not_available"
        elif not judged or not total:
            mark = "not_judged"
        else:
            mark = _mark(n, shares[n], largest, rules, grows.get(n, False))
        out.append(StageFigure(n, _ms(values[n]), _share(shares[n]), mark, bool(grows.get(n, False))))
    return total, out, largest


def _held_back_by(figures: list[StageFigure]) -> str | None:
    biggest = [f.stage for f in figures if f.mark == "biggest"]
    if biggest:
        return biggest[0]
    possible = [f for f in figures if f.mark == "possible"]
    return max(possible, key=lambda f: f.share or 0).stage if possible else None


def _server_of(r: LocationResult, report: ScanReport):
    return r.server_position if r.server_position is not None else report.server


def _km(r: LocationResult, report: ScanReport) -> float | None:
    s = _server_of(r, report)
    if s is None or r.location.lat is None or r.location.lon is None:
        return None
    return distance_km(r.location.lat, r.location.lon, s.lat, s.lon)


def _grows(points: list[tuple[float, float]], rules: Rules) -> bool | None:
    """Does the stage time rise with distance? None when there are too few places to say."""
    if len(points) < rules.min_places:
        return None
    kms, values = [p[0] for p in points], [p[1] for p in points]
    if max(values) - min(values) < rules.distance_spread_ms:
        return False
    try:
        r = statistics.correlation(kms, values)
    except statistics.StatisticsError:  # every place equally far: no trend to see
        return False
    return r >= rules.distance_correlation


def _cache(measured: list[LocationResult]) -> CacheFacts:
    hit = sum(1 for r in measured if r.cdn == "HIT")
    miss = sum(1 for r in measured if r.cdn == "MISS")
    repeats = [r for r in measured if r.repeat_stages is not None]
    same = [r for r in repeats if r.repeat_probe == SAME_PROBE]
    pairs = [(r.stages.first_byte, r.repeat_stages.first_byte) for r in same
             if r.stages is not None and r.stages.first_byte is not None and r.repeat_stages.first_byte is not None]
    gain = None
    if pairs:
        cold = statistics.fmean(c for c, _ in pairs)
        warm = statistics.fmean(w for _, w in pairs)
        if cold > 0:
            gain = round((cold - warm) / cold * 100, 1)
    return CacheFacts(hit, miss, len(measured) - hit - miss, len(pairs), len(repeats) - len(same), gain)


def _distance(report: ScanReport, rows: list[tuple[int, LocationResult]], rules: Rules,
              judged: bool) -> DistanceFacts:
    with_connect = [(i, r, _value(r, "connect")) for i, r in rows if _value(r, "connect") is not None]
    quickest = slowest = nearest = farthest = None
    if with_connect:
        lo = min(with_connect, key=lambda t: t[2])
        hi = max(with_connect, key=lambda t: t[2])
        quickest = Spot(_label(lo[1]), lo[0], connect_ms=_ms(lo[2]))
        slowest = Spot(_label(hi[1]), hi[0], connect_ms=_ms(hi[2]))
    placed = [(i, r, _km(r, report)) for i, r in rows]
    placed = [(i, r, km) for i, r, km in placed if km is not None]
    if placed:
        near = min(placed, key=lambda t: t[2])
        far = max(placed, key=lambda t: t[2])
        nearest = Spot(_label(near[1]), near[0], connect_ms=_ms(_value(near[1], "connect")), km=round(near[2]))
        farthest = Spot(_label(far[1]), far[0], connect_ms=_ms(_value(far[1], "connect")), km=round(far[2]))
    grows: dict[str, bool | None] = {}
    for name in _DISTANCE_STAGES:
        points = [(km, _value(r, name)) for _, r, km in placed if _value(r, name) is not None]
        grows[name] = _grows(points, rules) if judged else None
    return DistanceFacts(len(placed), quickest, slowest, nearest, farthest, grows["connect"], grows["tls"])


# --- the diagnosis -----------------------------------------------------------------------------------

def diagnose(report: ScanReport, rules: Rules | None = None) -> Diagnosis:
    rules = rules or Rules()
    rows = [(i, r) for i, r in enumerate(report.locations) if r.status == "measured"]
    measured = [r for _, r in rows]
    counts = {s: sum(1 for r in report.locations if r.status == s) for s in LEFT_OUT}
    enough = len(measured) >= rules.min_places

    distance = _distance(report, rows, rules, enough)
    grows = {"connect": bool(distance.connect_grows), "tls": bool(distance.tls_grows)}

    averages: dict[str, float | None] = {}
    for name in STAGES:
        vals = [v for v in (_value(r, name) for r in measured) if v is not None]
        averages[name] = statistics.fmean(vals) if vals else None
    total, stages, largest = _figures(averages, enough, rules, grows)

    if not measured:
        verdict = "nothing_measured"
    elif not enough:
        verdict = "too_few"
    elif not total:
        verdict = "no_timings"
    elif any(s.mark == "biggest" for s in stages):
        verdict = "biggest"
    else:
        verdict = "no_single_stage"
    judged = verdict in ("biggest", "no_single_stage")

    places = []
    for i, r in enumerate(report.locations):
        if r.status != "measured":
            places.append(PlaceFigure(_label(r), i, r.status, r.reason, None, [], None, None, None, None))
            continue
        values = {n: _value(r, n) for n in STAGES}
        p_total, p_stages, p_largest = _figures(values, judged, rules)
        places.append(PlaceFigure(_label(r), i, "measured", "", _ms(p_total), p_stages,
                                  p_largest if p_total else None, _held_back_by(p_stages) if judged else None,
                                  r.cdn, r.edge_code))

    near = None
    dns_heavy, dns_example = 0, None
    largest_in = 0
    if judged:
        largest_in = sum(1 for p in places if p.status == "measured" and p.largest == largest)
        for p in places:
            if p.status != "measured":
                continue
            fig = {s.stage: s for s in p.stages}
            wait, connect, dns = fig["server_wait"], fig["connect"], fig["dns"]
            if (connect.ms is not None and connect.ms < rules.near_connect_ms and wait.share is not None
                    and wait.share >= rules.biggest_share and (near is None or connect.ms < near.connect_ms)):
                near = Spot(p.place, p.row, ms=wait.ms, connect_ms=connect.ms, share=wait.share)
            if dns.share is not None and dns.share >= rules.dns_share:
                dns_heavy += 1
                if dns_example is None or dns.share > dns_example.share:
                    dns_example = Spot(p.place, p.row, ms=dns.ms, share=dns.share)

    biggest = largest if verdict == "biggest" else None
    return Diagnosis(rules=rules, measured=len(measured), blocked=counts["blocked"],
                     test_failed=counts["test_failed"], not_run=counts["not_run"], verdict=verdict,
                     total_ms=_ms(total), stages=stages, largest=largest if judged else None, biggest=biggest,
                     largest_in=largest_in, near_server_wait=near, dns_heavy=dns_heavy, dns_example=dns_example,
                     cache=_cache(measured), distance=distance, places=places)


# --- loading a saved diagnosis ---------------------------------------------------------------------

def _rules(value) -> Rules:
    if not isinstance(value, dict):
        raise ValueError("diagnosis rules must be an object with the known rules")
    keys = set(Rules.__dataclass_fields__)
    if set(value) == keys - {"dns_share"}:  # saved before DNS_SHARE existed, when DNS used possible_share
        value = {**value, "dns_share": value.get("possible_share")}
    if set(value) != keys:
        raise ValueError("diagnosis rules must be an object with the known rules")
    for key in keys:
        if not in_bounds(value[key], RULE_BOUNDS[key]):
            raise ValueError(f"diagnosis rule {key} is out of range")
    rules = Rules(**{key: value[key] for key in keys})
    if rules.possible_share > rules.biggest_share:
        raise ValueError("diagnosis rules: possible_share cannot be above biggest_share")
    return rules


def _same(a, b) -> bool:
    """Strict equality for JSON values: true is not 1, 1 is 1.0, no extra or missing keys."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b
    if isinstance(a, dict) or isinstance(b, dict):
        return (isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b)
                and all(_same(a[k], b[k]) for k in a))
    if isinstance(a, list) or isinstance(b, list):
        return (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
                and all(_same(x, y) for x, y in zip(a, b, strict=True)))
    return type(a) is type(b) and a == b


def diagnosis_from_dict(value, report: ScanReport) -> Diagnosis:
    """A saved diagnosis, accepted only if it is exactly what `diagnose` gives for this report's own
    results under the rules it records. Anything else raises ValueError: a diagnosis is never taken on
    trust, and never made up for a report that did not have one."""
    if not isinstance(value, dict):
        raise ValueError("diagnosis must be an object")
    rules = _rules(value.get("rules"))
    if isinstance(value["rules"], dict) and "dns_share" not in value["rules"]:  # an older copy (see _rules)
        value = {**value, "rules": {**value["rules"], "dns_share": rules.dns_share}}
    fresh = diagnose(report, rules)
    if not _same(value, fresh.to_dict()):
        raise ValueError("diagnosis does not follow from the report's own results")
    return fresh
