# Helmsman

**AI-assisted automation helper for Home Assistant.** Helmsman audits your existing automations, surfaces problems as native Repairs issues, and (in later milestones) proposes LLM-generated improvements and brand-new automations you can apply with one click — no copy-paste, powered by local Ollama.

> Part of the Beacon Ecosystem. See `docs/ADR-001` for the architecture decision record.

## Status: MVP-3 (approval panel, apply with rollback)

Audits are deterministic lint rules; with Ollama configured, flagged automations get an LLM review pass that proposes improved YAML (validated before you ever see it). New in MVP-3: a **Helmsman sidebar panel** shows each proposal as a side-by-side diff with **Approve and apply** / **Dismiss**. Applying snapshots the current config first — one-click rollback from the panel. Writes happen **only** on explicit approval and only to `automations.yaml` (the automation editor's own file).

| Milestone | Scope | Status |
|-----------|-------|--------|
| MVP-1 | Collector + rules pass, Repairs issues, findings sensor | Done |
| MVP-2 | Ollama review pass, schema-validated suggestions (read-only) | Done |
| MVP-3 | Approval panel, snapshot/apply/rollback | This release |
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
| Ollama server URL | `http://johns-macmini.lan:11434` | Leave blank to disable the LLM review pass |
| Ollama model | `qwen2.5-coder:14b` | Any local model that handles JSON structured output |
| Audit interval | 24 h | 1–168 |
| Stale threshold | 90 days | 7–365 |

## Usage

- Audits run automatically on the configured interval and once at startup.
- Run one on demand: **Developer Tools → Actions → `helmsman.run_audit`**.
- Findings: **Settings → Repairs** and `sensor.helmsman_findings` (counts in state, details in attributes).
- With Ollama configured, automations flagged with errors/warnings are reviewed in the background after each audit (up to 10 per pass). Proposals appear on `sensor.helmsman_suggestions` — the proposed YAML is in the attributes.
- Review any single automation on demand: **Developer Tools → Actions → `helmsman.review_automation`** with the automation selected, or leave it empty to review all flagged ones.
- **Helmsman panel** (sidebar, admin-only): suggestions as side-by-side diffs with Approve/Dismiss, the findings table, and per-automation snapshots with Roll back. Applying reloads automations immediately.
- Only automations with an `id` in `automations.yaml` (i.e. everything the UI editor manages) can be applied to; package/include-managed YAML is detected and refused.

### Suggestion gates

An LLM proposal is discarded (never shown) unless it:

1. Is a complete automation config with triggers and actions.
2. References only entities that exist (or that the original automation already referenced) — no invented entity IDs.
3. Passes the same config validation Home Assistant's automation editor uses.
- Resolved findings clear their Repairs issues automatically on the next audit.

## Development notes

- Raw automation configs are read in-process from the automation entity component (`raw_config`) — the same config the built-in automation editor operates on. If that internal surface moves in a future HA release, the collector degrades gracefully to state-based rules only and logs a warning.
- Rules are pure functions (`rules.py`) with no HA imports; run the unit assertions with plain Python.
- Entity extraction is conservative (explicit `entity_id`/`entities` keys plus a domain-restricted regex over templates) to keep false positives low.

## License

MIT
