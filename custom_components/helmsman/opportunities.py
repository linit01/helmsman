"""Proactive new-automation opportunities (MVP-4).

Deterministic registry scans that notice unlinked device patterns — no
LLM involved in noticing. Each opportunity carries a suggested
plain-language description; "Draft it" in the panel feeds that through
the same creator pipeline as a typed request.

MVP-4 ships one pattern: a motion sensor and lights in the same area
with no automation referencing the motion sensor.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .models import AutomationInfo

_LOGGER = logging.getLogger(__name__)

MAX_OPPORTUNITIES = 8

DISMISS_STORAGE_KEY = f"{DOMAIN}_dismissed_opportunities"
DISMISS_STORAGE_VERSION = 1


class DismissStore:
    """Persisted set of dismissed opportunity keys."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the backing Store; call async_load before use."""
        self._store: Store = Store(
            hass, DISMISS_STORAGE_VERSION, DISMISS_STORAGE_KEY
        )
        self._keys: set[str] = set()

    async def async_load(self) -> None:
        """Load persisted dismissals."""
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("keys"), list):
            self._keys = set(data["keys"])

    async def async_dismiss(self, key: str) -> None:
        """Persist one dismissal."""
        self._keys.add(key)
        await self._store.async_save({"keys": sorted(self._keys)})

    def is_dismissed(self, key: str) -> bool:
        """Whether an opportunity key has been dismissed."""
        return key in self._keys


def _entity_area(
    entry: er.RegistryEntry, devices: dr.DeviceRegistry
) -> str | None:
    """Area of an entity, falling back to its device's area."""
    if entry.area_id:
        return entry.area_id
    if entry.device_id:
        device = devices.async_get(entry.device_id)
        if device:
            return device.area_id
    return None


def scan_opportunities(
    hass: HomeAssistant, automations: list[AutomationInfo]
) -> list[dict[str, Any]]:
    """Find unlinked motion-sensor/light pairs, grouped by area."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)

    # Devices that expose a camera entity: their motion sensors are the
    # camera's own motion detection, labeled and ranked below standalone
    # sensors (still useful — e.g. outdoor lighting — but often noise).
    camera_devices = {
        entry.device_id
        for entry in entities.entities.values()
        if entry.domain == "camera" and entry.device_id
    }

    lights_by_area: dict[str, list[str]] = {}
    motion_by_area: dict[str, list[str]] = {}
    camera_based: set[str] = set()
    for entry in entities.entities.values():
        if entry.disabled_by is not None:
            continue
        area = _entity_area(entry, devices)
        if area is None:
            continue
        if entry.domain == "light":
            lights_by_area.setdefault(area, []).append(entry.entity_id)
        elif entry.domain == "binary_sensor" and "motion" in {
            str(entry.device_class or ""),
            str(entry.original_device_class or ""),
        }:
            motion_by_area.setdefault(area, []).append(entry.entity_id)
            if entry.device_id in camera_devices:
                camera_based.add(entry.entity_id)

    referenced: set[str] = set()
    for info in automations:
        referenced |= info.referenced_entities

    def _name(entity_id: str) -> str:
        state = hass.states.get(entity_id)
        if state:
            return str(state.attributes.get("friendly_name") or entity_id)
        return entity_id

    opportunities: list[dict[str, Any]] = []
    for area_id, motions in sorted(motion_by_area.items()):
        lights = lights_by_area.get(area_id)
        if not lights:
            continue
        area = areas.async_get_area(area_id)
        area_name = area.name if area else area_id
        for motion in sorted(motions):
            if motion in referenced:
                continue
            light_names = ", ".join(_name(l) for l in sorted(lights)[:3])
            motion_name = _name(motion)
            motion_ref = (
                motion_name
                if motion_name == motion
                else f"{motion_name} ({motion})"
            )
            is_camera = motion in camera_based
            opportunities.append(
                {
                    "key": f"motion_light:{motion}",
                    "title": (
                        f"{motion_name} and the lights in {area_name} "
                        "aren't linked"
                    ),
                    "detail": (
                        "Camera motion can drive lighting too — a common "
                        "outdoor pattern."
                        if is_camera
                        else "Motion-activated lighting is the usual pattern here."
                    ),
                    "camera_based": is_camera,
                    "suggested_description": (
                        f"Turn on {light_names} when {motion_ref} detects "
                        "motion, and turn them off 5 minutes after motion "
                        "stops."
                    ),
                }
            )
    # Standalone motion sensors first; camera-based ones after (and they
    # lose out when the cap bites).
    opportunities.sort(key=lambda opp: opp["camera_based"])
    return opportunities[:MAX_OPPORTUNITIES]
