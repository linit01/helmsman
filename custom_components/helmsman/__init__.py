"""Helmsman — AI-assisted automation helper for Home Assistant.

MVP-3: rules + LLM audits (Repairs issues, findings/suggestions sensors)
plus a sidebar approval panel. Writes happen only when the user approves a
suggestion in the panel, always behind a config snapshot with rollback.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
    PLATFORMS,
    SERVICE_DRAFT_AUTOMATION,
    SERVICE_REVIEW_AUTOMATION,
    SERVICE_RUN_AUDIT,
)
from .coordinator import HelmsmanCoordinator
from .panel import async_register_panel, async_remove_panel
from .websocket import async_register_commands

REVIEW_AUTOMATION_SCHEMA = vol.Schema(
    {vol.Optional("entity_id"): cv.entity_id}
)
DRAFT_AUTOMATION_SCHEMA = vol.Schema(
    {vol.Required("description"): cv.string}
)

_LOGGER = logging.getLogger(__name__)

HelmsmanConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: HelmsmanConfigEntry) -> bool:
    """Set up Helmsman from a config entry."""
    # Findings live in the Helmsman panel now, not Settings -> Repairs;
    # purge any issues left behind by pre-0.9 versions.
    issue_registry = ir.async_get(hass)
    for issue_domain, issue_id in [
        key for key in issue_registry.issues if key[0] == DOMAIN
    ]:
        ir.async_delete_issue(hass, issue_domain, issue_id)

    coordinator = HelmsmanCoordinator(hass, entry)
    await coordinator.snapshots.async_load()
    await coordinator.dismissed.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("ws_registered"):
        async_register_commands(hass)
        domain_data["ws_registered"] = True
    await async_register_panel(hass)

    async def _handle_run_audit(call: ServiceCall) -> None:
        """Handle the helmsman.run_audit service."""
        _LOGGER.info("Manual audit requested via %s.%s", DOMAIN, SERVICE_RUN_AUDIT)
        await coordinator.async_run_manual_audit()

    async def _handle_review_automation(call: ServiceCall) -> None:
        """Handle the helmsman.review_automation service."""
        entity_id = call.data.get("entity_id")
        _LOGGER.info(
            "LLM review requested via %s.%s (%s)",
            DOMAIN,
            SERVICE_REVIEW_AUTOMATION,
            entity_id or "all flagged automations",
        )
        coordinator.async_start_review(entity_id)

    if not hass.services.has_service(DOMAIN, SERVICE_RUN_AUDIT):
        hass.services.async_register(DOMAIN, SERVICE_RUN_AUDIT, _handle_run_audit)
    if not hass.services.has_service(DOMAIN, SERVICE_REVIEW_AUTOMATION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REVIEW_AUTOMATION,
            _handle_review_automation,
            schema=REVIEW_AUTOMATION_SCHEMA,
        )

    async def _handle_draft_automation(call: ServiceCall) -> None:
        """Handle the helmsman.draft_automation service."""
        draft = await coordinator.async_draft(
            call.data["description"], "describe"
        )
        _LOGGER.info(
            "Draft %s (%s) held for review in the Helmsman panel",
            draft.draft_id,
            draft.alias,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DRAFT_AUTOMATION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DRAFT_AUTOMATION,
            _handle_draft_automation,
            schema=DRAFT_AUTOMATION_SCHEMA,
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
        async_remove_panel(hass)
        if hass.services.has_service(DOMAIN, SERVICE_RUN_AUDIT):
            hass.services.async_remove(DOMAIN, SERVICE_RUN_AUDIT)
        if hass.services.has_service(DOMAIN, SERVICE_REVIEW_AUTOMATION):
            hass.services.async_remove(DOMAIN, SERVICE_REVIEW_AUTOMATION)
        if hass.services.has_service(DOMAIN, SERVICE_DRAFT_AUTOMATION):
            hass.services.async_remove(DOMAIN, SERVICE_DRAFT_AUTOMATION)
    return unload_ok
