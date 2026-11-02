import pytest

from speedward.core.budget import Plan, check_budget, cost_breakdown, estimate_cost
from speedward.core.checks import REACH_CHECKS, parse_checks
from speedward.core.globalping import Allowance

LOCS = [{"magic": f"L{i}"} for i in range(10)]


def allowance(remaining=100, max_probes=50, credits=None):
    return Allowance(tests_per_hour=250, max_probes=max_probes, remaining=remaining,
                     authenticated=False, credits=credits)


def test_estimate_default_plan_is_locations_times_3():
    assert estimate_cost(Plan(LOCS, False, False, False)) == 30


def test_traceroute_adds_one_per_location():
    assert estimate_cost(Plan(LOCS, True, False, False)) == 40
    assert estimate_cost(Plan(LOCS, True, True, True)) == 50


def test_reach_checks_add_their_cost_per_location():
    assert estimate_cost(Plan(LOCS, checks=("ipv6",))) == 40
    assert estimate_cost(Plan(LOCS, checks=("ipv6", "http"))) == 50
    assert estimate_cost(Plan(LOCS, checks=("dns",))) == 50  # two resolvers
    assert estimate_cost(Plan(LOCS, checks=("port",))) == 40
    assert estimate_cost(Plan(LOCS, True, True, checks=REACH_CHECKS)) == 10 * (5 + 5)


def test_no_checks_ticked_adds_nothing():
    assert estimate_cost(Plan(LOCS, checks=())) == 30


def test_cost_breakdown_lists_base_and_each_check():
    rows = cost_breakdown(Plan(LOCS, traceroute=True, checks=("ipv6", "dns")))
    assert [(r.key, r.per_place, r.cost) for r in rows] == [
        ("base", 3, 30), ("traceroute", 1, 10), ("ipv6", 1, 10), ("dns", 2, 20)]
    assert sum(r.cost for r in rows) == estimate_cost(Plan(LOCS, traceroute=True, checks=("ipv6", "dns")))


def test_over_allowance_counts_the_checks():
    v = check_budget(Plan(LOCS, checks=("ipv6", "http")), allowance(remaining=45))
    assert not v.fits and v.cost == 50


def test_parse_checks_orders_and_dedupes():
    assert parse_checks("port, ipv6,ipv6") == ("ipv6", "port")
    assert parse_checks("") == () and parse_checks(None) == ()


def test_parse_checks_refuses_unknown_names_without_echoing_them():
    with pytest.raises(ValueError) as e:
        parse_checks("ipv6,<script>")
    assert "<script>" not in str(e.value)


def test_plan_fits():
    v = check_budget(Plan(LOCS, False, False, False), allowance())
    assert v.fits and v.cost == 30 and v.reason == ""


def test_plan_over_probe_cap_does_not_fit():
    v = check_budget(Plan(LOCS, False, False, False), allowance(max_probes=5))
    assert not v.fits and "5" in v.reason and "locations" in v.reason


def test_plan_over_remaining_does_not_fit():
    v = check_budget(Plan(LOCS, False, False, False), allowance(remaining=20))
    assert not v.fits and v.cost == 30 and "20" in v.reason


def test_unknown_remaining_fits_and_credits_noted():
    assert check_budget(Plan(LOCS, False, False, False), allowance(remaining=None)).fits
    v = check_budget(Plan(LOCS, False, False, False), allowance(remaining=20, credits=100))
    assert not v.fits and "credits" in v.reason
