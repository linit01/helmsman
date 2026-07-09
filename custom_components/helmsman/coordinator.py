"""Audit coordinator for Helmsman.

Runs the collector + rules pass on a schedule (or on demand via the
helmsman.run_audit service) and syncs ERROR/WARNING findings to the
Repairs issue registry. Strictly read-only with respect to automations.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .collector import collect_automations
from .const import (
    CONF_SCAN_INTERVAL_HOURS,
    CONF_STALE_DAYS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_STALE_DAYS,
    DOMAIN,
)
from .models import AuditReport, Finding, Severity
from .rules import RuleContext, run_rules

_LOGGER = logging.getLogger(__name__)

_ISSUE_SEVERITY = {
    Severity.ERROR: ir.IssueSeverity.ERROR,
    Severity.WARNING: ir.IssueSeverity.WARNING,
}


class HelmsmanCoordinator(DataUpdateCoordinator[AuditReport]):
    """Coordinates scheduled audits and owns the Repairs issue lifecycle."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator from the config entry."""
        self.entry = entry
        interval_hours = entry.options.get(
            CONF_SCAN_INTERVAL_HOURS,
            entry.data.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=interval_hours),
        )
        self._active_issue_ids: set[str] = set()

    @property
    def _stale_days(self) -> int:
        return self.entry.options.get(
            CONF_STALE_DAYS,
            self.entry.data.get(CONF_STALE_DAYS, DEFAULT_STALE_DAYS),
        )

    async def _async_update_data(self) -> AuditReport:
        """Run one full audit pass."""
        automations = collect_automations(self.hass)

        known: set[str] = set()
        unavailable: set[str] = set()
        for state in self.hass.states.async_all():
            known.add(state.entity_id)
            if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                unavailable.add(state.entity_id)

        ctx = RuleContext(
            known_entity_ids=known,
            unavailable_entity_ids=unavailable,
            now=dt_util.utcnow(),
            stale_days=self._stale_days,
        )
        findings = run_rules(automations, ctx)

        self._sync_repairs_issues(findings)

        report = AuditReport(
            findings=findings,
            automations_audited=len(automations),
            finished_at=dt_util.utcnow(),
        )
        _LOGGER.debug(
            "Audit complete: %d automations, %d findings "
            "(%d error, %d warning, %d info)",
            report.automations_audited,
            len(findings),
            report.count(Severity.ERROR),
            report.count(Severity.WARNING),
            report.count(Severity.INFO),
        )
        return report

    def _sync_repairs_issues(self, findings: list[Finding]) -> None:
        """Create Repairs issues for new findings, clear resolved ones."""
        surfaced = [f for f in findings if f.severity in _ISSUE_SEVERITY]
        current_ids = {f.issue_id for f in surfaced}

        for finding in surfaced:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                finding.issue_id,
                is_fixable=False,
                severity=_ISSUE_SEVERITY[finding.severity],
                translation_key=finding.rule_id,
                translation_placeholders={
                    "alias": finding.alias,
                    "automation": finding.automation_entity_id,
                    "detail": finding.detail,
                },
                learn_more_url="https://github.com/linit01/helmsman",
            )

        for stale_id in self._active_issue_ids - current_ids:
            ir.async_delete_issue(self.hass, DOMAIN, stale_id)

        self._active_issue_ids = current_ids

    def async_clear_all_issues(self) -> None:
        """Remove every Repairs issue owned by this coordinator (unload)."""
        for issue_id in self._active_issue_ids:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        self._active_issue_ids = set()
