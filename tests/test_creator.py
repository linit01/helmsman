"""Tests for the draft fidelity gate (creator.py).

The structural / entity-existence / HA-validation gates only prove a draft
is well-formed. These tests cover the semantic gate that catches a draft
which is well-formed but drops a clause of the request or triggers on a
real-but-wrong entity.
"""

import copy

import pytest

from custom_components.helmsman.creator import (
    _fidelity_problem,
    draft_automation,
    probe_draft_quality,
)
from custom_components.helmsman.ollama import OllamaError


class FakeClient:
    """Stand-in OllamaClient that replays queued structured responses."""

    model = "fake-model"

    def __init__(self, drafts=None, fidelities=None):
        self._drafts = list(drafts or [])
        self._fidelities = list(fidelities or [])
        self.draft_calls = 0
        self.fidelity_calls = 0

    async def chat_structured_messages(
        self, messages, schema, timeout_s, temperature
    ):
        self.draft_calls += 1
        return self._drafts.pop(0)

    async def chat_structured(
        self, system, user, schema, timeout_s, temperature
    ):
        self.fidelity_calls += 1
        result = self._fidelities.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


async def test_fidelity_problem_none_when_faithful():
    client = FakeClient(fidelities=[{"faithful": True, "problems": []}])
    assert await _fidelity_problem(client, "req", "yaml", 30, 0.2) is None


async def test_fidelity_problem_lists_unmet_requirements():
    client = FakeClient(
        fidelities=[
            {
                "faithful": False,
                "problems": ["missing after-sunset condition", "wrong trigger"],
            }
        ]
    )
    problem = await _fidelity_problem(client, "req", "yaml", 30, 0.2)
    assert problem is not None
    assert "after-sunset" in problem
    assert "wrong trigger" in problem


async def test_fidelity_problem_degrades_on_transport_error():
    """A dead fidelity call must never block an otherwise-valid draft."""
    client = FakeClient(fidelities=[OllamaError("boom")])
    assert await _fidelity_problem(client, "req", "yaml", 30, 0.2) is None


async def test_fidelity_problem_faithful_false_but_no_problems():
    """Unfaithful with an empty problem list is treated as a pass."""
    client = FakeClient(fidelities=[{"faithful": False, "problems": []}])
    assert await _fidelity_problem(client, "req", "yaml", 30, 0.2) is None


_TRIGGER = [
    {"platform": "state", "entity_id": "binary_sensor.garage_door", "to": "on"}
]
_ACTION = [
    {"service": "light.turn_on", "target": {"entity_id": "light.kitchen_lights"}}
]
_NIGHT_CONDITION = [
    {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
]


def _draft_result(config):
    return {
        "possible": True,
        "reason": "",
        "alias": "Garage light",
        "summary": "Turns on the kitchen light",
        "explanation": "",
        "config": config,
    }


async def test_draft_self_corrects_when_clause_dropped(hass):
    """A draft that drops the night condition is rejected, then re-drafted."""
    hass.states.async_set("binary_sensor.garage_door", "off")
    hass.states.async_set("light.kitchen_lights", "off")
    hass.states.async_set("sun.sun", "below_horizon")
    await hass.async_block_till_done()

    incomplete = _draft_result({"trigger": _TRIGGER, "action": _ACTION})
    complete = _draft_result(
        {
            "trigger": _TRIGGER,
            "condition": copy.deepcopy(_NIGHT_CONDITION),
            "action": _ACTION,
        }
    )
    client = FakeClient(
        drafts=[incomplete, complete],
        fidelities=[
            {"faithful": False, "problems": ["missing after-sunset condition"]},
            {"faithful": True, "problems": []},
        ],
    )

    draft = await draft_automation(
        hass,
        client,
        "Turn on kitchen light when garage door opens after sunset",
        source="test",
        timeout_s=60,
        temperature=0.2,
    )

    assert client.draft_calls == 2
    assert client.fidelity_calls == 2
    assert draft.config.get("condition"), "corrected draft must keep the condition"


async def test_draft_repairs_invalid_sun_time_condition(hass):
    """The regression from 0.11.0: a time+sunset condition is repaired
    in code and the draft validates instead of hard-failing."""
    hass.states.async_set("binary_sensor.garage_door", "off")
    hass.states.async_set("light.kitchen_lights", "off")
    hass.states.async_set("sun.sun", "below_horizon")
    await hass.async_block_till_done()

    # The exact clause the live model produced and could not self-correct.
    with_bad_time = _draft_result(
        {
            "trigger": _TRIGGER,
            "condition": [
                {"condition": "time", "after": "sunset", "before": "sunrise"}
            ],
            "action": _ACTION,
        }
    )
    client = FakeClient(
        drafts=[with_bad_time],
        fidelities=[{"faithful": True, "problems": []}],
    )

    draft = await draft_automation(
        hass,
        client,
        "Turn on kitchen light when garage door opens after sunset",
        source="test",
        timeout_s=60,
        temperature=0.2,
    )

    # Repaired before validation — no self-correction round needed.
    assert client.draft_calls == 1
    assert draft.config["condition"] == [
        {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
    ]


async def test_draft_repairs_full_0_11_1_payload_end_to_end(hass):
    """The whole config from the 0.11.1 live failure (bare `and`, empty
    sun-word time conditions) is repaired and passes HA validation."""
    hass.states.async_set("cover.garage_door", "closed")
    hass.states.async_set("light.kitchen", "off")
    hass.states.async_set("sun.sun", "below_horizon")
    await hass.async_block_till_done()

    from_log = _draft_result(
        {
            "mode": "restart",
            "triggers": [
                {"trigger": "state", "entity_id": "cover.garage_door", "to": "open"}
            ],
            "conditions": [
                {"condition": "and"},
                {
                    "condition": "state",
                    "entity_id": "sun.sun",
                    "state": "below_horizon",
                },
                {"condition": "time", "after": "sunset"},
                {"condition": "time", "before": "sunrise"},
            ],
            "actions": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
    )
    client = FakeClient(
        drafts=[from_log], fidelities=[{"faithful": True, "problems": []}]
    )

    draft = await draft_automation(
        hass,
        client,
        "turn on kitchen light when garage door opens after sunset but before sunrise",
        source="test",
        timeout_s=60,
        temperature=0.2,
    )

    assert client.draft_calls == 1
    assert draft.config["conditions"] == [
        {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
    ]


async def test_draft_accepts_faithful_first_try(hass):
    """A faithful draft passes on the first attempt with one fidelity call."""
    hass.states.async_set("binary_sensor.garage_door", "off")
    hass.states.async_set("light.kitchen_lights", "off")
    hass.states.async_set("sun.sun", "below_horizon")
    await hass.async_block_till_done()

    complete = _draft_result(
        {
            "trigger": _TRIGGER,
            "condition": copy.deepcopy(_NIGHT_CONDITION),
            "action": _ACTION,
        }
    )
    client = FakeClient(
        drafts=[complete], fidelities=[{"faithful": True, "problems": []}]
    )

    draft = await draft_automation(
        hass,
        client,
        "Turn on kitchen light when garage door opens after sunset",
        source="test",
        timeout_s=60,
        temperature=0.2,
    )

    assert client.draft_calls == 1
    assert client.fidelity_calls == 1
    assert draft.alias == "Garage light"


class ProbeClient:
    """Returns one canned draft response for probe_draft_quality."""

    model = "probe-model"

    def __init__(self, response):
        self._response = response
        self.calls = 0

    async def chat_structured(self, system, user, schema, timeout_s, temperature):
        self.calls += 1
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


_FIXTURE = {
    "request": "Turn on the kitchen light when the garage door opens after sunset",
    "inventory": [
        ("cover.garage_door", "Garage Door"),
        ("light.kitchen", "Kitchen Light"),
    ],
}


def _cfg(**extra):
    base = {
        "trigger": [
            {"platform": "state", "entity_id": "cover.garage_door", "to": "open"}
        ],
        "action": [
            {"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
        ],
    }
    base.update(extra)
    return base


async def test_probe_clean_draft(hass):
    """A valid config needing zero repairs scores clean + passed."""
    client = ProbeClient(
        {
            "possible": True,
            "alias": "Garage night light",
            "config": _cfg(
                condition=[
                    {
                        "condition": "state",
                        "entity_id": "sun.sun",
                        "state": "below_horizon",
                    }
                ]
            ),
        }
    )
    out = await probe_draft_quality(hass, client, _FIXTURE, 30, 0.2)
    assert out["passed"] is True
    assert out["clean"] is True
    assert out["repairs"] == 0


async def test_probe_draft_needing_repair_is_passed_not_clean(hass):
    """A config with the invalid time+sunset clause passes only after repair."""
    client = ProbeClient(
        {
            "possible": True,
            "alias": "x",
            "config": _cfg(
                condition=[
                    {"condition": "time", "after": "sunset", "before": "sunrise"}
                ]
            ),
        }
    )
    out = await probe_draft_quality(hass, client, _FIXTURE, 30, 0.2)
    assert out["passed"] is True
    assert out["clean"] is False
    assert out["repairs"] >= 1


async def test_probe_refusal(hass):
    client = ProbeClient({"possible": False, "reason": "no garage door entity"})
    out = await probe_draft_quality(hass, client, _FIXTURE, 30, 0.2)
    assert out["passed"] is False
    assert out["possible"] is False
    assert out["note"].startswith("refused")


async def test_probe_invented_entity(hass):
    client = ProbeClient(
        {
            "possible": True,
            "alias": "x",
            "config": _cfg(
                action=[
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.bathroom"},
                    }
                ]
            ),
        }
    )
    out = await probe_draft_quality(hass, client, _FIXTURE, 30, 0.2)
    assert out["passed"] is False
    assert "invented" in out["note"]


async def test_probe_invalid_config(hass):
    client = ProbeClient(
        {
            "possible": True,
            "alias": "x",
            "config": {
                "trigger": [{"platform": "no_such_platform_xyz"}],
                "action": [
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.kitchen"},
                    }
                ],
            },
        }
    )
    out = await probe_draft_quality(hass, client, _FIXTURE, 30, 0.2)
    assert out["passed"] is False
    assert "invalid" in out["note"]
