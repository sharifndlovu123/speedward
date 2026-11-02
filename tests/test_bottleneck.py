"""Where the time goes: every rule and boundary of speedward.core.bottleneck.diagnose.

Only measured results enter a figure. Blocked, failed and not-run places are counted, never averaged.
"""
import copy
import json
from datetime import UTC, datetime

import pytest

from speedward.core.bottleneck import (
    BIGGEST_SHARE,
    MIN_PLACES,
    NEAR_CONNECT_MS,
    POSSIBLE_SHARE,
    STAGES,
    Rules,
    diagnose,
    diagnosis_from_dict,
)
from speedward.core.models import (
    OTHER_PROBE,
    Location,
    LocationResult,
    ScanReport,
    ServerPosition,
    Stages,
)

NOW = datetime(2026, 11, 1, tzinfo=UTC)
# Places and their great-circle distance from a server in Frankfurt (50.1, 8.7), roughly.
PLACES = {"Frankfurt": (50.1, 8.7), "London": (51.5, -0.1), "New York": (40.7, -74.0),
          "Tokyo": (35.7, 139.7), "Sydney": (-33.9, 151.2), "Lagos": (6.5, 3.4)}
FRANKFURT = ServerPosition(50.1, 8.7, "manual")


def loc(city):
    lat, lon = PLACES.get(city, (None, None))
    return Location("XX", city, lat, lon, 1, "Net", "isp")


def ok(city, dns=10, connect=10, tls=10, wait=60, download=10, **kw):
    stages = Stages(dns=dns, connect=connect, tls=tls, first_byte=wait, download=download,
                    total=sum(v for v in (dns, connect, tls, wait, download) if v is not None))
    return LocationResult(loc(city), "measured", stages=stages, **kw)


def bad(city, status="blocked", reason="timed out"):
    return LocationResult(loc(city), status, reason)


def report(*locations, server=FRANKFURT):
    return ScanReport("https://example.com/", NOW, NOW, "0", {}, server, list(locations), [], [], None)


def stage(d, name):
    return next(s for s in d.stages if s.stage == name)


def marks(d):
    return {s.stage: s.mark for s in d.stages}


# --- what counts --------------------------------------------------------------------------------

def test_stage_names_and_rule_values():
    assert STAGES == ("dns", "connect", "tls", "server_wait", "download")
    assert (BIGGEST_SHARE, POSSIBLE_SHARE, MIN_PLACES) == (0.40, 0.25, 3)
    assert NEAR_CONNECT_MS > 0


def test_server_wait_is_globalpings_first_byte_not_a_subtraction():
    # Observed: total 22 = dns 3 + tcp 0 + tls 11 + firstByte 5 + download 3, so firstByte is already the
    # wait after the handshake. It is used as stored.
    d = diagnose(report(ok("London", 3, 0, 11, 5, 3), ok("Tokyo", 3, 0, 11, 5, 3), ok("Lagos", 3, 0, 11, 5, 3)))
    assert stage(d, "server_wait").ms == 5
    assert d.total_ms == 22


def test_averages_shares_and_total():
    d = diagnose(report(ok("London", 20, 10, 10, 50, 10), ok("Tokyo", 40, 30, 30, 70, 30),
                        ok("Lagos", 30, 20, 20, 60, 20)))
    assert [stage(d, n).ms for n in STAGES] == [30, 20, 20, 60, 20]
    assert d.total_ms == 150
    assert stage(d, "server_wait").share == 0.4
    assert d.measured == 3


def test_blocked_failed_and_not_run_never_change_any_number():
    measured = [ok("London", 20, 10, 10, 50, 10, cdn="HIT"), ok("Tokyo", 40, 30, 30, 70, 30, cdn="MISS"),
                ok("Lagos", 30, 20, 20, 60, 20)]
    clean = diagnose(report(*measured))
    noisy = diagnose(report(bad("Sydney"), *measured[:2], bad("New York", "test_failed", "probe error"),
                            measured[2], bad("Frankfurt", "not_run", "no probe")))
    def no_rows(x):  # row numbers move with the report's order; every figure must not
        if isinstance(x, dict):
            return {k: no_rows(v) for k, v in x.items() if k != "row"}
        return [no_rows(v) for v in x] if isinstance(x, list) else x

    a, b = no_rows(clean.to_dict()), no_rows(noisy.to_dict())
    for key in ("stages", "total_ms", "verdict", "biggest", "largest", "largest_in", "cache", "dns_heavy",
                "dns_example", "near_server_wait", "distance", "measured"):
        assert a[key] == b[key], key
    assert [p for p in a["places"]] == [p for p in b["places"] if p["status"] == "measured"]
    assert (noisy.blocked, noisy.test_failed, noisy.not_run) == (1, 1, 1)
    # The excluded places are listed with their reason and no figures at all.
    left_out = [p for p in noisy.places if p.status != "measured"]
    assert [(p.place, p.reason) for p in left_out] == [("Sydney, XX", "timed out"),
                                                       ("New York, XX", "probe error"),
                                                       ("Frankfurt, XX", "no probe")]
    assert all(p.stages == [] and p.total_ms is None and p.held_back_by is None for p in left_out)
    # Rows keep the report's order, so a map dot (data-row) finds its place.
    assert [p.row for p in noisy.places] == [0, 1, 2, 3, 4, 5]


def test_distance_facts_ignore_blocked_places():
    measured = [ok("Frankfurt", connect=5), ok("London", connect=12), ok("Tokyo", connect=190)]
    clean = diagnose(report(*measured)).distance
    noisy = diagnose(report(*measured, bad("Sydney"), bad("Lagos", "test_failed"))).distance
    assert clean == noisy


def test_missing_stage_is_not_available_and_not_zero_filled():
    d = diagnose(report(ok("London", tls=None), ok("Tokyo", tls=None), ok("Lagos", tls=None)))
    tls = stage(d, "tls")
    assert (tls.ms, tls.share, tls.mark) == (None, None, "not_available")
    assert d.total_ms == 90


def test_a_stage_missing_in_one_place_averages_over_the_others():
    d = diagnose(report(ok("London", tls=30), ok("Tokyo", tls=None), ok("Lagos", tls=10)))
    assert stage(d, "tls").ms == 20


# --- too few places, nothing measured -----------------------------------------------------------

def test_nothing_measured():
    d = diagnose(report(bad("London"), bad("Tokyo", "test_failed")))
    assert d.verdict == "nothing_measured"
    assert d.total_ms is None and d.biggest is None
    assert all(s.mark == "not_judged" or s.mark == "not_available" for s in d.stages)
    assert all(s.ms is None for s in d.stages)


def test_all_blocked():
    d = diagnose(report(bad("London"), bad("Tokyo"), bad("Lagos")))
    assert d.verdict == "nothing_measured"
    assert d.blocked == 3 and d.measured == 0
    assert d.cache.hit == 0 and d.cache.gain_pct is None
    assert d.distance.quickest is None and d.distance.nearest is None


def test_fewer_than_three_places_marks_nothing():
    d = diagnose(report(ok("London", wait=500), ok("Tokyo", wait=500)))
    assert d.verdict == "too_few"
    assert set(marks(d).values()) == {"not_judged"}
    assert d.biggest is None and d.near_server_wait is None and d.dns_heavy == 0
    # The numbers themselves are still shown.
    assert stage(d, "server_wait").ms == 500
    assert all(s.mark == "not_judged" for p in d.places for s in p.stages)
    assert all(p.held_back_by is None for p in d.places)


def test_three_places_is_enough():
    d = diagnose(report(ok("London", wait=500), ok("Tokyo", wait=500), ok("Lagos", wait=500)))
    assert d.verdict == "biggest"


# --- marks and boundaries -----------------------------------------------------------------------

def three(**kw):
    return report(ok("London", **kw), ok("Tokyo", **kw), ok("Lagos", **kw), server=None)


def test_exactly_40_percent_is_the_biggest_block():
    d = diagnose(three(dns=15, connect=15, tls=15, wait=40, download=15))
    assert stage(d, "server_wait").share == 0.40
    assert d.verdict == "biggest" and d.biggest == "server_wait"
    assert marks(d)["server_wait"] == "biggest"


def test_just_under_40_percent_no_single_stage_dominates():
    d = diagnose(three(dns=15, connect=15, tls=15, wait=39.9, download=15.1))
    assert d.verdict == "no_single_stage"
    assert d.biggest is None and d.largest == "server_wait"
    assert marks(d)["server_wait"] == "possible"


def test_exactly_25_percent_is_a_possible_block():
    d = diagnose(three(dns=10, connect=25, tls=10, wait=45, download=10))
    assert stage(d, "connect").share == 0.25
    assert marks(d) == {"dns": "fine", "connect": "possible", "tls": "fine", "server_wait": "biggest",
                        "download": "fine"}


def test_just_under_25_percent_is_fine():
    d = diagnose(three(dns=10, connect=24.9, tls=10.1, wait=45, download=10))
    assert marks(d)["connect"] == "fine"


def test_biggest_needs_the_largest_share():
    # Two stages can both pass 40% only in theory with ties; the largest wins, ties go to path order.
    d = diagnose(three(dns=0, connect=50, tls=0, wait=50, download=0))
    assert d.biggest == "connect"
    assert marks(d)["server_wait"] == "possible"


def test_per_place_marks_and_what_holds_each_back():
    d = diagnose(report(ok("London", 10, 5, 5, 150, 10), ok("Tokyo", 10, 190, 60, 50, 10),
                        ok("Lagos", 72, 20, 20, 100, 28)))
    by = {p.place.split(",")[0]: p for p in d.places}
    assert by["London"].held_back_by == "server_wait"
    assert by["Tokyo"].held_back_by == "connect"
    assert by["Lagos"].held_back_by == "server_wait"
    lagos = {s.stage: s.mark for s in by["Lagos"].stages}
    assert lagos["dns"] == "possible"  # 72 of 240 = 30%
    assert by["London"].total_ms == 180
    assert by["London"].largest == "server_wait"


def test_place_with_no_single_stage_is_held_back_by_nothing():
    d = diagnose(report(ok("London", 20, 20, 20, 20, 20), ok("Tokyo"), ok("Lagos")))
    london = d.places[0]
    assert london.held_back_by is None
    assert {s.mark for s in london.stages} == {"fine"}


# --- server wait vs distance vs DNS ---------------------------------------------------------------

def server_heavy():
    return report(ok("Frankfurt", 5, 3, 6, 140, 6), ok("London", 6, 12, 14, 150, 8),
                  ok("Tokyo", 20, 190, 190, 250, 20), ok("Sydney", 20, 200, 200, 260, 20))


def test_server_heavy_near_places_still_wait():
    d = diagnose(server_heavy())
    assert d.biggest == "server_wait"
    near = d.near_server_wait
    assert near is not None
    assert near.place == "Frankfurt, XX" and near.ms == 140 and near.connect_ms == 3
    assert d.largest_in == 4


def test_server_wait_needs_a_near_place():
    # Every place is far (connect at or above NEAR_CONNECT_MS): no claim that close places wait too.
    far = NEAR_CONNECT_MS
    d = diagnose(report(ok("London", 5, far, 5, 200, 5), ok("Tokyo", 5, far + 10, 5, 200, 5),
                        ok("Lagos", 5, far + 20, 5, 200, 5)))
    assert d.biggest == "server_wait"
    assert d.near_server_wait is None


def test_distance_heavy_connect_and_tls_grow_with_distance():
    d = diagnose(report(ok("Frankfurt", 5, 4, 8, 20, 5), ok("London", 5, 12, 24, 20, 5),
                        ok("New York", 5, 90, 180, 20, 5), ok("Tokyo", 5, 190, 380, 20, 5),
                        ok("Sydney", 5, 200, 400, 20, 5)))
    assert d.distance.connect_grows is True and d.distance.tls_grows is True
    assert d.biggest == "tls"
    assert marks(d)["connect"] == "possible"  # 27% and grows with distance
    assert stage(d, "connect").grows_with_distance is True
    assert d.distance.nearest.place == "Frankfurt, XX" and d.distance.nearest.connect_ms == 4
    assert d.distance.farthest.place == "Sydney, XX" and d.distance.farthest.connect_ms == 200
    assert d.distance.farthest.km > d.distance.nearest.km


def test_growth_with_distance_marks_a_small_stage_as_possible():
    # Connect is only about 10% of the total but rises steadily with distance.
    d = diagnose(report(ok("Frankfurt", 5, 2, 5, 600, 5), ok("London", 5, 10, 5, 600, 5),
                        ok("New York", 5, 60, 5, 600, 5), ok("Tokyo", 5, 120, 5, 600, 5)))
    assert stage(d, "connect").share < POSSIBLE_SHARE
    assert marks(d)["connect"] == "possible"
    assert marks(d)["tls"] == "fine"


def test_small_spread_is_not_distance():
    d = diagnose(report(ok("Frankfurt", connect=10), ok("London", connect=11), ok("Tokyo", connect=12),
                        ok("Sydney", connect=13)))
    assert d.distance.connect_grows is False


def test_no_server_position_cannot_judge_distance():
    d = diagnose(report(ok("Frankfurt", connect=4), ok("London", connect=12), ok("Tokyo", connect=190),
                        server=None))
    assert d.distance.connect_grows is None and d.distance.nearest is None
    assert d.distance.positioned == 0
    # The plain spread is still a measured fact.
    assert d.distance.quickest.place == "Frankfurt, XX" and d.distance.quickest.connect_ms == 4
    assert d.distance.slowest.place == "Tokyo, XX" and d.distance.slowest.connect_ms == 190


def test_answering_server_position_is_preferred_over_the_report_server():
    # Each place was answered by an edge in its own city: nothing is far, so connect cannot grow.
    edge = [ok(c, connect=v, server_position=ServerPosition(*PLACES[c], "cdn header"))
            for c, v in (("Frankfurt", 4), ("London", 12), ("Tokyo", 190), ("Sydney", 200))]
    d = diagnose(report(*edge))
    assert d.distance.nearest.km == 0 and d.distance.farthest.km == 0
    assert d.distance.connect_grows is False


def test_dns_heavy_in_some_places():
    d = diagnose(report(ok("London", 72, 20, 20, 100, 28), ok("Tokyo"), ok("Frankfurt")))
    assert d.dns_heavy == 1
    assert d.dns_example.place == "London, XX"
    assert d.dns_example.ms == 72 and d.dns_example.share == 0.3


def test_dns_not_called_out_when_small_everywhere():
    d = diagnose(server_heavy())
    assert d.dns_heavy == 0 and d.dns_example is None


# --- caching ------------------------------------------------------------------------------------

def warm(city, cold_wait, warm_wait, same=True, **kw):
    r = ok(city, wait=cold_wait, **kw)
    r2 = copy.deepcopy(r)
    return LocationResult(r.location, "measured", stages=r.stages,
                          repeat_stages=Stages(first_byte=warm_wait),
                          repeat_same_probe=same, repeat_probe=None if same else OTHER_PROBE,
                          cdn=r2.cdn)


def test_cache_counts_and_same_probe_gain():
    d = diagnose(report(warm("London", 100, 50, cdn=None), warm("Tokyo", 300, 150),
                        ok("Lagos", cdn="HIT"), ok("Sydney", cdn="MISS"), ok("Frankfurt", cdn="HIT")))
    c = d.cache
    assert (c.hit, c.miss, c.unknown) == (2, 1, 2)
    assert c.same_probe_pairs == 2 and c.other_probe_repeats == 0
    assert c.gain_pct == 50.0


def test_cache_gain_only_from_the_same_probe():
    d = diagnose(report(warm("London", 100, 10, same=False), warm("Tokyo", 300, 30, same=False), ok("Lagos")))
    assert d.cache.gain_pct is None
    assert d.cache.same_probe_pairs == 0 and d.cache.other_probe_repeats == 2


def test_cache_gain_mixed_uses_only_same_probe_pairs():
    d = diagnose(report(warm("London", 100, 50), warm("Tokyo", 300, 3, same=False), ok("Lagos")))
    assert d.cache.gain_pct == 50.0
    assert d.cache.same_probe_pairs == 1 and d.cache.other_probe_repeats == 1


# --- JSON and strict loading --------------------------------------------------------------------

def test_to_dict_carries_per_stage_ms_share_and_mark():
    d = diagnose(server_heavy()).to_dict()
    assert [s["stage"] for s in d["stages"]] == list(STAGES)
    for s in d["stages"]:
        assert set(s) == {"stage", "ms", "share", "mark", "grows_with_distance"}
    assert d["rules"]["biggest_share"] == 0.4
    json.dumps(d)  # plain JSON


def test_diagnosis_round_trips_through_json():
    rep = server_heavy()
    d = diagnose(rep)
    again = diagnosis_from_dict(json.loads(json.dumps(d.to_dict())), rep)
    assert again == d


@pytest.mark.parametrize("change", [
    lambda d: d.update(verdict="biggest_cost"),
    lambda d: d["stages"][3].update(ms=1),
    lambda d: d["stages"][0].update(mark="biggest"),
    lambda d: d["stages"][0].update(share=True),
    lambda d: d.update(measured=True),
    lambda d: d.update(extra=1),
    lambda d: d.pop("places"),
    lambda d: d["places"][0].update(place="Somewhere else"),
    lambda d: d["cache"].update(hit=99),
    lambda d: d["rules"].update(biggest_share=2),
    lambda d: d["rules"].update(min_places="3"),
    lambda d: d["rules"].update(possible_share=0.5),  # above biggest_share
    lambda d: d.update(stages="all"),
])
def test_diagnosis_from_dict_refuses_anything_that_does_not_follow_from_the_report(change):
    rep = server_heavy()
    raw = json.loads(json.dumps(diagnose(rep).to_dict()))
    change(raw)
    with pytest.raises(ValueError):
        diagnosis_from_dict(raw, rep)


def test_diagnosis_from_dict_refuses_a_non_object():
    with pytest.raises(ValueError):
        diagnosis_from_dict(["biggest"], server_heavy())


@pytest.mark.parametrize("rules", [Rules(min_places=1), Rules(min_places=2), Rules(biggest_share=0),
                                   Rules(near_connect_ms=-1), Rules(distance_correlation=1.5)])
def test_saved_rules_below_or_outside_the_bounds_are_refused(rules):
    """The rules are settings now (Task 22), so a saved copy may bring other values, but never ones the
    settings refuse: a consistent diagnosis made with min_places=1 (a conclusion from one place) is still
    refused. In-bounds values: tests/test_thresholds.py."""
    rep = server_heavy()
    d = diagnose(rep, rules)
    with pytest.raises(ValueError):
        diagnosis_from_dict(json.loads(json.dumps(d.to_dict())), rep)


def test_readme_documents_every_rule_value():
    from pathlib import Path

    from speedward.core import bottleneck as b
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    assert "What it cannot tell you" in readme
    for name in ("BIGGEST_SHARE", "POSSIBLE_SHARE", "MIN_PLACES", "NEAR_CONNECT_MS", "DISTANCE_CORRELATION",
                 "DISTANCE_SPREAD_MS"):
        assert name in readme, name
    assert f"BIGGEST_SHARE = {b.BIGGEST_SHARE:.2f}" in readme and f"POSSIBLE_SHARE = {b.POSSIBLE_SHARE:.2f}" in readme
    assert f"MIN_PLACES = {b.MIN_PLACES}" in readme and f"NEAR_CONNECT_MS = {b.NEAR_CONNECT_MS:g}" in readme
    assert f"at least {b.DISTANCE_CORRELATION:g}" in readme and f"DISTANCE_SPREAD_MS = {b.DISTANCE_SPREAD_MS:g}" in readme


def test_readme_says_first_byte_is_globalpings_post_handshake_wait():
    from pathlib import Path
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    assert "smaller than a conventional time to first byte" in readme and "`--max-ttfb`" in readme
