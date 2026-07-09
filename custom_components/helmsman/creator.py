"""Draft brand-new automations from a plain-language description (MVP-4).

The user describes what should happen; a relevance-filtered inventory of
their real entities goes into the prompt so the model maps intent onto
entity IDs that actually exist. Drafts pass the same gates as review
suggestions (structure, entity existence, HA validation) before the user
ever sees them, and are created disabled by default.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as yaml_dump

from .collector import extract_entity_references
from .const import LLM_MAX_ATTEMPTS
from .models import Draft
from .ollama import OllamaClient
from .reviewer import ha_validation_error

_LOGGER = logging.getLogger(__name__)

MAX_INVENTORY_ENTITIES = 60

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "possible": {"type": "boolean"},
        "reason": {"type": "string"},
        "alias": {"type": "string"},
        "summary": {"type": "string"},
        "explanation": {"type": "string"},
        "config": {"type": "object"},
    },
    "required": ["possible", "reason", "alias", "summary", "explanation"],
}

_SYSTEM_PROMPT = """\
You are Helmsman, an automation author embedded in Home Assistant.
You turn ONE plain-language request into ONE complete automation.

Rules:
- Use modern Home Assistant syntax: `triggers:` / `conditions:` /
  `actions:` blocks, `trigger:` for the trigger type, and `action:` for
  service calls.
- Only reference entity IDs from the provided inventory. NEVER invent an
  entity ID. If the request needs a device that is not in the inventory,
  set possible to false and explain what is missing in reason.
- config must be the COMPLETE automation configuration as a JSON object.
  Do not include an `id` key.
- Give the automation a short, human alias and a sensible `description`.
- Pick a `mode` that fits (e.g. `restart` for motion-timeout patterns).
- summary is one sentence of what the automation does, written for the
  person who asked.
"""

_TRIGGER_KEYS = ("trigger", "triggers")
_ACTION_KEYS = ("action", "actions")

_WORD_RE = re.compile(r"[a-z0-9_]{3,}")

# Small domains that requests routinely need ("when someone is home") but
# rarely name explicitly — always in the inventory.
_ALWAYS_INCLUDE_DOMAINS = ("person", "zone")


def _relevant_entities(
    hass: HomeAssistant, description: str
) -> list[tuple[str, str]]:
    """Score entities against the description; return (entity_id, name)."""
    tokens = set(_WORD_RE.findall(description.lower()))
    always: list[tuple[str, str]] = []
    scored: list[tuple[int, str, str]] = []
    for state in hass.states.async_all():
        name = str(state.attributes.get("friendly_name") or "")
        if state.entity_id.split(".", 1)[0] in _ALWAYS_INCLUDE_DOMAINS:
            always.append((state.entity_id, name))
            continue
        haystack = f"{state.entity_id} {name}".lower()
        score = sum(1 for token in tokens if token in haystack)
        if score:
            scored.append((score, state.entity_id, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    matched = [
        (entity_id, name) for _, entity_id, name in scored[:MAX_INVENTORY_ENTITIES]
    ]
    return sorted(always) + matched


def build_draft_prompt(hass: HomeAssistant, description: str) -> str:
    """Assemble the draft prompt with a relevance-filtered inventory."""
    inventory = _relevant_entities(hass, description)
    parts = ["Request:", description.strip(), ""]
    if inventory:
        parts.append(
            "Entity inventory (the ONLY entity IDs you may reference):"
        )
        parts += [
            f"- {entity_id}" + (f" ({name})" if name else "")
            for entity_id, name in inventory
        ]
    else:
        parts.append(
            "No entities matched this request. If you cannot express the "
            "automation with zero entity references (e.g. purely "
            "time-based with a notification), set possible to false."
        )
    parts += [
        "",
        "Author the automation now, or set possible to false with a reason.",
    ]
    return "\n".join(parts)


def _structure_ok(config: dict) -> bool:
    return any(config.get(k) for k in _TRIGGER_KEYS) and any(
        config.get(k) for k in _ACTION_KEYS
    )


async def draft_automation(
    hass: HomeAssistant,
    client: OllamaClient,
    description: str,
    source: str,
    timeout_s: int,
    temperature: float,
) -> Draft:
    """Turn a description into a gated Draft.

    Raises HomeAssistantError with a user-readable reason when the model
    declines or the proposal fails a gate; raises OllamaError on
    transport/model failure.
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": build_draft_prompt(hass, description)},
    ]
    known = {state.entity_id for state in hass.states.async_all()}
    last_problem = ""

    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        result = await client.chat_structured_messages(
            messages, DRAFT_SCHEMA, timeout_s, temperature
        )

        if not result.get("possible"):
            reason = str(result.get("reason") or "").strip()
            raise HomeAssistantError(
                reason
                or "The model could not map this request onto your entities"
            )

        config = result.get("config")
        problem: str | None = None
        if not isinstance(config, dict) or not _structure_ok(config):
            problem = (
                "is not a complete automation (it needs both triggers "
                "and actions)"
            )
        else:
            config = dict(config)
            config.pop("id", None)
            alias = str(result.get("alias") or "").strip() or "New automation"
            config["alias"] = alias
            invented = extract_entity_references(config) - known
            if invented:
                problem = (
                    "references entities that do not exist: "
                    f"{', '.join(sorted(invented))} — use only entity IDs "
                    "from the inventory"
                )
            else:
                validation_error = await ha_validation_error(hass, config)
                if validation_error is not None:
                    problem = (
                        "failed Home Assistant config validation: "
                        f"{validation_error}"
                    )

        if problem is None:
            summary = str(result.get("summary") or "").strip() or alias
            if attempt > 1:
                _LOGGER.info(
                    "Draft self-corrected on attempt %d: %r",
                    attempt,
                    description,
                )
            return Draft(
                draft_id=uuid4().hex,
                alias=alias,
                summary=summary,
                explanation=str(result.get("explanation") or "").strip(),
                config=config,
                yaml=yaml_dump(config).strip(),
                source=source,
                model=client.model,
                created_at=dt_util.utcnow(),
            )

        last_problem = problem
        _LOGGER.info(
            "Draft attempt %d/%d for %r rejected: %s",
            attempt,
            LLM_MAX_ATTEMPTS,
            description,
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
                        f"REJECTED: your config {problem}. Return a "
                        "corrected COMPLETE config that fixes exactly this "
                        "problem and still does what was requested. If you "
                        "cannot, set possible to false with a reason."
                    ),
                }
            )

    raise HomeAssistantError(
        "The model couldn't produce a valid automation after "
        f"{LLM_MAX_ATTEMPTS} attempts — last error: {last_problem}"
    )
