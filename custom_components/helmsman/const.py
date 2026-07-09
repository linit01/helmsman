"""Constants for the Helmsman integration."""

from __future__ import annotations

DOMAIN = "helmsman"

CONF_OLLAMA_URL = "ollama_url"
CONF_MODEL = "model"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"
CONF_STALE_DAYS = "stale_days"

DEFAULT_OLLAMA_URL = ""
DEFAULT_MODEL = "qwen2.5-coder:14b"
DEFAULT_SCAN_INTERVAL_HOURS = 24
DEFAULT_STALE_DAYS = 90

SERVICE_RUN_AUDIT = "run_audit"
SERVICE_REVIEW_AUTOMATION = "review_automation"
SERVICE_DRAFT_AUTOMATION = "draft_automation"

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
ABS_MAX_REVIEW_CONFIG_CHARS = 40000
MAX_PREDICTED_REVIEW_S = 600
MAX_LLM_TIMEOUT_S = 900
LLM_REQUEST_TIMEOUT_S = 300
LLM_TEMPERATURE = 0.2

# Model benchmark (0.5.0)
BENCHMARK_MAX_MODELS = 4
BENCHMARK_SAMPLES = 2
# Names matching these fragments cannot review automations (embedding,
# reranking, vision-first, or safety-classifier models).
BENCHMARK_EXCLUDE_FRAGMENTS = (
    "embed",
    "bge",
    "minilm",
    "nomic",
    "rerank",
    "llava",
    "moondream",
    "clip",
    "guard",
    "shield",
    "vision",
    "-vl",
    "vl:",
    "minicpm-v",
)
# Model family fragments (from /api/tags metadata) that mark vision or
# embedding models regardless of how they are named.
BENCHMARK_EXCLUDE_FAMILIES = (
    "clip",
    "mllama",
    "bert",
    "vl",
    "minicpmv",
)
# Below this parameter count (billions), models are too weak for
# structured automation YAML to be worth benchmarking.
BENCHMARK_MIN_PARAM_B = 3.0

# Apply / rollback / panel (MVP-3)
MAX_SNAPSHOTS_PER_AUTOMATION = 10
PANEL_URL_PATH = "helmsman"
PANEL_STATIC_BASE = "/helmsman_panel_static"
PANEL_JS_VERSION = "0.6.4"
