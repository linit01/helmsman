"""Collect automation configuration and runtime state from Home Assistant.

Runs entirely in-process: reads the automation entities' raw_config (the same
config the automation editor operates on), plus the state machine and entity
registry. Read-only by design.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.util import dt as dt_util

from .models import AutomationInfo

_LOGGER = logging.getLogger(__name__)

AUTOMATION_DOMAIN = "automation"

# Keys whose values (string or list) are entity references.
_ENTITY_KEYS = {"entity_id", "entities"}

# Conservative pattern for entity IDs embedded in templates/strings,
# restricted to real HA domains to limit false positives.
_ENTITY_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.\"'/])"
    r"(?:alarm_control_panel|automation|binary_sensor|button|calendar|camera|"
    r"climate|counter|cover|device_tracker|event|fan|group|humidifier|"
    r"input_boolean|input_button|input_datetime|input_number|input_select|"
    r"input_text|light|lock|media_player|number|person|remote|scene|script|"
    r"select|sensor|siren|sun|switch|timer|todo|update|vacuum|valve|"
    r"water_heater|weather|zone)"
    r"\.[a-z0-9_]+"
    r"(?![A-Za-z0-9_])"
)


def _collect_entity_values(value: Any, found: set[str]) -> None:
    """Collect entity IDs from an entity_id-style value (str or list)."""
    if isinstance(value, str):
        if "." in value and " " not in value and "{" not in value:
            found.add(value)
        else:
            found.update(m.group(0) for m in _ENTITY_ID_RE.finditer(value))
    elif isinstance(value, list):
        for item in value:
            _collect_entity_values(item, found)


def _walk_config(node: Any, found: set[str]) -> None:
    """Recursively extract referenced entity IDs from an automation config."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _ENTITY_KEYS:
                _collect_entity_values(value, found)
            else:
                _walk_config(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_config(item, found)
    elif isinstance(node, str):
        found.update(m.group(0) for m in _ENTITY_ID_RE.finditer(node))


def _parse_last_triggered(value: Any) -> datetime | None:
    """Normalize the last_triggered attribute to an aware datetime."""
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if isinstance(value, str):
        return dt_util.parse_datetime(value)
    return None


def collect_automations(hass: HomeAssistant) -> list[AutomationInfo]:
    """Snapshot all automations with config and runtime state."""
    component = hass.data.get(DATA_INSTANCES, {}).get(AUTOMATION_DOMAIN)
    infos: list[AutomationInfo] = []

    for state in hass.states.async_all(AUTOMATION_DOMAIN):
        raw_config: dict | None = None
        if component is not None:
            entity = component.get_entity(state.entity_id)
            raw_config = getattr(entity, "raw_config", None)
            if raw_config is not None and not isinstance(raw_config, dict):
                raw_config = None

        referenced: set[str] = set()
        if raw_config:
            _walk_config(raw_config, referenced)
        # An automation referencing itself is normal (e.g. traces/UI links).
        referenced.discard(state.entity_id)

        infos.append(
            AutomationInfo(
                entity_id=state.entity_id,
                alias=state.attributes.get("friendly_name", state.entity_id),
                automation_id=state.attributes.get("id"),
                state=state.state,
                last_triggered=_parse_last_triggered(
                    state.attributes.get("last_triggered")
                ),
                mode=state.attributes.get("mode", "single"),
                raw_config=raw_config,
                referenced_entities=referenced,
            )
        )

    if component is None:
        _LOGGER.warning(
            "Automation entity component not found in hass.data; "
            "raw configs unavailable, rules limited to state-based checks"
        )

    return infos
