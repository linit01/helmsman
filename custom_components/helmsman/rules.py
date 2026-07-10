"""Deterministic lint rules for the Helmsman audit pass (MVP-1, no LLM).

Each rule is a pure function taking an AutomationInfo and a RuleContext and
returning zero or more Findings. Rules never mutate anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from .models import AutomationInfo, Finding, Severity


@dataclass
class RuleContext:
    """Read-only view of the HA instance state needed by rules."""

    known_entity_ids: set[str]
    unavailable_entity_ids: set[str]
    now: datetime
    stale_days: int
    # Entity-registry entries (not disabled) — an entity can be
    # registered but currently unloaded (integration down or still
    # starting), which is a different problem from "does not exist".
    registered_entity_ids: set[str] = field(default_factory=set)


RuleFunc = Callable[[AutomationInfo, RuleContext], list[Finding]]


def rule_missing_entity(info: AutomationInfo, ctx: RuleContext) -> list[Finding]:
    """R001: references to entities that are gone — or merely unloaded."""
    findings: list[Finding] = []
    for entity_id in sorted(info.referenced_entities - ctx.known_entity_ids):
        if entity_id in ctx.registered_entity_ids:
            findings.append(
                Finding(
                    rule_id="unloaded_entity",
                    severity=Severity.WARNING,
                    automation_entity_id=info.entity_id,
                    alias=info.alias,
                    summary=(
                        f"References {entity_id} — registered but not "
                        "loaded"
                    ),
                    detail=(
                        f"'{info.alias}' references {entity_id}, which "
                        "exists in the entity registry but currently has "
                        "no state. Its integration is probably failed or "
                        "still starting — check Devices & Services and "
                        "reload it; do not edit the automation."
                    ),
                )
            )
        else:
            findings.append(
                Finding(
                    rule_id="missing_entity",
                    severity=Severity.ERROR,
                    automation_entity_id=info.entity_id,
                    alias=info.alias,
                    summary=f"References non-existent entity {entity_id}",
                    detail=(
                        f"'{info.alias}' references {entity_id}, which is "
                        "not in the state machine or the entity registry. "
                        "It may have been renamed or removed."
                    ),
                )
            )
    return findings


def rule_unavailable_entity(info: AutomationInfo, ctx: RuleContext) -> list[Finding]:
    """R002: automation references an entity that is currently unavailable."""
    unavailable = sorted(info.referenced_entities & ctx.unavailable_entity_ids)
    return [
        Finding(
            rule_id="unavailable_entity",
            severity=Severity.WARNING,
            automation_entity_id=info.entity_id,
            alias=info.alias,
            summary=f"References unavailable entity {entity_id}",
            detail=(
                f"'{info.alias}' references {entity_id}, which exists but is "
                "currently unavailable. Triggers or actions using it will "
                "not behave as expected."
            ),
        )
        for entity_id in unavailable
    ]


def _config_uses_key(node: Any, key: str) -> bool:
    """Whether a config tree contains a given dict key anywhere."""
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_config_uses_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_config_uses_key(item, key) for item in node)
    return False


def rule_deprecated_service_key(
    info: AutomationInfo, ctx: RuleContext
) -> list[Finding]:
    """R003: actions use the legacy 'service:' key instead of 'action:'."""
    if not info.raw_config:
        return []
    actions = info.raw_config.get("action") or info.raw_config.get("actions")
    if actions is None or not _config_uses_key(actions, "service"):
        return []
    return [
        Finding(
            rule_id="deprecated_service_key",
            severity=Severity.WARNING,
            automation_entity_id=info.entity_id,
            alias=info.alias,
            summary="Uses legacy 'service:' syntax (renamed to 'action:')",
            detail=(
                f"'{info.alias}' calls services with the legacy 'service:' "
                "key. Home Assistant renamed it to 'action:' in 2024.8; the "
                "old key still works but is deprecated."
            ),
        )
    ]


def rule_deprecated_trigger_platform(
    info: AutomationInfo, ctx: RuleContext
) -> list[Finding]:
    """R004: triggers use the legacy 'platform:' key instead of 'trigger:'."""
    if not info.raw_config:
        return []
    triggers = info.raw_config.get("trigger") or info.raw_config.get("triggers")
    if triggers is None or not _config_uses_key(triggers, "platform"):
        return []
    return [
        Finding(
            rule_id="deprecated_trigger_platform",
            severity=Severity.WARNING,
            automation_entity_id=info.entity_id,
            alias=info.alias,
            summary="Uses legacy trigger 'platform:' syntax",
            detail=(
                f"'{info.alias}' defines triggers with the legacy 'platform:' "
                "key. Home Assistant renamed it to 'trigger:' in 2024.10; the "
                "old key still works but is deprecated."
            ),
        )
    ]


def rule_never_triggered(info: AutomationInfo, ctx: RuleContext) -> list[Finding]:
    """R005: enabled automation that has never fired."""
    if info.state != "on" or info.last_triggered is not None:
        return []
    return [
        Finding(
            rule_id="never_triggered",
            severity=Severity.INFO,
            automation_entity_id=info.entity_id,
            alias=info.alias,
            summary="Enabled but has never triggered",
            detail=(
                f"'{info.alias}' is enabled but has no recorded trigger. It "
                "may be new, dead logic, or waiting on a rare condition."
            ),
        )
    ]


def rule_stale_automation(info: AutomationInfo, ctx: RuleContext) -> list[Finding]:
    """R006: automation has not fired within the stale window."""
    if info.state != "on" or info.last_triggered is None:
        return []
    age = ctx.now - info.last_triggered
    if age <= timedelta(days=ctx.stale_days):
        return []
    return [
        Finding(
            rule_id="stale_automation",
            severity=Severity.INFO,
            automation_entity_id=info.entity_id,
            alias=info.alias,
            summary=f"Has not triggered in {age.days} days",
            detail=(
                f"'{info.alias}' last triggered {age.days} days ago "
                f"(threshold {ctx.stale_days}). It may reference conditions "
                "that no longer occur."
            ),
        )
    ]


_WAIT_KEYS = ("delay", "wait_template", "wait_for_trigger")


def rule_single_mode_with_waits(
    info: AutomationInfo, ctx: RuleContext
) -> list[Finding]:
    """R007: default 'single' mode combined with delays/waits."""
    if not info.raw_config or info.mode != "single":
        return []
    actions = info.raw_config.get("action") or info.raw_config.get("actions")
    if actions is None:
        return []
    if not any(_config_uses_key(actions, key) for key in _WAIT_KEYS):
        return []
    return [
        Finding(
            rule_id="single_mode_with_waits",
            severity=Severity.INFO,
            automation_entity_id=info.entity_id,
            alias=info.alias,
            summary="mode: single with delay/wait in actions",
            detail=(
                f"'{info.alias}' runs in 'single' mode but its actions "
                "contain a delay or wait. Re-triggers during the wait are "
                "silently dropped; 'restart' or 'queued' may be intended."
            ),
        )
    ]


ALL_RULES: tuple[RuleFunc, ...] = (
    rule_missing_entity,
    rule_unavailable_entity,
    rule_deprecated_service_key,
    rule_deprecated_trigger_platform,
    rule_never_triggered,
    rule_stale_automation,
    rule_single_mode_with_waits,
)


def run_rules(
    automations: list[AutomationInfo], ctx: RuleContext
) -> list[Finding]:
    """Run every rule against every automation."""
    findings: list[Finding] = []
    for info in automations:
        for rule in ALL_RULES:
            findings.extend(rule(info, ctx))
    return findings
