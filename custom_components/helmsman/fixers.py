"""Deterministic fixes for mechanical findings — no LLM involved.

Deprecated-syntax renames are pure mechanics (`service:` -> `action:`,
trigger `platform:` -> `trigger:`); a language model adds risk where
none is needed. These fixers transform the config directly; results
still pass HA validation and arrive as normal suggestions through the
same approve/apply/rollback flow.
"""

from __future__ import annotations

import json as _json
import re
from typing import Any

# Never rename keys inside payload containers: `service` there is data,
# not a service call (e.g. data: {service: ...} for some integrations).
_SKIP_DESCEND = frozenset(
    {"data", "data_template", "target", "variables", "event_data"}
)


def _fix_service_keys(node: Any) -> tuple[Any, int]:
    """Rename `service:` -> `action:` on action steps, recursively."""
    if isinstance(node, dict):
        fixed: dict = {}
        count = 0
        for key, value in node.items():
            if (
                key == "service"
                and isinstance(value, str)
                and "action" not in node
            ):
                fixed["action"] = value
                count += 1
            elif key in _SKIP_DESCEND:
                fixed[key] = value
            else:
                fixed_value, sub = _fix_service_keys(value)
                fixed[key] = fixed_value
                count += sub
        return fixed, count
    if isinstance(node, list):
        out = []
        count = 0
        for item in node:
            fixed_item, sub = _fix_service_keys(item)
            out.append(fixed_item)
            count += sub
        return out, count
    return node, 0


def _fix_platform_items(value: Any) -> tuple[Any, int]:
    """Rename `platform:` -> `trigger:` on trigger definitions."""
    if isinstance(value, list):
        out = []
        count = 0
        for item in value:
            fixed_item, sub = _fix_platform_items(item)
            out.append(fixed_item)
            count += sub
        return out, count
    if (
        isinstance(value, dict)
        and isinstance(value.get("platform"), str)
        and "trigger" not in value
    ):
        fixed = {
            ("trigger" if key == "platform" else key): item
            for key, item in value.items()
        }
        return fixed, 1
    return value, 0


def _fix_trigger_platforms(node: Any, in_trigger_block: bool) -> tuple[Any, int]:
    """Apply platform renames in trigger blocks and wait_for_trigger."""
    if isinstance(node, dict):
        fixed: dict = {}
        count = 0
        for key, value in node.items():
            if key in ("trigger", "triggers", "wait_for_trigger") and not (
                key == "trigger" and isinstance(value, str)
            ):
                fixed_value, sub = _fix_platform_items(value)
                fixed[key] = fixed_value
                count += sub
            elif key in _SKIP_DESCEND:
                fixed[key] = value
            else:
                fixed_value, sub = _fix_trigger_platforms(value, False)
                fixed[key] = fixed_value
                count += sub
        return fixed, count
    if isinstance(node, list):
        out = []
        count = 0
        for item in node:
            fixed_item, sub = _fix_trigger_platforms(item, in_trigger_block)
            out.append(fixed_item)
            count += sub
        return out, count
    return node, 0


def sanitize_llm_config(node: Any) -> tuple[Any, int]:
    """Normalize known LLM-output artifacts in automation configs.

    Small local models routinely emit junk that no prompt fully cures:
    null items (and their string ghosts, "None"/"null"/structural echo
    words like a bare "actions") inside block lists, empty dicts,
    choose-options flattened into bare action steps ({conditions,
    sequence} without the choose: wrapper — mapped onto if/then, which
    means exactly the same thing), `time` conditions that misuse
    'sunset'/'sunrise' (valid only in a `sun` condition or the `sun.sun`
    state), `state` triggers misused for a numeric threshold
    (`to: "above 25"` -> a `numeric_state` trigger), service-data fields
    stranded on a service call (`{action: notify.x, message: ...}` ->
    nested under `data:`), a non-dict service `target:` (a notify recipient
    list -> `data:`, a bare entity id -> `{entity_id: ...}`), legacy template
    syntax on a service call (`data_template:` folded into `data:`,
    `value_template`/`option_template` keys stripped to `value`/`option`),
    bare `{condition: and}` operators flattened out of their sub-conditions,
    and duplicate conditions. Payload containers are untouched.
    """
    return _sanitize(node, None)


_JUNK_STRINGS = frozenset(
    {"", "none", "null", "actions", "action", "conditions", "condition",
     "triggers", "trigger", "sequence"}
)

_SUN_WORDS = frozenset({"sunset", "sunrise"})


def _repair_sun_time_condition(cond: dict) -> tuple[dict, int]:
    """Repair a `time` condition that misuses 'sunset'/'sunrise'.

    HA's `time` condition rejects sun words outright ("Invalid time
    specified: sunset") — only a `sun` condition or the `sun.sun` state
    understands them. Small models routinely express a night/day window as
    `time` bounds ("after sunset", "before sunrise", or both) and cannot
    recover from the validation error on their own (observed: three
    attempts, all rejected).

    When the clause is purely about sun position (only sun-word bounds, no
    clock time, no extra keys) rewrite it to the canonical `sun.sun` state
    check — after sunset OR before sunrise = night (below_horizon); after
    sunrise OR before sunset = day (above_horizon). This also sidesteps the
    same-day midnight gotcha a `sun` condition with both bounds introduces,
    and — unlike bound-stripping — preserves the intent of a single-bound
    clause instead of leaving an empty `{condition: time}`. When a sun word
    is mixed with a real clock time, keep the clock bound and drop only the
    invalid sun-word bound so the remaining `time` condition still validates.
    """
    if cond.get("condition") != "time":
        return cond, 0
    after = cond.get("after")
    before = cond.get("before")
    aw = after.strip().lower() if isinstance(after, str) else None
    bw = before.strip().lower() if isinstance(before, str) else None
    if aw not in _SUN_WORDS and bw not in _SUN_WORDS:
        return cond, 0
    # A clock time in the other bound (or extra keys like weekday) means
    # we cannot reinterpret the whole clause; strip just the sun bound.
    has_clock = (aw is not None and aw not in _SUN_WORDS) or (
        bw is not None and bw not in _SUN_WORDS
    )
    only_bounds = set(cond) <= {"condition", "after", "before"}
    if only_bounds and not has_clock:
        # after sunset / before sunrise -> night; after sunrise / before
        # sunset -> day. Default to night if the pair is contradictory.
        is_day = aw == "sunrise" or bw == "sunset"
        is_night = aw == "sunset" or bw == "sunrise"
        state = "above_horizon" if is_day and not is_night else "below_horizon"
        return {
            "condition": "state",
            "entity_id": "sun.sun",
            "state": state,
        }, 1
    repaired = {
        key: value
        for key, value in cond.items()
        if not (
            key in ("after", "before")
            and isinstance(value, str)
            and value.strip().lower() in _SUN_WORDS
        )
    }
    return repaired, 1


_COMPARE_RE = re.compile(
    r"^\s*(above|over|greater than|more than|>=|>|"
    r"below|under|less than|<=|<)\s*([-+]?\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
_ABOVE_OPS = frozenset(
    {"above", "over", "greater than", "more than", ">=", ">"}
)


def _repair_numeric_state_trigger(node: dict) -> tuple[dict, int]:
    """Convert a `state` trigger misused for a numeric threshold.

    Models routinely write a threshold as a `state` trigger with a
    comparison string — `{trigger: state, to: "above 25"}`. That is
    schema-valid but SILENTLY BROKEN: no state ever equals the literal
    string "above 25", so the automation never fires. Rewrite it to the
    `numeric_state` trigger the model meant, preserving whichever type key
    it used (`trigger:` or the legacy `platform:`). A bare number in `to`
    ("25") is left alone — above vs below is genuinely ambiguous.
    """
    is_state = node.get("trigger") == "state" or node.get("platform") == "state"
    if not is_state:
        return node, 0
    to_val = node.get("to")
    if not isinstance(to_val, str):
        return node, 0
    match = _COMPARE_RE.match(to_val)
    if not match:
        return node, 0
    op = match.group(1).lower()
    raw = match.group(2)
    number = float(raw) if "." in raw else int(raw)
    bound = "above" if op in _ABOVE_OPS else "below"
    repaired = {k: v for k, v in node.items() if k not in ("to", "from")}
    if "trigger" in repaired:
        repaired["trigger"] = "numeric_state"
    else:
        repaired["platform"] = "numeric_state"
    repaired[bound] = number
    return repaired, 1


# Keys that are valid directly on a service/action call step. Everything
# else a model puts alongside `action:`/`service:` is service data sitting
# in the wrong place.
_ACTION_STEP_KEYS = frozenset(
    {
        "action", "service", "service_template",
        "target", "data", "data_template",
        "entity_id", "response_variable", "metadata",
        "alias", "enabled", "continue_on_error", "variables",
    }
)


def _service_name(node: dict) -> str | None:
    """The service id of an action step, or None if it is not a call."""
    for key in ("action", "service"):
        value = node.get(key)
        if isinstance(value, str):
            return value
    return None


def _repair_service_target(node: dict) -> tuple[dict, int]:
    """Fix a `target:` HA cannot accept on a service call.

    HA's service `target:` must be a dict of selectors (entity_id /
    device_id / area_id / ...). Two model mistakes produce "expected a
    dictionary for dictionary value @ ...['target']":

    - notify.* has NO HA target selector — its recipient is service DATA,
      not a target. A model writes `{action: notify.notify, target:
      [phone]}` and cannot recover from the error. Move `target:` into
      `data:` (merging, existing data wins), where the notify recipient
      belongs — yielding the canonical `data: {message, target}` form.
    - Any other service given a bare entity id or list of them
      (`target: light.kitchen`, `target: [light.a, light.b]`) — wrap it as
      `{entity_id: ...}`, the canonical target dict.

    A `target:` that is already a dict, or absent, is left untouched.
    """
    service = _service_name(node)
    if service is None:
        return node, 0
    target = node.get("target")
    if target is None or isinstance(target, dict):
        return node, 0
    if service.startswith("notify."):
        existing = node.get("data")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.setdefault("target", target)
        repaired = {
            key: value
            for key, value in node.items()
            if key not in ("target", "data")
        }
        repaired["data"] = merged
        return repaired, 1
    if isinstance(target, (str, list)):
        repaired = dict(node)
        repaired["target"] = {"entity_id": target}
        return repaired, 1
    return node, 0


def _hoist_service_data(node: dict) -> tuple[dict, int]:
    """Move stray service-data keys under `data:` on a service call.

    Small models routinely write a notify action as
    `{action: notify.phone, message: "..."}` — the `message`/`title`-style
    fields sit directly on the step instead of nested under `data:`. HA
    rejects the extra key outright ("extra keys not allowed @
    data['actions'][0]['message']") and the model, given only that terse
    error, often cannot place the key correctly on retry (observed: three
    attempts, all rejected).

    When the step is a service call (a string `action:`/`service:`), move
    every key that is not a valid action-step key into `data:`, merging
    with any existing `data:` (existing values win, so a model that got it
    right in one place is never clobbered). That is exactly the home those
    fields belong in, so the repair preserves intent and turns a hard
    validation failure into a passing config.
    """
    if not (
        isinstance(node.get("action"), str)
        or isinstance(node.get("service"), str)
    ):
        return node, 0
    stray = [key for key in node if key not in _ACTION_STEP_KEYS]
    if not stray:
        return node, 0
    existing = node.get("data")
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key in stray:
        merged.setdefault(key, node[key])
    repaired = {key: value for key, value in node.items() if key not in stray}
    repaired["data"] = merged
    return repaired, 1


_TEMPLATE_SUFFIX = "_template"


def _modernize_service_data(node: dict) -> tuple[dict, int]:
    """Modernize legacy/hallucinated template syntax on a service call.

    Two forms that pass HA CONFIG validation (which only checks that `data`
    is a dict) but break at RUNTIME when the service is actually called —
    exactly the silent failures the config gate cannot catch:

    - `data_template:` — the pre-2021 block for templated service data.
      Modern HA renders templates inline in `data:`, so fold data_template
      into `data:` (an existing `data:` key wins on a clash).
    - `X_template:` keys inside the data block (`value_template`,
      `option_template`, `message_template`, ...). Small models append
      `_template` to a key to signal "this is a template", but no service
      accepts those keys — the real key is the suffix-stripped name with the
      template inline (`value_template` -> `value`). Strip the suffix; if the
      plain key is already present, drop the redundant `_template` variant.

    Only touches service-call steps. Payload semantics are preserved — the
    template string itself is never altered, only the key it lives under.
    """
    if _service_name(node) is None:
        return node, 0
    original = node.get("data")
    data = dict(original) if isinstance(original, dict) else {}
    changed = 0

    data_template = node.get("data_template")
    if isinstance(data_template, dict):
        for key, value in data_template.items():
            data.setdefault(key, value)
        changed += 1

    modern: dict = {}
    for key, value in data.items():
        base = (
            key[: -len(_TEMPLATE_SUFFIX)]
            if key.endswith(_TEMPLATE_SUFFIX) and len(key) > len(_TEMPLATE_SUFFIX)
            else key
        )
        if base != key:
            changed += 1
            if base in data:
                # A correct plain key already exists — drop the variant.
                continue
        modern.setdefault(base, value)

    if changed == 0:
        return node, 0
    repaired = {
        key: value
        for key, value in node.items()
        if key not in ("data", "data_template")
    }
    if modern:
        repaired["data"] = modern
    return repaired, changed


def _is_bare_and_condition(item: Any) -> bool:
    """A logical `and` condition with no sub-conditions is invalid junk.

    Small models flatten `{condition: and, conditions: [...]}` into a bare
    `{condition: and}` plus its would-be children as siblings. HA rejects
    the empty operator ("required key not provided ... ['conditions']").
    Dropping it is safe: top-level conditions are already AND-ed, so the
    orphaned siblings keep the intended meaning. Only `and` is dropped —
    an empty `or`/`not` cannot be reconstructed and its intent (which is
    NOT implicit-AND) would be silently changed, so those are left to fail
    validation and drive a self-correction round instead.
    """
    return (
        isinstance(item, dict)
        and item.get("condition") == "and"
        and not item.get("conditions")
    )

_ACTION_LIST_KEYS = frozenset(
    {"actions", "action", "sequence", "then", "else"}
)

# List-valued keys that hold conditions — deduped, since a model that
# writes several night checks (or whose sun repairs converge on one)
# leaves redundant-but-valid duplicates that read as sloppy.
_CONDITION_LIST_KEYS = frozenset({"condition", "conditions"})


def _sanitize(node: Any, parent_key: str | None) -> tuple[Any, int]:
    if isinstance(node, dict):
        out: dict = {}
        fixed = 0
        for key, value in node.items():
            if key in _SKIP_DESCEND:
                out[key] = value
            else:
                clean, sub = _sanitize(value, key)
                out[key] = clean
                fixed += sub
        out, sun_fixed = _repair_sun_time_condition(out)
        out, num_fixed = _repair_numeric_state_trigger(out)
        out, data_fixed = _hoist_service_data(out)
        out, target_fixed = _repair_service_target(out)
        out, tmpl_fixed = _modernize_service_data(out)
        return out, (
            fixed + sun_fixed + num_fixed + data_fixed + target_fixed + tmpl_fixed
        )
    if isinstance(node, list):
        items = []
        fixed = 0
        dedup = parent_key in _CONDITION_LIST_KEYS
        seen: set[str] = set()
        for item in node:
            if item is None or (
                isinstance(item, str)
                and item.strip().lower() in _JUNK_STRINGS
            ):
                fixed += 1
                continue
            clean, sub = _sanitize(item, parent_key)
            fixed += sub
            if isinstance(clean, dict) and not clean:
                fixed += 1
                continue
            if _is_bare_and_condition(clean):
                fixed += 1
                continue
            if (
                parent_key in _ACTION_LIST_KEYS
                and isinstance(clean, dict)
                and "conditions" in clean
                and "sequence" in clean
                and not ({"choose", "if", "action", "service"} & set(clean))
            ):
                # A choose-option flattened into a bare action step —
                # if/then expresses the same conditional block validly.
                rebuilt = {
                    key: value
                    for key, value in clean.items()
                    if key not in ("conditions", "sequence")
                }
                rebuilt["if"] = clean["conditions"]
                rebuilt["then"] = clean["sequence"]
                clean = rebuilt
                fixed += 1
            if dedup and isinstance(clean, dict):
                marker = _json.dumps(clean, sort_keys=True, default=str)
                if marker in seen:
                    fixed += 1
                    continue
                seen.add(marker)
            items.append(clean)
        return items, fixed
    return node, 0


def apply_syntax_fixes(config: dict) -> tuple[dict, list[str]]:
    """All deterministic syntax fixes; returns (fixed_config, changes).

    The input config is not mutated. An empty changes list means the
    config was already modern.
    """
    fixed, service_count = _fix_service_keys(config)
    fixed, platform_count = _fix_trigger_platforms(fixed, False)
    changes = []
    if service_count:
        changes.append(
            f"renamed {service_count} legacy 'service:' "
            f"key{'s' if service_count != 1 else ''} to 'action:'"
        )
    if platform_count:
        changes.append(
            f"renamed {platform_count} legacy trigger 'platform:' "
            f"key{'s' if platform_count != 1 else ''} to 'trigger:'"
        )
    return fixed, changes
