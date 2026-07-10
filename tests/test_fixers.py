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


def test_single_after_sunset_becomes_night_state():
    """A lone 'after: sunset' -> sun.sun below_horizon, not an empty time."""
    fixed, count = sanitize_llm_config(
        {"condition": "time", "after": "sunset"}
    )
    assert count == 1
    assert fixed == {
        "condition": "state",
        "entity_id": "sun.sun",
        "state": "below_horizon",
    }


def test_single_before_sunrise_becomes_night_state():
    fixed, count = sanitize_llm_config(
        {"condition": "time", "before": "sunrise"}
    )
    assert count == 1
    assert fixed["state"] == "below_horizon"


def test_single_after_sunrise_becomes_day_state():
    fixed, count = sanitize_llm_config(
        {"condition": "time", "after": "sunrise"}
    )
    assert count == 1
    assert fixed["state"] == "above_horizon"


def test_state_trigger_above_becomes_numeric_state():
    """to: 'above 25' on a state trigger is a never-firing bug -> numeric_state."""
    trigger = {"trigger": "state", "entity_id": "sensor.temp", "to": "above 25"}
    fixed, count = sanitize_llm_config(trigger)
    assert count == 1
    assert fixed == {
        "trigger": "numeric_state",
        "entity_id": "sensor.temp",
        "above": 25,
    }


def test_state_trigger_below_float_becomes_numeric_state():
    trigger = {"platform": "state", "entity_id": "sensor.temp", "to": "below 18.5"}
    fixed, count = sanitize_llm_config(trigger)
    assert count == 1
    # legacy platform key is preserved
    assert fixed == {
        "platform": "numeric_state",
        "entity_id": "sensor.temp",
        "below": 18.5,
    }


def test_state_trigger_symbolic_comparison():
    trigger = {"trigger": "state", "entity_id": "sensor.temp", "to": ">= 30"}
    fixed, count = sanitize_llm_config(trigger)
    assert count == 1
    assert fixed["trigger"] == "numeric_state"
    assert fixed["above"] == 30


def test_bare_number_state_trigger_left_alone():
    """A bare number is ambiguous (above? below?) — do not guess."""
    trigger = {"trigger": "state", "entity_id": "sensor.temp", "to": "25"}
    fixed, count = sanitize_llm_config(trigger)
    assert count == 0
    assert fixed == trigger


def test_normal_state_trigger_untouched():
    trigger = {"trigger": "state", "entity_id": "cover.garage_door", "to": "open"}
    fixed, count = sanitize_llm_config(trigger)
    assert count == 0
    assert fixed == trigger


def test_bare_and_condition_is_dropped():
    """A flattened `{condition: and}` with no sub-conditions is removed."""
    config = {
        "conditions": [
            {"condition": "and"},
            {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"},
        ]
    }
    fixed, count = sanitize_llm_config(config)
    assert count == 1
    assert fixed["conditions"] == [
        {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
    ]


def test_bare_or_condition_is_preserved():
    """`or`/`not` can't be safely reconstructed, so they are left to fail."""
    config = {"conditions": [{"condition": "or"}]}
    fixed, count = sanitize_llm_config(config)
    assert count == 0
    assert fixed["conditions"] == [{"condition": "or"}]


def test_duplicate_conditions_are_collapsed():
    config = {
        "conditions": [
            {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"},
            {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"},
        ]
    }
    fixed, count = sanitize_llm_config(config)
    assert count == 1
    assert fixed["conditions"] == [
        {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
    ]


def test_full_regression_payload_from_0_11_1_log():
    """The exact conditions block from the 0.11.1 live failure collapses
    to a single valid night check."""
    config = {
        "mode": "restart",
        "triggers": [
            {"trigger": "state", "entity_id": "cover.garage_door", "to": "open"}
        ],
        "conditions": [
            {"condition": "and"},
            {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"},
            {"condition": "time", "after": "sunset"},
            {"condition": "time", "before": "sunrise"},
        ],
        "actions": [
            {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
        ],
    }
    fixed, count = sanitize_llm_config(config)
    assert fixed["conditions"] == [
        {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
    ]
    assert count >= 3


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
