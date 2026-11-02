"""Bounds checking shared by the analysis thresholds: the settings read at start-up and the values a
saved copy records. One place, so a value the settings refuse is refused in a saved copy too.

A bound is (lowest, highest, whole number). Messages name the setting or rule, never the value given.
"""
from __future__ import annotations

import math

Bound = tuple[float, float, bool]


def in_bounds(value, bound: Bound) -> bool:
    """True for a JSON number (never a bool) within the bound; a whole-number bound takes ints only."""
    lo, hi, whole = bound
    if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)):
        return False
    # The range check comes first: it compares a huge JSON integer (10**400) exactly, where
    # math.isfinite would raise OverflowError converting it to float. NaN fails the comparison too.
    if not lo <= value <= hi:
        return False
    try:
        return math.isfinite(value)
    except ArithmeticError:
        return False


def describe(name: str, bound: Bound, default) -> str:
    lo, hi, whole = bound
    kind = "a whole number" if whole else "a number"
    return f"{name} must be {kind} from {lo:g} to {hi:g} (default {default:g})."


def parse(name: str, raw: str, bound: Bound, default):
    """A setting's text as a number within its bound. ValueError(describe(...)) otherwise."""
    message = describe(name, bound, default)
    try:
        value = int(raw) if bound[2] else float(raw)
    except ValueError:
        raise ValueError(message) from None
    if not in_bounds(value, bound):
        raise ValueError(message)
    return int(value) if not bound[2] and value.is_integer() else value
