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
    head: str                         # "212 of 250 tests left this hour"
    reset_phrase: str                 # "resets in 37 min", "resets now", "reset time unknown"
    tail: str                         # what follows the reset phrase (scan advice, key prompt)
    text: str                         # the whole strip text
    keyless: bool
    next_full_scan_in_minutes: int | None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _minutes(seconds: int | None) -> int | None:
    if seconds is None or seconds <= 0:
        return None
    return math.ceil(seconds / 60)


def _reset_phrase(seconds: int | None, minutes: int | None, low: bool) -> str:
    if seconds is None:
        return "reset time unknown"
    if minutes is None:
        return "resets now"
    return f"{'next full allowance' if low else 'resets'} in {minutes} min"


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
    # A full scan fits again only after the allowance resets.
    next_full = minutes if state == "low" and cost is not None and left is not None and left < cost \
        and cost <= per_hour else None

    used = this = after = None
    caption = ""
    if left is not None and cost is not None and per_hour > 0:
        have = max(0, min(left, per_hour))
        used, this = per_hour - have, min(cost, have)
        after = have - this
        scan = f"{plain_number(cost)} for this scan" + (f" (only {plain_number(have)} left)" if cost > have else "")
        caption = f"{plain_number(used)} used already · {scan} · {plain_number(after)} left afterwards"

    head = (f"{plain_number(left)} of {plain_number(per_hour)} tests left this hour" if left is not None
            else f"Tests left this hour: unknown (limit {plain_number(per_hour)})")
    if keyless:
        head += " (no key)"
    phrase = _reset_phrase(seconds, minutes, state == "low")
    parts = []
    if state == "low" and cost is not None and left is not None and left < cost:
        parts.append(f"This scan needs about {plain_number(cost)}, so it will ask you to confirm.")
    if keyless:
        parts.append(KEY_PROMPT)
    tail = " ".join(parts)
    if state == "low":
        text = f"{head}. {phrase[0].upper()}{phrase[1:]}." + (f" {tail}" if tail else "")
    else:
        text = f"{head} · {phrase}" + (f" · {tail}" if tail else "")
    return AllowanceView(
        tests_per_hour=per_hour, tests_left=left, reset_in_minutes=minutes, reset_seconds=seconds,
        reset_at_epoch=None if seconds is None else now_epoch + seconds, state=state,
        used_before=used, this_scan=this, left_after=after, has_bar=used is not None, caption=caption,
        head=head, reset_phrase=phrase, tail=tail, text=text, keyless=keyless,
        next_full_scan_in_minutes=next_full)
