"""Deterministic fixes for mechanical findings — no LLM involved.

Deprecated-syntax renames are pure mechanics (`service:` -> `action:`,
trigger `platform:` -> `trigger:`); a language model adds risk where
none is needed. These fixers transform the config directly; results
still pass HA validation and arrive as normal suggestions through the
same approve/apply/rollback flow.
"""

from __future__ import annotations

import json as _json
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
    state), bare `{condition: and}` operators flattened out of their
    sub-conditions, and duplicate conditions. Payload containers are
    untouched.
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
        return out, fixed + sun_fixed
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
