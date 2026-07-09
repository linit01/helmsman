"""Apply approved suggestions and manage config snapshots (MVP-3).

Writes go through automations.yaml — the same file Home Assistant's own
automation editor manages — and only after the current config has been
snapshotted to integration storage. Every write is triggered by an
explicit user approval in the panel; nothing here runs autonomously.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as yaml_dump, load_yaml

from .const import DOMAIN, MAX_SNAPSHOTS_PER_AUTOMATION

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}_snapshots"
STORAGE_VERSION = 1
AUTOMATIONS_YAML = "automations.yaml"


class SnapshotStore:
    """Last-N config snapshots per automation, persisted to HA storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the backing Store; call async_load before use."""
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"snapshots": {}}

    async def async_load(self) -> None:
        """Load persisted snapshots."""
        data = await self._store.async_load()
        if isinstance(data, dict) and "snapshots" in data:
            self._data = data

    async def async_add(
        self, entity_id: str, config: dict, reason: str
    ) -> None:
        """Push a snapshot for an automation, newest first, capped."""
        entry = {
            "saved_at": dt_util.utcnow().isoformat(),
            "reason": reason,
            "config": config,
        }
        history: list = self._data["snapshots"].setdefault(entity_id, [])
        history.insert(0, entry)
        del history[MAX_SNAPSHOTS_PER_AUTOMATION:]
        await self._store.async_save(self._data)

    def latest(self, entity_id: str) -> dict | None:
        """Most recent snapshot for an automation, or None."""
        history = self._data["snapshots"].get(entity_id) or []
        return history[0] if history else None

    def summaries(self) -> list[dict[str, Any]]:
        """Per-automation snapshot summaries for the panel."""
        return [
            {
                "automation": entity_id,
                "count": len(history),
                "latest_saved_at": history[0]["saved_at"],
                "latest_reason": history[0]["reason"],
            }
            for entity_id, history in self._data["snapshots"].items()
            if history
        ]


def _read_automations(path: str) -> list[dict]:
    """Read automations.yaml as a list (executor)."""
    if not os.path.isfile(path):
        raise HomeAssistantError(
            f"{AUTOMATIONS_YAML} not found; Helmsman can only apply changes "
            "to automations managed there (the automation editor's file)"
        )
    content = load_yaml(path)
    if content is None:
        return []
    if not isinstance(content, list):
        raise HomeAssistantError(f"{AUTOMATIONS_YAML} is not a list")
    return content


def _write_automations(path: str, items: list[dict]) -> None:
    """Atomically write automations.yaml (executor)."""
    dirname = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".helmsman_", suffix=".yaml", dir=dirname
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(yaml_dump(items))
        # mkstemp files are 0600; keep the original file's permissions.
        os.chmod(tmp_path, os.stat(path).st_mode & 0o7777)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


async def async_apply_config(
    hass: HomeAssistant,
    snapshots: SnapshotStore,
    entity_id: str,
    automation_id: str,
    new_config: dict,
    reason: str,
) -> None:
    """Snapshot the current config, write the new one, reload automations."""
    path = hass.config.path(AUTOMATIONS_YAML)
    items = await hass.async_add_executor_job(_read_automations, path)

    index = next(
        (
            i
            for i, item in enumerate(items)
            if isinstance(item, dict)
            and str(item.get("id")) == str(automation_id)
        ),
        None,
    )
    if index is None:
        raise HomeAssistantError(
            f"Automation id {automation_id} is not in {AUTOMATIONS_YAML}; "
            "Helmsman cannot apply changes to package/include-managed "
            "automations yet"
        )

    await snapshots.async_add(entity_id, dict(items[index]), reason)

    replacement = dict(new_config)
    replacement["id"] = str(automation_id)
    items[index] = replacement

    await hass.async_add_executor_job(_write_automations, path, items)
    await hass.services.async_call("automation", "reload", blocking=True)
    _LOGGER.info("Applied %s change to %s (id %s)", reason, entity_id, automation_id)


async def async_create_automation(
    hass: HomeAssistant, config: dict, disabled: bool = True
) -> str:
    """Append a new automation to automations.yaml and reload.

    Returns the entity_id of the new automation. New automations are
    turned off right after creation (disabled=True) so the user reviews
    live behavior on their own schedule.
    """
    path = hass.config.path(AUTOMATIONS_YAML)
    items = await hass.async_add_executor_job(_read_automations, path)

    new_id = uuid4().hex
    new_item = dict(config)
    new_item["id"] = new_id
    items.append(new_item)

    await hass.async_add_executor_job(_write_automations, path, items)
    await hass.services.async_call("automation", "reload", blocking=True)

    entity_id = next(
        (
            state.entity_id
            for state in hass.states.async_all("automation")
            if state.attributes.get("id") == new_id
        ),
        None,
    )
    if entity_id is None:
        raise HomeAssistantError(
            "Automation was written but did not appear after reload; "
            f"check {AUTOMATIONS_YAML} and the Home Assistant log"
        )
    if disabled:
        await hass.services.async_call(
            "automation", "turn_off", {"entity_id": entity_id}, blocking=True
        )
    _LOGGER.info("Created automation %s (id %s, disabled=%s)", entity_id, new_id, disabled)
    return entity_id


async def async_rollback(
    hass: HomeAssistant, snapshots: SnapshotStore, entity_id: str
) -> None:
    """Restore the most recent snapshot for an automation.

    The config being replaced is snapshotted first, so a second rollback
    rolls forward again — nothing is ever lost.
    """
    snapshot = snapshots.latest(entity_id)
    if snapshot is None:
        raise HomeAssistantError(f"No snapshot stored for {entity_id}")
    config = snapshot["config"]
    automation_id = config.get("id")
    if not automation_id:
        raise HomeAssistantError(
            f"Snapshot for {entity_id} has no automation id; cannot restore"
        )
    await async_apply_config(
        hass, snapshots, entity_id, automation_id, config, "rollback"
    )
