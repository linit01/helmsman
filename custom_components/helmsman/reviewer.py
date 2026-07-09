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
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as yaml_dump

from .collector import extract_entity_references, relevant_entities
from .const import LLM_MAX_ATTEMPTS
from .fixers import sanitize_llm_config
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
- Candidate entity IDs are matched by NAME SIMILARITY ONLY and may be
  semantically unrelated. Substitute a missing entity with a candidate only
  when it plausibly measures or controls the same real-world thing — a
  garage-door open counter is not a yard person counter, no matter how
  similar the names look. If no candidate is a genuine semantic match, do
  NOT substitute; set has_suggestion to false and say in explanation that
  the missing entity has no plausible replacement.
- improved_config must be the COMPLETE automation configuration as a JSON
  object — not a fragment, not a diff, not a YAML string.
- Keep the same alias.
- If nothing clearly improves the automation, set has_suggestion to false
  and leave improved_config out.
"""

_REWRITE_SYSTEM_PROMPT = """\
You are Helmsman, an automation rebuilder embedded in Home Assistant.
This automation is STRANDED: it references entities that no longer exist
and have no direct replacement. Redesign it to achieve the same INTENT
(read the alias, description, and structure) using ONLY entities that
exist today.

Rules:
- You may restructure triggers, conditions, and actions freely — intent
  matters, not the old structure.
- Only reference entity IDs from the automation's still-valid references
  or the provided inventory. NEVER invent an entity ID, and NEVER keep a
  reference to an entity listed as missing.
- Use modern Home Assistant syntax: `triggers:` / `conditions:` /
  `actions:` blocks, `trigger:` for the trigger type, and `action:` for
  service calls.
- improved_config must be the COMPLETE automation configuration as a JSON
  object. Keep the same alias.
- If the intent genuinely cannot be achieved with the available entities,
  set has_suggestion to false and say what is missing in explanation.
"""

_TRIGGER_KEYS = ("trigger", "triggers")
_ACTION_KEYS = ("action", "actions")

_REWRITE_INVENTORY_LIMIT = 50


def build_user_prompt(
    info: AutomationInfo,
    findings: list[Finding],
    known_entity_ids: set[str],
    hass: HomeAssistant | None = None,
    rewrite: bool = False,
    log_errors: list[str] | None = None,
) -> str:
    """Assemble the per-automation review (or rewrite) prompt."""
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

    if log_errors:
        parts += [
            "",
            "Recent runtime errors logged for this automation — your "
            "improvement should address what causes these:",
        ]
        parts += [f"- {line}" for line in log_errors]

    missing = sorted(info.referenced_entities - known_entity_ids)
    if missing and not rewrite:
        parts += [
            "",
            "Referenced entities that do NOT exist, with the closest "
            "existing entity IDs BY NAME (string similarity only — verify "
            "each candidate is a real semantic match before using it):",
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

    if rewrite:
        if missing:
            parts += [
                "",
                "MISSING entities (these no longer exist — the rewrite "
                "must not reference any of them):",
            ]
            parts += [f"- {entity_id}" for entity_id in missing]
        if hass is not None:
            context_text = " ".join(
                [
                    info.alias,
                    str((info.raw_config or {}).get("description", "")),
                    " ".join(missing),
                ]
            )
            inventory = relevant_entities(
                hass, context_text, _REWRITE_INVENTORY_LIMIT
            )
            if inventory:
                parts += [
                    "",
                    "Available entities that may relate to this "
                    "automation's intent:",
                ]
                parts += [
                    f"- {entity_id}" + (f" ({name})" if name else "")
                    for entity_id, name in inventory
                ]
        parts += [
            "",
            "Rewrite this automation now to achieve its intent with "
            "available entities, or set has_suggestion to false with an "
            "explanation of what is missing.",
        ]
    else:
        parts += [
            "",
            "Review this automation. If you have one clear improvement, "
            "return it as a complete improved_config; otherwise set "
            "has_suggestion to false.",
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
    rewrite: bool = False,
    log_errors: list[str] | None = None,
) -> tuple[Suggestion | None, str]:
    """Ask the LLM to review (or rewrite) one automation; gate the outcome.

    Returns (suggestion, note) — the note explains the outcome either way
    and is surfaced in the panel. Raises OllamaError on transport/model
    failure so callers can decide whether to keep going. With rewrite=True
    the model redesigns a stranded automation using only entities that
    exist — dead references are not allowed to survive.
    """
    if not info.raw_config:
        return None, "No stored config available — cannot review"

    messages = [
        {
            "role": "system",
            "content": _REWRITE_SYSTEM_PROMPT if rewrite else _SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": build_user_prompt(
                info,
                findings,
                known_entity_ids,
                hass=hass,
                rewrite=rewrite,
                log_errors=log_errors,
            ),
        },
    ]
    last_problem = ""

    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        result = await client.chat_structured_messages(
            messages, SUGGESTION_SCHEMA, timeout_s, temperature
        )

        if not result.get("has_suggestion"):
            _LOGGER.debug("No suggestion for %s", info.entity_id)
            if attempt == 1:
                return None, "Model reviewed it and suggested no changes"
            return None, (
                "Model withdrew its proposal after rejection "
                f"({last_problem})"
            )

        improved = result.get("improved_config")
        problem: str | None = None
        if isinstance(improved, dict):
            improved, removed_nulls = sanitize_llm_config(improved)
            if removed_nulls:
                _LOGGER.debug(
                    "Stripped %d null entries from proposal for %s",
                    removed_nulls,
                    info.entity_id,
                )
        if not isinstance(improved, dict) or not _structure_ok(improved):
            problem = (
                "is not a complete automation config (it needs both "
                "triggers and actions)"
            )
        else:
            improved = dict(improved)
            improved["alias"] = info.alias
            if info.automation_id is not None:
                improved["id"] = info.automation_id
            # A rewrite must shed dead references entirely; a review may
            # keep entities the original already referenced.
            allowed = (
                known_entity_ids
                if rewrite
                else known_entity_ids | info.referenced_entities
            )
            invented = extract_entity_references(improved) - allowed
            if invented:
                problem = (
                    "references entities that do not exist: "
                    f"{', '.join(sorted(invented))}"
                )
            else:
                if improved == dict(info.raw_config):
                    problem = (
                        "is identical to the original configuration — if "
                        "you have no real improvement, set has_suggestion "
                        "to false instead"
                    )
                else:
                    validation_error = await ha_validation_error(
                        hass, improved
                    )
                    if validation_error is not None:
                        problem = (
                            "failed Home Assistant config validation: "
                            f"{validation_error}"
                        )
                        # WARNING so it reaches the system log and the
                        # panel's log viewer — payload evidence must not
                        # depend on debug logging being enabled.
                        _LOGGER.warning(
                            "Rejected proposal payload for %s: %s",
                            info.entity_id,
                            json.dumps(improved)[:1500],
                        )

        if problem is None:
            suggestion = Suggestion(
                automation_entity_id=info.entity_id,
                alias=info.alias,
                summary=str(result.get("summary") or "").strip()
                or ("Proposed rewrite" if rewrite else "Proposed improvement"),
                explanation=str(result.get("explanation") or "").strip(),
                improved_config=improved,
                improved_yaml=yaml_dump(improved).strip(),
                model=client.model,
                created_at=dt_util.utcnow(),
            )
            note = "Suggestion held for review"
            if attempt > 1:
                note += f" (self-corrected on attempt {attempt})"
            return suggestion, note

        last_problem = problem
        _LOGGER.info(
            "Proposal for %s rejected on attempt %d/%d: %s",
            info.entity_id,
            attempt,
            LLM_MAX_ATTEMPTS,
            problem,
        )
        if attempt < LLM_MAX_ATTEMPTS:
            messages.append(
                {"role": "assistant", "content": json.dumps(result)}
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"REJECTED: your improved_config {problem}. Return "
                        "a corrected COMPLETE improved_config that fixes "
                        "exactly this problem while preserving the "
                        "automation's intent. If you cannot fix it, set "
                        "has_suggestion to false."
                    ),
                }
            )

    return None, (
        f"Proposal rejected after {LLM_MAX_ATTEMPTS} attempts; "
        f"last error: {last_problem}"
    )
