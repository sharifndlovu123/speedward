"""Task 22: the "where the time goes" and "Is it fast?" thresholds as settings. Validated bounds, a clear
startup error naming only the variable, and strict loading that accepts a saved copy made under other
in-bounds values (and nothing below the bounds)."""
import json
import os
import subprocess
import sys

import pytest

from speedward.core.bottleneck import Rules, diagnose, diagnosis_from_dict
from speedward.core.config import RULE_SETTINGS, analysis_rules
from speedward.core.models import ScanReport
from speedward.core.speed import SpeedRules, speed_summary, summary_from_dict
from tests.test_bottleneck import FRANKFURT, ok, report
from tests.test_speed_reports import full

ODD = "2.71828"  # a distinctive bad value: never repeated in an error message


# --- reading the settings -----------------------------------------------------------------------

def test_defaults_when_nothing_is_set():
    assert analysis_rules({}) == (Rules(), SpeedRules())
    assert analysis_rules({name: "" for name in RULE_SETTINGS}) == (Rules(), SpeedRules())


def test_each_setting_overrides_its_rule():
    env = {"BIGGEST_SHARE": "0.5", "POSSIBLE_SHARE": "0.3", "MIN_PLACES": "4", "NEAR_CONNECT_MS": "25",
           "DISTANCE_CORRELATION": "0.8", "DISTANCE_SPREAD_MS": "80", "DNS_SHARE": "0.2", "VARIES_RATIO": "3",
           "VARIES_MIN_MS": "75", "BAD_HOP_LOSS_PCT": "20", "LONG_LINK_MS": "90"}
    rules, speed = analysis_rules(env)
    assert rules == Rules(biggest_share=0.5, possible_share=0.3, min_places=4, near_connect_ms=25,
                          distance_correlation=0.8, distance_spread_ms=80, dns_share=0.2)
    assert speed == SpeedRules(min_places=4, varies_ratio=3, varies_min_ms=75, loss_pct=20, rise_ms=90)
    assert isinstance(rules.min_places, int) and isinstance(speed.min_places, int)


def test_every_setting_is_allowed_from_dotenv_and_documented():
    from pathlib import Path

    from speedward.core.config import DOTENV_ALLOWED
    root = Path(__file__).parent.parent
    readme, example = (root / "README.md").read_text(), (root / ".env.example").read_text()
    compose = (root / "docker-compose.yml").read_text()
    for name in RULE_SETTINGS:
        assert name in DOTENV_ALLOWED, name
        assert f"`{name}`" in readme, name
        assert f"\n{name}=" in example, name
        assert f"      {name}: ${{{name}:-}}\n" in compose, name  # passed to the container


@pytest.mark.parametrize("name,value", [
    ("MIN_PLACES", "2"), ("MIN_PLACES", "0"), ("MIN_PLACES", "-3"), ("MIN_PLACES", "3.5"), ("MIN_PLACES", ODD),
    ("MIN_PLACES", "x"), ("MIN_PLACES", "1001"), ("MIN_PLACES", "1" + "0" * 400),
    ("BIGGEST_SHARE", "1.5"), ("BIGGEST_SHARE", "0"), ("BIGGEST_SHARE", "-0.4"), ("BIGGEST_SHARE", "40%"),
    ("BIGGEST_SHARE", "nan"), ("BIGGEST_SHARE", "inf"), ("POSSIBLE_SHARE", ODD), ("DNS_SHARE", "2"),
    ("NEAR_CONNECT_MS", "-1"), ("NEAR_CONNECT_MS", "1e400"), ("DISTANCE_CORRELATION", "1.2"),
    ("DISTANCE_SPREAD_MS", "-5"), ("VARIES_RATIO", "0.5"), ("VARIES_MIN_MS", "nan"),
    ("BAD_HOP_LOSS_PCT", "101"), ("BAD_HOP_LOSS_PCT", "0"), ("LONG_LINK_MS", "0"), ("LONG_LINK_MS", "-inf"),
])
def test_a_bad_value_is_a_clear_error_naming_only_the_variable(name, value):
    with pytest.raises(ValueError) as exc:
        analysis_rules({name: value})
    message = str(exc.value)
    assert message.startswith(name + " must be ")
    assert value not in message.replace(name, "") or value in ("0", "2", "1.5", "101")  # bounds are digits too
    assert ODD not in message


def test_possible_share_above_biggest_share_is_refused():
    with pytest.raises(ValueError, match="^POSSIBLE_SHARE must not be above BIGGEST_SHARE"):
        analysis_rules({"POSSIBLE_SHARE": "0.5", "BIGGEST_SHARE": "0.45"})


def test_web_settings_refuse_to_start_on_a_bad_value(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in RULE_SETTINGS}
    env.update(MIN_PLACES=ODD, DJANGO_SETTINGS_MODULE="speedward.web.settings")
    r = subprocess.run([sys.executable, "-c", "import speedward.web.settings"], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "ImproperlyConfigured: MIN_PLACES must be a whole number from 3 to 1000" in r.stderr
    assert ODD not in r.stderr


# --- the rules in use ---------------------------------------------------------------------------

def three_places():
    return report(ok("Frankfurt", 5, 3, 6, 140, 6), ok("London", 6, 12, 14, 150, 8),
                  ok("Tokyo", 20, 190, 190, 250, 20), server=FRANKFURT)


def test_min_places_above_three_holds_back_a_conclusion():
    assert diagnose(three_places()).verdict == "biggest"
    assert diagnose(three_places(), Rules(min_places=4)).verdict == "too_few"


def test_dns_share_is_its_own_threshold():
    rep = report(ok("A", 30, 10, 10, 40, 10), ok("B", 30, 10, 10, 40, 10), ok("C", 30, 10, 10, 40, 10))
    assert diagnose(rep).dns_heavy == 3  # 30% >= 25%
    assert diagnose(rep, Rules(dns_share=0.35)).dns_heavy == 0
    assert diagnose(rep, Rules(dns_share=0.35)).stages[0].mark == "possible"  # the block mark is unchanged


def test_speed_rules_change_the_verdicts_and_the_wording():
    from speedward.reports.speed import view
    varies = full(varies=True)
    assert speed_summary(varies).overall[0].verdict == "varies"
    strict = SpeedRules(varies_ratio=10)
    assert speed_summary(varies, strict).overall[0].verdict == "steady"
    varies.speed_rules = strict
    text = view(speed_summary(varies))["findings"][0]["text"]
    assert "no slowest run at 10 times its place's median and 50 ms above it" in text


def test_summary_records_the_rules_it_used():
    d = speed_summary(full(), SpeedRules(min_places=4, rise_ms=80)).to_dict()
    assert d["rules"] == {"min_places": 4, "varies_ratio": 2.0, "varies_min_ms": 50.0, "loss_pct": 10.0,
                          "rise_ms": 80}
    assert d["min_places"] == 4
    assert d["overall"][0]["verdict"] == "too_few"


# --- strict loading -------------------------------------------------------------------------------

def _saved(rep: ScanReport) -> dict:
    return json.loads(json.dumps(rep.to_dict()))


IN_BOUNDS = [Rules(biggest_share=0.9), Rules(biggest_share=0.3), Rules(min_places=4), Rules(min_places=3),
             Rules(near_connect_ms=1000), Rules(distance_spread_ms=0), Rules(dns_share=0.5),
             Rules(possible_share=0.4, biggest_share=0.4)]


@pytest.mark.parametrize("rules", IN_BOUNDS)
def test_a_saved_diagnosis_under_other_in_bounds_rules_is_accepted(rules):
    rep = three_places()
    d = diagnose(rep, rules)
    again = diagnosis_from_dict(json.loads(json.dumps(d.to_dict())), rep)
    assert again == d and again.rules == rules


@pytest.mark.parametrize("rules", [Rules(min_places=1), Rules(min_places=2), Rules(min_places=0)])
def test_a_saved_diagnosis_below_the_bounds_is_refused_even_if_consistent(rules):
    rep = three_places()
    raw = json.loads(json.dumps(diagnose(rep, rules).to_dict()))
    with pytest.raises(ValueError):
        diagnosis_from_dict(raw, rep)


@pytest.mark.parametrize("change", [
    lambda d: d["rules"].update(biggest_share=0.6),  # in bounds, but the marks follow 0.4
    lambda d: d["rules"].update(min_places=4),  # in bounds, but the verdict follows 3
    lambda d: d["rules"].update(dns_share=1.5),
    lambda d: d["rules"].update(dns_share="0.25"),
    lambda d: d["rules"].update(extra=1),
])
def test_a_saved_diagnosis_whose_figures_do_not_follow_from_its_rules_is_refused(change):
    rep = three_places()
    raw = json.loads(json.dumps(diagnose(rep).to_dict()))
    change(raw)
    with pytest.raises(ValueError):
        diagnosis_from_dict(raw, rep)


def test_a_diagnosis_saved_before_dns_share_existed_still_loads():
    rep = three_places()
    raw = json.loads(json.dumps(diagnose(rep).to_dict()))
    del raw["rules"]["dns_share"]  # then DNS used possible_share
    assert diagnosis_from_dict(raw, rep).rules == Rules()
    raw["rules"]["possible_share"] = 0.3  # but only when the figures follow from it
    with pytest.raises(ValueError):
        diagnosis_from_dict(raw, rep)


@pytest.mark.parametrize("rules", [SpeedRules(varies_ratio=10), SpeedRules(min_places=4), SpeedRules(loss_pct=50),
                                   SpeedRules(rise_ms=1), SpeedRules(varies_min_ms=0)])
def test_a_saved_summary_under_other_in_bounds_rules_is_accepted_and_kept(rules):
    rep = full(varies=True)
    rep.speed_rules = rules
    raw = _saved(rep)
    assert raw["speed_summary"]["rules"]["min_places"] == rules.min_places
    again = ScanReport.from_dict(raw)
    assert again.speed_rules == rules
    assert again.to_dict() == rep.to_dict()  # exports use the saved values


@pytest.mark.parametrize("change", [
    lambda s: s["rules"].update(min_places=2),
    lambda s: s.update(min_places=2) or s["rules"].update(min_places=2),
    lambda s: s["rules"].update(varies_ratio=0.5),
    lambda s: s["rules"].update(varies_ratio=10),  # in bounds, but the verdict follows 2
    lambda s: s["rules"].update(loss_pct=float("nan")),
    lambda s: s["rules"].update(rise_ms=True),
    lambda s: s["rules"].pop("rise_ms"),
    lambda s: s.update(min_places=4),  # must equal the rules' own
])
def test_a_forged_or_below_bound_summary_is_refused(change):
    raw = _saved(full(varies=True))
    change(raw["speed_summary"])
    with pytest.raises(ValueError):
        ScanReport.from_dict(raw)


def test_a_summary_saved_before_it_recorded_its_rules_loads_with_the_defaults():
    rep = full(varies=True)
    raw = _saved(rep)
    del raw["speed_summary"]["rules"]
    again = ScanReport.from_dict(raw)
    assert again.speed_rules is None and again.to_dict() == rep.to_dict()
    raw["speed_summary"]["overall"][0]["verdict"] = "steady"
    with pytest.raises(ValueError):
        ScanReport.from_dict(raw)


def test_a_summary_cannot_bring_rules_that_are_not_an_object():
    raw = _saved(full())
    raw["speed_summary"]["rules"] = [3]
    with pytest.raises(ValueError):
        summary_from_dict(raw["speed_summary"], ScanReport.from_dict(_saved(full())))
