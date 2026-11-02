"""Test-count estimate and allowance check. Billing is one test per location per measurement."""
from __future__ import annotations

from dataclasses import dataclass

from speedward.core.checks import LABEL, PER_PLACE
from speedward.core.globalping import Allowance

BASE_PER_LOCATION = 3  # cold http + warm http + ping


@dataclass
class Plan:
    locations: list[dict]
    traceroute: bool = False
    dns: bool = False
    browser: bool = False  # runs locally, costs no Globalping tests
    checks: tuple[str, ...] = ()  # ticked "More checks" (speedward.core.checks.REACH_CHECKS)


@dataclass
class BudgetVerdict:
    cost: int
    fits: bool
    reason: str = ""


@dataclass
class CostLine:
    key: str  # "base", "traceroute", "dns_answers" or a check name
    per_place: int
    cost: int


def cost_breakdown(plan: Plan) -> list[CostLine]:
    """What each part of the scan costs: the default, each optional measurement and each ticked check."""
    n = len(plan.locations)
    parts = [("base", BASE_PER_LOCATION)]
    if plan.traceroute:
        parts.append(("traceroute", 1))
    if plan.dns:
        parts.append(("dns_answers", 1))
    parts += [(c, PER_PLACE[c]) for c in plan.checks]
    return [CostLine(key, per, per * n) for key, per in parts]


PART_LABEL = {"base": "Default scan", "traceroute": "Network path", "dns_answers": "DNS answers", **LABEL}


def describe_breakdown(plan: Plan) -> str:
    """One line: what each part costs, then the total (shown before every scan, web and CLI)."""
    lines = cost_breakdown(plan)
    n = len(plan.locations)
    parts = [f"{PART_LABEL[x.key]} {x.per_place} per place = {x.cost}" if x.key == "base"
             else f"{PART_LABEL[x.key]} +{x.per_place} per place = {x.cost}" for x in lines]
    return (" · ".join(parts) + f" · {n} place{'' if n == 1 else 's'}: about {sum(x.cost for x in lines)} tests")


def estimate_cost(plan: Plan) -> int:
    return sum(line.cost for line in cost_breakdown(plan))


def check_budget(plan: Plan, allowance: Allowance) -> BudgetVerdict:
    cost = estimate_cost(plan)
    count = len(plan.locations)
    if count > allowance.max_probes:
        return BudgetVerdict(cost, False,
                             f"You chose {count} locations but at most {allowance.max_probes} are allowed "
                             f"in one scan. Remove some locations.")
    if allowance.remaining is not None and cost > allowance.remaining:
        reason = (f"This scan needs about {cost} tests but only {allowance.remaining} are left this hour. "
                  f"Remove locations or optional checks, or wait for the allowance to reset.")
        if allowance.credits and allowance.credits > 0:
            reason += " Your Globalping credits may cover the difference."
        return BudgetVerdict(cost, False, reason)
    return BudgetVerdict(cost, True, "")
