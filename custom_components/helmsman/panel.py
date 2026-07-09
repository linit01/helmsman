"""Sidebar panel registration for Helmsman (MVP-3).

Serves the build-free vanilla JS panel from frontend/ and registers it
as an admin-only custom sidebar panel. The sidebar title carries the
needs-attention count — "Helmsman (2)" — updated after each audit.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, PANEL_JS_VERSION, PANEL_STATIC_BASE, PANEL_URL_PATH

_LOGGER = logging.getLogger(__name__)


def _panel_kwargs(attention_count: int) -> dict:
    return {
        "component_name": "custom",
        "sidebar_title": (
            f"Helmsman ({attention_count})" if attention_count else "Helmsman"
        ),
        "sidebar_icon": "mdi:ship-wheel",
        "frontend_url_path": PANEL_URL_PATH,
        "require_admin": True,
        "config": {
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
    }


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
        hass, **_panel_kwargs(domain_data.get("sidebar_count", 0))
    )


@callback
def async_update_sidebar_count(hass: HomeAssistant, count: int) -> None:
    """Show the needs-attention count in the sidebar title.

    The startup audit runs before the panel registers, so the count is
    recorded unconditionally — initial registration reads it back.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("sidebar_count") == count:
        return
    domain_data["sidebar_count"] = count
    if PANEL_URL_PATH not in hass.data.get(frontend.DATA_PANELS, {}):
        return
    frontend.async_register_built_in_panel(
        hass, **_panel_kwargs(count), update=True
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel on unload."""
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
