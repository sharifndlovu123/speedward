"""Test-count estimate and allowance check. Billing is one test per location per measurement."""
from __future__ import annotations

from dataclasses import dataclass

from speedward.core.globalping import Allowance

BASE_PER_LOCATION = 3  # cold http + warm http + ping


@dataclass
class Plan:
    locations: list[dict]
    traceroute: bool = False
    dns: bool = False
    browser: bool = False  # runs locally, costs no Globalping tests


@dataclass
class BudgetVerdict:
    cost: int
    fits: bool
    reason: str = ""


def estimate_cost(plan: Plan) -> int:
    per_location = BASE_PER_LOCATION + int(plan.traceroute) + int(plan.dns)
    return len(plan.locations) * per_location


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
