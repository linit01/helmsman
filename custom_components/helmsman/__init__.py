"""Helmsman — AI-assisted automation helper for Home Assistant.

MVP-2: deterministic rules-pass audits surfaced as Repairs issues and a
findings sensor, plus an Ollama review pass that proposes schema-validated
improvements on a suggestions sensor. Read-only; never modifies automations.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    PLATFORMS,
    SERVICE_REVIEW_AUTOMATION,
    SERVICE_RUN_AUDIT,
)
from .coordinator import HelmsmanCoordinator

REVIEW_AUTOMATION_SCHEMA = vol.Schema(
    {vol.Optional("entity_id"): cv.entity_id}
)

_LOGGER = logging.getLogger(__name__)

HelmsmanConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: HelmsmanConfigEntry) -> bool:
    """Set up Helmsman from a config entry."""
    coordinator = HelmsmanCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_run_audit(call: ServiceCall) -> None:
        """Handle the helmsman.run_audit service."""
        _LOGGER.info("Manual audit requested via %s.%s", DOMAIN, SERVICE_RUN_AUDIT)
        await coordinator.async_request_refresh()

    async def _handle_review_automation(call: ServiceCall) -> None:
        """Handle the helmsman.review_automation service."""
        entity_id = call.data.get("entity_id")
        _LOGGER.info(
            "LLM review requested via %s.%s (%s)",
            DOMAIN,
            SERVICE_REVIEW_AUTOMATION,
            entity_id or "all flagged automations",
        )
        await coordinator.async_review_entity(entity_id)

    if not hass.services.has_service(DOMAIN, SERVICE_RUN_AUDIT):
        hass.services.async_register(DOMAIN, SERVICE_RUN_AUDIT, _handle_run_audit)
    if not hass.services.has_service(DOMAIN, SERVICE_REVIEW_AUTOMATION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REVIEW_AUTOMATION,
            _handle_review_automation,
            schema=REVIEW_AUTOMATION_SCHEMA,
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: HelmsmanConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HelmsmanConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.async_clear_all_issues()
        if hass.services.has_service(DOMAIN, SERVICE_RUN_AUDIT):
            hass.services.async_remove(DOMAIN, SERVICE_RUN_AUDIT)
        if hass.services.has_service(DOMAIN, SERVICE_REVIEW_AUTOMATION):
            hass.services.async_remove(DOMAIN, SERVICE_REVIEW_AUTOMATION)
    return unload_ok
