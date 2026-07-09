"""Sidebar panel registration for Helmsman (MVP-3).

Serves the build-free vanilla JS panel from frontend/ and registers it
as an admin-only custom sidebar panel.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PANEL_JS_VERSION, PANEL_STATIC_BASE, PANEL_URL_PATH

_LOGGER = logging.getLogger(__name__)


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the static path and sidebar panel (safe across reloads)."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    if not domain_data.get("static_registered"):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    PANEL_STATIC_BASE,
                    str(Path(__file__).parent / "frontend"),
                    cache_headers=False,
                )
            ]
        )
        domain_data["static_registered"] = True

    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return

    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="Helmsman",
        sidebar_icon="mdi:ship-wheel",
        frontend_url_path=PANEL_URL_PATH,
        require_admin=True,
        config={
            "_panel_custom": {
                "name": "helmsman-panel",
                "module_url": (
                    f"{PANEL_STATIC_BASE}/helmsman-panel.js"
                    f"?v={PANEL_JS_VERSION}"
                ),
                "embed_iframe": False,
                "trust_external": False,
            }
        },
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel on unload."""
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
