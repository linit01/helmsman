"""WebSocket API backing the Helmsman panel (MVP-3).

All commands are admin-only. Apply/rollback are the only paths in the
integration that write to automations, and both go through the snapshot
store in applier.py.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.yaml import dump as yaml_dump

from .collector import collect_automations
from .const import DOMAIN
from .coordinator import HelmsmanCoordinator
from .models import Severity


def _coordinator(hass: HomeAssistant) -> HelmsmanCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is not None:
            return coordinator
    raise HomeAssistantError("Helmsman is not set up")


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register all Helmsman WebSocket commands (idempotent per restart)."""
    websocket_api.async_register_command(hass, ws_report)
    websocket_api.async_register_command(hass, ws_run_audit)
    websocket_api.async_register_command(hass, ws_review)
    websocket_api.async_register_command(hass, ws_apply)
    websocket_api.async_register_command(hass, ws_dismiss)
    websocket_api.async_register_command(hass, ws_rollback)
    websocket_api.async_register_command(hass, ws_draft)
    websocket_api.async_register_command(hass, ws_create_draft)
    websocket_api.async_register_command(hass, ws_dismiss_draft)
    websocket_api.async_register_command(hass, ws_dismiss_opportunity)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/report"})
@websocket_api.async_response
async def ws_report(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Everything the panel needs to render, in one call."""
    coordinator = _coordinator(hass)
    report = coordinator.data
    infos = {a.entity_id: a for a in collect_automations(hass)}

    suggestions = []
    for suggestion in coordinator.suggestions.values():
        info = infos.get(suggestion.automation_entity_id)
        suggestions.append(
            {
                **suggestion.as_dict(),
                "current_yaml": (
                    yaml_dump(info.raw_config).strip()
                    if info and info.raw_config
                    else ""
                ),
                "can_apply": bool(
                    suggestion.improved_config.get("id")
                    and info is not None
                ),
            }
        )

    connection.send_result(
        msg["id"],
        {
            "findings": [
                f.as_dict() for f in (report.findings if report else [])
            ],
            "counts": {
                "errors": report.count(Severity.ERROR) if report else 0,
                "warnings": report.count(Severity.WARNING) if report else 0,
                "info": report.count(Severity.INFO) if report else 0,
            },
            "automations_audited": (
                report.automations_audited if report else 0
            ),
            "last_audit": (
                report.finished_at.isoformat()
                if report and report.finished_at
                else None
            ),
            "last_review": (
                coordinator.last_review.isoformat()
                if coordinator.last_review
                else None
            ),
            "suggestions": suggestions,
            "drafts": [d.as_dict() for d in coordinator.drafts.values()],
            "opportunities": coordinator.opportunities,
            "snapshots": coordinator.snapshots.summaries(),
            "ollama_configured": bool(coordinator.ollama_url),
            "review_in_progress": coordinator.review_in_progress,
            "review_progress": coordinator.review_progress,
            "last_review_note": coordinator.last_review_note,
            "review_notes": list(coordinator.review_notes.values()),
        },
    )


async def _guarded(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    coro,
) -> None:
    """Run a coordinator action, translating errors for the panel."""
    try:
        await coro
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "helmsman_error", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/run_audit"})
@websocket_api.async_response
async def ws_run_audit(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Trigger an audit pass now."""
    coordinator = _coordinator(hass)
    await _guarded(connection, msg, coordinator.async_request_refresh())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/review",
        vol.Optional("entity_id"): str,
    }
)
@websocket_api.async_response
async def ws_review(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Start an LLM review of one automation or all flagged ones.

    The review runs in the background; the panel polls the report for
    progress. Returns immediately.
    """
    coordinator = _coordinator(hass)
    try:
        coordinator.async_start_review(msg.get("entity_id"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "helmsman_error", str(err))
        return
    connection.send_result(msg["id"], {"started": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/apply",
        vol.Required("entity_id"): str,
    }
)
@websocket_api.async_response
async def ws_apply(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Apply a held suggestion after user approval in the panel."""
    coordinator = _coordinator(hass)
    await _guarded(
        connection, msg, coordinator.async_apply_suggestion(msg["entity_id"])
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/dismiss",
        vol.Required("entity_id"): str,
    }
)
@websocket_api.async_response
async def ws_dismiss(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Dismiss a held suggestion."""
    coordinator = _coordinator(hass)
    try:
        coordinator.async_dismiss_suggestion(msg["entity_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "helmsman_error", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/rollback",
        vol.Required("entity_id"): str,
    }
)
@websocket_api.async_response
async def ws_rollback(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Restore the most recent snapshot for an automation."""
    coordinator = _coordinator(hass)
    await _guarded(
        connection, msg, coordinator.async_rollback_automation(msg["entity_id"])
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/draft",
        vol.Required("description"): str,
        vol.Optional("source", default="describe"): str,
    }
)
@websocket_api.async_response
async def ws_draft(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Draft a new automation from a plain-language description."""
    coordinator = _coordinator(hass)
    try:
        draft = await coordinator.async_draft(
            msg["description"], msg["source"]
        )
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "helmsman_error", str(err))
        return
    connection.send_result(msg["id"], {"draft": draft.as_dict()})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create_draft",
        vol.Required("draft_id"): str,
    }
)
@websocket_api.async_response
async def ws_create_draft(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create an approved draft as a real automation (disabled)."""
    coordinator = _coordinator(hass)
    try:
        entity_id = await coordinator.async_create_draft(msg["draft_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "helmsman_error", str(err))
        return
    connection.send_result(msg["id"], {"entity_id": entity_id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/dismiss_draft",
        vol.Required("draft_id"): str,
    }
)
@websocket_api.async_response
async def ws_dismiss_draft(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Drop a draft without creating it."""
    coordinator = _coordinator(hass)
    try:
        coordinator.async_dismiss_draft(msg["draft_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "helmsman_error", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/dismiss_opportunity",
        vol.Required("key"): str,
    }
)
@websocket_api.async_response
async def ws_dismiss_opportunity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persistently dismiss a noticed opportunity."""
    coordinator = _coordinator(hass)
    await _guarded(
        connection, msg, coordinator.async_dismiss_opportunity(msg["key"])
    )
