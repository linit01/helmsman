"""Contract smoke tests for the HA internals Helmsman relies on.

Helmsman deliberately builds on internal HA surfaces (the automation
config validator, raw_config access, panel registration). These tests
assert those contracts against the installed homeassistant package; CI
runs them against the current release and the beta channel so drift is
caught before it reaches users. The validator-signature change that
silently broke every LLM proposal (fixed in 0.5.7) is the class of
failure this file exists to catch.
"""

from homeassistant.setup import async_setup_component

from custom_components.helmsman.collector import (
    collect_automations,
    extract_entity_references,
)
from custom_components.helmsman.reviewer import ha_validation_error

VALID_CONFIG = {
    "alias": "Contract test",
    "trigger": [
        {
            "platform": "state",
            "entity_id": "binary_sensor.contract",
            "to": "on",
        }
    ],
    "action": [
        {
            "service": "homeassistant.turn_on",
            "target": {"entity_id": "light.contract"},
        }
    ],
}

INVALID_CONFIG = {
    "alias": "Broken contract test",
    "trigger": [{"platform": "no_such_trigger_platform_xyz"}],
    "action": [{"service": "homeassistant.turn_on"}],
}


async def test_validator_accepts_valid_config(hass):
    """Our adapted validator call must validate a known-good config."""
    assert await ha_validation_error(hass, VALID_CONFIG) is None


async def test_validator_rejects_invalid_config(hass):
    """A known-bad config must produce an error string, not a crash."""
    error = await ha_validation_error(hass, INVALID_CONFIG)
    assert error, "invalid config must yield a non-empty error"


async def test_automation_surfaces_raw_config(hass):
    """The collector's in-process raw_config read must keep working."""
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": [{"id": "contract_1", **VALID_CONFIG}]},
    )
    await hass.async_block_till_done()

    infos = collect_automations(hass)
    assert len(infos) == 1
    info = infos[0]
    assert info.automation_id == "contract_1"
    assert isinstance(info.raw_config, dict), (
        "raw_config surface moved — collector degrades to state-only rules"
    )
    assert "binary_sensor.contract" in info.referenced_entities
    assert hass.services.has_service("automation", "reload")


def test_entity_extraction_ignores_service_calls():
    """Service calls must not be mistaken for entity references."""
    refs = extract_entity_references(
        {
            "trigger": [{"platform": "state", "entity_id": "sensor.a"}],
            "action": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.b"},
                },
                {
                    "action": "switch.toggle",
                    "target": {"entity_id": "switch.c"},
                },
            ],
        }
    )
    assert refs == {"sensor.a", "light.b", "switch.c"}


def test_panel_registration_apis_exist():
    """The (internal) frontend/http surfaces the panel depends on."""
    from homeassistant.components import frontend
    from homeassistant.components.http import StaticPathConfig

    assert callable(frontend.async_register_built_in_panel)
    assert callable(frontend.async_remove_panel)
    assert hasattr(frontend, "DATA_PANELS")
    assert StaticPathConfig is not None
