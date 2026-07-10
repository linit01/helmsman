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
import time
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as yaml_dump

from .collector import extract_entity_references, relevant_entities
from .const import LLM_MAX_ATTEMPTS
from .fixers import sanitize_llm_config
from .models import Draft
from .ollama import OllamaClient, OllamaError
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

Before writing, break the request into its parts and implement EVERY one:
- WHEN it should run -> triggers
- ANY qualifier that limits when it runs -> conditions
- WHAT it should do -> actions
Never silently drop a part of the request. If the request says "when X,
if Y, do Z", the automation must have a trigger for X, a condition for Y,
and an action for Z.

Rules:
- Use modern Home Assistant syntax: `triggers:` / `conditions:` /
  `actions:` blocks, `trigger:` for the trigger type, and `action:` for
  service calls.
- The trigger must fire on the REAL-WORLD event named in the request. If
  the request is about a physical thing (a door, a motion sensor, a
  person arriving), trigger on that device's entity. NEVER trigger on an
  `automation.*` entity unless the request is explicitly about another
  automation.
- Every time-of-day or day-of-week qualifier becomes a condition, never a
  dropped clause: "after sunset"/"before sunrise"/"at night" -> a SINGLE
  `sun.sun` state condition (below_horizon = night, above_horizon = day);
  a clock time -> a `time` condition; "on weekdays"/"on weekends" -> a
  `time` condition with `weekday`. An empty `conditions` list is only
  correct when the request states no qualifier at all.
- NEVER put 'sunset' or 'sunrise' in a `time` condition — its `after`/
  `before` accept ONLY clock times like "07:00" and Home Assistant
  rejects the config outright. And never add a redundant second condition
  for the same qualifier: one night check is enough.

Worked example — request "turn on the kitchen light when the garage door
opens after sunset but before sunrise" becomes exactly this config:
  triggers:
  - trigger: state
    entity_id: cover.garage_door
    to: open
  conditions:
  - condition: state
    entity_id: sun.sun
    state: below_horizon
  actions:
  - action: light.turn_on
    target:
      entity_id: light.kitchen
(Use the real door and light entity IDs from the inventory. Note: ONE
sun.sun condition, no `time` condition, and the trigger is the door
itself — never an automation.* entity.)
- Only reference entity IDs from the provided inventory (plus `sun.sun`
  for day/night). NEVER invent an entity ID. If the request needs a device
  that is not in the inventory, set possible to false and explain what is
  missing in reason.
- config must be the COMPLETE automation configuration as a JSON object.
  Do not include an `id` key.
- Give the automation a short, human alias and a sensible `description`.
- Pick a `mode` that fits (e.g. `restart` for motion-timeout patterns).
- summary is one sentence of what the automation does, written for the
  person who asked.
"""

_FIDELITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "faithful": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["faithful"],
}

_FIDELITY_SYSTEM_PROMPT = """\
You are Helmsman's automation checker. You are given ONE plain-language
request and the YAML automation that another model wrote for it. Decide
whether the automation faithfully implements EVERY part of the request.

Check clause by clause:
- Trigger: does it fire on the real-world event named in the request? An
  automation about a physical device (a door, a sensor, a person) that
  triggers on an `automation.*` entity is WRONG.
- Conditions: is every qualifier in the request expressed? A time-of-day
  or day-of-week phrase ("after sunset", "before sunrise", "at night",
  "on weekdays") that is stated in the request but absent from the config
  is a failure. An empty conditions list when the request states a
  qualifier is a failure.
- Action: does it do what was asked, to the thing that was asked?

Report ONLY requirements that are clearly unmet, each as one short phrase
in `problems`. If a clause is arguably satisfied, treat it as satisfied —
do not invent problems. If every part of the request is implemented, set
faithful to true and leave problems empty.
"""

_TRIGGER_KEYS = ("trigger", "triggers")
_ACTION_KEYS = ("action", "actions")

def build_draft_prompt(hass: HomeAssistant, description: str) -> str:
    """Assemble the draft prompt with a relevance-filtered inventory."""
    inventory = relevant_entities(hass, description, MAX_INVENTORY_ENTITIES)
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


async def _fidelity_problem(
    client: OllamaClient,
    description: str,
    yaml_text: str,
    timeout_s: int,
    temperature: float,
) -> str | None:
    """Ask the model whether the config implements the WHOLE request.

    The structural, entity-existence, and HA-validation gates only check
    that a draft is well-formed — a draft can drop a whole clause of the
    request ("after sunset") or trigger on a real-but-wrong entity and
    still pass all three. This is the semantic gate: it returns a problem
    string naming the unmet requirements, or None when the draft is
    faithful. It is best-effort — any transport/parse failure returns None
    rather than block an otherwise-valid draft.
    """
    prompt = (
        f"Request:\n{description.strip()}\n\n"
        f"Automation the other model produced (YAML):\n{yaml_text}\n\n"
        "Does this automation implement every part of the request?"
    )
    try:
        result = await client.chat_structured(
            _FIDELITY_SYSTEM_PROMPT,
            prompt,
            _FIDELITY_SCHEMA,
            timeout_s,
            temperature,
        )
    except OllamaError as err:
        _LOGGER.debug("Fidelity check unavailable, allowing draft: %s", err)
        return None
    if result.get("faithful"):
        return None
    problems = [
        text
        for text in (str(p).strip() for p in (result.get("problems") or []))
        if text
    ]
    if not problems:
        return None
    return "does not match the request: " + "; ".join(problems)


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
    # One budget for all attempts — see reviewer.review_automation.
    deadline = time.monotonic() + timeout_s * 1.5

    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining < 30:
            raise HomeAssistantError(
                "The model ran out of time after "
                f"{attempt - 1} attempt(s) — last error: "
                f"{last_problem or 'the first attempt did not finish'}"
            )
        result = await client.chat_structured_messages(
            messages, DRAFT_SCHEMA, int(remaining), temperature
        )

        if not result.get("possible"):
            reason = str(result.get("reason") or "").strip()
            raise HomeAssistantError(
                reason
                or "The model could not map this request onto your entities"
            )

        config = result.get("config")
        problem: str | None = None
        if isinstance(config, dict):
            config, removed_nulls = sanitize_llm_config(config)
            if removed_nulls:
                _LOGGER.debug(
                    "Stripped %d null entries from draft config",
                    removed_nulls,
                )
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
                    # WARNING so the payload reaches the panel's log
                    # viewer — same forensics as review rejections.
                    _LOGGER.warning(
                        "Rejected draft payload for %r: %s",
                        description,
                        json.dumps(config)[:1500],
                    )
                else:
                    # Well-formed and real — now check it actually does
                    # what was asked. Skip when almost no budget remains
                    # rather than burn the tail on a call that will time
                    # out (a missed fidelity check degrades to allowing
                    # the draft, never to a hard failure here).
                    remaining = deadline - time.monotonic()
                    if remaining >= 20:
                        fidelity = await _fidelity_problem(
                            client,
                            description,
                            yaml_dump(config).strip(),
                            int(remaining),
                            temperature,
                        )
                        if fidelity is not None:
                            problem = fidelity
                            _LOGGER.info(
                                "Draft failed fidelity check for %r: %s",
                                description,
                                fidelity,
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
