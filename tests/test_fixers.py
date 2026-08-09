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


def test_notify_message_hoisted_into_data():
    """The reported bug: message on a notify action -> nested under data."""
    action = {"action": "notify.iphone16promax", "message": "Weather alert."}
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {
        "action": "notify.iphone16promax",
        "data": {"message": "Weather alert."},
    }


def test_notify_title_and_message_both_hoisted():
    """Several stray service-data fields all move under data:."""
    action = {
        "service": "notify.phone",
        "title": "Alert",
        "message": "Door opened",
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {
        "service": "notify.phone",
        "data": {"title": "Alert", "message": "Door opened"},
    }


def test_hoist_merges_with_existing_data_existing_wins():
    """Stray keys merge into an existing data: dict without clobbering it."""
    action = {
        "action": "notify.phone",
        "message": "stray",
        "data": {"message": "kept", "title": "T"},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {
        "action": "notify.phone",
        "data": {"message": "kept", "title": "T"},
    }


def test_well_formed_service_call_untouched():
    """A correct service call (only structural keys) is left alone."""
    action = {
        "action": "light.turn_on",
        "target": {"entity_id": "light.kitchen"},
        "data": {"brightness": 255},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 0
    assert fixed == action


def test_hoist_reaches_nested_action_in_full_config():
    """The repair fires wherever the service call is nested in a draft."""
    config = {
        "alias": "Notify on alert",
        "triggers": [
            {"trigger": "state", "entity_id": "sensor.nws_alerts", "to": "on"}
        ],
        "conditions": [],
        "actions": [
            {"action": "notify.iphone16promax", "message": "Alert detected."}
        ],
    }
    fixed, count = sanitize_llm_config(config)
    assert count == 1
    assert fixed["actions"][0] == {
        "action": "notify.iphone16promax",
        "data": {"message": "Alert detected."},
    }


def test_non_service_step_with_extra_keys_untouched():
    """A step without action:/service: is not a service call — leave it."""
    action = {"delay": "00:05:00", "alias": "wait a bit"}
    fixed, count = sanitize_llm_config(action)
    assert count == 0
    assert fixed == action


def test_notify_list_target_moved_into_data():
    """The reported bug: notify target as a list -> data.target (not a target:)."""
    action = {
        "action": "notify.notify",
        "target": ["iphone16promax"],
        "data": {"message": "NWS severe weather alert."},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {
        "action": "notify.notify",
        "data": {
            "message": "NWS severe weather alert.",
            "target": ["iphone16promax"],
        },
    }


def test_notify_target_without_existing_data():
    """A notify target with no data: dict yet still lands under data.target."""
    action = {"service": "notify.notify", "target": "iphone16promax"}
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {
        "service": "notify.notify",
        "data": {"target": "iphone16promax"},
    }


def test_notify_target_does_not_clobber_existing_data_target():
    """An existing data.target wins over a stray top-level target."""
    action = {
        "action": "notify.notify",
        "target": ["stray"],
        "data": {"target": ["kept"], "message": "hi"},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {
        "action": "notify.notify",
        "data": {"target": ["kept"], "message": "hi"},
    }


def test_bare_entity_id_target_wrapped():
    """A non-notify service with a bare entity id target -> {entity_id: ...}."""
    action = {"action": "light.turn_on", "target": "light.kitchen"}
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {
        "action": "light.turn_on",
        "target": {"entity_id": "light.kitchen"},
    }


def test_list_entity_id_target_wrapped():
    """A list of entity ids as target -> {entity_id: [...]}."""
    action = {"action": "light.turn_on", "target": ["light.a", "light.b"]}
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {
        "action": "light.turn_on",
        "target": {"entity_id": ["light.a", "light.b"]},
    }


def test_dict_target_left_alone():
    """A correct dict target is untouched."""
    action = {"action": "light.turn_on", "target": {"entity_id": "light.a"}}
    fixed, count = sanitize_llm_config(action)
    assert count == 0
    assert fixed == action


def test_target_repair_in_full_multi_action_config():
    """The reported draft: repair fires on the notify action, others untouched."""
    config = {
        "alias": "Adjust Mango Inverter Settings Based on NWS Alert",
        "triggers": [
            {"trigger": "state", "entity_id": "sensor.nws_alerts", "to": "Severe"}
        ],
        "conditions": [],
        "actions": [
            {
                "action": "number.set_value",
                "entity_id": "number.mango_battery_reserve",
                "data": {"value": 70},
            },
            {
                "action": "select.select_option",
                "entity_id": "select.mango_inverter_mode",
                "data": {"option": "Backup"},
            },
            {
                "action": "notify.notify",
                "target": ["iphone16promax"],
                "data": {"message": "NWS severe weather alert detected."},
            },
        ],
        "mode": "single",
    }
    fixed, count = sanitize_llm_config(config)
    assert count == 1
    assert fixed["actions"][0] == config["actions"][0]
    assert fixed["actions"][1] == config["actions"][1]
    assert fixed["actions"][2] == {
        "action": "notify.notify",
        "data": {
            "message": "NWS severe weather alert detected.",
            "target": ["iphone16promax"],
        },
    }


def test_data_template_folded_into_data():
    """Legacy data_template: on a service call -> modern data:."""
    action = {
        "action": "notify.notify",
        "data_template": {"message": "{{ 'hi' }}"},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed == {"action": "notify.notify", "data": {"message": "{{ 'hi' }}"}}


def test_value_template_key_stripped_to_value():
    """A hallucinated value_template key -> value (template string kept)."""
    action = {
        "action": "number.set_value",
        "target": {"entity_id": "number.x"},
        "data": {"value_template": "{{ 70 }}"},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed["data"] == {"value": "{{ 70 }}"}


def test_option_template_key_stripped_to_option():
    """option_template inside data -> option."""
    action = {
        "action": "select.select_option",
        "target": {"entity_id": "select.x"},
        "data": {"option_template": "{{ 'Backup' }}"},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed["data"] == {"option": "{{ 'Backup' }}"}


def test_plain_key_wins_over_template_variant():
    """When both value and value_template exist, keep the plain value."""
    action = {
        "action": "number.set_value",
        "data": {"value": 70, "value_template": "{{ 20 }}"},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 1
    assert fixed["data"] == {"value": 70}


def test_data_template_with_template_key_fully_modernized():
    """Doubly-legacy data_template + value_template -> data: {value: ...}."""
    action = {
        "action": "number.set_value",
        "data_template": {"value_template": "{{ 70 }}"},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 2
    assert fixed["data"] == {"value": "{{ 70 }}"}


def test_modern_data_untouched():
    """A correct data: block with no legacy keys is left alone."""
    action = {
        "action": "number.set_value",
        "target": {"entity_id": "number.x"},
        "data": {"value": 70},
    }
    fixed, count = sanitize_llm_config(action)
    assert count == 0
    assert fixed == action


def test_template_keys_only_on_service_calls():
    """A non-service dict with a _template key is not touched."""
    node = {"value_template": "{{ 1 }}", "condition": "template"}
    fixed, count = sanitize_llm_config(node)
    assert count == 0
    assert fixed == node
