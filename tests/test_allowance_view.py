"""describe_allowance: the numbers and words behind the status strip, the bar and the CLI line."""
from speedward.core.allowance_view import describe_allowance
from speedward.core.globalping import Allowance

NOW = 1_000_000.0


def allowance(remaining=212, reset=2220, per_hour=250, authenticated=False, credits=None):
    return Allowance(per_hour, 50, remaining, authenticated, credits, reset)


def test_reset_rounds_up_to_whole_minutes():
    assert describe_allowance(allowance(reset=61), 6, NOW).reset_in_minutes == 2
    assert describe_allowance(allowance(reset=1), 6, NOW).reset_in_minutes == 1
    assert describe_allowance(allowance(reset=60), 6, NOW).reset_in_minutes == 1
    zero = describe_allowance(allowance(reset=0), 6, NOW)
    assert zero.reset_in_minutes is None and "resets now" in zero.text


def test_unknown_reset_is_not_guessed():
    v = describe_allowance(allowance(reset=None), 6, NOW)
    assert v.reset_in_minutes is None and v.reset_at_epoch is None
    assert "reset time unknown" in v.text and "0 min" not in v.text
    assert v.next_full_scan_in_minutes is None


def test_unknown_remaining_is_unknown_and_has_no_bar():
    v = describe_allowance(allowance(remaining=None), 6, NOW)
    assert v.state == "unknown" and v.tests_left is None and not v.has_bar
    assert v.used_before is None and v.this_scan is None and v.left_after is None
    assert "unknown" in v.text and "limit 250" in v.text


def test_ok_state_and_text():
    v = describe_allowance(allowance(authenticated=True), 36, NOW)
    assert v.state == "ok" and v.reset_in_minutes == 37
    assert v.text.startswith("Globalping key active: 250 tests/hour, up to 50 locations. "
                             "212 of 250 tests left this hour · resets in 37 min")
    assert v.reset_at_epoch == NOW + 2220


def test_low_state_when_remaining_below_cost():
    v = describe_allowance(allowance(remaining=14, reset=520), 36, NOW)
    assert v.state == "low" and v.next_full_scan_in_minutes == 9
    assert "14 of 250 tests left this hour" in v.text and "Next full allowance in 9 min" in v.text
    assert "about 36" in v.text
    assert describe_allowance(allowance(remaining=37), 6, NOW).state == "low"  # <= 15% of 250
    assert describe_allowance(allowance(remaining=38), 6, NOW).state == "ok"
    assert describe_allowance(allowance(remaining=20), 6, NOW).next_full_scan_in_minutes is None  # it fits now


def test_bar_parts_sum_to_limit_and_never_negative():
    v = describe_allowance(allowance(remaining=212), 36, NOW)
    assert (v.used_before, v.this_scan, v.left_after) == (38, 36, 176)
    assert v.caption == "38 used already · 36 for this scan · 176 left afterwards"
    for remaining, cost in ((0, 36), (14, 36), (250, 300), (999, 6), (-5, 6), (5, 0)):
        p = describe_allowance(allowance(remaining=remaining), cost, NOW)
        assert min(p.used_before, p.this_scan, p.left_after) >= 0
        assert p.used_before + p.this_scan + p.left_after == 250
    over = describe_allowance(allowance(remaining=14), 36, NOW)
    assert "only 14 left" in over.caption and over.left_after == 0


def test_no_bar_without_a_cost():
    v = describe_allowance(allowance(), None, NOW)
    assert not v.has_bar and v.caption == ""


def test_keyless_text_says_free_up_to_a_limit_and_never_bare_free():
    v = describe_allowance(allowance(), 6, NOW)
    assert v.keyless and "No Globalping key set: 250 tests/hour, 50 locations." in v.text
    assert "A key is free up to a limit: https://globalping.io" in v.text
    assert " free " not in v.text.replace("free up to a limit", "")
    keyed = describe_allowance(allowance(per_hour=500, authenticated=True), 6, NOW)
    assert not keyed.keyless and "free" not in keyed.text and "No Globalping key" not in keyed.text


def test_templates_hold_one_placeholder_for_the_live_countdown():
    v = describe_allowance(allowance(remaining=14, reset=520), 36, NOW)
    assert "Next full allowance in {minutes} min." in v.text_template
    assert "{minutes}" not in v.text and "in 9 min." in v.text
    assert "Resets now." in v.text_now and "{minutes}" not in v.text_now
    assert "reset time unknown" in v.text_unknown.lower()
    ok = describe_allowance(allowance(), 6, NOW)
    assert "resets in {minutes} min" in ok.text_template


def test_low_wording_is_the_same_whenever_it_is_computed():
    # Low only because of the 15% rule and the scan fits: plain "resets", never "next full allowance".
    v = describe_allowance(allowance(remaining=20), 6, NOW)
    assert v.state == "low" and "Resets in {minutes} min." in v.text_template
    assert "Next full allowance" not in v.text_template


def test_no_full_scan_time_when_cost_exceeds_the_hourly_limit():
    v = describe_allowance(allowance(remaining=14, reset=520), 300, NOW)
    assert v.next_full_scan_in_minutes is None and "Next full allowance" not in v.text_template


def test_zero_reset_with_a_full_allowance_is_not_a_countdown():
    v = describe_allowance(allowance(remaining=250, reset=0), 6, NOW)
    assert "allowance reset time unknown" in v.text and "resets now" not in v.text
    used = describe_allowance(allowance(remaining=200, reset=0), 6, NOW)
    assert "resets now" in used.text
