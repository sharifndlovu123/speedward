"""What the allowance means for one scan: the words and numbers behind the status strip, the
progress bar and the command-line line. Pure functions; nothing is stored.

Never a guess: when Globalping did not say how many tests are left, or when they reset, that part
is "unknown" and there is no bar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from speedward.core.analysis import plain_number
from speedward.core.globalping import Allowance

LOW_FRACTION = 0.15
KEY_PROMPT = "A key is free up to a limit: https://globalping.io"


@dataclass
class AllowanceView:
    tests_per_hour: int
    tests_left: int | None
    reset_in_minutes: int | None      # rounded up; never 0 while a reset is pending
    reset_seconds: int | None
    reset_at_epoch: float | None
    state: str                        # "ok", "low" or "unknown"
    used_before: int | None           # the three bar parts sum to tests_per_hour
    this_scan: int | None
    left_after: int | None
    has_bar: bool
    caption: str
    head: str                         # key state and tests left
    reset_phrase: str                 # "resets in 37 min", "resets now", "reset time unknown"
    tail: str                         # what follows the reset phrase (scan advice, credits, key prompt)
    text: str                         # the whole strip text now
    text_template: str                # same text with {minutes} for a live countdown (== text when no countdown)
    text_now: str                     # the text once the countdown reaches zero
    text_unknown: str                 # the text when the countdown cannot be trusted
    keyless: bool
    next_full_scan_in_minutes: int | None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _minutes(seconds: int | None) -> int | None:
    if seconds is None or seconds <= 0:
        return None
    return math.ceil(seconds / 60)


def describe_allowance(allowance: Allowance, cost: int | None, now_epoch: float) -> AllowanceView:
    per_hour = allowance.tests_per_hour
    left = allowance.remaining
    seconds = allowance.reset_seconds
    minutes = _minutes(seconds)
    cost = None if cost is None else max(0, cost)
    keyless = not allowance.authenticated

    if left is None:
        state = "unknown"
    elif (cost is not None and left < cost) or left <= LOW_FRACTION * per_hour:
        state = "low"
    else:
        state = "ok"
    # Only claim a time for a full scan when one can ever fit (cost <= the hourly limit).
    scan_waits = cost is not None and left is not None and left < cost
    next_full = minutes if scan_waits and cost <= per_hour else None

    used = this = after = None
    caption = ""
    if left is not None and cost is not None and per_hour > 0:
        have = max(0, min(left, per_hour))
        used, this = per_hour - have, min(cost, have)
        after = have - this
        scan = f"{plain_number(cost)} for this scan" + (f" (only {plain_number(have)} left)" if cost > have else "")
        caption = f"{plain_number(used)} used already · {scan} · {plain_number(after)} left afterwards"

    if keyless:
        intro = f"No Globalping key set: {plain_number(per_hour)} tests/hour, {plain_number(allowance.max_probes)} locations"
    else:
        intro = (f"Globalping key active: {plain_number(per_hour)} tests/hour, "
                 f"up to {plain_number(allowance.max_probes)} locations")
    count = (f"{plain_number(left)} of {plain_number(per_hour)} tests left this hour" if left is not None
             else f"Tests left this hour: unknown (limit {plain_number(per_hour)})")
    head = f"{intro}. {count}"

    parts = []
    if next_full is not None or (scan_waits and state == "low"):
        parts.append(f"This scan needs about {plain_number(cost)}, so it will ask you to confirm.")
    if allowance.credits is not None:
        parts.append(f"Credits: {plain_number(allowance.credits)}.")
    if keyless:
        parts.append(KEY_PROMPT)
    tail = " ".join(parts)

    # With nothing used yet a reset of 0 is not a countdown we can vouch for.
    unvouched = seconds == 0 and left is not None and left >= per_hour
    waiting = next_full is not None

    def phrase(kind: str) -> str:
        if kind == "unknown":
            return "allowance reset time unknown" if unvouched else "reset time unknown"
        if kind == "now":
            return "allowance reset time unknown" if unvouched else "resets now"
        return f"{'next full allowance' if waiting else 'resets'} in {{minutes}} min"

    def compose(p: str) -> str:
        if state == "low":
            return f"{head}. {p[0].upper()}{p[1:]}." + (f" {tail}" if tail else "")
        return f"{head} · {p}" + (f" · {tail}" if tail else "")

    if seconds is None:
        kind = "unknown"
    elif minutes is None:
        kind = "now"
    else:
        kind = "countdown"
    template = compose(phrase(kind))
    shown = phrase(kind).replace("{minutes}", str(minutes))
    return AllowanceView(
        tests_per_hour=per_hour, tests_left=left, reset_in_minutes=minutes, reset_seconds=seconds,
        reset_at_epoch=None if seconds is None else now_epoch + seconds, state=state,
        used_before=used, this_scan=this, left_after=after, has_bar=used is not None, caption=caption,
        head=head, reset_phrase=shown, tail=tail, text=template.replace("{minutes}", str(minutes)),
        text_template=template, text_now=compose(phrase("now")), text_unknown=compose(phrase("unknown")),
        keyless=keyless, next_full_scan_in_minutes=next_full)
