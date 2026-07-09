"""Audit coordinator for Helmsman.

Runs the collector + rules pass on a schedule (or on demand via the
helmsman.run_audit service) and syncs ERROR/WARNING findings to the
Repairs issue registry. When an Ollama URL is configured, a background
LLM review pass follows each audit and proposes improvements for flagged
automations. Strictly read-only with respect to automations.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as yaml_dump

from .applier import (
    SnapshotStore,
    async_apply_config,
    async_create_automation,
    async_rollback,
)
from .collector import collect_automations
from .creator import draft_automation
from .opportunities import DismissStore, scan_opportunities
from .const import (
    CONF_MODEL,
    CONF_OLLAMA_URL,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_STALE_DAYS,
    DEFAULT_MODEL,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_STALE_DAYS,
    DOMAIN,
    LLM_REQUEST_TIMEOUT_S,
    LLM_TEMPERATURE,
    MAX_REVIEW_CONFIG_CHARS,
    MAX_REVIEWS_PER_PASS,
)
from .models import (
    AuditReport,
    AutomationInfo,
    Draft,
    Finding,
    Severity,
    Suggestion,
)
from .ollama import OllamaClient, OllamaError
from .reviewer import review_automation
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
        self.suggestions: dict[str, Suggestion] = {}
        self.last_review: datetime | None = None
        self.last_review_note: str | None = None
        self.review_progress: str | None = None
        self.review_notes: dict[str, dict[str, str]] = {}
        self._review_lock = asyncio.Lock()
        self.snapshots = SnapshotStore(hass)
        self.drafts: dict[str, Draft] = {}
        self.opportunities: list[dict] = []
        self.dismissed = DismissStore(hass)

    @property
    def review_in_progress(self) -> bool:
        """Whether a background LLM review pass is currently running."""
        return self._review_lock.locked()

    def _option(self, key: str, default):
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def _stale_days(self) -> int:
        return self._option(CONF_STALE_DAYS, DEFAULT_STALE_DAYS)

    @property
    def ollama_url(self) -> str:
        """Configured Ollama base URL; empty string disables the LLM pass."""
        return (self._option(CONF_OLLAMA_URL, "") or "").strip()

    def _make_client(self) -> OllamaClient:
        return OllamaClient(
            async_get_clientsession(self.hass),
            self.ollama_url,
            self._option(CONF_MODEL, DEFAULT_MODEL),
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

        # Drop suggestions for automations that no longer exist.
        existing = {a.entity_id for a in automations}
        for stale in [e for e in self.suggestions if e not in existing]:
            del self.suggestions[stale]

        self.opportunities = [
            opp
            for opp in scan_opportunities(self.hass, automations)
            if not self.dismissed.is_dismissed(opp["key"])
        ]

        if self.ollama_url and not self._review_lock.locked():
            self.entry.async_create_background_task(
                self.hass,
                self._async_review_flagged(automations, findings, known),
                name="helmsman_llm_review",
            )

        return report

    async def _async_review_flagged(
        self,
        automations: list[AutomationInfo],
        findings: list[Finding],
        known_entity_ids: set[str],
    ) -> None:
        """Review automations with ERROR/WARNING findings (background)."""
        flagged_ids = {
            f.automation_entity_id
            for f in findings
            if f.severity in (Severity.ERROR, Severity.WARNING)
        }
        targets = [a for a in automations if a.entity_id in flagged_ids]
        if len(targets) > MAX_REVIEWS_PER_PASS:
            _LOGGER.info(
                "LLM review capped at %d of %d flagged automations this "
                "pass; the rest queue for the next audit",
                MAX_REVIEWS_PER_PASS,
                len(targets),
            )
            targets = targets[:MAX_REVIEWS_PER_PASS]
        await self._async_review_targets(targets, findings, known_entity_ids)

    async def _async_review_targets(
        self,
        targets: list[AutomationInfo],
        findings: list[Finding],
        known_entity_ids: set[str],
    ) -> None:
        """Run the LLM review sequentially over targets, updating listeners."""
        if self._review_lock.locked():
            _LOGGER.debug("LLM review already in progress; skipping")
            return
        async with self._review_lock:
            client = self._make_client()
            reviewed = 0
            done = 0
            consecutive_errors = 0
            abort_error: str | None = None
            self.review_notes = {}
            self.review_progress = f"0/{len(targets)}"
            self.async_update_listeners()

            def _note(info: AutomationInfo, text: str) -> None:
                self.review_notes[info.entity_id] = {
                    "automation": info.entity_id,
                    "alias": info.alias,
                    "note": text,
                }

            for info in targets:
                config_size = (
                    len(yaml_dump(info.raw_config)) if info.raw_config else 0
                )
                if config_size > MAX_REVIEW_CONFIG_CHARS:
                    _note(
                        info,
                        f"Skipped — config is {config_size} characters "
                        f"(limit {MAX_REVIEW_CONFIG_CHARS} for local "
                        "review; large configs are too slow to regenerate)",
                    )
                    done += 1
                    self.review_progress = f"{done}/{len(targets)}"
                    self.async_update_listeners()
                    continue
                own_findings = [
                    f for f in findings
                    if f.automation_entity_id == info.entity_id
                ]
                try:
                    suggestion, note = await review_automation(
                        self.hass,
                        client,
                        info,
                        own_findings,
                        known_entity_ids,
                        timeout_s=LLM_REQUEST_TIMEOUT_S,
                        temperature=LLM_TEMPERATURE,
                    )
                except OllamaError as err:
                    consecutive_errors += 1
                    _LOGGER.warning(
                        "LLM review of %s failed: %s", info.entity_id, err
                    )
                    _note(info, f"Error: {err}")
                    done += 1
                    self.review_progress = f"{done}/{len(targets)}"
                    self.async_update_listeners()
                    if consecutive_errors >= 2:
                        abort_error = (
                            "aborted after two consecutive failures — the "
                            "Ollama server looks unresponsive"
                        )
                        break
                    continue
                consecutive_errors = 0
                reviewed += 1
                done += 1
                self.review_progress = f"{done}/{len(targets)}"
                _note(info, note)
                if suggestion is not None:
                    self.suggestions[info.entity_id] = suggestion
                elif info.entity_id in self.suggestions:
                    # Re-review produced nothing; the old proposal is stale.
                    del self.suggestions[info.entity_id]
                self.last_review = dt_util.utcnow()
                self.async_update_listeners()
            self.review_progress = None
            self.last_review_note = (
                f"Reviewed {reviewed} of {len(targets)} flagged "
                f"automations; {len(self.suggestions)} suggestions held"
                + (f" ({abort_error})" if abort_error else "")
            )
            _LOGGER.info("LLM review pass done: %s", self.last_review_note)
            self.async_update_listeners()

    async def async_review_entity(self, entity_id: str | None) -> None:
        """Service entry point: review one automation, or all flagged ones."""
        if not self.ollama_url:
            raise HomeAssistantError(
                "Configure the Ollama server URL in the Helmsman options "
                "before requesting an LLM review"
            )

        automations = collect_automations(self.hass)
        known = {s.entity_id for s in self.hass.states.async_all()}
        findings = self.data.findings if self.data else []

        if entity_id is None:
            await self._async_review_flagged(automations, findings, known)
            return

        targets = [a for a in automations if a.entity_id == entity_id]
        if not targets:
            raise HomeAssistantError(f"Unknown automation: {entity_id}")
        await self._async_review_targets(targets, findings, known)

    def async_start_review(self, entity_id: str | None) -> None:
        """Launch a review pass in the background (panel/service path)."""
        if not self.ollama_url:
            raise HomeAssistantError(
                "Configure the Ollama server URL in the Helmsman options "
                "before requesting an LLM review"
            )
        if self.review_in_progress:
            raise HomeAssistantError(
                "A review pass is already running — watch the progress "
                "indicator at the top of the panel"
            )
        if entity_id is not None and self.hass.states.get(entity_id) is None:
            raise HomeAssistantError(f"Unknown automation: {entity_id}")
        self.entry.async_create_background_task(
            self.hass,
            self.async_review_entity(entity_id),
            name="helmsman_manual_review",
        )

    async def async_apply_suggestion(self, entity_id: str) -> None:
        """Apply an approved suggestion: snapshot, write, reload, re-audit."""
        suggestion = self.suggestions.get(entity_id)
        if suggestion is None:
            raise HomeAssistantError(f"No suggestion held for {entity_id}")
        automation_id = suggestion.improved_config.get("id")
        if not automation_id:
            raise HomeAssistantError(
                f"{entity_id} has no automation id; only automations "
                "managed via automations.yaml can be modified"
            )
        await async_apply_config(
            self.hass,
            self.snapshots,
            entity_id,
            automation_id,
            suggestion.improved_config,
            "apply_suggestion",
        )
        del self.suggestions[entity_id]
        self.async_update_listeners()
        await self.async_request_refresh()

    def async_dismiss_suggestion(self, entity_id: str) -> None:
        """Drop a suggestion without applying it."""
        if entity_id not in self.suggestions:
            raise HomeAssistantError(f"No suggestion held for {entity_id}")
        del self.suggestions[entity_id]
        self.async_update_listeners()

    async def async_rollback_automation(self, entity_id: str) -> None:
        """Restore the most recent snapshot for an automation."""
        await async_rollback(self.hass, self.snapshots, entity_id)
        self.suggestions.pop(entity_id, None)
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_draft(self, description: str, source: str) -> Draft:
        """Draft a new automation from a plain-language description."""
        if not self.ollama_url:
            raise HomeAssistantError(
                "Configure the Ollama server URL in the Helmsman options "
                "before drafting automations"
            )
        description = (description or "").strip()
        if not description:
            raise HomeAssistantError("Describe what the automation should do")
        draft = await draft_automation(
            self.hass,
            self._make_client(),
            description,
            source,
            timeout_s=LLM_REQUEST_TIMEOUT_S,
            temperature=LLM_TEMPERATURE,
        )
        self.drafts[draft.draft_id] = draft
        self.async_update_listeners()
        return draft

    async def async_create_draft(self, draft_id: str) -> str:
        """Create an approved draft as a real (disabled) automation."""
        draft = self.drafts.get(draft_id)
        if draft is None:
            raise HomeAssistantError("That draft is no longer held")
        entity_id = await async_create_automation(
            self.hass, draft.config, disabled=True
        )
        del self.drafts[draft_id]
        self.async_update_listeners()
        await self.async_request_refresh()
        return entity_id

    def async_dismiss_draft(self, draft_id: str) -> None:
        """Drop a draft without creating it."""
        if draft_id not in self.drafts:
            raise HomeAssistantError("That draft is no longer held")
        del self.drafts[draft_id]
        self.async_update_listeners()

    async def async_dismiss_opportunity(self, key: str) -> None:
        """Persistently dismiss a noticed opportunity."""
        await self.dismissed.async_dismiss(key)
        self.opportunities = [
            opp for opp in self.opportunities if opp["key"] != key
        ]
        self.async_update_listeners()

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
