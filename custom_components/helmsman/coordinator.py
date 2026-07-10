"""Audit coordinator for Helmsman.

Runs the collector + rules pass on a schedule (or on demand via the
helmsman.run_audit service) and syncs ERROR/WARNING findings to the
Repairs issue registry. When an Ollama URL is configured, a background
LLM review pass follows each audit and proposes improvements for flagged
automations. Strictly read-only with respect to automations.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import time
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as yaml_dump

from .applier import (
    SnapshotStore,
    async_apply_config,
    async_create_automation,
    async_replace_entities,
    async_rollback,
)
from .collector import automation_log_errors, collect_automations
from .creator import BENCHMARK_FIXTURES, draft_automation, probe_draft_quality
from .fixers import apply_syntax_fixes
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
    ABS_MAX_REVIEW_CONFIG_CHARS,
    BENCHMARK_EXCLUDE_FAMILIES,
    BENCHMARK_EXCLUDE_FRAGMENTS,
    BENCHMARK_MAX_MODELS,
    BENCHMARK_MIN_PARAM_B,
    MAX_LLM_TIMEOUT_S,
    MAX_PREDICTED_REVIEW_S,
    MAX_REVIEWS_PER_PASS,
    SNAPSHOT_RETENTION_DAYS,
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
from .panel import async_update_sidebar_count
from .reviewer import ha_validation_error, review_automation
from .rules import RuleContext, run_rules

_LOGGER = logging.getLogger(__name__)

# Suggestions produced by the rules engine, not a model.
DETERMINISTIC_MODEL = "deterministic rules — no AI"

_SYNTAX_RULES = {"deprecated_service_key", "deprecated_trigger_platform"}


def _benchmark_sort_key(result: dict) -> tuple:
    """Best model first: draft quality, then speed.

    Ranked on what actually matters for authoring automations — clean
    first-try drafts, then drafts that pass after repair, then the fewest
    repairs needed, then error-free runs, and only finally speed. This is
    deliberately NOT speed-first: the old benchmark rewarded a fast, eager
    model over a disciplined one, which is how an 8B model that needs the
    fixer safety net got recommended over qwen2.5-coder.
    """
    completed = sum(
        1 for s in result["samples"] if s.get("seconds") is not None
    )
    avg = result["avg_seconds"] if result["avg_seconds"] is not None else 1e9
    return (
        -result["clean"],
        -result["passed"],
        result["repairs"],
        -completed,
        avg,
    )


def _short_request(fixture: dict, limit: int = 48) -> str:
    """A compact label for a benchmark fixture, for the panel tables."""
    request = fixture["request"]
    return request if len(request) <= limit else request[: limit - 1] + "…"


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
        self.suggestions: dict[str, Suggestion] = {}
        self.last_review: datetime | None = None
        self.last_review_note: str | None = None
        self.review_progress: str | None = None
        self.review_notes: dict[str, dict[str, str]] = {}
        self._speed_cache: dict[str, dict[str, float]] = {}
        self.benchmark_in_progress = False
        self.benchmark_progress: str | None = None
        self._review_task: asyncio.Task | None = None
        self._startup_audit_done = False
        self._suppress_auto_review = False
        self._review_lock = asyncio.Lock()
        self.snapshots = SnapshotStore(hass)
        self.drafts: dict[str, Draft] = {}
        self.opportunities: list[dict] = []
        self.stranded: list[dict] = []
        self.dismissed = DismissStore(hass)

    @property
    def review_in_progress(self) -> bool:
        """Whether a background LLM review pass is currently running.

        The benchmark shares the LLM lock but is not a review; without
        the exclusion the panel would show review UI during benchmarks.
        """
        return self._review_lock.locked() and not self.benchmark_in_progress

    def _option(self, key: str, default):
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def _stale_days(self) -> int:
        return self._option(CONF_STALE_DAYS, DEFAULT_STALE_DAYS)

    @property
    def ollama_url(self) -> str:
        """Configured Ollama base URL; empty string disables the LLM pass."""
        return (self._option(CONF_OLLAMA_URL, "") or "").strip()

    @property
    def model(self) -> str:
        """Currently configured Ollama model."""
        return self._option(CONF_MODEL, DEFAULT_MODEL)

    def _make_client(self, model: str | None = None) -> OllamaClient:
        return OllamaClient(
            async_get_clientsession(self.hass),
            self.ollama_url,
            model or self.model,
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

        # Registered-but-unloaded entities (integration failed or still
        # starting) are a different problem from "does not exist" —
        # never offer to replace an entity that is merely unloaded.
        registered = {
            entity_id
            for entity_id, entry in er.async_get(self.hass).entities.items()
            if entry.disabled_by is None
        }

        ctx = RuleContext(
            known_entity_ids=known,
            unavailable_entity_ids=unavailable,
            now=dt_util.utcnow(),
            stale_days=self._stale_days,
            registered_entity_ids=registered,
        )
        findings = run_rules(automations, ctx)

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

        # Drop suggestions for automations that no longer exist, and
        # prune snapshot history (deleted automations, stale versions).
        existing = {a.entity_id for a in automations}
        for stale in [e for e in self.suggestions if e not in existing]:
            del self.suggestions[stale]
        await self.snapshots.async_prune(existing, SNAPSHOT_RETENTION_DAYS)

        self.opportunities = [
            opp
            for opp in scan_opportunities(self.hass, automations)
            if not self.dismissed.is_dismissed(opp["key"])
        ]

        self.stranded = self._build_stranded(automations, known | registered)

        await self._async_hold_deterministic_fixes(automations, findings)

        async_update_sidebar_count(
            self.hass,
            report.count(Severity.ERROR) + report.count(Severity.WARNING),
        )

        suppress = self._suppress_auto_review
        self._suppress_auto_review = False
        if not self._startup_audit_done:
            # The startup audit populates findings but never auto-starts
            # an LLM review — it would grab the model right when the user
            # may want to benchmark or review something specific.
            self._startup_audit_done = True
            _LOGGER.debug("Startup audit: skipping automatic LLM review")
        elif suppress:
            # User-initiated audits (panel button, run_audit service) and
            # post-apply refreshes are rules-only; automatic LLM reviews
            # follow scheduled audits, manual reviews are always available.
            _LOGGER.debug("Manual audit: skipping automatic LLM review")
        elif self.ollama_url and not self._review_lock.locked():
            self._review_task = self.entry.async_create_background_task(
                self.hass,
                self._async_cancelable_review(
                    self._async_review_flagged(automations, findings, known)
                ),
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

    async def _async_measure_speed(
        self, client: OllamaClient
    ) -> dict[str, float] | None:
        """Measured tokens/sec for the current server+model, probing once."""
        key = f"{self.ollama_url}::{client.model}"
        if key not in self._speed_cache:
            try:
                stats = await client.probe_speed()
            except OllamaError as err:
                _LOGGER.warning("Model speed probe failed: %s", err)
                return None
            if stats:
                self._speed_cache[key] = stats
                _LOGGER.info(
                    "Measured %s at %s: %.0f tok/s generation, "
                    "%.0f tok/s prompt",
                    client.model,
                    self.ollama_url,
                    stats.get("gen_tps", 0.0),
                    stats.get("prompt_tps", 0.0),
                )
        return self._speed_cache.get(key)

    def _refine_speed(self, client: OllamaClient) -> None:
        """Fold a real call's timing metadata back into the cache."""
        if client.last_stats and client.last_stats.get("gen_tps"):
            key = f"{self.ollama_url}::{client.model}"
            self._speed_cache[key] = {
                **self._speed_cache.get(key, {}),
                **client.last_stats,
            }

    @staticmethod
    def _plan_review(
        config_chars: int, speed: dict[str, float] | None
    ) -> tuple[int, float | None]:
        """Per-request (timeout_s, predicted_s) for one automation.

        The model must re-emit the whole config as grammar-constrained
        JSON, so cost scales with config size. Grammar decoding runs well
        below raw generation speed — measured worse than half on large
        configs, hence the 0.35 factor and the wide timeout margin.
        """
        if not speed or not speed.get("gen_tps"):
            return LLM_REQUEST_TIMEOUT_S, None
        prompt_tokens = config_chars / 4 + 900
        output_tokens = config_chars / 4 * 1.3 + 120
        gen_tps = speed["gen_tps"] * 0.35
        prompt_tps = speed.get("prompt_tps") or gen_tps * 10
        predicted = prompt_tokens / prompt_tps + output_tokens / gen_tps
        timeout = int(min(max(predicted * 2.5 + 90, 120), MAX_LLM_TIMEOUT_S))
        return timeout, predicted

    async def _async_review_targets(
        self,
        targets: list[AutomationInfo],
        findings: list[Finding],
        known_entity_ids: set[str],
        rewrite: bool = False,
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
            speed = await self._async_measure_speed(client)

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
                timeout_s, predicted = self._plan_review(config_size, speed)
                too_slow = (
                    predicted is not None and predicted > MAX_PREDICTED_REVIEW_S
                )
                if too_slow or config_size > ABS_MAX_REVIEW_CONFIG_CHARS:
                    reason = (
                        f"predicted ~{predicted / 60:.0f} min at this "
                        f"model's measured speed "
                        f"({(speed or {}).get('gen_tps', 0):.0f} tok/s)"
                        if too_slow
                        else f"config is {config_size} characters"
                    )
                    _note(
                        info,
                        f"Skipped — {reason}. A faster server or model "
                        "will include it automatically.",
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
                        timeout_s=timeout_s,
                        temperature=LLM_TEMPERATURE,
                        rewrite=rewrite,
                        log_errors=automation_log_errors(self.hass, info),
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
                self._refine_speed(client)
                speed = self._speed_cache.get(
                    f"{self.ollama_url}::{client.model}", speed
                )
                reviewed += 1
                done += 1
                self.review_progress = f"{done}/{len(targets)}"
                _note(info, note)
                if suggestion is not None:
                    self.suggestions[info.entity_id] = suggestion
                elif (
                    info.entity_id in self.suggestions
                    and self.suggestions[info.entity_id].model
                    != DETERMINISTIC_MODEL
                ):
                    # Re-review produced nothing; the old LLM proposal is
                    # stale. Deterministic fixes stay — the model saying
                    # "no changes" doesn't invalidate a mechanical rename.
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

    async def async_review_entity(
        self, entity_id: str | None, rewrite: bool = False
    ) -> None:
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
        await self._async_review_targets(
            targets, findings, known, rewrite=rewrite
        )

    def async_start_review(
        self, entity_id: str | None, rewrite: bool = False
    ) -> None:
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
        self._review_task = self.entry.async_create_background_task(
            self.hass,
            self._async_cancelable_review(
                self.async_review_entity(entity_id, rewrite=rewrite)
            ),
            name="helmsman_manual_review",
        )

    async def _async_cancelable_review(self, review_coro) -> None:
        """Run a review pass, leaving clean state if the user stops it."""
        try:
            await review_coro
        except asyncio.CancelledError:
            self.review_progress = None
            self.last_review_note = (
                "Review stopped by user; "
                f"{len(self.suggestions)} suggestions held"
            )
            self.async_update_listeners()
            raise

    def _build_stranded(
        self, automations: list[AutomationInfo], known: set[str]
    ) -> list[dict]:
        """Automations referencing missing entities, with swap candidates.

        Powers the panel's Stranded automations section: per missing
        entity, up to 8 same-domain candidates sorted by name similarity
        for the user to choose a replacement from.
        """
        def _name(candidate: str) -> str:
            state = self.hass.states.get(candidate)
            if state:
                return str(
                    state.attributes.get("friendly_name") or candidate
                )
            return candidate

        stranded: list[dict] = []
        for info in automations:
            if not info.raw_config or not info.automation_id:
                continue
            missing = sorted(info.referenced_entities - known)
            if not missing:
                continue
            entries = []
            for entity_id in missing:
                domain = entity_id.split(".", 1)[0]
                pool = [k for k in known if k.startswith(f"{domain}.")]
                candidates = difflib.get_close_matches(
                    entity_id, pool, n=8, cutoff=0.2
                ) or sorted(pool)[:8]
                entries.append(
                    {
                        "entity_id": entity_id,
                        "candidates": [
                            {"entity_id": c, "name": _name(c)}
                            for c in candidates
                        ],
                    }
                )
            stranded.append(
                {
                    "automation": info.entity_id,
                    "alias": info.alias,
                    "automation_id": info.automation_id,
                    "enabled": info.state == "on",
                    "missing": entries,
                }
            )
        return stranded

    async def _async_hold_deterministic_fixes(
        self,
        automations: list[AutomationInfo],
        findings: list[Finding],
    ) -> None:
        """Hold rules-engine fixes for mechanical syntax findings.

        Deprecated-syntax renames need no LLM: the fixer rewrites the
        config directly, validation still gates it, and the result is a
        normal suggestion. An LLM suggestion for the same automation is
        never overwritten — the model may have fixed more than syntax.
        """
        flagged = {
            f.automation_entity_id
            for f in findings
            if f.rule_id in _SYNTAX_RULES
        }
        for info in automations:
            if info.entity_id not in flagged or not info.raw_config:
                continue
            existing = self.suggestions.get(info.entity_id)
            if existing and existing.model != DETERMINISTIC_MODEL:
                continue
            fixed, changes = apply_syntax_fixes(info.raw_config)
            if not changes:
                continue
            fixed = dict(fixed)
            fixed["alias"] = info.alias
            if info.automation_id is not None:
                fixed["id"] = info.automation_id
            error = await ha_validation_error(self.hass, fixed)
            if error is not None:
                _LOGGER.warning(
                    "Deterministic fix for %s failed validation (%s) — "
                    "skipped",
                    info.entity_id,
                    error,
                )
                continue
            self.suggestions[info.entity_id] = Suggestion(
                automation_entity_id=info.entity_id,
                alias=info.alias,
                summary="Modernize deprecated syntax",
                explanation=(
                    "Deterministic fix by the rules engine (no AI): "
                    + "; ".join(changes)
                    + ". Behavior is unchanged — these keys were renamed "
                    "by Home Assistant and the old names are deprecated."
                ),
                improved_config=fixed,
                improved_yaml=yaml_dump(fixed).strip(),
                model=DETERMINISTIC_MODEL,
                created_at=dt_util.utcnow(),
            )

    async def async_replace_entities(
        self, entity_id: str, replacements: dict[str, str]
    ) -> None:
        """User-chosen entity swap for a stranded automation."""
        automations = collect_automations(self.hass)
        info = next(
            (a for a in automations if a.entity_id == entity_id), None
        )
        if info is None or not info.automation_id:
            raise HomeAssistantError(
                f"{entity_id} is not editable (no automation id)"
            )
        for new in replacements.values():
            if self.hass.states.get(new) is None:
                raise HomeAssistantError(
                    f"Replacement entity {new} does not exist"
                )
        await async_replace_entities(
            self.hass,
            self.snapshots,
            entity_id,
            info.automation_id,
            replacements,
        )
        self.suggestions.pop(entity_id, None)
        self.async_update_listeners()
        await self.async_run_manual_audit()

    async def async_disable_automation(self, entity_id: str) -> None:
        """Turn a stranded automation off (reversible in the HA UI)."""
        await self.hass.services.async_call(
            "automation",
            "turn_off",
            {"entity_id": entity_id},
            blocking=True,
        )
        await self.async_run_manual_audit()

    async def async_run_manual_audit(self) -> None:
        """User-requested audit: rules only, never auto-starts a review.

        Runs immediately (async_refresh) rather than through the
        coordinator's request debouncer — a debounced audit lands ~10s
        after the click, making the button look dead now that manual
        audits no longer start a visible review.
        """
        self._suppress_auto_review = True
        await self.async_refresh()

    def async_stop_review(self) -> None:
        """Cancel the running review pass (aborts the in-flight request)."""
        if (
            self._review_task is None
            or self._review_task.done()
            or not self.review_in_progress
        ):
            raise HomeAssistantError("No review is running")
        self._review_task.cancel()

    @property
    def benchmark(self) -> dict | None:
        """Last benchmark result; survives entry reloads (model switch)."""
        return self.hass.data.get(DOMAIN, {}).get("benchmark")

    @staticmethod
    def _rank_models(models: list[dict], current: str) -> list[str]:
        """Pick benchmark candidates: current model first, then by fit.

        Filters on /api/tags metadata (vision/embedding families, tiny
        parameter counts) with name fragments as a fallback, so only
        models that can plausibly do this work get benchmarked.
        """
        def usable(model: dict) -> bool:
            lowered = model["name"].lower()
            if any(frag in lowered for frag in BENCHMARK_EXCLUDE_FRAGMENTS):
                return False
            if any(
                frag in fam
                for fam in model.get("families", [])
                for frag in BENCHMARK_EXCLUDE_FAMILIES
            ):
                return False
            param_b = model.get("param_b")
            if param_b is not None and param_b < BENCHMARK_MIN_PARAM_B:
                return False
            return True

        def score(model: dict) -> float:
            lowered = model["name"].lower()
            points = 0.0
            if "coder" in lowered or "code" in lowered:
                points += 4
            if "qwen3" in lowered or "devstral" in lowered:
                points += 2
            if "instruct" in lowered:
                points += 1
            param_b = model.get("param_b")
            if param_b is not None:
                if 7 <= param_b <= 35:
                    points += 2
                elif 3 <= param_b < 7:
                    points += 1
            return points

        ranked = sorted(
            (m for m in models if usable(m) and m["name"] != current),
            key=lambda m: (-score(m), m["name"]),
        )
        return [current, *(m["name"] for m in ranked)][:BENCHMARK_MAX_MODELS]

    def async_start_benchmark(self) -> None:
        """Launch a model benchmark in the background."""
        if not self.ollama_url:
            raise HomeAssistantError(
                "Configure the Ollama server URL in the Helmsman options "
                "before benchmarking models"
            )
        if self.benchmark_in_progress or self._review_lock.locked():
            raise HomeAssistantError(
                "A benchmark or review is already running"
            )
        self.entry.async_create_background_task(
            self.hass,
            self._async_run_benchmark(),
            name="helmsman_benchmark",
        )

    async def _async_run_benchmark(self) -> None:
        """Benchmark candidate models on draft quality (golden fixtures)."""
        async with self._review_lock:
            self.benchmark_in_progress = True
            self.async_update_listeners()
            try:
                available = await self._make_client().list_models()
                models = self._rank_models(available, self.model)
                results = []
                for index, model in enumerate(models):
                    self.benchmark_progress = (
                        f"{model} ({index + 1}/{len(models)})"
                    )
                    self.async_update_listeners()
                    results.append(
                        await self._async_benchmark_model(model)
                    )
                results.sort(key=_benchmark_sort_key)
                # Recommend the best usable model: clean first-try drafts,
                # then drafts valid after repair, then fewest repairs, then
                # speed. Only a run where every candidate errored earns no
                # recommendation.
                usable = [r for r in results if r["error"] is None]
                self.hass.data.setdefault(DOMAIN, {})["benchmark"] = {
                    "results": results,
                    "recommended": usable[0]["model"] if usable else None,
                    "samples": [_short_request(f) for f in BENCHMARK_FIXTURES],
                    "finished_at": dt_util.utcnow().isoformat(),
                }
                _LOGGER.info(
                    "Benchmark done: %s",
                    ", ".join(
                        f"{r['model']} clean={r['clean']} passed={r['passed']} "
                        f"repairs={r['repairs']} avg={r['avg_seconds'] or '-'}s"
                        for r in results
                    ),
                )
            except (HomeAssistantError, OllamaError) as err:
                _LOGGER.warning("Benchmark failed: %s", err)
                self.hass.data.setdefault(DOMAIN, {})["benchmark"] = {
                    "results": [],
                    "recommended": None,
                    "samples": [],
                    "error": str(err),
                    "finished_at": dt_util.utcnow().isoformat(),
                }
            finally:
                self.benchmark_in_progress = False
                self.benchmark_progress = None
                self.async_update_listeners()

    async def _async_benchmark_model(self, model: str) -> dict:
        """Score one model on draft quality across the golden fixtures."""
        client = self._make_client(model)
        result: dict = {
            "model": model,
            "gen_tps": None,
            "clean": 0,
            "passed": 0,
            "repairs": 0,
            "avg_seconds": None,
            "samples": [],
            "error": None,
        }
        try:
            speed = await self._async_measure_speed(client)
        except OllamaError as err:
            result["error"] = str(err)
            return result
        if speed:
            result["gen_tps"] = round(speed.get("gen_tps", 0), 1)
        timings: list[float] = []
        for fixture in BENCHMARK_FIXTURES:
            label = _short_request(fixture)
            # A draft config is small and roughly fixed in size; base the
            # timeout on the prompt rather than a stored automation.
            timeout_s, predicted = self._plan_review(
                len(fixture["request"]) * 2 + 800, speed
            )
            if predicted is not None and predicted > MAX_PREDICTED_REVIEW_S:
                result["samples"].append(
                    {
                        "alias": label,
                        "seconds": None,
                        "note": f"Too slow (~{predicted / 60:.0f} min predicted)",
                    }
                )
                continue
            started = time.monotonic()
            try:
                outcome = await probe_draft_quality(
                    self.hass,
                    client,
                    fixture,
                    timeout_s=timeout_s,
                    temperature=LLM_TEMPERATURE,
                )
            except OllamaError as err:
                result["samples"].append(
                    {"alias": label, "seconds": None, "note": f"Error: {err}"}
                )
                continue
            elapsed = round(time.monotonic() - started, 1)
            timings.append(elapsed)
            self._refine_speed(client)
            if outcome["passed"]:
                result["passed"] += 1
            if outcome["clean"]:
                result["clean"] += 1
            result["repairs"] += outcome["repairs"]
            result["samples"].append(
                {"alias": label, "seconds": elapsed, "note": outcome["note"]}
            )
        if timings:
            result["avg_seconds"] = round(sum(timings) / len(timings), 1)
        if result["gen_tps"] is None:
            # Probe may have timed out on a cold-loading large model;
            # real review calls refine the cache, so read it back.
            refined = self._speed_cache.get(f"{self.ollama_url}::{model}")
            if refined and refined.get("gen_tps"):
                result["gen_tps"] = round(refined["gen_tps"], 1)
        if model != self.model:
            # Free GPU memory before the next candidate loads; the
            # configured model stays warm for upcoming reviews.
            try:
                await client.unload()
            except OllamaError as err:
                _LOGGER.debug("Could not unload %s: %s", model, err)
        return result

    async def async_set_model(self, model: str) -> None:
        """Switch the configured model; the entry reloads automatically."""
        model = (model or "").strip()
        if not model:
            raise HomeAssistantError("No model given")
        options = {
            **self.entry.data,
            **self.entry.options,
            CONF_MODEL: model,
        }
        self.hass.config_entries.async_update_entry(
            self.entry, options=options
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
        await self.async_run_manual_audit()

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
        await self.async_run_manual_audit()

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
        await self.async_run_manual_audit()
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

