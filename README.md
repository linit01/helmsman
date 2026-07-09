# Helmsman

**AI-assisted automation helper for Home Assistant.** Helmsman audits your existing automations, surfaces problems in its panel with fixes attached, proposes LLM-generated improvements and brand-new automations you can apply with one click — no copy-paste, powered by local Ollama. It even benchmarks your Ollama server's models against your own automations and recommends the best one.

> Part of the Beacon Ecosystem. See `docs/ADR-001` for the architecture decision record.

## Status

All four planned milestones are implemented; current releases harden them based on real-world use. Writes happen **only** on explicit approval in the panel, always behind a config snapshot, and only to `automations.yaml`.

| Milestone | Scope | Status |
|-----------|-------|--------|
| MVP-1 | Collector + rules pass, Repairs issues, findings sensor | Done |
| MVP-2 | Ollama review pass, schema-validated suggestions (read-only) | Done |
| MVP-3 | Approval panel, snapshot/apply/rollback | Done |
| MVP-4 | New-automation creation: describe-it box + proactive suggestions, same validation/approval flow | Done |
| 0.4.x–0.5.x | Hardening: visible review progress and per-automation outcomes, auto-tuned LLM timeouts from measured model speed, model benchmark with recommendation | Done |

Updates ship as [GitHub releases](https://github.com/linit01/helmsman/releases); HACS surfaces them as standard update entities in **Settings → Updates**, with the release notes shown in the update dialog.

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

Error and Warning findings appear at the top of the Helmsman panel under **Needs attention**, right next to the tools that fix them (as of 0.9 Helmsman no longer publishes to Settings → Repairs — fixing happens where the fixes live). The full list including info findings is in the panel's Findings table and on `sensor.helmsman_findings`.

## Requirements

| Requirement | Needed for | Notes |
|-------------|-----------|-------|
| Home Assistant 2025.1+ | Everything | The in-panel brand icon needs 2026.3+ |
| [HACS](https://hacs.xyz/docs/use/download/download/) | Installation | Or copy the folder manually (below) |
| [Ollama](https://ollama.com) server on your LAN | AI features only | Reviews, rewrites, drafts, and the model benchmark |

**Without Ollama, Helmsman still works**: rules-based audits, the panel's Needs attention and Findings sections, the stranded-automation replace/disable tools, and the panel all function with the Ollama URL left blank. The AI features light up the moment you point Helmsman at a server.

### Setting up Ollama (one-time, ~10 minutes)

1. **Install Ollama** on any reasonably powerful machine on your network (a Mac, a gaming PC, a server with a GPU): [ollama.com/download](https://ollama.com/download) has installers for macOS, Windows, and Linux (Linux is a one-line script). Avoid running it on the HA host itself unless that machine has real horsepower.
2. **Pull a coder-class model** — automation configs are code, so coder models do best. From the [model library](https://ollama.com/library):
   - `ollama pull qwen2.5-coder:7b` — good starting point, runs in ~8 GB of RAM/VRAM
   - `ollama pull qwen2.5-coder:14b` — better quality, ~16 GB
   - Have several pulled? Helmsman's built-in benchmark tests them against your own automations and recommends one.
3. **Expose Ollama to your network** — by default it listens only on `localhost`, so Home Assistant on another machine cannot reach it. Set `OLLAMA_HOST=0.0.0.0` per the [Ollama FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.md#how-do-i-configure-ollama-server) (systemd override on Linux, `launchctl setenv` on macOS).
4. **Verify from another machine**: `curl http://<ollama-host>:11434/api/tags` should return your model list. That same URL (without `/api/tags`) is what you enter in Helmsman's configuration.

All inference stays on your LAN — no automation data leaves your network.

## Installation

**Install on a test HA instance first.** Helmsman is read-only until you approve a change in the panel, but it is new code touching automation internals.

### HACS (custom repository)

1. HACS → ⋮ (top right) → Custom repositories
2. Add `https://github.com/linit01/helmsman`, category *Integration*
3. Back in HACS, search for **Helmsman**, open it → ⋮ → **Download**
4. Restart Home Assistant when prompted
5. Settings → Devices & Services → Add Integration → **Helmsman**

### Manual

Copy `custom_components/helmsman/` into your HA `config/custom_components/` directory and restart.

## Icon

Since Home Assistant 2026.3, custom integrations ship their own brand icons: the gold ship's wheel lives in [`custom_components/helmsman/brand/`](custom_components/helmsman/brand/) (`icon.png` 256×256, `icon@2x.png` 512×512, plus the `helmsman.svg` source) and appears automatically in Devices & Services — no `home-assistant/brands` PR required. On older HA versions a placeholder is shown; the sidebar panel shows the wheel regardless, as it's embedded in the panel itself.

## Configuration

| Option | Default | Notes |
|--------|---------|-------|
| Ollama server URL | *(blank)* | e.g. `http://192.168.1.50:11434` — see [Requirements](#requirements); blank disables the AI features |
| Ollama model | `qwen2.5-coder:14b` | Any pulled model that handles JSON structured output; use the panel's benchmark to pick |
| Audit interval | 24 h | 1–168 |
| Stale threshold | 90 days | 7–365 |

## Usage

- Audits run automatically on the configured interval and once at startup.
- Run one on demand: **Developer Tools → Actions → `helmsman.run_audit`**.
- Findings: the panel's **Needs attention** section (errors/warnings) and Findings table, plus `sensor.helmsman_findings` (counts in state, details in attributes).
- With Ollama configured, automations flagged with errors/warnings are reviewed in the background after each **scheduled** audit (up to 10 per pass). Startup and manual audits (panel button, run_audit service) are rules-only — automatic reviews follow the schedule; start one yourself with Review flagged whenever you like. Proposals appear on `sensor.helmsman_suggestions` — the proposed YAML is in the attributes.
- Review any single automation on demand: **Developer Tools → Actions → `helmsman.review_automation`** with the automation selected, or leave it empty to review all flagged ones. Reviews run in the background: the panel shows a progress banner (N/M automations), and a **Last review details** table records the outcome for every automation — no changes suggested, rejected by a gate (with the reason), suggestion held, or skipped.
- **Timeouts are self-tuning.** A quick probe measures the model's real speed (Ollama reports token timings), and each automation's time budget is predicted from its config size at that speed. Automations predicted to take over 10 minutes are skipped with a note quoting the prediction — a faster server or model includes them again automatically. Nothing to configure.
- **Helmsman panel** (sidebar, admin-only): suggestions as side-by-side diffs with Approve/Dismiss, the findings table, and per-automation snapshots with Roll back. Applying reloads automations immediately.
- Only automations with an `id` in `automations.yaml` (i.e. everything the UI editor manages) can be applied to; package/include-managed YAML is detected and refused.
- **New automation** (panel, top section): type what should happen in plain language and hit **Draft it**. The draft — same three validation gates as review suggestions — appears as a card with summary, explanation, and YAML. **Create automation** writes it disabled; enable it from the automations page when ready. Also scriptable via `helmsman.draft_automation`.
- **Helmsman noticed**: after each audit, a registry scan flags motion sensors that share an area with lights but aren't referenced by any automation. Motion sensors that belong to camera devices are labeled *camera motion* and sorted after standalone sensors — still offered (camera-driven outdoor lighting is a real pattern), just not ahead of dedicated PIRs. **Draft it** feeds the suggested wording through the same draft pipeline; **Dismiss** is remembered permanently.
- **Stranded automations** (panel): automations referencing entities that no longer exist get a card with three ways forward — pick replacement entities from same-domain dropdowns (applied through snapshot/rollback), **Rewrite with AI** (the model redesigns the automation around currently available entities and the result arrives as a normal diff suggestion), or **Disable** the automation in one click.
- **Model benchmark** (panel, Model section): Helmsman lists the models on your Ollama server, ranks the plausible candidates (coder-class preferred, embedding/vision models excluded, current model always included), and benchmarks up to four against a sample of your own automations — smallest flagged plus median size. The results table shows measured speed, valid-proposal rate, and average time per automation, badges the recommended winner, and a **Use** button switches models in one click (the integration reloads; results survive). Point the URL at a different Ollama server and re-run to compare hardware the same way.

- **Deterministic fixes, no AI needed**: deprecated-syntax findings (`service:` → `action:`, trigger `platform:` → `trigger:`) are fixed by the rules engine directly — the corrected config appears as a suggestion right after the audit, marked "deterministic rules — no AI", validated like everything else, applied through the same approve/snapshot/rollback flow.

### Suggestion gates

A rejected proposal is not the end: Helmsman feeds the exact rejection back to the model and demands a corrected config, up to 3 attempts, before reporting failure. An LLM proposal is discarded (never shown) unless it:

1. Is a complete automation config with triggers and actions.
2. References only entities that exist (or that the original automation already referenced) — no invented entity IDs.
3. Passes the same config validation Home Assistant's automation editor uses.

Rejections aren't silent: the reason (including HA's validation error text) appears in the panel's Last review details table or the draft error banner. Resolved findings clear from Needs attention automatically on the next audit.

## Development notes

### CI

GitHub Actions runs three jobs on every push and weekly on a schedule: **contract tests** (pytest against both the current Home Assistant release and the beta channel — asserting the internal surfaces Helmsman relies on: config validator call, `raw_config` access, panel registration), **hassfest**, and **HACS validation**. The weekly schedule is the early-warning system: it fails while a breaking HA change is still in beta, before any user updates.


- Raw automation configs are read in-process from the automation entity component (`raw_config`) — the same config the built-in automation editor operates on. If that internal surface moves in a future HA release, the collector degrades gracefully to state-based rules only and logs a warning.
- Rules are pure functions (`rules.py`) with no HA imports; run the unit assertions with plain Python.
- Entity extraction is conservative (explicit `entity_id`/`entities` keys plus a domain-restricted regex over templates) to keep false positives low.

## License

MIT
