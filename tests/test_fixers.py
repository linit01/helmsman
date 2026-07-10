"""Tests for deterministic LLM-output repairs (fixers.py).

Focus on the sun-word `time` condition repair: small models bolt an
invalid {condition: time, after: sunset} clause onto drafts and cannot
self-correct it, so it must be fixed in code before HA validation.
"""

from custom_components.helmsman.fixers import sanitize_llm_config


def test_night_time_condition_becomes_sun_state():
    """time+sunset/sunrise (night) -> canonical sun.sun below_horizon."""
    cond = {"condition": "time", "after": "sunset", "before": "sunrise"}
    fixed, count = sanitize_llm_config(cond)
    assert count == 1
    assert fixed == {
        "condition": "state",
        "entity_id": "sun.sun",
        "state": "below_horizon",
    }


def test_day_time_condition_becomes_sun_state():
    """time+sunrise/sunset (day) -> sun.sun above_horizon."""
    cond = {"condition": "time", "after": "sunrise", "before": "sunset"}
    fixed, count = sanitize_llm_config(cond)
    assert count == 1
    assert fixed["state"] == "above_horizon"


def test_mixed_sun_and_clock_strips_only_the_sun_bound():
    """A clock bound is valid in a time condition; keep it, drop the sun word."""
    cond = {"condition": "time", "after": "sunset", "before": "07:00"}
    fixed, count = sanitize_llm_config(cond)
    assert count == 1
    assert fixed == {"condition": "time", "before": "07:00"}


def test_valid_time_condition_untouched():
    cond = {"condition": "time", "after": "22:00", "before": "07:00"}
    fixed, count = sanitize_llm_config(cond)
    assert count == 0
    assert fixed == cond


def test_sun_condition_untouched():
    """A real `sun` condition already accepts sun words — leave it alone."""
    cond = {"condition": "sun", "after": "sunset", "before": "sunrise"}
    fixed, count = sanitize_llm_config(cond)
    assert count == 0
    assert fixed == cond


def test_time_condition_with_weekday_strips_sun_bound_only():
    """Extra keys block whole-clause reinterpretation; strip the bad bound."""
    cond = {
        "condition": "time",
        "after": "sunset",
        "weekday": ["mon", "tue"],
    }
    fixed, count = sanitize_llm_config(cond)
    assert count == 1
    assert fixed == {"condition": "time", "weekday": ["mon", "tue"]}


def test_repair_reaches_nested_conditions_in_full_config():
    """The repair must fire wherever the bad condition is nested."""
    config = {
        "alias": "x",
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}
        ],
        "conditions": [
            {"condition": "time", "after": "sunset", "before": "sunrise"}
        ],
        "actions": [
            {"action": "light.turn_on", "target": {"entity_id": "light.a"}}
        ],
    }
    fixed, count = sanitize_llm_config(config)
    assert count == 1
    assert fixed["conditions"][0] == {
        "condition": "state",
        "entity_id": "sun.sun",
        "state": "below_horizon",
    }
