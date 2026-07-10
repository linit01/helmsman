"""Tests for the draft fidelity gate (creator.py).

The structural / entity-existence / HA-validation gates only prove a draft
is well-formed. These tests cover the semantic gate that catches a draft
which is well-formed but drops a clause of the request or triggers on a
real-but-wrong entity.
"""

import copy

import pytest

from custom_components.helmsman.creator import _fidelity_problem, draft_automation
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
