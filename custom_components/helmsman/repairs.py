"""Repairs fix flows: route users from a finding to the Helmsman panel.

Findings are surfaced as fixable Repairs issues; the fix flow explains
the finding and links to the panel where Review flagged can propose and
apply a validated change. Completing the flow closes the repair — the
next audit re-creates it if the problem remains.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult


class HelmsmanRepairFlow(RepairsFlow):
    """Explain the finding and point at the panel."""

    def __init__(self, data: dict[str, Any] | None) -> None:
        """Hold the finding details for the dialog text."""
        self._data = data or {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First step receives the issue data as user_input — ignore it.

        Repairs passes the issue's data dict into the first step, so a
        single-step flow that checks `user_input is not None` completes
        instantly without ever showing the form. Delegate to a second
        step, per the core convention.
        """
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the finding and the panel link; submitting closes it."""
        if user_input is not None:
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "detail": str(self._data.get("detail", "")),
                "automation": str(self._data.get("automation", "")),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for any Helmsman issue."""
    return HelmsmanRepairFlow(data)
