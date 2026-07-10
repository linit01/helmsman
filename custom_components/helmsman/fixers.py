"""Deterministic fixes for mechanical findings — no LLM involved.

Deprecated-syntax renames are pure mechanics (`service:` -> `action:`,
trigger `platform:` -> `trigger:`); a language model adds risk where
none is needed. These fixers transform the config directly; results
still pass HA validation and arrive as normal suggestions through the
same approve/apply/rollback flow.
"""

from __future__ import annotations

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
    words like a bare "actions") inside block lists, empty dicts, and
    choose-options flattened into bare action steps ({conditions,
    sequence} without the choose: wrapper — mapped onto if/then, which
    means exactly the same thing). Payload containers are untouched.
    """
    return _sanitize(node, None)


_JUNK_STRINGS = frozenset(
    {"", "none", "null", "actions", "action", "conditions", "condition",
     "triggers", "trigger", "sequence"}
)

_ACTION_LIST_KEYS = frozenset(
    {"actions", "action", "sequence", "then", "else"}
)


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
        return out, fixed
    if isinstance(node, list):
        items = []
        fixed = 0
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
