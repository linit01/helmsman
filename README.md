# Helmsman

**AI-assisted automation helper for Home Assistant.** Helmsman audits your existing automations, surfaces problems as native Repairs issues, and (in later milestones) proposes LLM-generated improvements and brand-new automations you can apply with one click — no copy-paste, powered by local Ollama.

> Part of the Beacon Ecosystem. See `docs/ADR-001` for the architecture decision record.

## Status: MVP-4 (new-automation creation)

Audits are deterministic lint rules; with Ollama configured, flagged automations get an LLM review pass that proposes improved YAML (validated before you ever see it). The **Helmsman sidebar panel** shows proposals as side-by-side diffs with **Approve and apply** / **Dismiss**, snapshots every change, and offers one-click rollback. New in MVP-4: describe a new automation in plain language and Helmsman drafts it — plus proactive "Helmsman noticed" cards for unlinked devices. New automations are **created disabled** so you enable them when ready. Writes happen **only** on explicit approval and only to `automations.yaml`.

| Milestone | Scope | Status |
|-----------|-------|--------|
| MVP-1 | Collector + rules pass, Repairs issues, findings sensor | Done |
| MVP-2 | Ollama review pass, schema-validated suggestions (read-only) | Done |
| MVP-3 | Approval panel, snapshot/apply/rollback | Done |
| MVP-4 | New-automation creation: describe-it box + proactive suggestions, same validation/approval flow | This release |

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

## Icon

Home Assistant does not read integration icons from the integration itself — the frontend fetches them from [home-assistant/brands](https://github.com/home-assistant/brands) by domain. Until Helmsman is in that repo, HA shows a placeholder ("icon not available") in Devices & Services and Repairs.

The brand assets (gold ship's wheel on deep blue) live in [`brand/`](brand/): `icon.png` (256×256), `icon@2x.png` (512×512), and the `helmsman.svg` source. To get the icon showing, open a one-time PR against `home-assistant/brands` adding the two PNGs under `custom_integrations/helmsman/`. The sidebar panel shows the wheel regardless — it's embedded in the panel itself.

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
- **New automation** (panel, top section): type what should happen in plain language and hit **Draft it**. The draft — same three validation gates as review suggestions — appears as a card with summary, explanation, and YAML. **Create automation** writes it disabled; enable it from the automations page when ready. Also scriptable via `helmsman.draft_automation`.
- **Helmsman noticed**: after each audit, a registry scan flags motion sensors that share an area with lights but aren't referenced by any automation. **Draft it** feeds the suggested wording through the same draft pipeline; **Dismiss** is remembered permanently.

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
