"""Config flow for Helmsman."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_MODEL,
    CONF_OLLAMA_URL,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_STALE_DAYS,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_STALE_DAYS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the shared user/options schema with given defaults."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_OLLAMA_URL,
                default=defaults.get(CONF_OLLAMA_URL, DEFAULT_OLLAMA_URL),
            ): str,
            vol.Optional(
                CONF_MODEL,
                default=defaults.get(CONF_MODEL, DEFAULT_MODEL),
            ): str,
            vol.Required(
                CONF_SCAN_INTERVAL_HOURS,
                default=defaults.get(
                    CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
            vol.Required(
                CONF_STALE_DAYS,
                default=defaults.get(CONF_STALE_DAYS, DEFAULT_STALE_DAYS),
            ): vol.All(vol.Coerce(int), vol.Range(min=7, max=365)),
        }
    )


async def _validate_ollama(hass: HomeAssistant, url: str) -> bool:
    """Check that an Ollama server answers at the given URL."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"{url.rstrip('/')}/api/tags", timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            return resp.status == 200
    except (aiohttp.ClientError, TimeoutError):
        return False


class HelmsmanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of Helmsman."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the user setup step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            url = (user_input.get(CONF_OLLAMA_URL) or "").strip()
            user_input[CONF_OLLAMA_URL] = url
            # Ollama is unused in MVP-1 (rules-only audits); leaving the URL
            # blank skips validation and defers LLM setup entirely.
            if url and not await _validate_ollama(self.hass, url):
                errors[CONF_OLLAMA_URL] = "cannot_connect"
            else:
                return self.async_create_entry(title="Helmsman", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HelmsmanOptionsFlow:
        """Return the options flow handler."""
        return HelmsmanOptionsFlow()


class HelmsmanOptionsFlow(config_entries.OptionsFlow):
    """Handle option changes after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the options step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = (user_input.get(CONF_OLLAMA_URL) or "").strip()
            user_input[CONF_OLLAMA_URL] = url
            if url and not await _validate_ollama(self.hass, url):
                errors[CONF_OLLAMA_URL] = "cannot_connect"
            else:
                return self.async_create_entry(title="", data=user_input)

        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(user_input or defaults),
            errors=errors,
        )
