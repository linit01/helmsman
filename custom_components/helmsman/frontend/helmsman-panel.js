/* Helmsman approval panel (MVP-3).
   Build-free vanilla web component: talks to the backend over the
   helmsman/* WebSocket commands and styles itself with HA theme vars. */

const STYLES = `
  :host { display: block; height: 100%; overflow-y: auto;
    background: var(--primary-background-color); color: var(--primary-text-color);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar { display: flex; align-items: center; gap: 12px; padding: 12px 16px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, #fff); position: sticky; top: 0; z-index: 2; }
  .toolbar h1 { font-size: 20px; font-weight: 400; margin: 0; flex: 1; }
  .toolbar .logo { width: 28px; height: 28px; flex: none; border-radius: 6px; }
  .toolbar .menu-btn { flex: none; background: none; border: none; color: inherit;
    padding: 4px; display: inline-flex; align-items: center; cursor: pointer; }
  .toolbar .menu-btn:hover { filter: none; opacity: 0.85; }
  .toolbar .meta { font-size: 12px; opacity: 0.85; }
  button { font: inherit; cursor: pointer; border-radius: 4px; padding: 6px 14px;
    border: 1px solid var(--divider-color); background: var(--card-background-color);
    color: var(--primary-text-color); }
  button:hover { filter: brightness(0.95); }
  button.primary { background: var(--primary-color); border-color: var(--primary-color);
    color: var(--text-primary-color, #fff); }
  button.danger { color: var(--error-color); border-color: var(--error-color); }
  button:disabled { opacity: 0.45; cursor: default; }
  .content { max-width: 1200px; margin: 0 auto; padding: 16px; }
  .section-title { font-size: 16px; font-weight: 500; margin: 24px 0 8px; }
  .card { background: var(--card-background-color); border-radius: 8px;
    box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,0.2));
    margin-bottom: 16px; overflow: hidden; }
  .card-header { display: flex; align-items: baseline; gap: 10px;
    padding: 12px 16px; border-bottom: 1px solid var(--divider-color); flex-wrap: wrap; }
  .card-header .alias { font-weight: 500; }
  .card-header .entity { font-size: 12px; color: var(--secondary-text-color); }
  .card-body { padding: 12px 16px; }
  .summary { margin: 0 0 4px; font-weight: 500; }
  .explanation { margin: 0 0 12px; color: var(--secondary-text-color); font-size: 14px; }
  .actions { display: flex; gap: 8px; padding: 10px 16px;
    border-top: 1px solid var(--divider-color); justify-content: flex-end; align-items: center; }
  .actions .note { margin-right: auto; font-size: 12px; color: var(--secondary-text-color); }
  .diff { display: grid; grid-template-columns: 1fr 1fr; gap: 0;
    font-family: var(--code-font-family, monospace); font-size: 12px;
    border: 1px solid var(--divider-color); border-radius: 4px; overflow-x: auto; }
  .diff .col { min-width: 0; }
  .diff .col + .col { border-left: 1px solid var(--divider-color); }
  .diff .col-title { padding: 4px 8px; font-weight: 600; font-family: inherit;
    background: var(--secondary-background-color); border-bottom: 1px solid var(--divider-color); }
  .diff pre { margin: 0; padding: 0; }
  .diff .line { padding: 0 8px; white-space: pre-wrap; word-break: break-all; min-height: 1.4em; line-height: 1.4; }
  .diff .del { background: rgba(219, 68, 55, 0.14); }
  .diff .add { background: rgba(15, 157, 88, 0.14); }
  .diff .pad { background: var(--secondary-background-color); opacity: 0.4; }
  table.findings { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.findings th, table.findings td { text-align: left; padding: 6px 10px;
    border-bottom: 1px solid var(--divider-color); }
  table.findings th { color: var(--secondary-text-color); font-weight: 500; }
  .sev { font-size: 11px; padding: 1px 8px; border-radius: 8px; text-transform: uppercase; }
  .sev.error { background: rgba(219, 68, 55, 0.15); color: var(--error-color); }
  .sev.warning { background: rgba(244, 180, 0, 0.15); color: var(--warning-color, #b26a00); }
  .sev.info { background: rgba(66, 133, 244, 0.12); color: var(--info-color, var(--primary-color)); }
  .empty { padding: 20px; text-align: center; color: var(--secondary-text-color); }
  .describe-row { display: flex; gap: 8px; padding: 12px 16px; }
  .describe-row input { flex: 1; font: inherit; padding: 8px 10px; border-radius: 4px;
    border: 1px solid var(--divider-color); background: var(--card-background-color);
    color: var(--primary-text-color); }
  .yaml-details { margin-top: 8px; }
  .yaml-details summary { cursor: pointer; font-size: 13px; color: var(--secondary-text-color); }
  .yaml-details pre { font-family: var(--code-font-family, monospace); font-size: 12px;
    background: var(--secondary-background-color); border-radius: 4px; padding: 8px 10px;
    overflow-x: auto; margin: 8px 0 0; }
  .opps { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
  .opp { background: var(--card-background-color); border-radius: 8px; padding: 12px 14px;
    box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,0.2)); }
  .opp p { margin: 0 0 4px; font-size: 14px; }
  .opp .detail { color: var(--secondary-text-color); font-size: 12px; margin-bottom: 10px; }
  .opp .row { display: flex; gap: 8px; }
  .banner { padding: 8px 16px; font-size: 13px; border-radius: 4px; margin-bottom: 12px;
    background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .error-banner { background: rgba(219, 68, 55, 0.12); color: var(--error-color); }
  .spin { display: inline-block; animation: spin 1.2s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
`;

function esc(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* Line-based LCS diff -> rows of {left, right, kind} for side-by-side view. */
function diffRows(aText, bText) {
  const a = (aText || "").split("\n");
  const b = (bText || "").split("\n");
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) { rows.push({ left: a[i], right: b[j], kind: "same" }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { rows.push({ left: a[i], right: null, kind: "del" }); i++; }
    else { rows.push({ left: null, right: b[j], kind: "add" }); j++; }
  }
  while (i < m) { rows.push({ left: a[i++], right: null, kind: "del" }); }
  while (j < n) { rows.push({ left: null, right: b[j++], kind: "add" }); }
  return rows;
}

function diffHtml(currentYaml, improvedYaml) {
  const rows = diffRows(currentYaml, improvedYaml);
  const left = rows.map((r) =>
    r.left === null
      ? `<div class="line pad"></div>`
      : `<div class="line ${r.kind === "del" ? "del" : ""}">${esc(r.left)}</div>`
  ).join("");
  const right = rows.map((r) =>
    r.right === null
      ? `<div class="line pad"></div>`
      : `<div class="line ${r.kind === "add" ? "add" : ""}">${esc(r.right)}</div>`
  ).join("");
  return `<div class="diff">
    <div class="col"><div class="col-title">Current</div><pre>${left}</pre></div>
    <div class="col"><div class="col-title">Proposed</div><pre>${right}</pre></div>
  </div>`;
}

function relTime(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

class HelmsmanPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._report = null;
    this._error = null;
    this._reportError = null;
    this._busy = false;
    this._drafting = false;
    this._describeValue = "";
    this._loaded = false;
    this._narrow = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._refresh();
    }
  }

  // HA sets `narrow` on custom panels; on a narrow (portrait) screen the
  // sidebar is hidden and the panel owns the full header, so we must
  // render our own menu button to toggle it (see toolbar + "menu" action).
  set narrow(value) {
    const v = !!value;
    if (v === this._narrow) return;
    this._narrow = v;
    if (this._loaded) this._render();
  }

  get narrow() {
    return this._narrow;
  }

  async _refresh() {
    try {
      this._report = await this._hass.callWS({ type: "helmsman/report" });
      this._reportError = null;
    } catch (err) {
      this._reportError =
        err && err.message ? err.message : "Failed to load report";
    }
    try {
      const all = await this._hass.callWS({ type: "system_log/list" });
      this._logs = all
        .filter((e) => {
          const name = e.name || "";
          return (
            name.startsWith("custom_components.helmsman") ||
            name.startsWith("homeassistant.components.automation")
          );
        })
        .slice(0, 20);
    } catch (err) {
      this._logs = [];
    }
    this._render();
    if (this._report
        && (this._report.review_in_progress || this._report.benchmark_in_progress)
        && !this._pollTimer) {
      this._pollTimer = setTimeout(() => {
        this._pollTimer = null;
        this._refresh();
      }, 5000);
    }
  }

  async _call(type, data = {}, confirmText = null) {
    if (confirmText && !window.confirm(confirmText)) return;
    this._busy = true;
    this._render();
    try {
      await this._hass.callWS({ type, ...data });
      this._error = null;
    } catch (err) {
      this._error = err && err.message ? err.message : `${type} failed`;
    }
    this._busy = false;
    await this._refresh();
  }

  async _draft(description, source) {
    if (!description || !description.trim()) return;
    this._drafting = true;
    this._render();
    try {
      await this._hass.callWS({ type: "helmsman/draft", description, source });
      this._error = null;
      if (source === "describe") this._describeValue = "";
    } catch (err) {
      this._error = err && err.message ? err.message : "Draft failed";
    }
    this._drafting = false;
    await this._refresh();
  }

  _draftCard(d) {
    return `<div class="card">
      <div class="card-header">
        <span class="alias">${esc(d.alias)}</span>
        <span class="entity">draft · ${esc(d.model)} · ${relTime(d.created_at)}</span>
      </div>
      <div class="card-body">
        <p class="summary">${esc(d.summary)}</p>
        <p class="explanation">${esc(d.explanation)}</p>
        <details class="yaml-details"><summary>View YAML</summary><pre>${esc(d.yaml)}</pre></details>
      </div>
      <div class="actions">
        <span class="note">Created disabled — enable it from the automations page when ready.</span>
        <button data-action="dismiss_draft" data-id="${esc(d.draft_id)}">Dismiss</button>
        <button class="primary" data-action="create_draft" data-id="${esc(d.draft_id)}"
          ${this._busy ? "disabled" : ""}>Create automation</button>
      </div>
    </div>`;
  }

  _opportunityCard(o) {
    return `<div class="opp">
      <p>${esc(o.title)}${o.camera_based ? ` <span class="sev info">camera motion</span>` : ""}</p>
      <p class="detail">${esc(o.detail)}</p>
      <div class="row">
        <button data-action="draft_opp" data-desc="${esc(o.suggested_description)}"
          ${this._drafting ? "disabled" : ""}>Draft it</button>
        <button data-action="dismiss_opp" data-key="${esc(o.key)}">Dismiss</button>
      </div>
    </div>`;
  }

  _strandedCard(st) {
    const rows = st.missing.map((m) => `
      <div style="display: flex; align-items: center; gap: 10px; padding: 6px 0; flex-wrap: wrap;">
        <code style="font-size: 12px; color: var(--error-color);">${esc(m.entity_id)}</code>
        <span style="color: var(--secondary-text-color);">→</span>
        <select data-old="${esc(m.entity_id)}" style="font: inherit; font-size: 13px; padding: 4px 8px; border-radius: 4px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); max-width: 100%;">
          <option value="">— choose a replacement —</option>
          ${m.candidates.map((c) => `<option value="${esc(c.entity_id)}">${esc(c.entity_id)}${c.name && c.name !== c.entity_id ? ` (${esc(c.name)})` : ""}</option>`).join("")}
        </select>
      </div>`).join("");
    return `<div class="card" data-stranded="${esc(st.automation)}">
      <div class="card-header">
        <span class="alias">${esc(st.alias)}</span>
        <span class="entity">${esc(st.automation)}${st.enabled ? "" : " · disabled"}</span>
      </div>
      <div class="card-body">
        <p class="explanation">These references point at entities that no longer exist. Pick replacements, let the AI rewrite it around available devices, or disable it.</p>
        ${rows}
      </div>
      <div class="actions">
        <span class="note">Replace and rewrite go through snapshot/apply — one-click rollback below.</span>
        ${st.enabled ? `<button data-action="disable_stranded" data-entity="${esc(st.automation)}" ${this._busy ? "disabled" : ""}>Disable</button>` : ""}
        <button data-action="rewrite_stranded" data-entity="${esc(st.automation)}" ${this._busy || !(this._report && this._report.ollama_configured) || (this._report && this._report.review_in_progress) ? "disabled" : ""}>Rewrite with AI</button>
        <button class="primary" data-action="replace_stranded" data-entity="${esc(st.automation)}" ${this._busy ? "disabled" : ""}>Replace selected</button>
      </div>
    </div>`;
  }

  _suggestionCard(s) {
    const applyNote = s.can_apply
      ? "Snapshots the current config first — one-click rollback below."
      : "Cannot apply: automation is not managed via automations.yaml.";
    return `<div class="card">
      <div class="card-header">
        <span class="alias">${esc(s.alias)}</span>
        <span class="entity">${esc(s.automation)} · ${esc(s.model)} · ${relTime(s.created_at)}</span>
      </div>
      <div class="card-body">
        <p class="summary">${esc(s.summary)}</p>
        <p class="explanation">${esc(s.explanation)}</p>
        ${diffHtml(s.current_yaml, s.improved_yaml)}
      </div>
      <div class="actions">
        <span class="note">${applyNote}</span>
        <button data-action="dismiss" data-entity="${esc(s.automation)}">Dismiss</button>
        <button class="primary" data-action="apply" data-entity="${esc(s.automation)}"
          ${s.can_apply && !this._busy ? "" : "disabled"}>Approve and apply</button>
      </div>
    </div>`;
  }

  _render() {
    const r = this._report;
    const busy = this._busy;
    const suggestions = r ? r.suggestions : [];
    const findings = r ? r.findings : [];
    const snapshots = r ? r.snapshots : [];
    const drafts = r && r.drafts ? r.drafts : [];
    const opps = r && r.opportunities ? r.opportunities : [];
    const reviewNotes = r && r.review_notes ? r.review_notes : [];
    const bench = r ? r.benchmark : null;
    const benchRunning = !!(r && r.benchmark_in_progress);
    const stranded = r && r.stranded ? r.stranded : [];
    const attention = findings.filter((f) => f.severity !== "info");
    const ollamaOk = !!(r && r.ollama_configured);

    this.shadowRoot.innerHTML = `<style>${STYLES}</style>
      <div class="toolbar">
        ${this._narrow ? `<button class="menu-btn" data-action="menu" title="Open sidebar" aria-label="Open sidebar">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" aria-hidden="true"><path d="M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z"/></svg>
        </button>` : ""}
        <svg class="logo" viewBox="0 0 256 256" aria-hidden="true">
          <rect width="256" height="256" rx="56" fill="#1E4477"/>
          <g transform="translate(128,128)" stroke="#E3B34B" stroke-linecap="round" fill="none">
            <circle r="76" stroke-width="15"/>
            <g stroke-width="6.5">${[0,45,90,135,180,225,270,315].map((a)=>`<line y1="-11" y2="-76" transform="rotate(${a})"/>`).join("")}</g>
            <g stroke-width="8.5">${[0,45,90,135,180,225,270,315].map((a)=>`<line y1="-82.5" y2="-114" transform="rotate(${a})"/>`).join("")}</g>
            <circle r="22" fill="#E3B34B" stroke="none"/>
            <circle r="8" fill="#16335A" stroke="none"/>
          </g>
        </svg>
        <h1>Helmsman</h1>
        <span class="meta">
          ${r ? `${r.automations_audited} automations · audit ${relTime(r.last_audit)}` : ""}
        </span>
        <button data-action="run_audit" ${busy ? "disabled" : ""}>Run audit</button>
        ${r && r.review_in_progress
          ? `<button class="danger" data-action="stop_review" ${busy ? "disabled" : ""}><span class="spin">⟳</span> Stop review</button>`
          : `<button data-action="review" ${busy || benchRunning || !(r && r.ollama_configured) ? "disabled" : ""}>Review flagged</button>`}
      </div>
      <div class="content">
        ${this._reportError ? `<div class="banner error-banner">${esc(this._reportError)}</div>` : ""}
        ${this._error ? `<div class="banner error-banner">${esc(this._error)}</div>` : ""}
        ${this._drafting
          ? `<div class="banner"><span class="spin">⟳</span> Drafting with the local model — this can take a minute or two…</div>`
          : ""}
        ${r && r.review_in_progress
          ? `<div class="banner"><span class="spin">⟳</span> LLM review running${r.review_progress ? ` — ${esc(r.review_progress)} automations done` : ""}. A few minutes per automation on local models; suggestions appear below as they pass validation.</div>`
          : ""}
        ${r && !r.review_in_progress && r.last_review_note
          ? `<div class="banner">Last review: ${esc(r.last_review_note)}</div>`
          : ""}
        ${r && !r.ollama_configured
          ? `<div class="banner">Ollama is not configured — set the server URL in Helmsman's options to enable suggestions.</div>`
          : ""}

        ${attention.length
          ? `<div class="section-title">Needs attention (${attention.length})</div>
             <div class="card">
               <table class="findings">
                 <tr><th>Severity</th><th>Automation</th><th>Problem</th></tr>
                 ${attention.map((f) => `<tr>
                   <td><span class="sev ${esc(f.severity)}">${esc(f.severity)}</span></td>
                   <td>${esc(f.alias)}</td>
                   <td>${esc(f.summary)}</td>
                 </tr>`).join("")}
               </table>
               <div style="padding: 10px 16px 12px; font-size: 13px; color: var(--secondary-text-color);">
                 Fix these right here: deprecated syntax arrives as a ready-made suggestion below after each audit, missing entities get replace/rewrite options under Stranded automations, and <b>Review flagged</b> (top right) asks the AI to repair the rest. Unavailable entities usually mean a device is offline — check its battery or power. "Registered but not loaded" means the entity exists but its integration is down or still starting — reload it in Devices &amp; Services; the automation itself is fine.
               </div>
             </div>`
          : ""}

        <div class="section-title">New automation</div>
        <div class="card">
          <div class="describe-row">
            <input type="text" id="describe" placeholder="Describe what should happen — e.g. turn on the porch light at sunset when someone is home"
              value="${esc(this._describeValue)}" ${this._drafting || !ollamaOk ? "disabled" : ""} />
            <button class="primary" data-action="draft" ${this._drafting || !ollamaOk ? "disabled" : ""}>
              ${this._drafting ? `<span class="spin">⟳</span> Drafting…` : "Draft it"}
            </button>
          </div>
        </div>
        ${drafts.map((d) => this._draftCard(d)).join("")}
        ${opps.length
          ? `<div class="section-title">Helmsman noticed</div><div class="opps">${opps.map((o) => this._opportunityCard(o)).join("")}</div>`
          : ""}

        ${stranded.length
          ? `<div class="section-title">Stranded automations (${stranded.length})</div>
             ${stranded.map((st) => this._strandedCard(st)).join("")}`
          : ""}

        <div class="section-title">Suggestions (${suggestions.length})</div>
        ${suggestions.length
          ? suggestions.map((s) => this._suggestionCard(s)).join("")
          : `<div class="card"><div class="empty">No suggestions held. Run an audit, then a review — proposals that pass validation show up here.</div></div>`}
        ${reviewNotes.length
          ? `<div class="section-title">Last review details</div>
             <div class="card"><table class="findings">
               <tr><th>Automation</th><th>Outcome</th></tr>
               ${reviewNotes.map((n) => `<tr><td>${esc(n.alias)}</td><td>${esc(n.note)}</td></tr>`).join("")}
             </table></div>`
          : ""}
        ${(this._logs || []).length
          ? `<details class="yaml-details" style="margin-top: 12px;">
               <summary>Recent log entries — Helmsman and automations (${this._logs.length})</summary>
               <div class="card" style="margin-top: 8px;"><table class="findings">
                 <tr><th>Level</th><th>When</th><th>Source</th><th>Message</th></tr>
                 ${this._logs.map((e) => {
                   const level = String(e.level || "").toLowerCase();
                   const sev = level === "error" || level === "critical" ? "error" : level === "warning" ? "warning" : "info";
                   const when = e.timestamp ? relTime(new Date(e.timestamp * 1000).toISOString()) : "";
                   const source = String(e.name || "").replace("custom_components.helmsman.", "helmsman.").replace("homeassistant.components.", "");
                   const msg = Array.isArray(e.message) ? e.message.join(" ") : String(e.message || "");
                   return `<tr>
                     <td><span class="sev ${sev}">${esc(level)}</span>${e.count > 1 ? ` ×${e.count}` : ""}</td>
                     <td style="white-space: nowrap;">${esc(when)}</td>
                     <td>${esc(source)}</td>
                     <td>${esc(msg)}</td>
                   </tr>`;
                 }).join("")}
               </table></div>
             </details>`
          : ""}

        <div class="section-title">Model</div>
        <div class="card">
          <div style="display: flex; align-items: center; gap: 12px; padding: 12px 16px;">
            <span style="font-size: 14px;">Current: <b>${esc(r ? r.model : "")}</b></span>
            <span style="flex: 1;"></span>
            <button data-action="benchmark" ${busy || benchRunning || !ollamaOk ? "disabled" : ""}>
              ${benchRunning ? `<span class="spin">⟳</span> Benchmarking — ${esc((r && r.benchmark_progress) || "starting")}` : "Benchmark available models"}
            </button>
          </div>
          ${bench && bench.error
            ? `<div class="banner error-banner" style="margin: 0 16px 12px;">Benchmark failed: ${esc(bench.error)}</div>`
            : ""}
          ${bench && bench.results && bench.results.length
            ? `<table class="findings">
                <tr><th>Model</th><th>Speed</th><th>Clean drafts</th><th>Repairs</th><th>Avg time</th><th></th></tr>
                ${bench.results.map((m) => `<tr>
                  <td>${esc(m.model)}${bench.recommended === m.model ? ` <span class="sev info">recommended</span>` : ""}${r && r.model === m.model ? ` <span class="sev info" style="opacity:0.7">current</span>` : ""}</td>
                  <td>${m.gen_tps != null ? `${m.gen_tps} tok/s` : "—"}</td>
                  <td>${m.error ? esc(m.error) : `${m.clean}/${m.samples.length}${m.passed > m.clean ? ` (+${m.passed - m.clean} repaired)` : ""}`}</td>
                  <td>${m.error ? "—" : m.repairs}</td>
                  <td>${m.avg_seconds != null ? `${m.avg_seconds}s` : "—"}</td>
                  <td style="text-align:right">${!m.error && r && r.model !== m.model
                    ? `<button data-action="use_model" data-model="${esc(m.model)}" ${busy || benchRunning ? "disabled" : ""}>Use</button>`
                    : ""}</td>
                </tr>`).join("")}
              </table>
              ${!bench.recommended
                ? `<div class="banner error-banner" style="margin: 8px 16px 0;">Every model failed to run — check the outcomes below and the Ollama server.</div>`
                : (() => {
                    const winner = bench.results.find((m) => m.model === bench.recommended);
                    return winner && winner.passed === 0
                      ? `<div class="banner" style="margin: 8px 16px 0;">No model produced a valid draft on these fixtures, so the recommendation reflects only error-free runs and speed — see the outcomes below.</div>`
                      : "";
                  })()}
              <details class="yaml-details" style="padding: 8px 16px 4px;">
                <summary>Per-sample outcomes</summary>
                <table class="findings" style="margin-top: 6px;">
                  <tr><th>Model</th><th>Draft task</th><th>Time</th><th>Outcome</th></tr>
                  ${bench.results.flatMap((m) => (m.samples || []).map((s) => `<tr>
                    <td>${esc(m.model)}</td>
                    <td>${esc(s.alias)}</td>
                    <td>${s.seconds != null ? `${s.seconds}s` : "—"}</td>
                    <td>${esc(s.note)}</td>
                  </tr>`)).join("")}
                </table>
              </details>
              <div style="padding: 8px 16px 12px; font-size: 12px; color: var(--secondary-text-color);">
                Draft tasks: ${esc((bench.samples || []).join(" · "))} · ${relTime(bench.finished_at)}
              </div>`
            : ""}
        </div>

        <div class="section-title">Findings (${findings.length})</div>
        <div class="card">
          ${findings.length
            ? `<table class="findings">
                <tr><th>Severity</th><th>Automation</th><th>Rule</th><th>Summary</th></tr>
                ${findings.map((f) => `<tr>
                  <td><span class="sev ${esc(f.severity)}">${esc(f.severity)}</span></td>
                  <td>${esc(f.alias)}</td>
                  <td>${esc(f.rule)}</td>
                  <td>${esc(f.summary)}</td>
                </tr>`).join("")}
              </table>`
            : `<div class="empty">No findings — clean audit.</div>`}
        </div>

        <div class="section-title">Snapshots</div>
        <div class="card">
          ${snapshots.length
            ? `<table class="findings">
                <tr><th>Automation</th><th>Versions</th><th>Latest</th><th></th></tr>
                ${snapshots.map((s) => `<tr>
                  <td>${esc(s.automation)}</td>
                  <td>${s.count}</td>
                  <td>${relTime(s.latest_saved_at)} (${esc(s.latest_reason)})</td>
                  <td style="text-align:right">
                    <button class="danger" data-action="rollback" data-entity="${esc(s.automation)}"
                      data-reason="${esc(s.latest_reason)}"
                      ${busy ? "disabled" : ""}>${s.latest_reason === "rollback" ? "Undo roll back" : "Roll back"}</button>
                  </td>
                </tr>`).join("")}
              </table>`
            : `<div class="empty">No snapshots yet. One is saved automatically before every applied change.</div>`}
        </div>
      </div>`;

    const describeInput = this.shadowRoot.getElementById("describe");
    if (describeInput) {
      describeInput.addEventListener("input", (ev) => {
        this._describeValue = ev.target.value;
      });
      describeInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") this._draft(this._describeValue, "describe");
      });
    }

    this.shadowRoot.querySelectorAll("button[data-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = btn.dataset.action;
        const entity = btn.dataset.entity;
        if (action === "menu")
          this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
        else if (action === "run_audit") this._call("helmsman/run_audit");
        else if (action === "review") this._call("helmsman/review");
        else if (action === "dismiss") this._call("helmsman/dismiss", { entity_id: entity });
        else if (action === "apply")
          this._call("helmsman/apply", { entity_id: entity },
            `Apply the proposed change to ${entity}?\n\nThe current config is snapshotted first and automations reload immediately.`);
        else if (action === "rollback")
          this._call("helmsman/rollback", { entity_id: entity },
            btn.dataset.reason === "rollback"
              ? `Undo the roll back of ${entity}?\n\nThis re-applies the change you previously rolled back. Nothing is lost either way — the last 10 versions are kept.`
              : `Restore the most recent snapshot of ${entity}?\n\nThe current config is snapshotted first, so this can itself be undone.`);
        else if (action === "draft") this._draft(this._describeValue, "describe");
        else if (action === "draft_opp") this._draft(btn.dataset.desc, "opportunity");
        else if (action === "create_draft")
          this._call("helmsman/create_draft", { draft_id: btn.dataset.id },
            "Create this automation?\n\nIt starts disabled — enable it from the automations page when you're ready.");
        else if (action === "dismiss_draft")
          this._call("helmsman/dismiss_draft", { draft_id: btn.dataset.id });
        else if (action === "dismiss_opp")
          this._call("helmsman/dismiss_opportunity", { key: btn.dataset.key });
        else if (action === "stop_review") this._call("helmsman/stop_review");
        else if (action === "replace_stranded") {
          const card = btn.closest("[data-stranded]");
          const replacements = {};
          card.querySelectorAll("select[data-old]").forEach((sel) => {
            if (sel.value) replacements[sel.dataset.old] = sel.value;
          });
          if (!Object.keys(replacements).length) {
            this._error = "Choose at least one replacement first.";
            this._render();
            return;
          }
          const summary = Object.entries(replacements)
            .map(([o, n]) => `${o} → ${n}`).join("\n");
          this._call("helmsman/replace_entities",
            { entity_id: entity, replacements },
            `Replace in ${entity}?\n\n${summary}\n\nThe current config is snapshotted first.`);
        }
        else if (action === "rewrite_stranded")
          this._call("helmsman/rewrite", { entity_id: entity },
            `Ask the AI to rewrite ${entity} around currently available entities?\n\nThe result appears as a normal suggestion with a diff — nothing is applied without your approval.`);
        else if (action === "disable_stranded")
          this._call("helmsman/disable_automation", { entity_id: entity },
            `Disable ${entity}?\n\nIt stops running but keeps its config; re-enable it any time from the automations page.`);
        else if (action === "benchmark")
          this._call("helmsman/benchmark", {},
            "Benchmark the models on your Ollama server on draft-quality tasks?\n\nEach model drafts a few sample automations; this runs in the background and can take several minutes of GPU time (longer with large models).");
        else if (action === "use_model")
          this._call("helmsman/set_model", { model: btn.dataset.model },
            `Switch Helmsman to ${btn.dataset.model}?\n\nThe integration reloads with the new model.`);
      });
    });
  }
}

customElements.define("helmsman-panel", HelmsmanPanel);
