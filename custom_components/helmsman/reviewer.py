"""LLM review pass for Helmsman (MVP-2).

Sends one automation at a time to a local Ollama model and asks for a
concrete improvement. Every proposal must survive three gates before it
becomes a Suggestion:

1. Structural: a complete config object with triggers and actions.
2. Entity existence: may only reference entities that exist now or that
   the original automation already referenced (no invented entity IDs).
3. HA validation: the same automation config validation the built-in
   editor uses.

Read-only: nothing here writes to automations. Apply lands in MVP-3.
"""

from __future__ import annotations

import difflib
import inspect
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as yaml_dump

from .collector import extract_entity_references
from .models import AutomationInfo, Finding, Suggestion
from .ollama import OllamaClient

_LOGGER = logging.getLogger(__name__)

SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_suggestion": {"type": "boolean"},
        "summary": {"type": "string"},
        "explanation": {"type": "string"},
        "improved_config": {"type": "object"},
    },
    "required": ["has_suggestion", "summary", "explanation"],
}

_SYSTEM_PROMPT = """\
You are Helmsman, an automation reviewer embedded in Home Assistant.
You review ONE automation and decide whether a concrete improvement exists.

Rules:
- Preserve the automation's intent and observable behavior, except where a
  listed finding says it is broken.
- Prefer modern Home Assistant syntax: `triggers:` / `conditions:` /
  `actions:` blocks, `trigger:` for the trigger type, and `action:` for
  service calls (not the legacy `service:` key).
- Only reference entity IDs that appear in the automation itself or in the
  provided candidate list. NEVER invent an entity ID.
- improved_config must be the COMPLETE automation configuration as a JSON
  object — not a fragment, not a diff, not a YAML string.
- Keep the same alias.
- If nothing clearly improves the automation, set has_suggestion to false
  and leave improved_config out.
"""

_TRIGGER_KEYS = ("trigger", "triggers")
_ACTION_KEYS = ("action", "actions")


def build_user_prompt(
    info: AutomationInfo,
    findings: list[Finding],
    known_entity_ids: set[str],
) -> str:
    """Assemble the per-automation review prompt."""
    parts = [
        "Automation configuration (YAML):",
        yaml_dump(info.raw_config).strip(),
        "",
        f"Mode: {info.mode}. Currently {info.state}. "
        + (
            f"Last triggered: {info.last_triggered.isoformat()}."
            if info.last_triggered
            else "Never triggered."
        ),
    ]

    if findings:
        parts += ["", "Findings from the deterministic lint pass:"]
        parts += [
            f"- [{finding.severity}] {finding.rule_id}: {finding.summary}"
            for finding in findings
        ]

    missing = sorted(info.referenced_entities - known_entity_ids)
    if missing:
        parts += [
            "",
            "Referenced entities that do NOT exist, with the closest "
            "existing entity IDs (candidates you may use):",
        ]
        for entity_id in missing:
            domain = entity_id.split(".", 1)[0]
            pool = [e for e in known_entity_ids if e.startswith(f"{domain}.")]
            candidates = difflib.get_close_matches(
                entity_id, pool or list(known_entity_ids), n=5, cutoff=0.4
            )
            parts.append(
                f"- {entity_id} -> {', '.join(candidates) if candidates else 'no close match found'}"
            )

    parts += [
        "",
        "Review this automation. If you have one clear improvement, return "
        "it as a complete improved_config; otherwise set has_suggestion to "
        "false.",
    ]
    return "\n".join(parts)


def _structure_ok(config: dict) -> bool:
    """Complete-automation shape: at least one trigger and one action."""
    return any(config.get(k) for k in _TRIGGER_KEYS) and any(
        config.get(k) for k in _ACTION_KEYS
    )


async def ha_validation_error(
    hass: HomeAssistant, config: dict
) -> str | None:
    """Run HA's own automation config validation; None means valid.

    async_validate_config_item is the (internal) path the automation editor
    uses; its return contract has drifted across HA releases, so read the
    validation status defensively and fail closed on exceptions.
    """
    try:
        from homeassistant.components.automation.config import (
            async_validate_config_item,
        )
    except ImportError:
        _LOGGER.warning(
            "automation config validator unavailable; falling back to "
            "structural checks only"
        )
        return None

    try:
        # The signature drifted across HA releases:
        # (hass, config) in older cores, (hass, config_key, config) now.
        params = inspect.signature(async_validate_config_item).parameters
        if "config_key" in params:
            validated = await async_validate_config_item(
                hass, "helmsman_proposal", dict(config)
            )
        else:
            validated = await async_validate_config_item(hass, dict(config))
    except Exception as err:  # noqa: BLE001 - any validator error means reject
        _LOGGER.warning("Proposal rejected by HA validation: %s", err)
        return str(err) or type(err).__name__

    status = getattr(validated, "validation_status", None)
    if status is None:
        return None
    if str(status).lower() == "ok":
        return None
    error = getattr(validated, "validation_error", None)
    _LOGGER.warning("Proposal failed HA validation (%s): %s", status, error)
    return str(error) if error else str(status)


async def review_automation(
    hass: HomeAssistant,
    client: OllamaClient,
    info: AutomationInfo,
    findings: list[Finding],
    known_entity_ids: set[str],
    timeout_s: int,
    temperature: float,
) -> tuple[Suggestion | None, str]:
    """Ask the LLM to review one automation; gate and return the outcome.

    Returns (suggestion, note) — the note explains the outcome either way
    and is surfaced in the panel. Raises OllamaError on transport/model
    failure so callers can decide whether to keep going.
    """
    if not info.raw_config:
        return None, "No stored config available — cannot review"

    result = await client.chat_structured(
        system=_SYSTEM_PROMPT,
        user=build_user_prompt(info, findings, known_entity_ids),
        schema=SUGGESTION_SCHEMA,
        timeout_s=timeout_s,
        temperature=temperature,
    )

    if not result.get("has_suggestion"):
        _LOGGER.debug("No suggestion for %s", info.entity_id)
        return None, "Model reviewed it and suggested no changes"

    improved = result.get("improved_config")
    if not isinstance(improved, dict) or not _structure_ok(improved):
        _LOGGER.info(
            "Proposal for %s rejected: not a complete automation config",
            info.entity_id,
        )
        return None, "Proposal rejected: not a complete automation config"

    improved = dict(improved)
    improved["alias"] = info.alias
    if info.automation_id is not None:
        improved["id"] = info.automation_id

    allowed = known_entity_ids | info.referenced_entities
    invented = extract_entity_references(improved) - allowed
    if invented:
        _LOGGER.info(
            "Proposal for %s rejected: references invented entities %s",
            info.entity_id,
            sorted(invented),
        )
        return None, (
            "Proposal rejected: referenced non-existent entities "
            f"({', '.join(sorted(invented))})"
        )

    validation_error = await ha_validation_error(hass, improved)
    if validation_error is not None:
        return None, f"Proposal rejected by HA validation: {validation_error}"

    suggestion = Suggestion(
        automation_entity_id=info.entity_id,
        alias=info.alias,
        summary=str(result.get("summary") or "").strip()
        or "Proposed improvement",
        explanation=str(result.get("explanation") or "").strip(),
        improved_config=improved,
        improved_yaml=yaml_dump(improved).strip(),
        model=client.model,
        created_at=dt_util.utcnow(),
    )
    return suggestion, "Suggestion held for review"
