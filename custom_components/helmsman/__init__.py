"""Helmsman — AI-assisted automation auditor for Home Assistant.

MVP-1: deterministic rules-pass audits surfaced as Repairs issues and a
findings sensor. Read-only; never modifies automations.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, PLATFORMS, SERVICE_RUN_AUDIT
from .coordinator import HelmsmanCoordinator

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

    if not hass.services.has_service(DOMAIN, SERVICE_RUN_AUDIT):
        hass.services.async_register(DOMAIN, SERVICE_RUN_AUDIT, _handle_run_audit)

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
    return unload_ok
