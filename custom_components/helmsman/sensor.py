"""Findings sensor for Helmsman."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_AUTOMATIONS_AUDITED,
    ATTR_FINDINGS,
    ATTR_LAST_AUDIT,
    DOMAIN,
    MAX_FINDINGS_IN_ATTRIBUTES,
)
from .coordinator import HelmsmanCoordinator
from .models import Severity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Helmsman sensor platform."""
    coordinator: HelmsmanCoordinator = entry.runtime_data
    async_add_entities([HelmsmanFindingsSensor(coordinator, entry.entry_id)])


class HelmsmanFindingsSensor(
    CoordinatorEntity[HelmsmanCoordinator], SensorEntity
):
    """Total audit findings, with details in attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "findings"
    _attr_icon = "mdi:ship-wheel"
    _attr_native_unit_of_measurement = "findings"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: HelmsmanCoordinator, entry_id: str) -> None:
        """Initialize the findings sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_findings"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Helmsman",
            "manufacturer": "Beacon Ecosystem",
            "model": "Automation Auditor",
        }

    @property
    def native_value(self) -> int | None:
        """Total number of findings from the last audit."""
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.findings)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose per-severity counts and finding details."""
        report = self.coordinator.data
        if report is None:
            return {}
        return {
            "errors": report.count(Severity.ERROR),
            "warnings": report.count(Severity.WARNING),
            "info": report.count(Severity.INFO),
            ATTR_AUTOMATIONS_AUDITED: report.automations_audited,
            ATTR_LAST_AUDIT: (
                report.finished_at.isoformat() if report.finished_at else None
            ),
            ATTR_FINDINGS: [
                f.as_dict()
                for f in report.findings[:MAX_FINDINGS_IN_ATTRIBUTES]
            ],
        }
