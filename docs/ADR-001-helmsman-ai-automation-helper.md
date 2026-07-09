# ADR-001: "Helmsman" — AI-Powered Home Assistant Automation Auditor & Helper

**Status:** Proposed
**Date:** 2026-07-08
**Deciders:** Project owner

## Context

The home lab's Home Assistant has accumulated a body of automations that would benefit from systematic review: stale entity references, overlapping triggers, missing conditions, deprecated syntax, and missed opportunities for new automations. Existing AI tooling for this is unsatisfying:

- **AI Automation Suggester** (HACS, v1.5.7, actively maintained, MIT) suggests *new* automations from entity/device scans and supports Ollama, but it delivers suggestions as notifications requiring **manual copy-paste**, and it does not audit or rewrite *existing* automations.
- **Home Assistant's built-in Ollama integration** provides a conversation agent (Assist) that can control devices, but it does not manage automation lifecycle (audit → improve → apply).
- **HA's MCP server / external agents** can act on HA, but push the work outside HA's UI and depend on an evolving MCP surface.

The gap: a tool that **audits existing automations, proposes concrete improvements, and applies them directly to HA after approval — zero copy-paste**.

**Constraints (decided upfront):**

- LLM backend: **local Ollama first** — existing on-LAN inference hardware (an Apple-silicon Mac, primary, plus a ROCm box) already serves a large model library. No HA data leaves the LAN.
- Form factor: **HACS custom integration** — runs inside HA, native UI, direct object access.
- Write model: **suggest + apply on approval** — human in the loop, never autonomous writes.
- Project naming: nautical theme → working name **Helmsman** (steers your automations).

## Decision

Build **Helmsman**, a purpose-built HACS custom integration that (1) audits existing automations against a rules + LLM pipeline, (2) generates improved YAML via local Ollama, (3) applies approved changes through HA's own automation config API — the same path the built-in automation editor uses — so changes take effect immediately with no copy-paste, and (4) creates **net-new automations** from a plain-language description or from proactive entity/device/area scans, flowing through the same validation gate and approval UI as audit fixes. Reuse ideas (and, where sensible, MIT-licensed provider-abstraction code) from AI Automation Suggester rather than forking it wholesale.

## Options Considered

### Option A: Adopt/extend AI Automation Suggester (fork or upstream PRs)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low–Med (plumbing exists; core features don't) |
| Cost | Free; Ollama already supported |
| Scalability | Limited — architecture is "scan entities → notify suggestion" |
| Team familiarity | High (HACS, Python, HA config flow) |

**Pros:** Mature provider abstraction (Ollama, Anthropic, OpenAI, etc.); HACS packaging, config flow, and update cadence solved; active upstream (40 releases); could land audit features as PRs and benefit the community.
**Cons:** Its core loop is *new-automation suggestion via notification with copy-paste* — the two features that matter here (auditing existing automations, direct apply-on-approval) are architectural additions, not tweaks; upstream may not want direct-write features (liability/scope); fork divergence becomes a maintenance tax.

### Option B: Purpose-built HACS custom integration ("Helmsman") — chosen

| Dimension | Assessment |
|-----------|------------|
| Complexity | Med–High (custom panel + diff/approval flow is the hard part) |
| Cost | Free (local inference on existing hardware) |
| Scalability | Good — designed around audit/apply from day one |
| Team familiarity | High (Python, YAML, k8s-adjacent GitOps thinking) |

**Pros:** Runs *inside* HA — direct access to entity/device/area registries, automation state (`last_triggered`, `current` mode), traces, and logs, giving far richer audit context than any external API consumer; applies changes via HA's internal automation config endpoint (`POST /api/config/automation/config/{id}` — the automation editor's own path, auto-reloads on write); approval UX can use HA-native surfaces (Repairs-style issues or a custom sidebar panel with YAML diff); snapshot-before-write gives one-click rollback.
**Cons:** Most engineering effort; coupled to HA's release cycle and *internal* (not formally documented) config API — needs a CI smoke test against HA release candidates; Ollama-class models are weaker at complex YAML reasoning than frontier models, so output must be schema-validated before it's ever shown as a suggestion.

### Option C: External service on k3s / MCP-based agent

| Dimension | Assessment |
|-----------|------------|
| Complexity | Med (service) + Med (HA API surface) |
| Cost | Free; GitOps-deployable via ArgoCD |
| Scalability | Best for multi-instance (both sites' HAs) |
| Team familiarity | Very high (FastAPI/k3s/ArgoCD is the PatchPilot pattern) |

**Pros:** Fits the existing GitOps stack; own UI; decoupled from HA releases; one service could serve HA at both sites over the WireGuard tunnel.
**Cons:** Fails the "no copy-paste, native feel" requirement — everything goes over WebSocket/REST with a long-lived token; weaker context (no in-process registry/trace access); another always-on service and cross-site dependency (tunnel-down = tool-down, a known failure mode).

## Trade-off Analysis

The decisive requirement is **direct integration with audit of existing automations**. Option A solves distribution but not the product; retrofitting audit + direct-write into a notification-centric codebase costs nearly as much as building clean, without control of the architecture. Option C matches existing skills (FastAPI on k3s) but structurally reproduces the copy-paste-era distance from HA — API-only access to traces and registries, token management, and a cross-site availability dependency. Option B costs the most engineering but is the only option where the two core features are load-bearing walls instead of extensions. The main risks of B — internal API drift and local-model YAML quality — are both mitigable: pin/smoke-test against HA betas, and gate every LLM output through `hass` config validation (`voluptuous` schemas / `automation` config validation) plus Ollama structured-output (JSON schema) so malformed YAML is rejected before a human ever sees it.

## Architecture Sketch (Option B)

- **Collector:** in-process read of automation configs, entity/device/area registries, `last_triggered`, automation traces, and recent error logs.
- **Rules pass (no LLM):** deterministic lints — references to unavailable/renamed entities, deprecated `service:` syntax, `mode: single` collisions evidenced in traces, automations never triggered in N days.
- **LLM pass (Ollama):** per-automation review + improvement proposal; a coder-class local model via the configured Ollama URL, structured JSON output, temperature low. Provider layer kept pluggable (Anthropic API as optional escalation later — hybrid was explicitly deferred, not rejected).
- **Validation gate:** every proposed YAML must pass HA's automation config validation before becoming a suggestion.
- **Approval UI:** sidebar panel listing findings with side-by-side YAML diff → Approve / Dismiss / Edit.
- **New-automation suggester:** two inputs — a natural-language "describe it" box and proactive suggestions from unlinked entity/device/area patterns — both producing draft automations that pass the same validation gate and approval UI before creation; new automations are created disabled by default.
- **Apply + rollback:** on approve, snapshot current config to integration storage, write via the automation config API (auto-reload), keep last N versions for one-click revert.

## Consequences

- **Easier:** automation hygiene becomes a review queue instead of manual YAML archaeology; new-device automation ideas arrive pre-validated; all inference stays on-LAN.
- **Harder:** every HA monthly release is a potential breaking change (internal config API, frontend panel APIs); a custom Lovelace panel means some TypeScript/Lit work, not just Python.
- **Revisit:** hybrid LLM escalation (Anthropic API) if local-model suggestion quality disappoints; multi-site support (a second site's HA) — Option C's strength — could later be added as a "remote HA" provider without changing the core; upstreaming the rules-pass linter to AI Automation Suggester.

## Action Items

1. [ ] Verify the automation config write path (`POST /api/config/automation/config/{id}`) and in-process equivalents against the current HA version *on a test HA instance, not production*
2. [x] Scaffold HACS integration skeleton (config flow: Ollama URL, model, audit schedule) — reference AI Automation Suggester's provider layer (MIT)
3. [x] MVP-1: Collector + rules pass, findings surfaced as Repairs issues (no LLM, no writes)
4. [x] MVP-2: Ollama review pass with schema-validated suggestions, still read-only
5. [x] MVP-3: Approval panel + snapshot/apply/rollback
6. [x] MVP-4: New-automation creation — describe-it box + proactive suggestion cards, created disabled by default, reusing the MVP-2 LLM pipeline and MVP-3 approval/apply path
7. [ ] Benchmark 2–3 local models (qwen3-coder, llama, devstral-class) on a fixed set of real automations before locking the default
8. [ ] Add CI smoke test against HA release candidates (config API contract)
