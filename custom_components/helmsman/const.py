"""Constants for the Helmsman integration."""

from __future__ import annotations

DOMAIN = "helmsman"

CONF_OLLAMA_URL = "ollama_url"
CONF_MODEL = "model"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"
CONF_STALE_DAYS = "stale_days"

DEFAULT_OLLAMA_URL = "http://johns-macmini.lan:11434"
DEFAULT_MODEL = "qwen2.5-coder:14b"
DEFAULT_SCAN_INTERVAL_HOURS = 24
DEFAULT_STALE_DAYS = 90

SERVICE_RUN_AUDIT = "run_audit"
SERVICE_REVIEW_AUTOMATION = "review_automation"

PLATFORMS: list[str] = ["sensor"]

ATTR_FINDINGS = "findings"
ATTR_LAST_AUDIT = "last_audit"
ATTR_AUTOMATIONS_AUDITED = "automations_audited"
ATTR_SUGGESTIONS = "suggestions"
ATTR_LAST_REVIEW = "last_review"

MAX_FINDINGS_IN_ATTRIBUTES = 50
MAX_SUGGESTIONS_IN_ATTRIBUTES = 20

# LLM review pass (MVP-2)
MAX_REVIEWS_PER_PASS = 10
LLM_REQUEST_TIMEOUT_S = 180
LLM_TEMPERATURE = 0.2
