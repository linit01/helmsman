"""Data models for Helmsman."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Severity(StrEnum):
    """Finding severity.

    ERROR and WARNING findings are surfaced as Repairs issues.
    INFO findings appear only on the findings sensor.
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    """A single audit finding against one automation."""

    rule_id: str
    severity: Severity
    automation_entity_id: str
    alias: str
    summary: str
    detail: str = ""

    @property
    def issue_id(self) -> str:
        """Stable ID for the Repairs issue registry."""
        return f"{self.rule_id}_{self.automation_entity_id}"

    def as_dict(self) -> dict[str, str]:
        """Compact representation for sensor attributes."""
        return {
            "rule": self.rule_id,
            "severity": str(self.severity),
            "automation": self.automation_entity_id,
            "alias": self.alias,
            "summary": self.summary,
        }


@dataclass
class AutomationInfo:
    """Snapshot of one automation gathered by the collector."""

    entity_id: str
    alias: str
    automation_id: str | None
    state: str
    last_triggered: datetime | None
    mode: str
    raw_config: dict | None
    referenced_entities: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Suggestion:
    """An LLM-proposed improvement for one automation (MVP-2, read-only).

    improved_config has passed HA's automation config validation and the
    entity-existence gate before a Suggestion is ever constructed.
    """

    automation_entity_id: str
    alias: str
    summary: str
    explanation: str
    improved_config: dict
    improved_yaml: str
    model: str
    created_at: datetime

    def as_dict(self) -> dict[str, str]:
        """Compact representation for sensor attributes."""
        return {
            "automation": self.automation_entity_id,
            "alias": self.alias,
            "summary": self.summary,
            "explanation": self.explanation,
            "improved_yaml": self.improved_yaml,
            "model": self.model,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class AuditReport:
    """Result of one full audit pass."""

    findings: list[Finding] = field(default_factory=list)
    automations_audited: int = 0
    finished_at: datetime | None = None

    def count(self, severity: Severity) -> int:
        """Number of findings at a given severity."""
        return sum(1 for f in self.findings if f.severity is severity)
