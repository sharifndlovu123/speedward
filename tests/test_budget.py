from speedward.core.budget import Plan, check_budget, estimate_cost
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
