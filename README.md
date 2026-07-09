# Helmsman

**AI-assisted automation helper for Home Assistant.** Helmsman audits your existing automations, surfaces problems as native Repairs issues, and (in later milestones) proposes LLM-generated improvements and brand-new automations you can apply with one click — no copy-paste, powered by local Ollama.

> Part of the Beacon Ecosystem. See `docs/ADR-001` for the architecture decision record.

## Status: MVP-1 (rules-only, read-only)

This milestone is **deterministic lint rules only**. No LLM calls are made and **nothing ever writes to your automations**. Ollama settings are collected in the config flow but unused until MVP-2.

| Milestone | Scope | Status |
|-----------|-------|--------|
| MVP-1 | Collector + rules pass, Repairs issues, findings sensor | This release |
| MVP-2 | Ollama review pass, schema-validated suggestions (read-only) | Planned |
| MVP-3 | Approval panel, snapshot/apply/rollback | Planned |
| MVP-4 | New-automation creation: describe-it box + proactive suggestions, same validation/approval flow | Planned |

## Rules

| Rule | Severity | What it catches |
|------|----------|-----------------|
| `missing_entity` | Error | References to entities that no longer exist (renamed/removed) |
| `unavailable_entity` | Warning | References to entities currently unavailable |
| `deprecated_service_key` | Warning | Legacy `service:` syntax (renamed to `action:` in HA 2024.8) |
| `deprecated_trigger_platform` | Warning | Legacy trigger `platform:` syntax (renamed in HA 2024.10) |
| `never_triggered` | Info | Enabled automations that have never fired |
| `stale_automation` | Info | No trigger within the stale window (default 90 days) |
| `single_mode_with_waits` | Info | `mode: single` plus delay/wait — re-triggers get dropped |

Error and Warning findings appear in **Settings → Repairs**. Info findings appear only on the `sensor.helmsman_findings` attributes (Repairs has no info severity, and stale-automation notices would be noise there).

## Installation

**Install on a test HA instance first.** Helmsman is read-only, but it is new code touching automation internals.

### HACS (custom repository)

1. HACS → ⋮ (top right) → Custom repositories
2. Add `https://github.com/linit01/helmsman`, category *Integration*
3. Back in HACS, search for **Helmsman**, open it → ⋮ → **Download**
4. Restart Home Assistant when prompted
5. Settings → Devices & Services → Add Integration → **Helmsman**

### Manual

Copy `custom_components/helmsman/` into your HA `config/custom_components/` directory and restart.

## Configuration

| Option | Default | Notes |
|--------|---------|-------|
| Ollama server URL | `http://johns-macmini.lan:11434` | Optional; unused in MVP-1. Leave blank to skip validation. |
| Ollama model | `qwen2.5-coder:14b` | Unused in MVP-1 |
| Audit interval | 24 h | 1–168 |
| Stale threshold | 90 days | 7–365 |

## Usage

- Audits run automatically on the configured interval and once at startup.
- Run one on demand: **Developer Tools → Actions → `helmsman.run_audit`**.
- Findings: **Settings → Repairs** and `sensor.helmsman_findings` (counts in state, details in attributes).
- Resolved findings clear their Repairs issues automatically on the next audit.

## Development notes

- Raw automation configs are read in-process from the automation entity component (`raw_config`) — the same config the built-in automation editor operates on. If that internal surface moves in a future HA release, the collector degrades gracefully to state-based rules only and logs a warning.
- Rules are pure functions (`rules.py`) with no HA imports; run the unit assertions with plain Python.
- Entity extraction is conservative (explicit `entity_id`/`entities` keys plus a domain-restricted regex over templates) to keep false positives low.

## License

MIT
