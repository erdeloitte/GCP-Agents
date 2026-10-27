/* agri.js — UI for the AgriRisk CTRM prototype.
   Presentation only: every figure shown is fetched from /api/agri, where the
   deterministic engine computes it. No calculations of record happen here
   (client-side arithmetic is limited to display helpers such as a tonnes hint). */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const main = $("#main");
  let REF = null;
  let SESSION = null;
  let USER = "demo.trader";
  let SCOPE = "";
  try { USER = localStorage.getItem("agri.demoUser") || USER; SCOPE = localStorage.getItem("agri.scope") || ""; } catch (e) { /* storage unavailable */ }

  // ── Utilities ─────────────────────────────────────────────────────────────
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, dp = 0) => (v === null || v === undefined || Number.isNaN(v)) ? "—" :
    Number(v).toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const usd = (v) => v === null || v === undefined ? "—" : `${v < 0 ? "−" : ""}${num(Math.abs(v))}`;
  const tonnes = (v, dp = 0) => v === null || v === undefined ? "—" : `${v < 0 ? "−" : ""}${num(Math.abs(v), dp)}`;
  const signCls = (v) => v === null || v === undefined ? "" : v < 0 ? "neg" : v > 0 ? "pos" : "";
  const pct = (v) => v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const dt = (iso) => {
    if (!iso) return "—";
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    return m ? `${m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}` : esc(iso);
  };
  const mon = (ym) => { const m = /^(\d{4})-(\d{2})$/.exec(ym || ""); return m ? `${MONTHS[+m[2] - 1]}-${m[1].slice(2)}` : esc(ym); };
  const ts = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return esc(iso);
    return d.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit",
      minute: "2-digit", timeZone: "UTC" }) + " UTC";
  };
  /** Table timestamp: "25 Oct 2026 20:30" in UTC (the column header says UTC). */
  const dtm = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "—" : `${dt(d.toISOString().slice(0, 10))} ${d.toISOString().slice(11, 16)}`; };
  const hhmm = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(11, 16) + " UTC"; };
  const commodityName = (k) => (REF && REF.commodities[k] ? REF.commodities[k].name : k);
  const strategyName = (c) => { if (!c || c === "Unassigned") return "Unassigned"; const s = REF && REF.strategies.find((x) => x.code === c); return s ? s.name : c; };
  const locationName = (c) => { const l = REF && REF.locations.find((x) => x.code === c); return l ? l.name : c; };
  const dimLabel = (dim, key) => (dim === "commodity" ? commodityName(key) : dim === "strategy" ? strategyName(key) : key);
  const label = (s) => String(s || "").replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
  const PROVIDER = { claude: "Claude (Anthropic)" };

  async function api(path, opts = {}) {
    const res = await fetch("/api/agri" + path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", "X-Demo-User": USER },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { data = { error: `Unexpected response (${res.status})` }; }
    if (!res.ok) { const err = new Error((data && data.error) || `Request failed (${res.status})`); err.data = data; err.status = res.status; throw err; }
    return data;
  }

  function toast(msg, isError = false) {
    const t = document.createElement("div");
    t.className = "toast" + (isError ? " error" : "");
    t.setAttribute("role", isError ? "alert" : "status");
    t.textContent = msg;
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), 6000);
  }

  const loading = (msg = "Loading…") => `<div class="state" role="status"><div class="spinner"></div>${esc(msg)}</div>`;
  const errorState = (e) => `<div class="alert alert-error" role="alert"><strong>Could not load data.</strong> ${esc(e.message)}
    <div style="margin-top:6px"><button class="btn btn-sm" data-retry>Retry</button></div></div>`;
  const empty = (msg) => `<div class="state">${esc(msg)}</div>`;

  // ── Tags ──────────────────────────────────────────────────────────────────
  const DET = `<span class="tag tag-det" title="Calculated by deterministic, tested system code">Deterministic</span>`;
  const DEMO = `<span class="tag tag-demo">Synthetic data</span>`;
  const AI = `<span class="tag tag-ai" title="AI-generated; not authoritative">AI assistive · not authoritative</span>`;
  const TRADE_STATUS = { PENDING_REVIEW: "tag-warn", VALIDATED: "tag-ok", REJECTED: "tag-high", CANCELLED: "tag-low" };
  const VAL_STATUS = { OK: ["tag-ok", "Valued"], STALE: ["tag-high", "Stale — excluded"], STALE_ACCEPTED: ["tag-warn", "Stale — accepted"],
    MISSING: ["tag-crit", "Missing input"], EXCLUDED: ["tag-low", "Not in risk"] };
  const SEV = { CRITICAL: ["tag-crit", "Critical"], HIGH: ["tag-high", "High"], MEDIUM: ["tag-med", "Medium"], LOW: ["tag-low", "Low"] };
  const SEV_RANK = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
  const LIM = { OK: ["tag-ok", "Within"], WARN: ["tag-warn", "Warning"], BREACH: ["tag-crit", "Breach"] };
  const tradeTag = (s) => `<span class="tag ${TRADE_STATUS[s] || "tag-low"}">${esc(label(s))}</span>`;
  const valTag = (s) => { const [c, l] = VAL_STATUS[s] || ["tag-low", s]; return `<span class="tag ${c}">${esc(l)}</span>`; };
  const sevTag = (s) => { const [c, l] = SEV[s] || ["tag-low", s]; return `<span class="tag ${c}">${esc(l)}</span>`; };
  const limTag = (s) => { const [c, l] = LIM[s] || ["tag-low", s]; return `<span class="tag ${c}">${esc(l)}</span>`; };
  const stateTag = (s) => `<span class="tag ${s === "OPEN" ? "tag-info" : "tag-low"}">${esc(label(s))}</span>`;
  const isRisk = () => SESSION && SESSION.role === "RISK";

  /** Valuation methodology — shown wherever valuation results appear. */
  const basis = () => `<p class="basis"><strong>Valuation basis (prototype):</strong> ${REF.valuation_methodology.map(esc).join(" · ")}. Illustrative only — not a production valuation.</p>`;

  // ── Sortable table helper ─────────────────────────────────────────────────
  // cols: [{ key, label, num, sort(row)->value, render(row)->html, cls(row)->string, nosort }]
  const SORT = {};
  const RERENDER = {};
  const cmp = (a, b) => {
    if (a === b) return 0;
    if (a === null || a === undefined || a === "") return 1;
    if (b === null || b === undefined || b === "") return -1;
    return typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "en", { numeric: true });
  };
  function grid(id, cols, rows, o = {}) {
    const st = SORT[id] || (SORT[id] = { key: o.sortKey || null, dir: o.sortDir || 1 });
    const data = rows.slice();
    const col = cols.find((c) => c.key === st.key);
    if (col) {
      const f = col.sort || ((r) => r[col.key]);
      data.sort((a, b) => { const x = f(a), y = f(b); const n = x == null || x === "" || y == null || y === "" ? cmp(x, y) : cmp(x, y) * st.dir; return n; });
    }
    if (!data.length) return o.emptyHtml || empty(o.empty || "No records.");
    // Optional column metadata: w (preferred width), unit (shown in the header), title(row) (cell tooltip)
    const head = (c) => `${c.label}${c.unit ? ` <span class="unit">(${c.unit})</span>` : ""}`;
    const th = (c) => {
      const cls = c.num ? "num" : "";
      if (c.nosort) return `<th scope="col" class="${cls}">${head(c)}</th>`;
      const on = st.key === c.key;
      return `<th scope="col" class="${cls}" aria-sort="${on ? (st.dir > 0 ? "ascending" : "descending") : "none"}"><button class="sort" data-tsort="${id}|${c.key}">${head(c)}<span class="ind" aria-hidden="true">${on ? (st.dir > 0 ? "▲" : "▼") : "↕"}</span></button></th>`;
    };
    const td = (c, r) => `<td class="${c.num ? "num" : ""} ${c.cls ? c.cls(r) : ""}"${c.title ? ` title="${esc(c.title(r))}"` : ""}>${c.render ? c.render(r) : esc(r[c.key])}</td>`;
    const colgroup = cols.some((c) => c.w) ? `<colgroup>${cols.map((c) => `<col${c.w ? ` style="width:${c.w}"` : ""}>`).join("")}</colgroup>` : "";
    return `<div class="table-wrap"><table${o.minWidth ? ` style="min-width:${o.minWidth}px"` : ""}${o.cls ? ` class="${o.cls}"` : ""}><caption class="sr-only">${esc(o.caption || "")}</caption>
      ${colgroup}<thead><tr>${cols.map(th).join("")}</tr></thead>
      <tbody>${data.map((r) => `<tr${o.href ? ` class="clickable" data-href="${esc(o.href(r))}"` : ""}>${cols.map((c) => td(c, r)).join("")}</tr>`).join("")}</tbody>
      ${o.foot ? `<tfoot>${o.foot}</tfoot>` : ""}</table></div>`;
  }
  /** Render a grid into a container and keep it re-renderable on sort. */
  function mountGrid(container, id, cols, rows, o) {
    RERENDER[id] = () => { container.innerHTML = grid(id, cols, rows, o); };
    RERENDER[id]();
  }

  // ── Dialogs ───────────────────────────────────────────────────────────────
  const dlg = $("#dlg");
  const drawer = $("#drawer");
  $$("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

  function setFoot(buttons = [], cancelText = "Cancel") {
    const foot = $("#dlg-foot");
    foot.innerHTML = "";
    const cancel = document.createElement("button");
    cancel.className = "btn"; cancel.textContent = cancelText; cancel.type = "button";
    cancel.addEventListener("click", () => dlg.close());
    foot.appendChild(cancel);
    buttons.forEach((b) => {
      const el = document.createElement("button");
      el.type = "button"; el.className = "btn " + (b.cls || "btn-primary"); el.textContent = b.text;
      el.disabled = Boolean(b.disabled);
      el.addEventListener("click", async () => {
        el.disabled = true;
        try { await b.onClick(el); } finally { if (el.isConnected) el.disabled = false; }
      });
      foot.appendChild(el);
    });
    return cancel;
  }

  function openDialog(title, bodyHtml, buttons = []) {
    $("#dlg-title").textContent = title;
    $("#dlg-body").innerHTML = bodyHtml;
    const cancel = setFoot(buttons);
    if (!dlg.open) dlg.showModal();
    const first = $("#dlg-body textarea, #dlg-body input, #dlg-body select", dlg);
    (first || cancel).focus();
  }

  function openDrawer(title, html) {
    $("#drawer-title").textContent = title;
    $("#drawer-body").innerHTML = html;
    if (!drawer.open) drawer.showModal();
  }

  /** Reason-required controlled action dialog. */
  function reasonDialog({ title, intro, confirmText, confirmCls, extraHtml = "", onConfirm }) {
    openDialog(title, `${intro}${extraHtml}
      <div class="field" style="margin-top:12px"><label for="dlg-reason">Reason <span class="req" aria-hidden="true">*</span></label>
      <textarea id="dlg-reason" rows="3" maxlength="500" required aria-describedby="dlg-reason-err"></textarea>
      <span class="err" id="dlg-reason-err"></span></div>
      <div id="dlg-error"></div>`, [{
      text: confirmText, cls: confirmCls, onClick: async () => {
        const reason = $("#dlg-reason").value.trim();
        if (reason.length < 5) { $("#dlg-reason-err").textContent = "Enter a reason of at least 5 characters."; $("#dlg-reason").focus(); return; }
        try { await onConfirm(reason); dlg.close(); }
        catch (e) { $("#dlg-error").innerHTML = `<div class="alert alert-error" role="alert">${esc(e.message)}</div>`; }
      },
    }]);
  }

  // ── AI explanation: preview → consent → send ─────────────────────────────
  const diagHtml = (d) => `<strong>${esc(PROVIDER[d.provider] || d.provider)}</strong>: <span class="mono">${esc(d.code)}</span>${d.http_status ? ` (HTTP ${d.http_status})` : ""} — ${esc(d.message)}${d.hint ? `<div class="muted">${esc(d.hint)}</div>` : ""}`;

  async function explainWithAI(topic, id) {
    openDialog("Explain with AI", loading("Preparing data preview…"));
    let p;
    try { p = await api("/ai/preview", { method: "POST", body: { topic, id } }); }
    catch (e) { $("#dlg-body").innerHTML = `<div class="alert alert-error">${esc(e.message)}</div>`; return; }
    const chain = `${PROVIDER[p.provider] || p.provider} · ${p.model}`;
    const canSend = p.enabled && p.configured;
    $("#dlg-body").innerHTML = `
      <p>${AI}</p>
      <p>The assistant explains figures the deterministic engine has already calculated. It cannot change data, approve trades, change limits or apply overrides.</p>
      <p style="margin-bottom:2px"><strong>Data to be sent</strong> <span class="muted">(${p.fields.length} fields · to ${esc(chain)})</span></p>
      <pre class="payload" aria-label="Exact payload">${esc(JSON.stringify(p.payload.data, null, 2))}</pre>
      <p class="muted" style="font-size:12px"><strong>Never sent:</strong> ${p.never_sent.map(esc).join(", ")}.</p>
      ${canSend ? "" : `<div class="alert alert-warn">${p.enabled ? "The AI provider is not configured." : "AI features are disabled (LLM_ENABLED=false)."} See <a href="#/system">System &amp; AI</a>. All deterministic results remain available.</div>`}`;
    setFoot([{ text: "Send to AI", disabled: !canSend, onClick: () => sendToAI(topic, id) }]);
  }

  async function sendToAI(topic, id) {
    $("#dlg-body").innerHTML = loading("Waiting for the AI provider…");
    setFoot([], "Close");
    try {
      const r = await api("/ai/explain", { method: "POST", body: { topic, id, confirm: true } });
      $("#dlg-body").innerHTML = `
        ${r.diagnostic ? `<div class="alert alert-error">${diagHtml(r.diagnostic)}</div>` : ""}
        <section class="panel ai"><div class="panel-head"><h2>Explanation</h2>${AI}</div>
          <div class="panel-body"><p style="margin:0" ${r.available ? "" : 'class="muted"'}>${esc(r.text).replace(/\n/g, "<br>")}</p></div>
          <div class="panel-note">${r.available ? `Answered by ${esc(PROVIDER[r.provider] || r.provider)} · ${esc(r.model)}<br>` : ""}${esc(r.notice)}</div></section>`;
    } catch (e) { $("#dlg-body").innerHTML = `<div class="alert alert-error">${esc(e.message)}</div>`; }
    loadAiStatus();
  }

  // ── Chrome: identity, scope, freshness, AI status, badge ─────────────────
  const SECTIONS = { dashboard: "Dashboard", blotter: "Trade blotter", trade: "Trade blotter", capture: "Capture trade", monitoring: "Monitoring",
    risk: "Exposure & risk", exceptions: "Exceptions", audit: "Audit trail", "market-data": "Market data", system: "System & AI",
    "data-setup": "Data Setup" };

  async function loadSession() {
    SESSION = await api("/session");
    USER = SESSION.user;
    $("#demo-user").innerHTML = SESSION.demo_users.map((u) => `<option value="${esc(u.id)}" ${u.id === USER ? "selected" : ""}>${esc(u.label)}</option>`).join("");
  }
  $("#demo-user").addEventListener("change", async (e) => {
    USER = e.target.value;
    try { localStorage.setItem("agri.demoUser", USER); } catch (err) { /* ignore */ }
    await loadSession();
    toast(`Acting as ${SESSION.label} — demo identity, role ${SESSION.role}`);
    route();
  });

  function initScope() {
    const sel = $("#scope");
    sel.innerHTML = `<option value="">All portfolios</option>` + REF.all_portfolios.map((p) => `<option ${p === SCOPE ? "selected" : ""}>${esc(p)}</option>`).join("");
    if (SCOPE && !REF.all_portfolios.includes(SCOPE)) SCOPE = "";
    sel.addEventListener("change", () => {
      SCOPE = sel.value;
      try { localStorage.setItem("agri.scope", SCOPE); } catch (err) { /* ignore */ }
      blotterState.filters.portfolio = SCOPE;
      route();
    });
  }

  async function refreshChrome() {
    try {
      const d = await api("/dashboard");
      const f = d.freshness;
      const issues = f.stale_marks + f.missing_marks;
      $("#freshness").innerHTML = `Valued ${hhmm(d.valued_at)} · <span class="${issues ? "neg" : ""}">${f.stale_marks} stale, ${f.missing_marks} missing mark${f.missing_marks === 1 ? "" : "s"}</span>`;
      const b = $("#exc-badge");
      b.textContent = d.kpis.open_exceptions || "";
      b.className = "badge" + (d.kpis.critical_exceptions ? " badge-red" : "");
      b.setAttribute("aria-label", `${d.kpis.open_exceptions} open exceptions`);
    } catch (e) { $("#freshness").textContent = "Freshness unavailable"; }
  }

  async function loadAiStatus() {
    const chip = $("#ai-chip");
    try {
      const s = await api("/ai/status");
      let cls = "", text;
      if (!s.enabled) text = "AI off";
      else if (!s.configured) text = "AI not configured";
      else {
        const failed = s.last && !s.last.ok;
        cls = failed ? "warn" : "ok";
        text = `AI: Claude${failed ? " (last call failed)" : ""}`;
      }
      chip.innerHTML = `<span class="dot ${cls}" aria-hidden="true"></span>${esc(text)}`;
    } catch (e) { chip.textContent = "AI status unavailable"; }
  }

  $("#nav-toggle").addEventListener("click", () => {
    const nav = $("#sidenav"); const open = nav.classList.toggle("open");
    $("#nav-toggle").setAttribute("aria-expanded", String(open));
  });

  // ── Shared fragments ──────────────────────────────────────────────────────
  function meter(l) {
    const cls = l.status === "BREACH" ? "breach" : l.status === "WARN" ? "warn" : "ok";
    return `<span class="util" title="${esc(limitName(l))}: ${num(l.exposure)} / ${num(l.limit_value)} ${esc(l.unit)}">
      <span class="meter" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${(l.utilisation * 100).toFixed(1)}" aria-label="Utilisation ${pct(l.utilisation)}">
      <span class="${cls}" style="width:${Math.min(100, l.utilisation * 100)}%"></span><span class="tick" style="left:${l.warn_pct * 100}%"></span></span>
      <span class="${l.status === "BREACH" ? "neg" : ""}">${pct(l.utilisation)}</span></span>`;
  }
  const limitName = (l) => `${label(l.dimension)}: ${dimLabel(l.dimension, l.key)}`;
  const tradeLink = (id) => `<a class="mono" href="#/trade/${encodeURIComponent(id)}">${esc(id)}</a>`;

  function entityLink(e) {
    if (e.trade_id) return tradeLink(e.trade_id);
    if (e.limit_id) return `<a href="#/risk" class="mono">${esc(e.limit_id)}</a>`;
    if (e.price_key) { const [c, m] = e.price_key.split("|"); return `<a href="#/market-data">${esc(commodityName(c))} ${mon(m)}</a>`; }
    return esc(e.entity_id);
  }

  function timeline(events) {
    if (!events.length) return empty("No audit events.");
    return `<ol class="timeline">${events.map((e) => `<li><div><strong>${esc(label(e.action))}</strong> by ${esc(e.actor)}</div>
      <div class="when">${ts(e.timestamp)}</div>${e.reason ? `<div>“${esc(e.reason)}”</div>` : ""}${diffTable(e.changes)}</li>`).join("")}</ol>`;
  }

  function diffTable(changes) {
    const keys = Object.keys(changes || {});
    if (!keys.length) return "";
    return `<table class="diff"><thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>
      ${keys.map((k) => `<tr><td>${esc(k)}</td><td class="neg">${esc(changes[k].from ?? "—")}</td><td class="pos">${esc(changes[k].to ?? "—")}</td></tr>`).join("")}</tbody></table>`;
  }

  // ── Dashboard ─────────────────────────────────────────────────────────────
  const WL = { critical: ["wl-critical", "Critical"], high: ["wl-high", "High"], warning: ["wl-warning", "Warning"],
    action: ["wl-action", "To do"], info: ["wl-info", "Info"], clear: ["wl-clear", "Clear"] };
  const WL_RANK = { critical: 0, high: 1, warning: 2, action: 3, info: 4, clear: 9 };
  const EVENT = { TRADE_CREATED: "Trade captured", TRADE_AMENDED: "Trade amended", TRADE_APPROVED: "Trade approved",
    TRADE_REJECTED: "Trade rejected", TRADE_CANCELLED: "Trade cancelled", EXCEPTION_ACKNOWLEDGED: "Exception acknowledged",
    EXCEPTION_OVERRIDDEN: "Exception overridden", LIMIT_CHANGED: "Limit changed", REFERENCE_CREATED: "Reference data created",
    REFERENCE_UPDATED: "Reference data updated", REFERENCE_DEACTIVATED: "Reference data deactivated",
    REFERENCE_ACTIVATED: "Reference data activated", REFERENCE_DELETED: "Reference data deleted",
    AI_EXPLANATION_REQUESTED: "AI explanation requested", DEMO_DATA_RESET: "Demo data reset" };
  const entityHref = (type, id) => type === "trade" ? `#/trade/${encodeURIComponent(id)}` : type === "reference" ? `#/data-setup/${encodeURIComponent(id.split(":")[0])}`
    : type === "limit" ? "#/risk" : type === "exception" ? "#/exceptions" : "";
  const entityCell = (type, id) => { const h = entityHref(type, id); const txt = type === "reference" ? id.replace(":", " · ") : id; return h ? `<a class="mono" href="${h}">${esc(txt)}</a>` : `<span class="mono">${esc(txt)}</span>`; };
  const tradeSummary = (t) => `${esc(t.direction)} ${num(t.quantity)} ${esc(t.quantity_unit)} ${esc(commodityName(t.commodity))} ${esc(label(t.instrument_type))} ${mon(t.contract_month)}`;

  async function viewDashboard() {
    main.innerHTML = loading();
    const d = await api("/dashboard" + (SCOPE ? `?portfolio=${encodeURIComponent(SCOPE)}` : ""));
    const k = d.kpis, f = d.freshness, ds = d.data_status, store = ds.store;
    const items = d.attention.slice().sort((a, b) => WL_RANK[a.severity] - WL_RANK[b.severity]);
    const open = items.filter((i) => i.count);
    const commodityLimit = (c) => d.limits.filter((l) => l.dimension === "commodity" && l.key === c).sort((a, b) => b.utilisation - a.utilisation)[0];
    const kv = (dt_, dd) => `<div><dt>${dt_}</dt><dd>${dd}</dd></div>`;
    main.innerHTML = `
      <div class="page-head"><div><h1>Overview</h1>
        <p>${esc(SCOPE || "All portfolios")} · valued ${ts(d.valued_at)} ${DET} ${DEMO}</p></div>
        <div class="page-actions"><a class="btn btn-primary" href="#/capture">Capture trade</a><a class="btn" href="#/exceptions">Exceptions queue</a><a class="btn" href="#/data-setup">Data Setup</a></div></div>
      <div class="cols cols-main">
        <section class="panel" aria-labelledby="h-attn"><div class="panel-head"><h2 id="h-attn">Needs attention<span class="sub">${open.length ? `${open.length} of ${items.length} checks need action` : "all checks clear"}</span></h2></div>
          <ul class="worklist">${items.map((i) => { const [cls, txt] = WL[i.severity]; return `<li class="${cls}">
            <span class="wl-sev">${txt}</span><span class="wl-count">${i.count ? num(i.count) : "✓"}</span>
            <span class="wl-main"><a class="wl-title" href="${i.href}">${esc(i.label)}</a><span class="wl-detail">${esc(i.detail)}</span></span>
            ${i.count ? `<a class="btn btn-sm" href="${i.href}">${esc(i.action)}</a>` : "<span></span>"}</li>`; }).join("")}</ul>
          <div class="panel-note">Checks are deterministic rules evaluated on every load. Limit checks are firm-wide; the rest follow the portfolio selector.</div></section>
        <div class="stack">
          <section class="panel" aria-labelledby="h-book"><div class="panel-head"><h2 id="h-book">Open book</h2>${DET}</div>
            <div class="panel-body"><dl class="kv kv-rows">
              ${kv("Open trades", `${num(k.open_trades)}${k.pending_review ? ` <span class="muted">· ${k.pending_review} pending review</span>` : ""}`)}
              ${kv("Unrealised MtM (USD)", `<span class="${signCls(k.mtm_usd)}">${usd(k.mtm_usd)}</span> <span class="muted">· ${k.mtm_included} of ${k.open_trades} valued</span>`)}
              ${kv("Gross notional (USD)", usd(k.gross_notional_usd))}
              ${kv("Limits (firm-wide)", k.limit_breaches ? `<span class="neg">${k.limit_breaches} breach</span> · ${k.limit_warnings} warning` : `${k.limit_warnings} warning`)}</dl></div>
            <div class="panel-note">${basis()}</div></section>
          <section class="panel" aria-labelledby="h-ds"><div class="panel-head"><h2 id="h-ds">Data status</h2></div>
            <div class="panel-body"><dl class="kv kv-rows">
              ${kv("Storage", store.persistent ? `BigQuery <span class="mono muted">${esc(store.dataset)}</span>` : '<span class="warn-text">In memory — not saved</span>')}
              ${kv("Market marks", `${f.marks_in_use} in use${f.stale_marks || f.missing_marks ? ` · <span class="warn-text">${f.stale_marks} stale, ${f.missing_marks} missing</span>` : " · all current"}`)}
              ${kv("Oldest mark", ts(f.oldest_mark))}
              ${kv("Reference data", `${ds.reference_records} records${ds.inactive_reference_in_use ? ` · <span class="warn-text">${ds.inactive_reference_in_use} open trades on inactive records</span>` : ""}`)}</dl></div>
            <div class="panel-note"><a href="#/market-data">Market data</a> · <a href="#/data-setup">Data Setup</a> · <a href="#/system">System &amp; AI</a></div></section>
        </div>
      </div>
      <section class="panel" aria-labelledby="h-act"><div class="panel-head"><h2 id="h-act">Recent activity</h2><a class="btn btn-sm" href="#/audit">Audit trail</a></div><div id="act-grid"></div></section>
      <div class="cols cols-2">
        <section class="panel core" aria-labelledby="h-pos"><div class="panel-head"><h2 id="h-pos">Exposure by commodity<span class="sub">delta-equivalent t</span></h2><a class="btn btn-sm" href="#/risk">Exposure &amp; risk</a></div><div id="pos-grid"></div></section>
        <section class="panel core" aria-labelledby="h-str"><div class="panel-head"><h2 id="h-str">Exposure by strategy</h2><a class="btn btn-sm" href="#/data-setup/strategy">Strategies</a></div><div id="str-grid"></div></section>
      </div>`;

    // Totals rows show the server's own scoped totals (kpis); nothing is summed in the browser.
    mountGrid($("#act-grid"), "dash-act", [
      { key: "timestamp", label: "Time", unit: "UTC", w: "136px", cls: () => "nowrap num-text", render: (e) => dtm(e.timestamp) },
      { key: "action", label: "Event", w: "190px", cls: () => "nowrap", render: (e) => esc(EVENT[e.action] || label(e.action)) },
      { key: "entity_id", label: "Record", w: "150px", cls: () => "nowrap", render: (e) => entityCell(e.entity_type, e.entity_id) },
      { key: "summary", label: "Detail", nosort: true, cls: () => "wrap",
        render: (e) => (e.summary ? tradeSummary(e.summary) : `<span class="muted">${esc(e.reason)}</span>`) },
      { key: "status", label: "Status", w: "140px", sort: (e) => (e.summary ? e.summary.status : ""), cls: () => "nowrap", render: (e) => (e.summary ? tradeTag(e.summary.status) : '<span class="muted">—</span>') },
      { key: "actor", label: "By", w: "150px", cls: () => "nowrap muted" },
    ], d.recent_activity, { caption: "Recent activity", sortKey: "timestamp", sortDir: -1, minWidth: 860, empty: "No activity yet." });

    mountGrid($("#pos-grid"), "dash-pos", [
      { key: "commodity", label: "Commodity", sort: (r) => commodityName(r.commodity),
        render: (r) => `<button class="btn-link" data-derive="commodity|${esc(r.commodity)}">${esc(commodityName(r.commodity))}</button><span class="sub">${esc(REF.commodities[r.commodity].exchange)} · ${esc(REF.commodities[r.commodity].code)}</span>` },
      { key: "net_t", label: "Net", unit: "t", num: true, w: "96px", render: (r) => tonnes(r.net_t) },
      { key: "mtm_usd", label: "MtM", unit: "USD", num: true, w: "112px", cls: (r) => signCls(r.mtm_usd), render: (r) => usd(r.mtm_usd) },
      { key: "valued", label: "Valued", num: true, w: "76px", sort: (r) => r.valued / r.trades, render: (r) => `<span class="${r.data_complete ? "" : "warn-text"}" title="${r.valued} of ${r.trades} trades have a usable mark">${r.valued} / ${r.trades}</span>` },
      { key: "limit", label: "Limit", w: "132px", cls: () => "nowrap", sort: (r) => (commodityLimit(r.commodity) || {}).utilisation ?? null, render: (r) => { const l = commodityLimit(r.commodity); return l ? `${limTag(l.status)} <span class="util-pct">${pct(l.utilisation)}</span>` : '<span class="muted">—</span>'; } },
    ], d.positions, { caption: "Exposure by commodity", sortKey: "net_t", sortDir: -1, minWidth: 520, empty: "No open positions in this scope.",
      foot: d.positions.length ? `<tr><th scope="row">Total</th><td class="num muted" title="Net tonnes are not summed across different commodities">—</td><td class="num ${signCls(k.mtm_usd)}">${usd(k.mtm_usd)}</td><td class="num">${k.mtm_included} / ${k.open_trades}</td><td></td></tr>` : "" });

    mountGrid($("#str-grid"), "dash-str", [
      { key: "name", label: "Strategy", render: (a) => `<button class="btn-link" data-derive="strategy|${esc(a.key)}">${esc(a.name)}</button>${a.key === "Unassigned" ? "" : `<span class="sub mono">${esc(a.key)}</span>`}` },
      { key: "trades", label: "Trades", num: true, w: "72px", sort: (a) => a.trade_ids.length, render: (a) => a.trade_ids.length },
      { key: "gross_notional_usd", label: "Notional", unit: "USD", num: true, w: "128px", render: (a) => usd(a.gross_notional_usd) },
      { key: "mtm_usd", label: "MtM", unit: "USD", num: true, w: "112px", cls: (a) => signCls(a.mtm_usd), render: (a) => usd(a.mtm_usd) },
      { key: "data", label: "Data", w: "112px", cls: () => "nowrap", sort: (a) => a.incomplete.length, render: (a) => (a.data_complete ? '<span class="tag tag-ok">Complete</span>' : `<span class="tag tag-warn" title="${a.incomplete.length} trade(s) without a usable mark are excluded from notional and MtM">${a.incomplete.length} excluded</span>`) },
    ], d.by_strategy, { caption: "Exposure by strategy", sortKey: "gross_notional_usd", sortDir: -1, minWidth: 520, empty: "No open positions in this scope.",
      foot: d.by_strategy.length ? `<tr><th scope="row">Total</th><td class="num">${k.open_trades}</td><td class="num">${usd(k.gross_notional_usd)}</td><td class="num ${signCls(k.mtm_usd)}">${usd(k.mtm_usd)}</td><td></td></tr>` : "" });
  }

  // ── Blotter ───────────────────────────────────────────────────────────────
  const blotterState = { filters: {} };
  const LINK_FILTERS = { counterparty: "Counterparty", delivery_location: "Delivery location", broker: "Broker" };
  async function viewBlotter(arg, params) {
    if (params && [...params.keys()].length) {
      blotterState.filters = { portfolio: SCOPE };
      params.forEach((v, key) => { blotterState.filters[key] = v; });
      history.replaceState(null, "", "#/blotter");
    }
    if (blotterState.filters.portfolio === undefined) blotterState.filters.portfolio = SCOPE;
    const fl = blotterState.filters;
    const opt = (name, values, lbl, fmt = (v) => v) => `<div class="field"><label for="f-${name}">${lbl}</label><select id="f-${name}" data-filter="${name}"><option value="">All</option>
      ${values.map((v) => `<option value="${esc(v)}" ${fl[name] === v ? "selected" : ""}>${esc(fmt(v))}</option>`).join("")}</select></div>`;
    const linkChips = Object.keys(LINK_FILTERS).filter((k) => fl[k]).map((k) =>
      `<button class="chip" aria-pressed="true" data-unfilter="${k}" title="Remove filter">${esc(LINK_FILTERS[k])}: ${esc(k === "delivery_location" ? locationName(fl[k]) : fl[k])} ✕</button>`).join("");
    main.innerHTML = `<div class="page-head"><div><h1>Trade blotter</h1><p>All captured trades with current valuation status ${DET} ${DEMO}</p></div>
      <div class="page-actions"><a class="btn btn-primary" href="#/capture">Capture trade</a></div></div>
      <section class="panel"><div class="filters" role="search">
        <div class="field" style="flex:1 1 220px"><label for="f-q">Search</label><input id="f-q" type="search" placeholder="Trade ID, counterparty, execution ref…" value="${esc(fl.q || "")}" data-filter="q"></div>
        ${opt("commodity", Object.keys(REF.commodities), "Commodity", commodityName)}
        ${opt("strategy", ["UNASSIGNED", ...REF.strategies.map((x) => x.code)], "Strategy", strategyName)}
        ${opt("instrument_type", REF.instrument_types, "Instrument", label)}
        ${opt("direction", REF.directions, "Direction", label)}
        ${opt("status", REF.statuses, "Status", label)}
        ${opt("portfolio", REF.all_portfolios, "Portfolio")}
        <button class="btn" id="f-clear" type="button">Clear</button></div>
        ${linkChips ? `<div class="chips">${linkChips}</div>` : ""}
        <div id="blotter-out">${loading()}</div>
        <div class="panel-note"><span id="blotter-count" aria-live="polite"></span>${basis()}</div></section>`;
    let timer;
    $$("[data-filter]").forEach((el) => el.addEventListener(el.tagName === "INPUT" ? "input" : "change", () => {
      fl[el.dataset.filter] = el.value;
      clearTimeout(timer); timer = setTimeout(loadBlotter, el.tagName === "INPUT" ? 250 : 0);
    }));
    $$("[data-unfilter]").forEach((b) => b.addEventListener("click", () => { delete fl[b.dataset.unfilter]; viewBlotter(); }));
    $("#f-clear").addEventListener("click", () => { blotterState.filters = { portfolio: "" }; viewBlotter(); });
    await loadBlotter();
  }

  async function loadBlotter() {
    const out = $("#blotter-out");
    if (!out) return;
    const qs = new URLSearchParams(Object.entries(blotterState.filters).filter(([, v]) => v)).toString();
    try {
      const rows = await api("/trades" + (qs ? "?" + qs : ""));
      $("#blotter-count").textContent = `${rows.length} trade${rows.length === 1 ? "" : "s"}. `;
      mountGrid(out, "blotter", [
        { key: "id", label: "Trade", render: (t) => `${tradeLink(t.id)}<span class="sub">v${t.version}</span>` },
        { key: "trade_date", label: "Trade date", cls: () => "nowrap", render: (t) => dt(t.trade_date) },
        { key: "portfolio", label: "Portfolio", render: (t) => `${esc(t.portfolio)}<span class="sub">${esc(strategyName(t.strategy))}</span>` },
        { key: "counterparty", label: "Counterparty" },
        { key: "commodity", label: "Commodity", sort: (t) => commodityName(t.commodity), render: (t) => esc(commodityName(t.commodity)) },
        { key: "instrument_type", label: "Instrument", render: (t) => `${esc(label(t.instrument_type))}${t.option_type ? ` <span class="muted">${esc(t.option_type)} ${num(t.strike, 2)}</span>` : ""}` },
        { key: "direction", label: "B/S" },
        { key: "quantity", label: "Quantity", num: true, render: (t) => `${num(t.quantity)} <span class="muted">${esc(t.quantity_unit)}</span>` },
        { key: "contract_month", label: "Month", render: (t) => mon(t.contract_month) },
        { key: "price", label: "Price", num: true, render: (t) => `${num(t.price, 2)} <span class="muted">${esc(t.price_unit)}</span>` },
        { key: "maturity_date", label: "Maturity", cls: () => "nowrap", render: (t) => dt(t.maturity_date) },
        { key: "status", label: "Status", render: (t) => tradeTag(t.status) },
        { key: "val", label: "Valuation", sort: (t) => t.valuation.status, render: (t) => valTag(t.valuation.status) },
        { key: "mtm", label: "MtM USD", num: true, sort: (t) => t.valuation.mtm_usd, cls: (t) => signCls(t.valuation.mtm_usd), render: (t) => usd(t.valuation.mtm_usd) },
        { key: "open_exceptions", label: "Exc.", num: true, render: (t) => (t.open_exceptions ? `<span class="badge badge-red">${t.open_exceptions}</span>` : "") },
      ], rows, { caption: "Trades", sortKey: "id", sortDir: -1, href: (t) => `#/trade/${encodeURIComponent(t.id)}`, empty: "No trades match these filters." });
    } catch (e) { out.innerHTML = errorState(e); }
  }

  // ── Trade detail ──────────────────────────────────────────────────────────
  async function viewTrade(id) {
    main.innerHTML = loading();
    const d = await api("/trades/" + encodeURIComponent(id));
    const t = d.trade, v = d.valuation;
    const spec = REF.commodities[t.commodity];
    const canReview = t.status === "PENDING_REVIEW";
    const kv = (k, val) => `<div><dt>${k}</dt><dd>${val}</dd></div>`;
    const missing = '<span class="neg">Missing</span>';
    main.innerHTML = `<div class="page-head"><div><h1><span class="mono">${esc(t.id)}</span> · ${esc(t.direction)} ${num(t.quantity)} ${esc(t.quantity_unit)} ${esc(commodityName(t.commodity))} ${esc(label(t.instrument_type))} ${mon(t.contract_month)}</h1>
        <p>${tradeTag(t.status)} Version ${t.version} · last changed by ${esc(t.updated_by)} at ${ts(t.updated_at)}${t.reviewed_by ? ` · reviewed by ${esc(t.reviewed_by)}` : ""}</p></div>
      <div class="page-actions">
        ${canReview ? `<button class="btn btn-teal" id="btn-review">Review…</button>` : ""}
        ${t.status !== "CANCELLED" ? `<a class="btn btn-primary" href="#/capture/${encodeURIComponent(t.id)}">Amend</a><button class="btn btn-danger" id="btn-cancel">Cancel trade…</button>` : ""}
        <button class="btn" data-explain="trade|${esc(t.id)}">Explain with AI…</button></div></div>
      <div class="cols cols-2">
        <section class="panel" aria-labelledby="h-terms"><div class="panel-head"><h2 id="h-terms">Key terms</h2>${DEMO}</div><div class="panel-body"><dl class="kv">
          ${kv("Counterparty", esc(t.counterparty))}${kv("Portfolio", esc(t.portfolio))}
          ${kv("Commodity", `${esc(commodityName(t.commodity))} <span class="muted">${esc(spec.exchange)} ${esc(spec.code)}</span>`)}
          ${kv("Instrument", esc(label(t.instrument_type)) + (t.option_type ? ` ${esc(t.option_type)} @ ${num(t.strike, 2)}` : ""))}
          ${kv("Direction", esc(t.direction))}${kv("Quantity", `${num(t.quantity)} ${esc(t.quantity_unit)} <span class="muted">(${num(v.quantity_t, 1)} t)</span>`)}
          ${kv("Contract month", mon(t.contract_month))}${kv("Pricing", `${esc(label(t.pricing_type))} · ${esc(t.reference_market)}`)}
          ${kv(t.instrument_type === "OPTION" ? "Premium" : "Price", `${num(t.price, 2)} ${esc(t.price_unit)}`)}${kv("Currency", esc(t.currency))}
          ${kv("Trade / effective", `${dt(t.trade_date)} / ${dt(t.effective_date)}`)}${kv("Maturity / delivery", `${dt(t.maturity_date)} <span class="muted">(${esc(d.maturity_bucket)})</span>`)}
          ${kv("Broker", t.broker ? esc(t.broker) : missing)}${kv("Execution ref", t.execution_ref ? `<span class="mono">${esc(t.execution_ref)}</span>` : missing)}
          ${kv("Strategy", t.strategy ? `<a href="#/blotter?strategy=${encodeURIComponent(t.strategy)}">${esc(strategyName(t.strategy))}</a>` : '<span class="muted">Unassigned</span>')}
          ${t.instrument_type === "FORWARD" ? kv("Delivery location", t.delivery_location ? esc(locationName(t.delivery_location)) : '<span class="warn-text">Not set</span>') : ""}
          ${kv("Source", esc(label(t.source)))}${kv("Captured by", `${esc(t.created_by)} · ${ts(t.created_at)}`)}
        </dl>${t.notes ? `<p class="muted" style="margin:10px 0 0"><strong>Notes:</strong> ${esc(t.notes)}</p>` : ""}</div></section>
        <section class="panel core" aria-labelledby="h-val"><div class="panel-head"><h2 id="h-val">Valuation</h2><span>${valTag(v.status)} ${DET}</span></div>
          <div class="panel-body">
          ${v.status_reason ? `<div class="alert ${v.status === "OK" ? "alert-info" : v.status === "MISSING" ? "alert-error" : "alert-warn"}">${esc(v.status_reason)}</div>` : ""}
          <dl class="kv">${kv("MtM (USD)", `<span class="${signCls(v.mtm_usd)}">${usd(v.mtm_usd)}</span>`)}${kv(`MtM (${esc(v.currency)})`, usd(v.mtm_ccy))}
            ${kv("Delta-equivalent", v.delta_t === null ? "—" : `${tonnes(v.delta_t, 1)} t`)}${kv("Notional (USD)", usd(v.notional_usd))}
            ${kv("Mark", v.mark_price === null ? missing : `${num(v.mark_price, 2)} ${esc(spec.price_unit)}`)}${kv("Mark as of", `${ts(v.mark_as_of)}${v.mark_age_hours !== null ? ` <span class="muted">(${v.mark_age_hours}h)</span>` : ""}`)}</dl>
          ${v.formula ? `<p class="muted" style="margin:12px 0 4px"><strong>Formula</strong></p><div class="formula">${esc(v.formula)}</div>` : ""}
          <p class="muted" style="margin:12px 0 4px"><strong>Assumptions</strong></p><ul class="list">${v.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
          <details style="margin-top:10px"><summary>Inputs</summary>
            <div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>Input</th><th class="num">Value</th><th>Unit / source</th></tr></thead><tbody>
              ${v.inputs.map((i) => `<tr><td>${esc(i.name)}</td><td class="num">${typeof i.value === "number" ? num(i.value, 4).replace(/\.?0+$/, "") : esc(i.value)}</td><td>${esc(i.unit)}${i.as_of ? `<span class="sub">${ts(i.as_of)} · ${esc(i.source)}</span>` : ""}</td></tr>`).join("")}
            </tbody></table></div></details></div>
          <div class="panel-note">${basis()}</div></section>
      </div>
      <div class="cols cols-2">
        <section class="panel" aria-labelledby="h-texc"><div class="panel-head"><h2 id="h-texc">Exceptions on this trade</h2></div><div id="trade-exc"></div></section>
        <section class="panel" aria-labelledby="h-conf"><div class="panel-head"><h2 id="h-conf">Broker confirmation</h2><span class="tag tag-demo">Simulated</span></div><div class="panel-body">
          ${d.confirmation ? `<dl class="kv">${kv("Execution ref", `<span class="mono">${esc(d.confirmation.execution_ref)}</span>`)}${kv("Quantity", `${num(d.confirmation.quantity)} ${d.confirmation.quantity !== t.quantity ? '<span class="tag tag-high">Mismatch</span>' : '<span class="tag tag-ok">Match</span>'}`)}
            ${kv("Price", `${num(d.confirmation.price, 2)} ${d.confirmation.price !== t.price ? '<span class="tag tag-high">Mismatch</span>' : '<span class="tag tag-ok">Match</span>'}`)}${kv("Received", ts(d.confirmation.received_at))}</dl>`
            : `<p class="muted" style="margin:0">${spec && ["FUTURE", "OPTION"].includes(t.instrument_type) ? "No confirmation received." : "Not applicable (OTC — bilateral confirmation out of scope for the prototype)."}</p>`}</div></section>
      </div>
      <section class="panel" aria-labelledby="h-aud"><div class="panel-head"><h2 id="h-aud">Audit history</h2><a class="btn btn-sm" href="#/audit?entity=${encodeURIComponent(t.id)}">Full audit trail</a></div><div class="panel-body">${timeline(d.audit)}</div></section>`;

    mountGrid($("#trade-exc"), "trade-exc", excCols(false), d.exceptions, { caption: "Exceptions on this trade", empty: "No exceptions on this trade." });
    const btnCancel = $("#btn-cancel");
    if (btnCancel) btnCancel.addEventListener("click", () => reasonDialog({
      title: `Cancel ${t.id}?`, confirmText: "Cancel trade", confirmCls: "btn-danger",
      intro: `<div class="alert alert-warn"><strong>This removes the trade from open exposure.</strong> The record is kept (soft cancel) and the action is written to the audit trail.</div>`,
      onConfirm: async (reason) => { await api(`/trades/${encodeURIComponent(t.id)}/cancel`, { method: "POST", body: { reason } }); toast(`${t.id} cancelled`); route(); },
    }));
    const btnReview = $("#btn-review");
    if (btnReview) btnReview.addEventListener("click", () => reviewDialog(t));
  }

  function reviewDialog(t) {
    const blocked = !isRisk() ? `<div class="alert alert-warn">Reviewing requires the <strong>Risk</strong> role. Switch the demo identity to “Demo Risk Reviewer” in the top bar.</div>`
      : t.updated_by === USER ? `<div class="alert alert-warn">Maker-checker: you last changed this trade, so you cannot review it.</div>` : "";
    reasonDialog({
      title: `Review ${t.id}`, confirmText: "Record decision", confirmCls: "btn-teal",
      intro: `${blocked}<p>Captured by <strong>${esc(t.updated_by)}</strong>. Check terms, valuation inputs and broker confirmation before deciding.</p>`,
      extraHtml: `<fieldset style="margin:8px 0 0"><legend>Decision</legend>
        <label><input type="radio" name="decision" value="APPROVE" checked> Approve — mark as validated</label><br>
        <label><input type="radio" name="decision" value="REJECT"> Reject — return to trader for correction</label></fieldset>`,
      onConfirm: async (reason) => {
        const decision = $("input[name=decision]:checked").value;
        await api(`/trades/${encodeURIComponent(t.id)}/review`, { method: "POST", body: { decision, reason } });
        toast(`${t.id} ${decision === "APPROVE" ? "approved" : "rejected"}`); route();
      },
    });
  }

  // ── Capture / amend ───────────────────────────────────────────────────────
  function monthOptions(commodity, current) {
    const spec = REF.commodities[commodity];
    if (!spec) return [];
    const out = []; const d = new Date(); let y = d.getUTCFullYear(), m = d.getUTCMonth() + 1;
    for (let i = 0; i < 30; i++) {
      if (spec.listed_months.includes(m)) out.push(`${y}-${String(m).padStart(2, "0")}`);
      m++; if (m > 12) { m = 1; y++; }
    }
    if (current && !out.includes(current)) out.unshift(current);
    return out;
  }

  function suggestedMaturity(instrument, month) {
    if (!month) return "";
    const [y, m] = month.split("-").map(Number);
    const iso = (yy, mm, dd) => `${yy}-${String(mm).padStart(2, "0")}-${String(dd).padStart(2, "0")}`;
    if (instrument === "FUTURE") return iso(y, m, 14);
    if (instrument === "OPTION") return m === 1 ? iso(y - 1, 12, 25) : iso(y, m - 1, 25);
    return iso(y, m, new Date(Date.UTC(y, m, 0)).getUTCDate());
  }

  const activeCodes = (list) => list.filter((x) => x.status === "ACTIVE").map((x) => x.code);
  /** Active options plus the record's current value (which may since have been deactivated). */
  const withCurrent = (opts, cur) => (cur && !opts.includes(cur) ? [...opts, cur] : opts);

  async function viewCapture(id) {
    main.innerHTML = loading();
    const editing = Boolean(id);
    let t = null;
    if (editing) {
      t = (await api("/trades/" + encodeURIComponent(id))).trade;
      if (t.status === "CANCELLED") { main.innerHTML = `<div class="alert alert-warn">Cancelled trades cannot be amended. <a href="#/trade/${encodeURIComponent(id)}">Back to trade</a></div>`; return; }
    }
    const today = new Date().toISOString().slice(0, 10);
    const v = t || { commodity: "CBOT_CORN", instrument_type: "FUTURE", direction: "BUY", pricing_type: "FIXED", source: "MANUAL",
      trade_date: today, effective_date: today, portfolio: SCOPE || REF.portfolios[0] };
    const sel = (name, options, lbl, req = true, fmt = (o) => o, hint = "") => `<div class="field" data-field="${name}"><label for="t-${name}">${lbl}${req ? ' <span class="req" aria-hidden="true">*</span>' : ""}</label>
      <select id="t-${name}" name="${name}" ${req ? "required" : ""} aria-describedby="e-${name}${hint ? ` h-${name}` : ""}">${req ? "" : '<option value=""></option>'}${!v[name] && req ? '<option value="">Select…</option>' : ""}
      ${options.map((o) => `<option value="${esc(o)}" ${String(v[name] ?? "") === String(o) ? "selected" : ""}>${esc(fmt(o))}</option>`).join("")}</select>
      ${hint ? `<span class="hint" id="h-${name}">${hint}</span>` : ""}<span class="err" id="e-${name}"></span></div>`;
    const inp = (name, lbl, type = "text", req = true, extra = "", hint = "") => `<div class="field" data-field="${name}"><label for="t-${name}">${lbl}${req ? ' <span class="req" aria-hidden="true">*</span>' : ""}</label>
      <input id="t-${name}" name="${name}" type="${type}" value="${esc(v[name] ?? "")}" ${req ? "required" : ""} ${extra} aria-describedby="e-${name} h-${name}">
      <span class="hint" id="h-${name}">${hint}</span><span class="err" id="e-${name}"></span></div>`;

    main.innerHTML = `<div class="page-head"><div><h1>${editing ? `Amend <span class="mono">${esc(t.id)}</span>` : "Capture trade"}</h1>
      <p>${editing ? `Version ${t.version} → ${t.version + 1}. Amendments return the trade to <strong>pending review</strong> and require a reason.` : "New trades are saved as <strong>pending review</strong> and must be approved by a second (risk) user."} ${DEMO}</p></div></div>
      <div id="form-alert"></div>
      <div class="form-layout">
      <form id="trade-form" novalidate>
        <fieldset><legend>Parties</legend><div class="form-grid">
          ${sel("counterparty", withCurrent(REF.counterparties, v.counterparty), "Counterparty")}
          ${sel("portfolio", withCurrent(REF.portfolios, v.portfolio), "Portfolio / business unit")}
          ${sel("strategy", withCurrent(activeCodes(REF.strategies), v.strategy), "Strategy", false, strategyName, "Optional — used for exposure by strategy")}</div></fieldset>
        <fieldset><legend>Product</legend><div class="form-grid">
          ${sel("commodity", Object.keys(REF.commodities), "Commodity", true, (k) => `${REF.commodities[k].name} (${REF.commodities[k].exchange} ${REF.commodities[k].code})`)}
          ${sel("instrument_type", REF.instrument_types, "Instrument type", true, label)}
          ${sel("direction", REF.directions, "Buy / sell", true, label)}
          ${sel("option_type", REF.option_types, "Option type", true, label)}
          ${inp("strike", "Strike", "number", true, 'step="any" min="0" inputmode="decimal"')}
          ${sel("pricing_type", REF.pricing_types, "Pricing terms", true, label, "Index-average applies to swaps only")}
          ${inp("reference_market", "Reference market", "text", false, 'maxlength="120"', "Defaults to exchange, code and month")}</div></fieldset>
        <fieldset><legend>Quantity &amp; price</legend><div class="form-grid">
          ${inp("quantity", "Quantity", "number", true, 'step="any" min="0" inputmode="decimal"')}
          ${inp("quantity_unit", "Quantity unit", "text", false, "readonly tabindex=-1", "Set by contract")}
          ${inp("price", "Price", "number", true, 'step="any" min="0" inputmode="decimal"')}
          ${inp("price_unit", "Price unit", "text", false, "readonly tabindex=-1", "Set by contract")}
          ${inp("currency", "Currency", "text", false, "readonly tabindex=-1", "Set by contract")}</div></fieldset>
        <fieldset><legend>Dates &amp; delivery</legend><div class="form-grid">
          ${sel("contract_month", monthOptions(v.commodity, v.contract_month), "Contract / delivery month")}
          ${inp("trade_date", "Trade date", "date")}
          ${inp("effective_date", "Effective date", "date", true, "", "Defaults to trade date")}
          ${inp("maturity_date", "Maturity / last trade / delivery", "date", true, "", "Suggested from instrument and month")}
          ${sel("delivery_location", withCurrent(activeCodes(REF.locations), v.delivery_location), "Delivery location", false, locationName, "Forwards only")}</div></fieldset>
        <fieldset><legend>Execution &amp; source</legend><div class="form-grid">
          ${sel("broker", withCurrent(REF.brokers, v.broker), "Broker", false)}
          ${inp("execution_ref", "Execution / broker reference", "text", false, 'maxlength="120"', "Required for exchange-traded trades")}
          ${sel("source", REF.sources, "Source", true, label)}
          <div class="field" data-field="notes" style="grid-column:1/-1"><label for="t-notes">Supporting notes</label><textarea id="t-notes" name="notes" rows="2" maxlength="1000" aria-describedby="e-notes">${esc(v.notes || "")}</textarea><span class="err" id="e-notes"></span></div></div></fieldset>
        ${editing ? `<fieldset><legend>Amendment</legend><div class="field" data-field="reason"><label for="t-reason">Reason for amendment <span class="req" aria-hidden="true">*</span></label>
          <textarea id="t-reason" name="reason" rows="2" maxlength="500" required aria-describedby="e-reason"></textarea><span class="err" id="e-reason"></span></div></fieldset>` : ""}
        <div class="form-actions"><a class="btn" href="${editing ? `#/trade/${encodeURIComponent(t.id)}` : "#/blotter"}">Discard</a>
          <button class="btn" type="button" id="btn-check">Check validation</button>
          <button class="btn btn-primary" type="submit">${editing ? "Save amendment…" : "Submit for review…"}</button></div>
      </form>
      <aside class="panel side-summary" aria-labelledby="h-ticket"><div class="panel-head"><h2 id="h-ticket">Trade ticket</h2></div>
        <div class="panel-body" id="ticket" aria-live="polite"></div>
        <div class="panel-note">Saved trades go to <strong>pending review</strong>. Validation runs on the server; this ticket is a display summary only.</div></aside>
      </div>`;

    const form = $("#trade-form");
    const f = {};   // named controls (explicit lookup rather than form[name])
    $$("[name]", form).forEach((el) => { f[el.name] = el; });
    let maturityTouched = editing;
    $("#t-maturity_date").addEventListener("input", () => { maturityTouched = true; });
    const ticket = () => {
      const c = REF.commodities[f.commodity.value];
      const q = parseFloat(f.quantity.value);
      const row = (k, val) => `<div><dt>${k}</dt><dd>${val || '<span class="muted">—</span>'}</dd></div>`;
      $("#ticket").innerHTML = `<dl class="kv" style="grid-template-columns:1fr">
        ${row("Trade", `${esc(f.direction.value)} ${q ? num(q) : ""} ${c ? esc(c.qty_unit) : ""} ${c ? esc(c.name) : ""}`)}
        ${row("Instrument", `${esc(label(f.instrument_type.value))}${f.option_type.value ? ` ${esc(f.option_type.value)} @ ${esc(f.strike.value)}` : ""}`)}
        ${row("Month / maturity", `${mon(f.contract_month.value)} / ${dt(f.maturity_date.value)}`)}
        ${row(f.instrument_type.value === "OPTION" ? "Premium" : "Price", f.price.value ? `${esc(f.price.value)} ${c ? esc(c.price_unit) : ""}` : "")}
        ${row("Approx. tonnes", q && c ? `${num(q * c.tonnes_per_unit, 1)} t <span class="muted">(display only)</span>` : "")}
        ${row("Counterparty", esc(f.counterparty.value))}${row("Portfolio", esc(f.portfolio.value))}
        ${row("Strategy", f.strategy.value ? esc(strategyName(f.strategy.value)) : "")}</dl>`;
    };
    const sync = (changed) => {
      const c = REF.commodities[f.commodity.value];
      const inst = f.instrument_type.value;
      const isOpt = inst === "OPTION";
      $("[data-field=option_type]").hidden = !isOpt;
      $("[data-field=strike]").hidden = !isOpt;
      if (!isOpt) { f.option_type.value = ""; f.strike.value = ""; }
      $("[data-field=delivery_location]").hidden = inst !== "FORWARD";
      if (inst !== "FORWARD") f.delivery_location.value = "";
      $("label[for=t-price]").innerHTML = (isOpt ? "Premium" : "Price") + ' <span class="req" aria-hidden="true">*</span>';
      if (changed === "commodity") {
        const cur = f.contract_month.value;
        const opts = monthOptions(f.commodity.value);
        f.contract_month.innerHTML = opts.map((o) => `<option value="${o}" ${o === cur ? "selected" : ""}>${o}</option>`).join("");
      }
      if (c) {
        f.quantity_unit.value = c.qty_unit; f.price_unit.value = c.price_unit; f.currency.value = c.currency;
        $("#h-quantity").textContent = REF.exchange_traded.includes(inst) ? `Multiple of lot ${num(c.lot_size)} ${c.qty_unit}` : `In ${c.qty_unit}`;
        $("#h-price").textContent = `${c.price_unit}`;
      }
      if (!maturityTouched && ["commodity", "instrument_type", "contract_month", "init"].includes(changed)) {
        f.maturity_date.value = suggestedMaturity(inst, f.contract_month.value);
      }
      if (changed === "trade_date" && !f.effective_date.dataset.touched) f.effective_date.value = f.trade_date.value;
      ticket();
    };
    f.effective_date.addEventListener("input", () => { f.effective_date.dataset.touched = "1"; });
    ["commodity", "instrument_type", "contract_month", "quantity", "trade_date"].forEach((n) => f[n].addEventListener("change", () => sync(n)));
    form.addEventListener("input", () => ticket());
    sync("init");

    const collect = () => {
      const data = {};
      ["counterparty", "portfolio", "commodity", "instrument_type", "direction", "option_type", "strike", "pricing_type", "reference_market",
        "quantity", "price", "contract_month", "trade_date", "effective_date", "maturity_date", "broker", "execution_ref", "source", "notes",
        "strategy", "delivery_location"]
        .forEach((n) => { data[n] = f[n].value; });
      return data;
    };
    const showErrors = (errors = {}, warnings = [], heading = "") => {
      $$(".field", form).forEach((f) => { f.classList.remove("invalid"); const e = $(".err", f); if (e) e.textContent = ""; });
      $$("[aria-invalid]", form).forEach((el) => el.removeAttribute("aria-invalid"));
      const keys = Object.keys(errors);
      keys.forEach((k) => {
        const f = $(`[data-field="${k}"]`, form); if (!f) return;
        f.classList.add("invalid"); $(".err", f).textContent = errors[k];
        const ctl = $("input,select,textarea", f); if (ctl) ctl.setAttribute("aria-invalid", "true");
      });
      let html = "";
      if (keys.length) html += `<div class="alert alert-error" role="alert" tabindex="-1" id="err-summary"><strong>${esc(heading || "Please correct the following:")}</strong><ul>
        ${keys.map((k) => `<li><a href="#t-${esc(k)}" data-focus="t-${esc(k)}">${esc(label(k))}</a>: ${esc(errors[k])}</li>`).join("")}</ul></div>`;
      if (warnings.length) html += `<div class="alert alert-warn" role="status"><strong>Warnings</strong><ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
      $("#form-alert").innerHTML = html;
      $$("[data-focus]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); const el = document.getElementById(a.dataset.focus); if (el) el.focus(); }));
      if (keys.length) $("#err-summary").focus();
    };

    $("#btn-check").addEventListener("click", async () => {
      try {
        const r = await api("/trades/validate", { method: "POST", body: { trade: collect(), id: editing ? t.id : null } });
        showErrors(r.errors, r.warnings);
        if (r.ok) $("#form-alert").insertAdjacentHTML("afterbegin", `<div class="alert alert-ok" role="status">All validation rules pass.</div>`);
      } catch (e) { toast(e.message, true); }
    });

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const data = collect();
      const reason = editing ? f.reason.value.trim() : "";
      let pre;
      try { pre = await api("/trades/validate", { method: "POST", body: { trade: data, id: editing ? t.id : null } }); }
      catch (e) { toast(e.message, true); return; }
      const errs = { ...pre.errors };
      if (editing && reason.length < 5) errs.reason = "A reason of at least 5 characters is required.";
      if (Object.keys(errs).length) { showErrors(errs, pre.warnings, "Trade not saved — please correct the following:"); return; }
      showErrors({}, pre.warnings);
      const c = REF.commodities[data.commodity];
      openDialog(editing ? `Save amendment to ${t.id}?` : "Submit trade for review?", `
        <dl class="kv">
          <div><dt>Trade</dt><dd>${esc(data.direction)} ${num(data.quantity)} ${esc(c.qty_unit)} ${esc(c.name)} ${esc(label(data.instrument_type))}${data.option_type ? " " + esc(data.option_type) + " @ " + esc(data.strike) : ""}</dd></div>
          <div><dt>Month / maturity</dt><dd>${mon(data.contract_month)} / ${dt(data.maturity_date)}</dd></div>
          <div><dt>${data.instrument_type === "OPTION" ? "Premium" : "Price"}</dt><dd>${esc(data.price)} ${esc(c.price_unit)}</dd></div>
          <div><dt>Counterparty</dt><dd>${esc(data.counterparty)}</dd></div>
          <div><dt>Portfolio</dt><dd>${esc(data.portfolio)}</dd></div></dl>
        ${pre.warnings.length ? `<div class="alert alert-warn" style="margin-top:12px">${pre.warnings.map(esc).join("<br>")}</div>` : ""}
        <p class="muted">The trade will be <strong>pending review</strong> until approved by a second user. This action is recorded in the audit trail.</p><div id="dlg-error"></div>`,
      [{ text: editing ? "Save amendment" : "Submit", onClick: async () => {
        try {
          const r = editing ? await api(`/trades/${encodeURIComponent(t.id)}`, { method: "PUT", body: { trade: data, reason } })
            : await api("/trades", { method: "POST", body: { trade: data } });
          dlg.close();
          toast(`${r.trade.id} ${editing ? "amended" : "captured"} — pending review`);
          location.hash = `#/trade/${encodeURIComponent(r.trade.id)}`;
        } catch (e) {
          dlg.close();
          if (e.data && e.data.errors) showErrors(e.data.errors, e.data.warnings || [], e.message);
          else toast(e.message, true);
        }
      } }]);
    });
  }

  // ── Monitoring ────────────────────────────────────────────────────────────
  const MON_GROUPS = [
    ["Maturities", ["MATURITY_SOON", "MATURED_OPEN"]],
    ["Missing data", ["MISSING_DATA"]],
    ["Market inputs", ["STALE_PRICE", "MISSING_PRICE"]],
    ["Unmatched records", ["UNCONFIRMED", "CONFIRM_MISMATCH"]],
    ["Valuation exceptions", ["PRICE_OUT_OF_BAND"]],
    ["Manual reviews", ["PENDING_REVIEW", "REJECTED_TRADE"]],
    ["Limits", ["LIMIT_BREACH", "LIMIT_WARNING"]],
  ];
  const monState = { group: "" };
  async function viewMonitoring() {
    main.innerHTML = loading();
    const [allExc, allTrades] = await Promise.all([api("/exceptions"), api("/trades" + (SCOPE ? `?portfolio=${encodeURIComponent(SCOPE)}` : ""))]);
    const inScope = new Set(allTrades.map((t) => t.id));
    const exc = allExc.filter((e) => !e.trade_id || inScope.has(e.trade_id));
    const groupOf = (rule) => (MON_GROUPS.find(([, rules]) => rules.includes(rule)) || ["Other"])[0];
    exc.forEach((e) => { e.group = groupOf(e.rule); });
    const byStatus = REF.statuses.map((s) => [s, allTrades.filter((t) => t.status === s).length]);
    const now = Date.now();
    const upcoming = allTrades.filter((t) => ["PENDING_REVIEW", "VALIDATED"].includes(t.status))
      .map((t) => ({ ...t, days: Math.round((Date.parse(t.maturity_date) - now) / 86400000) }))
      .filter((t) => t.days <= 60);
    const shown = exc.filter((e) => !monState.group || e.group === monState.group);
    main.innerHTML = `<div class="page-head"><div><h1>Trade monitoring</h1><p>${esc(SCOPE || "All portfolios")} · monitoring rules evaluated on every request ${DET}</p></div>
      <div class="page-actions"><a class="btn" href="#/exceptions">Act on exceptions</a></div></div>
      <section class="panel"><div class="panel-head"><h2>Trades by status</h2></div>
        <div class="chips">${byStatus.map(([s, n]) => `<a class="chip" href="#/blotter" data-status="${esc(s)}">${esc(label(s))}<span class="n">${n}</span></a>`).join("")}</div></section>
      <section class="panel"><div class="panel-head"><h2>Monitoring items</h2><span class="muted">${shown.length} of ${exc.length}</span></div>
        <div class="chips" role="group" aria-label="Filter by category">
          <button class="chip" aria-pressed="${!monState.group}" data-group="">All<span class="n">${exc.length}</span></button>
          ${MON_GROUPS.map(([g]) => { const n = exc.filter((e) => e.group === g).length; return `<button class="chip" aria-pressed="${monState.group === g}" data-group="${esc(g)}">${esc(g)}<span class="n">${n}</span></button>`; }).join("")}</div>
        <div id="mon-grid"></div></section>
      <section class="panel"><div class="panel-head"><h2>Maturity calendar — next 60 days</h2>${DET}</div><div id="mat-grid"></div></section>`;
    mountGrid($("#mon-grid"), "mon", [
      { key: "severity", label: "Severity", sort: (e) => SEV_RANK[e.severity], render: (e) => sevTag(e.severity) },
      { key: "group", label: "Category" },
      { key: "title", label: "Item", render: (e) => `<strong>${esc(e.title)}</strong>` },
      { key: "entity_id", label: "Entity", render: entityLink },
      { key: "detail", label: "Detail", nosort: true, cls: () => "wrap", render: (e) => esc(e.detail) },
      { key: "state", label: "State", render: (e) => stateTag(e.state) },
    ], shown, { caption: "Monitoring items", sortKey: "severity", empty: "No items in this category." });
    mountGrid($("#mat-grid"), "mon-mat", [
      { key: "id", label: "Trade", render: (t) => tradeLink(t.id) },
      { key: "maturity_date", label: "Maturity", render: (t) => dt(t.maturity_date) },
      { key: "days", label: "Days", num: true, cls: (t) => (t.days < 0 ? "neg" : ""), render: (t) => t.days },
      { key: "commodity", label: "Commodity", sort: (t) => commodityName(t.commodity), render: (t) => esc(commodityName(t.commodity)) },
      { key: "instrument_type", label: "Instrument", render: (t) => esc(label(t.instrument_type)) },
      { key: "quantity", label: "Quantity", num: true, render: (t) => `${num(t.quantity)} ${esc(t.quantity_unit)}` },
      { key: "status", label: "Status", render: (t) => tradeTag(t.status) },
    ], upcoming, { caption: "Maturity calendar", sortKey: "days", empty: "No open trades mature in the next 60 days." });
    $$("[data-group]").forEach((b) => b.addEventListener("click", () => { monState.group = b.dataset.group; viewMonitoring(); }));
    $$("[data-status]").forEach((a) => a.addEventListener("click", () => { blotterState.filters = { portfolio: SCOPE, status: a.dataset.status }; }));
  }

  // ── Exposure & risk ───────────────────────────────────────────────────────
  let riskDim = "commodity";
  async function viewRisk() {
    main.innerHTML = loading();
    const r = await api("/risk");
    const keyLabel = (k) => dimLabel(riskDim, k);
    const aggs = Object.values(r.aggregates[riskDim]);
    main.innerHTML = `<div class="page-head"><div><h1>Exposure &amp; risk</h1><p>Firm-wide · valued ${ts(r.valued_at)} ${DET} ${DEMO}</p></div></div>
      ${SCOPE ? `<div class="alert alert-info">Exposure and limits are shown firm-wide; the portfolio selector does not filter this page. Use the <em>Portfolio</em> dimension below.</div>` : ""}
      <section class="panel core"><div class="panel-head"><h2>Exposure by ${esc(label(riskDim).toLowerCase())}</h2>
        <div class="chips" style="padding:0;border:0" role="group" aria-label="Dimension">${r.dimensions.map((d) => `<button class="chip" aria-pressed="${d === riskDim}" data-dim="${d}">${esc(label(d))}</button>`).join("")}</div></div>
        <div id="exp-grid"></div>
        <div class="panel-note"><p>Tonnes are delta-equivalent (options: delta 1 if in-the-money, else 0). Notional and MtM include only trades with a current or explicitly accepted mark; excluded trades are counted under “Data”.</p>${basis()}</div></section>
      <section class="panel core"><div class="panel-head"><h2>Configured limits</h2>${isRisk() ? '<span class="tag tag-info">Risk role — limits editable</span>' : '<span class="tag tag-low">Read-only · Risk role required to change</span>'}</div>
        <div id="lim-grid"></div></section>`;
    mountGrid($("#exp-grid"), "risk-exp-" + riskDim, [
      { key: "key", label: label(riskDim), sort: (a) => keyLabel(a.key), render: (a) => `<button class="btn-link" data-derive="${esc(riskDim)}|${esc(a.key)}">${esc(keyLabel(a.key))}</button>` },
      { key: "net_tonnes", label: "Net t", num: true, render: (a) => tonnes(a.net_tonnes) },
      { key: "gross_tonnes", label: "Gross t", num: true, render: (a) => tonnes(a.gross_tonnes) },
      { key: "gross_notional_usd", label: "Gross notional USD", num: true, render: (a) => usd(a.gross_notional_usd) },
      { key: "net_notional_usd", label: "Net notional USD", num: true, render: (a) => usd(a.net_notional_usd) },
      { key: "mtm_usd", label: "MtM USD", num: true, cls: (a) => signCls(a.mtm_usd), render: (a) => usd(a.mtm_usd) },
      { key: "trades", label: "Trades", num: true, sort: (a) => a.trade_ids.length, render: (a) => a.trade_ids.length },
      { key: "data", label: "Data", sort: (a) => a.incomplete.length, render: (a) => (a.data_complete ? '<span class="tag tag-ok">Complete</span>' : `<span class="tag tag-warn" title="${esc(a.incomplete.map((i) => i.trade_id + ": " + i.reason).join("; "))}">${a.incomplete.length} excluded</span>`) },
    ], aggs, { caption: `Exposure by ${label(riskDim)}`, empty: "No open exposure." });
    mountGrid($("#lim-grid"), "risk-lim", [
      { key: "id", label: "Limit", render: (l) => `<span class="mono">${esc(l.id)}</span><span class="sub">${esc(limitName(l))}</span>` },
      { key: "metric_label", label: "Metric", cls: () => "wrap", render: (l) => `${esc(l.metric_label)}<span class="sub">${esc(l.formula)}</span>` },
      { key: "exposure", label: "Exposure", num: true, render: (l) => `${num(l.exposure)} <span class="muted">${esc(l.unit)}</span>` },
      { key: "limit_value", label: "Limit", num: true, render: (l) => `${num(l.limit_value)} <span class="muted">${esc(l.unit)}</span><span class="sub">warn at ${pct(l.warn_pct)}</span>` },
      { key: "utilisation", label: "Utilisation", render: meter },
      { key: "status", label: "Status", sort: (l) => ({ BREACH: 0, WARN: 1, OK: 2 }[l.status]), render: (l) => limTag(l.status) + (l.data_complete ? "" : '<span class="sub"><span class="tag tag-warn">Incomplete data</span></span>') },
      { key: "owner", label: "Owner / last change", render: (l) => `${esc(l.owner)}<span class="sub">${esc(l.updated_by)} · ${ts(l.updated_at)}</span>` },
      { key: "actions", label: "", nosort: true, render: (l) => `<div class="page-actions"><button class="btn btn-sm" data-derive="${esc(l.dimension)}|${esc(l.key)}">Derivation</button>
          <button class="btn btn-sm" data-edit-limit="${esc(l.id)}" ${isRisk() ? "" : 'disabled title="Requires the Risk role"'}>Change…</button>
          <button class="btn btn-sm" data-explain="limit|${esc(l.id)}">Explain with AI…</button></div>` },
    ], r.limits, { caption: "Configured limits", sortKey: "utilisation", sortDir: -1 });
    $$("[data-dim]").forEach((b) => b.addEventListener("click", () => { riskDim = b.dataset.dim; viewRisk(); }));
    $("#lim-grid").addEventListener("click", (e) => {
      const b = e.target.closest("[data-edit-limit]");
      if (!b || b.disabled) return;
      const l = r.limits.find((x) => x.id === b.dataset.editLimit);
      reasonDialog({
        title: `Change limit ${l.id}`, confirmText: "Apply change", confirmCls: "btn-danger",
        intro: `<div class="alert alert-warn"><strong>Controlled action.</strong> Limit changes are restricted to the Risk role and recorded with before/after values. AI cannot change limits.</div>
          <p>${esc(limitName(l))} — ${esc(l.metric_label)}</p>`,
        extraHtml: `<div class="form-grid"><div class="field"><label for="lim-value">Limit (${esc(l.unit)})</label><input id="lim-value" type="number" min="0" step="any" value="${l.limit_value}"></div>
          <div class="field"><label for="lim-warn">Warning threshold (0.10–0.99)</label><input id="lim-warn" type="number" min="0.1" max="0.99" step="0.01" value="${l.warn_pct}"></div></div>`,
        onConfirm: async (reason) => {
          await api(`/limits/${encodeURIComponent(l.id)}`, { method: "PUT", body: { limit_value: $("#lim-value").value, warn_pct: $("#lim-warn").value, reason } });
          toast(`${l.id} updated`); route();
        },
      });
    });
  }

  async function showDerivation(dim, key) {
    openDrawer(`Derivation — ${label(dim)}: ${dimLabel(dim, key)}`, loading());
    try {
      const d = await api(`/risk/derivation?dim=${encodeURIComponent(dim)}&key=${encodeURIComponent(key)}`);
      const a = d.aggregate;
      if (!a) { $("#drawer-body").innerHTML = empty("No open trades contribute to this figure."); return; }
      $("#drawer-body").innerHTML = `<p>${DET} ${DEMO} <span class="muted">Valued ${ts(d.valued_at)}</span></p>
        <dl class="kv"><div><dt>Net tonnes</dt><dd>${tonnes(a.net_tonnes, 1)}</dd></div><div><dt>Gross tonnes</dt><dd>${tonnes(a.gross_tonnes, 1)}</dd></div>
          <div><dt>Gross notional USD</dt><dd>${usd(a.gross_notional_usd)}</dd></div><div><dt>MtM USD</dt><dd class="${signCls(a.mtm_usd)}">${usd(a.mtm_usd)}</dd></div></dl>
        <p class="muted" style="margin:12px 0 4px"><strong>Aggregation</strong></p>
        <div class="formula">Net t = Σ delta_t · Gross t = Σ |delta_t| · Notional / MtM = Σ over trades with status Valued or Stale — accepted</div>
        ${d.limits.map((l) => `<div class="alert ${l.status === "BREACH" ? "alert-error" : l.status === "WARN" ? "alert-warn" : "alert-ok"}" style="margin-top:12px">
          <strong>${esc(l.id)}</strong> ${esc(l.metric_label)}: ${num(l.exposure)} / ${num(l.limit_value)} ${esc(l.unit)} = <strong>${pct(l.utilisation)}</strong> (${esc(l.status)})<div class="mono">${esc(l.formula)}</div></div>`).join("")}
        ${a.incomplete.length ? `<div class="alert alert-warn" style="margin-top:12px"><strong>${a.incomplete.length} trade(s) not included in notional / MtM:</strong><ul>${a.incomplete.map((i) => `<li><span class="mono">${esc(i.trade_id)}</span> — ${esc(i.reason)}</li>`).join("")}</ul></div>` : ""}
        <h3 style="font-size:13px;color:var(--navy);margin:16px 0 6px">Contributing trades (${d.contributions.length})</h3>
        <div id="der-grid"></div>
        <div style="margin-top:12px">${basis()}</div>`;
      mountGrid($("#der-grid"), "derivation", [
        { key: "trade_id", label: "Trade", render: (c) => `${tradeLink(c.trade_id)}<span class="sub">${esc(commodityName(c.commodity))} ${esc(label(c.instrument_type))}</span>` },
        { key: "direction", label: "B/S" },
        { key: "quantity", label: "Qty", num: true, render: (c) => `${num(c.quantity)} <span class="muted">${esc(c.quantity_unit)}</span>` },
        { key: "contract_month", label: "Month", render: (c) => mon(c.contract_month) },
        { key: "trade_price", label: "Trade px", num: true, render: (c) => num(c.trade_price, 2) },
        { key: "mark_price", label: "Mark", num: true, render: (c) => (c.mark_price === null ? '<span class="neg">—</span>' : num(c.mark_price, 2)) },
        { key: "mark_as_of", label: "Mark as of", render: (c) => ts(c.mark_as_of) },
        { key: "delta_t", label: "Delta t", num: true, render: (c) => (c.delta_t === null ? "—" : tonnes(c.delta_t, 1)) },
        { key: "mtm_usd", label: "MtM USD", num: true, cls: (c) => signCls(c.mtm_usd), render: (c) => usd(c.mtm_usd) },
        { key: "valuation_status", label: "Status", render: (c) => `${valTag(c.valuation_status)}${c.included_in_totals ? "" : '<span class="sub">not in totals</span>'}` },
      ], d.contributions, { caption: "Contributing trades" });
    } catch (e) { $("#drawer-body").innerHTML = errorState(e); }
  }

  // ── Exceptions & reviews ──────────────────────────────────────────────────
  function excCols(withActions) {
    const cols = [
      { key: "severity", label: "Severity", sort: (e) => SEV_RANK[e.severity], render: (e) => sevTag(e.severity) },
      { key: "title", label: "Exception", render: (e) => `<strong>${esc(e.title)}</strong>` },
      { key: "entity_id", label: "Entity", render: entityLink },
      { key: "detail", label: "Detail", nosort: true, cls: () => "wrap", render: (e) => `${esc(e.detail)}${e.last_action ? `<span class="sub">${esc(label(e.last_action.action))} by ${esc(e.last_action.actor)} · ${ts(e.last_action.timestamp)}: “${esc(e.last_action.reason)}”</span>` : ""}` },
      { key: "state", label: "State", render: (e) => stateTag(e.state) },
    ];
    if (withActions) cols.push({ key: "actions", label: "Actions", nosort: true, render: (e) => `<div class="page-actions">
      ${e.rule === "PENDING_REVIEW" ? `<button class="btn btn-sm btn-teal" data-review="${esc(e.trade_id)}">Review</button>` : ""}
      ${e.state === "OPEN" ? `<button class="btn btn-sm" data-ack="${esc(e.id)}">Acknowledge</button>` : ""}
      ${e.overridable && e.state !== "OVERRIDDEN" ? `<button class="btn btn-sm btn-danger" data-override="${esc(e.id)}">Override</button>` : ""}
      <button class="btn btn-sm" data-explain="exception|${esc(e.id)}">Explain with AI…</button></div>` });
    return cols;
  }

  const excFilter = { severity: "", state: "OPEN" };
  async function viewExceptions() {
    main.innerHTML = loading();
    const list = await api("/exceptions");
    refreshChrome();
    const filtered = list.filter((e) => (!excFilter.severity || e.severity === excFilter.severity) && (!excFilter.state || e.state === excFilter.state));
    main.innerHTML = `<div class="page-head"><div><h1>Exceptions &amp; reviews</h1><p>Detected by deterministic rules · manual actions require a reason and are audited ${DET}</p></div></div>
      <div class="alert alert-info">Acting as <strong>${esc(SESSION.label)}</strong> (demo identity, role ${esc(SESSION.role)}). Overrides and trade reviews need the Risk role; you cannot review your own changes (maker-checker).</div>
      <section class="panel"><div class="filters">
        <div class="field"><label for="x-sev">Severity</label><select id="x-sev"><option value="">All</option>${["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => `<option ${excFilter.severity === s ? "selected" : ""} value="${s}">${label(s)}</option>`).join("")}</select></div>
        <div class="field"><label for="x-state">State</label><select id="x-state"><option value="">All</option>${["OPEN", "ACKNOWLEDGED", "OVERRIDDEN"].map((s) => `<option ${excFilter.state === s ? "selected" : ""} value="${s}">${label(s)}</option>`).join("")}</select></div>
        <span class="muted" style="align-self:center">${filtered.length} of ${list.length}</span></div>
        <div id="exc-grid"></div></section>`;
    mountGrid($("#exc-grid"), "exc", excCols(true), filtered, { caption: "Exceptions", sortKey: "severity", empty: "No exceptions match these filters." });
    $("#x-sev").addEventListener("change", (e) => { excFilter.severity = e.target.value; viewExceptions(); });
    $("#x-state").addEventListener("change", (e) => { excFilter.state = e.target.value; viewExceptions(); });
    const find = (id) => list.find((e) => e.id === id);
    $("#exc-grid").addEventListener("click", async (ev) => {
      const ack = ev.target.closest("[data-ack]"), ovr = ev.target.closest("[data-override]"), rev = ev.target.closest("[data-review]");
      if (ack) {
        const e = find(ack.dataset.ack);
        reasonDialog({ title: "Acknowledge exception", confirmText: "Acknowledge", intro: `<p>${sevTag(e.severity)} <strong>${esc(e.title)}</strong></p><p>${esc(e.detail)}</p><p class="muted">Acknowledging records that you have seen the exception. It does not change any figure.</p>`,
          onConfirm: async (reason) => { await api(`/exceptions/${encodeURIComponent(e.id)}/action`, { method: "POST", body: { action: "ACKNOWLEDGE", reason } }); toast("Exception acknowledged"); route(); } });
      } else if (ovr) {
        const e = find(ovr.dataset.override);
        const effect = e.rule === "STALE_PRICE" ? "Dependent trades will be <strong>included in MtM totals using the stale mark</strong> (flagged “Stale — accepted”) until a new mark arrives."
          : "The exception will be marked overridden; figures are unchanged.";
        reasonDialog({ title: "Override exception", confirmText: "Apply override", confirmCls: "btn-danger",
          intro: `<div class="alert alert-error"><strong>Controlled manual override.</strong> Requires the Risk role, is recorded in the audit trail and is subject to review. AI cannot apply overrides.</div>
            <p>${sevTag(e.severity)} <strong>${esc(e.title)}</strong> — ${esc(e.detail)}</p><p>${effect}</p>`,
          onConfirm: async (reason) => { await api(`/exceptions/${encodeURIComponent(e.id)}/action`, { method: "POST", body: { action: "OVERRIDE", reason } }); toast("Override recorded"); route(); } });
      } else if (rev) {
        try { reviewDialog((await api("/trades/" + encodeURIComponent(rev.dataset.review))).trade); } catch (e) { toast(e.message, true); }
      }
    });
  }

  // ── Audit ─────────────────────────────────────────────────────────────────
  async function viewAudit(params) {
    main.innerHTML = loading();
    const entity = params.get("entity") || "";
    const ev = await api("/audit" + (entity ? `?entity_id=${encodeURIComponent(entity)}` : ""));
    const actions = [...new Set(ev.map((e) => e.action))].sort();
    main.innerHTML = `<div class="page-head"><div><h1>Audit trail</h1><p>Append-only log of trade changes, reviews, overrides, limit changes and AI requests · demo identities are not real users</p></div></div>
      <div class="alert alert-info">${esc(REF.persistence.note)}</div>
      <section class="panel"><div class="filters">
        <div class="field"><label for="a-entity">Entity ID</label><input id="a-entity" value="${esc(entity)}" placeholder="e.g. AGD-0001"></div>
        <div class="field"><label for="a-action">Action</label><select id="a-action"><option value="">All</option>${actions.map((a) => `<option value="${esc(a)}">${esc(label(a))}</option>`).join("")}</select></div>
        <button class="btn" id="a-go">Apply</button></div><div id="audit-out"></div></section>`;
    const render = () => {
      const act = $("#a-action").value;
      mountGrid($("#audit-out"), "audit", [
        { key: "timestamp", label: "Timestamp", cls: () => "nowrap", render: (e) => ts(e.timestamp) },
        { key: "actor", label: "Actor" },
        { key: "action", label: "Action", render: (e) => esc(EVENT[e.action] || label(e.action)) },
        { key: "entity_id", label: "Entity", render: (e) => entityCell(e.entity_type, e.entity_id) },
        { key: "reason", label: "Reason", nosort: true, cls: () => "wrap" },
        { key: "changes", label: "Changes", nosort: true, render: (e) => (Object.keys(e.changes || {}).length ? `<details><summary>${Object.keys(e.changes).length} field(s)</summary>${diffTable(e.changes)}</details>` : '<span class="muted">—</span>') },
      ], ev.filter((e) => !act || e.action === act), { caption: "Audit events", sortKey: "timestamp", sortDir: -1, empty: "No audit events match." });
    };
    $("#a-action").addEventListener("change", render);
    $("#a-go").addEventListener("click", () => { const v = $("#a-entity").value.trim(); location.hash = v ? `#/audit?entity=${encodeURIComponent(v)}` : "#/audit"; });
    render();
  }

  // ── Market data ───────────────────────────────────────────────────────────
  async function viewMarketData() {
    main.innerHTML = loading();
    const d = await api("/market-data");
    main.innerHTML = `<div class="page-head"><div><h1>Market data</h1><p>${DEMO} Stale threshold ${d.stale_threshold_hours}h</p></div></div>
      <div class="alert alert-warn"><strong>${esc(d.source)}.</strong> No market-data provider is connected. Marks are fabricated for demonstration and must not be used for any decision.</div>
      <section class="panel"><div id="md-grid"></div><div class="panel-note">Contract months with open trades but no row here are reported as <strong>missing market input</strong> exceptions; those trades are not valued.</div></section>`;
    mountGrid($("#md-grid"), "md", [
      { key: "commodity", label: "Commodity", sort: (p) => commodityName(p.commodity), render: (p) => esc(commodityName(p.commodity)) },
      { key: "contract_month", label: "Contract month", render: (p) => mon(p.contract_month) },
      { key: "price", label: "Mark", num: true, render: (p) => num(p.price, 2) },
      { key: "price_unit", label: "Unit" },
      { key: "as_of", label: "As of", render: (p) => ts(p.as_of) },
      { key: "age_hours", label: "Age (h)", num: true },
      { key: "stale", label: "Freshness", render: (p) => (p.stale ? '<span class="tag tag-high">Stale</span>' : '<span class="tag tag-ok">Current</span>') },
      { key: "source", label: "Source", cls: () => "muted" },
    ], d.prices, { caption: "Market data" });
  }

  // ── System & AI ───────────────────────────────────────────────────────────
  async function viewSystem() {
    main.innerHTML = loading();
    const s = await api("/ai/status");
    main.innerHTML = `<div class="page-head"><div><h1>System &amp; AI</h1><p>Where deterministic system control ends and AI assistance begins; provider status and data handling.</p></div></div>
      <div class="boundary">
        <section class="panel core"><div class="panel-head"><h2>Deterministic core — system-controlled</h2></div><div class="panel-body"><ul>
          <li>Trade validation (lots, listed months, date order, option terms, units)</li><li>MtM valuation, delta-equivalent tonnes and notional</li>
          <li>Aggregation by commodity, maturity, portfolio, counterparty</li><li>Limit utilisation, warnings and breaches</li>
          <li>Exception rules (stale or missing inputs, unmatched records, maturities, off-market prices)</li><li>Maker-checker review, reason-coded overrides, append-only audit</li></ul></div></section>
        <section class="panel ai"><div class="panel-head"><h2>AI — assistive only</h2></div><div class="panel-body"><ul>
          <li>Plain-language explanation (Claude) of figures the engine already calculated, after you preview and confirm the data sent</li>
          <li>Advisory counterparty memos (market, credit, liquidity agents on the <a href="/counterparty-analytics">counterparty analytics</a> page)</li>
          <li>If AI is unavailable, every core function still works</li></ul></div></section>
        <section class="panel never"><div class="panel-head"><h2>Never AI</h2></div><div class="panel-body"><ul>
          <li>Authoritative valuations, MtM, exposures, sensitivities</li><li>Limit setting or utilisation</li><li>Trade approval, rejection or overrides</li>
          <li>Accounting entries or financial reporting</li><li>Acting as the system of record</li></ul></div></section>
      </div>
      <section class="panel"><div class="panel-head"><h2>AI provider</h2><div class="page-actions">
          <span class="tag ${s.enabled ? "tag-ok" : "tag-low"}">${s.enabled ? "Enabled" : "Disabled"}</span>
          <button class="btn btn-sm" id="btn-ai-check" ${s.enabled && s.configured ? "" : "disabled"}>Test connection</button></div></div>
        ${s.invalid_settings.length ? `<div class="panel-body" style="padding-bottom:0"><div class="alert alert-warn"><strong>Configuration warnings</strong><ul>${s.invalid_settings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>` : ""}
        <div class="panel-body"><dl class="kv">
          <div><dt>Provider</dt><dd>${esc(PROVIDER[s.provider] || s.provider)}</dd></div>
          <div><dt>Model</dt><dd class="mono">${esc(s.model)}</dd></div>
          <div><dt>API key</dt><dd>${s.configured ? '<span class="tag tag-ok">Present</span>' : '<span class="tag tag-low">Missing</span>'}</dd></div>
          <div><dt>Last result</dt><dd>${!s.last ? '<span class="muted">Not used since start</span>' : s.last.ok ? `<span class="tag tag-ok">OK</span> <span class="sub">${ts(s.last.at)}</span>` : `<span class="tag tag-high">Failed</span> <span class="sub">${ts(s.last.at)}</span>`}</dd></div></dl>
          ${s.last && !s.last.ok && s.last.diagnostic ? `<div class="alert alert-warn" style="margin:12px 0 0">${diagHtml(s.last.diagnostic)}</div>` : ""}
          <div id="ai-check-out"></div></div>
        <div class="panel-note"><p><strong>Configuration</strong> (environment or <span class="mono">.env</span>): <span class="mono">LLM_ENABLED</span>, <span class="mono">ANTHROPIC_API_KEY</span>, <span class="mono">CLAUDE_MODEL</span>, <span class="mono">LLM_TIMEOUT_SECONDS</span>. Keys are never displayed.</p>
          <p>“Test connection” sends only the fixed text “Reply with the single word OK.” — no application data.</p></div></section>
      <section class="panel"><div class="panel-head"><h2>Data sent to AI providers</h2></div><div class="panel-body">
        <p style="margin-top:0">${esc(s.data_policy)}</p><p class="muted" style="margin-bottom:0"><strong>Never sent:</strong> ${s.never_sent.map(esc).join(", ")}.</p></div></section>
      <section class="panel"><div class="panel-head"><h2>Data &amp; persistence</h2></div><div class="panel-body">
        <p style="margin-top:0">${esc(REF.persistence.note)}</p>
        <ul class="list"><li>All trades, counterparties, prices, FX rates, broker confirmations and limits are <strong>synthetic</strong>.</li>
          <li>${REF.persistence.persistent ? `Storage: BigQuery <span class="mono">${esc(REF.persistence.dataset)}</span>, tables ${REF.persistence.tables.map((t) => `<span class="mono">${esc(t)}</span>`).join(", ")}; append-only (generation ${REF.persistence.generation}).` : "Storage: in memory only — not saved."}</li>
          <li>Demo identities replace real authentication and entitlements (IAP still protects the deployed service).</li>
          <li>${REF.valuation_methodology.map(esc).join("; ")}.</li>
          <li>Out of scope: integrations (exchange or broker feeds, ETRM, accounting), physical logistics, settlement, payments.</li></ul></div></section>
      <section class="panel"><div class="panel-head"><h2>Demo controls</h2></div><div class="panel-body"><p style="margin-top:0">Reload the synthetic data set for every user. ${REF.persistence.persistent ? "Earlier data stays in BigQuery as a previous generation but is no longer shown." : "All changes since the last restart are discarded."}</p>
        <button class="btn btn-danger" id="btn-reset">Reset demo data…</button></div></section>`;
    $("#btn-ai-check").addEventListener("click", async (ev) => {
      ev.target.disabled = true;
      $("#ai-check-out").innerHTML = loading("Testing connection…");
      try {
        const { result: x } = await api("/ai/check", { method: "POST", body: {} });
        $("#ai-check-out").innerHTML = x.ok
          ? `<div class="alert alert-ok" style="margin:12px 0 0">Connection OK · ${esc(x.model)}</div>`
          : `<div class="alert alert-error" style="margin:12px 0 0">${diagHtml(x.diagnostic)}</div>`;
        loadAiStatus();
      } catch (e) { $("#ai-check-out").innerHTML = `<div class="alert alert-error" style="margin:12px 0 0">${esc(e.message)}</div>`; }
      ev.target.disabled = false;
    });
    $("#btn-reset").addEventListener("click", () => openDialog("Reset demo data?", `<div class="alert alert-warn">All trades, reviews, overrides and limit changes will be replaced by a fresh synthetic data set for every user.${REF.persistence.persistent ? " The current data is kept in BigQuery as a previous generation." : ""}</div>`,
      [{ text: "Reset", cls: "btn-danger", onClick: async () => { await api("/demo/reset", { method: "POST", body: {} }); dlg.close(); toast("Demo data reset"); refreshChrome(); route(); } }]));
  }

  // ── Data Setup ────────────────────────────────────────────────────────────
  const dsFilter = { kind: "", q: "", status: "" };
  const statusTag = (s) => (s === "ACTIVE" ? '<span class="tag tag-ok">Active</span>' : '<span class="tag tag-low">Inactive</span>');
  const recordFields = (r) => ({ name: r.name, description: r.description, ...r.attrs });
  const persistNote = () => (REF.persistence.persistent ? "Saved to BigQuery and recorded in the audit trail."
    : "Kept in memory only — lost when the service restarts. Recorded in the audit trail.");
  async function refreshRef() { REF = await api("/reference"); }
  const reloadSetup = async (kind, msg) => { toast(msg); await refreshRef(); refreshChrome(); viewDataSetup(kind); };

  function usageCell(kind, meta, r) {
    if (kind === "price_source") return '<span class="muted">All valuations</span>';
    const u = r.usage, parts = [];
    if (u.trades) {
      const txt = `${u.trades} trade${u.trades === 1 ? "" : "s"}${u.open_trades !== u.trades ? ` <span class="muted">(${u.open_trades} open)</span>` : ""}`;
      parts.push(meta.trade_field ? `<a href="#/blotter?${meta.trade_field}=${encodeURIComponent(r.code)}">${txt}</a>` : txt);
    }
    if (u.limits.length) parts.push(`<a href="#/risk">${u.limits.length} limit${u.limits.length === 1 ? "" : "s"}</a>`);
    return parts.join(" · ") || '<span class="muted">Not used</span>';
  }

  function dsActions(kind, meta, r) {
    if (!meta.editable) return "";
    const dis = isRisk() ? "" : 'disabled title="Requires the Risk role — switch the demo identity"';
    const id = esc(r.code);
    return `<div class="row-actions">
      <button class="btn btn-sm" data-ds-edit="${id}" ${dis}>Edit</button>
      <button class="btn btn-sm" data-ds-toggle="${id}" ${dis}>${r.status === "ACTIVE" ? "Deactivate" : "Activate"}</button>
      ${meta.deletable && !r.system ? `<button class="btn btn-sm btn-danger" data-ds-delete="${id}" ${dis}>Delete</button>` : ""}</div>`;
  }

  function dsColumns(kind, meta) {
    const code = { key: "code", label: "Code", cls: () => "mono" };
    const name = { key: "name", label: "Name", render: (r) => `<strong>${esc(r.name)}</strong>` };
    const status = { key: "status", label: "Status", render: (r) => statusTag(r.status) };
    const used = { key: "usage", label: "Used by", sort: (r) => r.usage.trades + r.usage.limits.length, render: (r) => usageCell(kind, meta, r) };
    const changed = { key: "updated_at", label: "Last change", render: (r) => (r.updated_at ? `${esc(r.updated_by)}<span class="sub">${ts(r.updated_at)}</span>` : '<span class="muted">—</span>') };
    const desc = { key: "description", label: "Description", nosort: true, cls: () => "wrap", render: (r) => esc(r.description) || '<span class="muted">—</span>' };
    const attr = (k, l) => ({ key: k, label: l, sort: (r) => r.attrs[k], render: (r) => esc(r.attrs[k] || "—") });
    const actions = { key: "actions", label: '<span class="sr-only">Actions</span>', nosort: true, render: (r) => dsActions(kind, meta, r) };
    switch (kind) {
      case "commodity": return [
        { key: "name", label: "Commodity", render: (r) => `<strong>${esc(r.name)}</strong><span class="sub mono">${esc(r.code)}</span>` },
        attr("category", "Category"),
        { key: "exchange", label: "Exchange · code", sort: (r) => r.spec.exchange, render: (r) => `${esc(r.spec.exchange)} · <span class="mono">${esc(r.spec.code)}</span>` },
        { key: "lot", label: "Lot size", num: true, sort: (r) => r.spec.lot_size, render: (r) => `${num(r.spec.lot_size)} <span class="muted">${esc(r.spec.qty_unit)}</span>` },
        { key: "price_unit", label: "Price unit", sort: (r) => r.spec.price_unit, render: (r) => esc(r.spec.price_unit) },
        { key: "months", label: "Listed months", nosort: true, render: (r) => r.spec.listed_months.map((m) => MONTHS[m - 1]).join(", ") },
        status, used, actions];
      case "strategy": return [code, name, desc, status, used, changed, actions];
      case "unit": return [code, name,
        { key: "tonnes", label: "Tonnes per unit", num: true, sort: (r) => r.attrs.tonnes_per_unit, render: (r) => r.attrs.tonnes_per_unit.toLocaleString("en-GB", { maximumFractionDigits: 9 }) },
        { key: "commodities", label: "Commodities", nosort: true, cls: () => "wrap", render: (r) => r.attrs.commodities.map((c) => esc(commodityName(c))).join(", ") || '<span class="muted">—</span>' },
        used];
      case "location": return [code, name, attr("location_type", "Type"), attr("region", "Region"), status, used, changed, actions];
      case "counterparty": return [name, attr("counterparty_type", "Type"), desc, status, used, changed, actions];
      case "portfolio": return [name, desc, status, used, changed, actions];
      case "broker": return [name, attr("broker_type", "Type"), status, used, changed, actions];
      case "price_source": return [name, desc,
        { key: "frequency", label: "Frequency", render: (r) => esc(r.attrs.frequency) },
        { key: "coverage", label: "Coverage", nosort: true, render: (r) => `${r.attrs.commodities.length} commodities` },
        { key: "stale", label: "Stale after", num: true, render: (r) => `${r.attrs.stale_after_hours} h` },
        { key: "link", label: '<span class="sr-only">Actions</span>', nosort: true, render: () => '<a class="btn btn-sm" href="#/market-data">View marks</a>' }];
      default: return [code, name, status];
    }
  }

  const DS_NOTES = {
    commodity: "Contract specifications are simplified demo approximations, not an authoritative contract master. Deactivating a commodity blocks new trades only.",
    unit: "Adding or changing a unit requires a conversion factor in code (agri/reference.py) covered by tests, because exposure figures depend on it.",
    price_source: "No market-data provider is connected. Marks are synthetic and regenerated daily.",
  };

  function showDsErrors(errs, msg) {
    $$("#ds-form .field").forEach((f) => { f.classList.remove("invalid"); const e = $(".err", f); if (e) e.textContent = ""; });
    const other = [];
    Object.entries(errs).forEach(([k, v]) => {
      const f = $(`#ds-form [data-dsf="${k}"]`);
      if (f) { f.classList.add("invalid"); $(".err", f).textContent = v; const c = $("input,select,textarea", f); if (c) c.setAttribute("aria-invalid", "true"); }
      else other.push(v);
    });
    $("#dlg-error").innerHTML = msg || other.length ? `<div class="alert alert-error" role="alert">${esc(msg || "")}${other.length ? ` ${other.map(esc).join(" ")}` : ""}</div>` : "";
    const first = $("#ds-form .field.invalid input, #ds-form .field.invalid select, #ds-form .field.invalid textarea");
    if (first) first.focus();
  }

  function dsForm(kind, meta, r) {
    const isNew = !r;
    const vals = r ? recordFields(r) : {};
    const fieldHtml = (fd) => {
      const ro = !isNew && fd.immutable;
      const req = fd.required && !ro;
      const id = `ds-${fd.name}`;
      const val = vals[fd.name] ?? "";
      const attrs = `id="${id}" name="${fd.name}" ${req ? 'required aria-required="true"' : ""} ${fd.max ? `maxlength="${fd.max}"` : ""} aria-describedby="${id}-err"`;
      const ctl = fd.type === "select"
        ? `<select ${attrs}><option value="">Select…</option>${fd.options.map((o) => `<option ${o === val ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`
        : fd.type === "textarea" ? `<textarea rows="3" ${attrs}>${esc(val)}</textarea>`
          : `<input ${attrs} value="${esc(val)}" ${ro ? "readonly" : ""}>`;
      return `<div class="field" data-dsf="${fd.name}"><label for="${id}">${esc(fd.label)}${req ? ' <span class="req" aria-hidden="true">*</span>' : ""}</label>${ctl}
        ${ro ? '<span class="hint">Stored on trades — cannot be changed.</span>' : ""}<span class="err" id="${id}-err"></span></div>`;
    };
    const codeField = isNew && meta.code_input
      ? `<div class="field" data-dsf="code"><label for="ds-code">Code <span class="req" aria-hidden="true">*</span></label>
         <input id="ds-code" name="code" maxlength="30" required aria-required="true" class="mono" aria-describedby="ds-code-hint ds-code-err">
         <span class="hint" id="ds-code-hint">2–30 capital letters, digits, “_” or “-”. Cannot be changed later.</span><span class="err" id="ds-code-err"></span></div>`
      : (!isNew && !meta.name_is_code ? `<div class="field"><span class="label">Code</span><span class="mono">${esc(r.code)}</span></div>` : "");
    const spec = kind === "commodity" && r ? `<div class="alert alert-info">Contract specification (${esc(r.spec.exchange)} ${esc(r.spec.code)}, lot ${num(r.spec.lot_size)} ${esc(r.spec.qty_unit)}, ${esc(r.spec.price_unit)}) is code-managed and not editable here.</div>` : "";
    const statusField = `<div class="field" data-dsf="status"><label for="ds-status">Status <span class="req" aria-hidden="true">*</span></label>
      <select id="ds-status" name="status">${["ACTIVE", "INACTIVE"].map((st) => `<option value="${st}" ${(r ? r.status : "ACTIVE") === st ? "selected" : ""}>${label(st)}</option>`).join("")}</select></div>`;
    const reasonField = `<div class="field" data-dsf="reason"><label for="ds-reason">Reason for change${isNew ? " (optional)" : ' <span class="req" aria-hidden="true">*</span>'}</label>
      <textarea id="ds-reason" name="reason" rows="2" maxlength="500" ${isNew ? "" : 'required aria-required="true"'}></textarea><span class="err" id="ds-reason-err"></span></div>`;
    openDialog(isNew ? `Add ${meta.singular}` : `Edit ${meta.singular} — ${r.name}`,
      `${spec}<form id="ds-form" class="ds-form" novalidate>${codeField}${meta.fields.map(fieldHtml).join("")}${statusField}${reasonField}</form>
       <p class="muted" style="font-size:12px;margin-bottom:0"><span class="req">*</span> Required. ${persistNote()}</p><div id="dlg-error"></div>`,
      [{ text: isNew ? `Create ${meta.singular}` : "Save changes", onClick: async () => {
        const data = {};
        $$("#ds-form [name]").forEach((el) => { if (!el.readOnly) data[el.name] = el.value.trim(); });
        if (data.code) data.code = data.code.toUpperCase();
        const errs = {};
        $$("#ds-form [required]").forEach((el) => { if (!el.readOnly && !el.value.trim()) errs[el.name] = "This field is required."; });
        if (!isNew && (data.reason || "").length < 5) errs.reason = "Enter a reason of at least 5 characters.";
        if (Object.keys(errs).length) { showDsErrors(errs, "Please correct the highlighted fields."); return; }
        try {
          if (isNew) await api(`/refdata/${kind}`, { method: "POST", body: data });
          else await api(`/refdata/${kind}/${encodeURIComponent(r.code)}`, { method: "PUT", body: data });
          dlg.close();
          await reloadSetup(kind, isNew ? `${label(meta.singular)} created` : `${r.name} updated`);
        } catch (e) {
          if (e.data && e.data.errors) showDsErrors(e.data.errors, e.message);
          else $("#dlg-error").innerHTML = `<div class="alert alert-error" role="alert">${esc(e.message)}</div>`;
        }
      } }]);
  }

  function dsToggle(kind, meta, r) {
    const deact = r.status === "ACTIVE";
    const effect = deact
      ? `<p>New trades and amendments can no longer select <strong>${esc(r.name)}</strong>.
         ${r.usage.open_trades ? `${r.usage.open_trades} open trade(s) keep it and will be flagged on the dashboard.` : "No open trades use it."}</p>`
      : `<p><strong>${esc(r.name)}</strong> becomes available for new trades again.</p>`;
    reasonDialog({
      title: `${deact ? "Deactivate" : "Activate"} ${meta.singular}`, confirmText: deact ? "Deactivate" : "Activate",
      confirmCls: deact ? "btn-danger" : "btn-teal", intro: `${effect}<p class="muted">${persistNote()}</p>`,
      onConfirm: async (reason) => {
        await api(`/refdata/${kind}/${encodeURIComponent(r.code)}`, { method: "PUT", body: { ...recordFields(r), status: deact ? "INACTIVE" : "ACTIVE", reason } });
        await reloadSetup(kind, `${r.name} ${deact ? "deactivated" : "activated"}`);
      },
    });
  }

  function dsDelete(kind, meta, r) {
    const u = r.usage;
    if (u.trades || u.limits.length) {
      const deps = [...u.trade_ids.slice(0, 10).map((id) => `<li>Trade ${tradeLink(id)}</li>`),
        ...(u.trades > 10 ? [`<li>… and ${u.trades - 10} more trades</li>`] : []),
        ...u.limits.map((l) => `<li>Limit <a class="mono" href="#/risk">${esc(l)}</a></li>`)];
      openDialog(`${label(meta.singular)} is in use`, `
        <div class="alert alert-warn"><strong>${esc(r.name)}</strong> cannot be deleted: deleting it would break the link from the records below.</div>
        <p style="margin-bottom:4px"><strong>Dependencies</strong></p><ul class="list">${deps.join("")}</ul>
        <p>Safe alternative: <strong>deactivate</strong> it. Existing trades keep the reference; new trades cannot select it.</p>`,
        r.status === "ACTIVE" && isRisk() ? [{ text: "Deactivate instead", onClick: () => dsToggle(kind, meta, r) }] : []);
      return;
    }
    reasonDialog({
      title: `Delete ${meta.singular}?`, confirmText: "Delete", confirmCls: "btn-danger",
      intro: `<div class="alert alert-error">This removes <strong>${esc(r.name)}</strong> from Data Setup. Nothing references it.
        The deletion is recorded in the audit trail${REF.persistence.persistent ? "; earlier versions remain in BigQuery" : ""}.</div>`,
      onConfirm: async (reason) => {
        await api(`/refdata/${kind}/${encodeURIComponent(r.code)}/delete`, { method: "POST", body: { reason } });
        await reloadSetup(kind, `${r.name} deleted`);
      },
    });
  }

  async function viewDataSetup(kind) {
    main.innerHTML = loading();
    const d = await api("/refdata");
    const meta = d.kinds.find((k) => k.kind === kind) || d.kinds[0];
    kind = meta.kind;
    if (dsFilter.kind !== kind) Object.assign(dsFilter, { kind, q: "", status: "" });
    const all = d.records[kind];
    const hasStatus = meta.editable;
    const persist = d.persistence.persistent
      ? `Changes are saved to BigQuery (<span class="mono">${esc(d.persistence.dataset)}</span>, table <span class="mono">agri_reference_data</span>) and recorded in the audit trail.`
      : "Changes are kept <strong>in memory only</strong> and are lost when the service restarts. They are recorded in the audit trail.";
    main.innerHTML = `
      <div class="page-head"><div><h1>Data Setup</h1><p>Reference data used by trade capture, validation and reporting ${DEMO}</p></div></div>
      <div class="alert ${d.persistence.persistent ? "alert-info" : "alert-warn"}">${persist}
        ${isRisk() ? "" : " You are acting as <strong>Demo Trader</strong>: this area is read-only. Switch the demo identity to Demo Risk Reviewer to make changes."}</div>
      <div class="setup">
        <nav class="setup-nav" aria-label="Reference data types"><ul>${d.kinds.map((k) => `<li><a href="#/data-setup/${k.kind}" ${k.kind === kind ? 'aria-current="page"' : ""}>
          <span>${esc(k.label)}</span><span class="n">${d.records[k.kind].length}</span></a></li>`).join("")}</ul></nav>
        <section class="panel setup-main" aria-labelledby="h-ds-kind">
          <div class="panel-head"><div class="ph-text"><h2 id="h-ds-kind">${esc(meta.label)}</h2><p class="panel-about">${esc(meta.about)}</p></div>
            ${meta.creatable ? `<button class="btn btn-primary btn-sm" id="ds-add" ${isRisk() ? "" : 'disabled title="Requires the Risk role — switch the demo identity"'}>Add ${esc(meta.singular)}</button>`
              : `<span class="tag tag-low">${meta.editable ? "Existing records only" : "Read-only"}</span>`}</div>
          <div class="filters">
            <div class="field" style="flex:1 1 220px"><label for="ds-q">Search</label><input id="ds-q" type="search" value="${esc(dsFilter.q)}" placeholder="Code, name, description…"></div>
            ${hasStatus ? `<div class="field"><label for="ds-st">Status</label><select id="ds-st"><option value="">All</option>
              <option value="ACTIVE" ${dsFilter.status === "ACTIVE" ? "selected" : ""}>Active</option><option value="INACTIVE" ${dsFilter.status === "INACTIVE" ? "selected" : ""}>Inactive</option></select></div>` : ""}
            <span class="muted" id="ds-count" style="align-self:center" aria-live="polite"></span></div>
          <div id="ds-grid"></div>
          <div class="panel-note">${esc(DS_NOTES[kind] || "Records in use cannot be deleted — deactivate them instead. Inactive records stay on existing trades but cannot be chosen for new ones.")}</div>
        </section></div>`;
    const render = () => {
      const q = dsFilter.q.toLowerCase();
      const shown = all.filter((r) => (!dsFilter.status || r.status === dsFilter.status)
        && (!q || `${r.code} ${r.name} ${r.description} ${Object.values(r.attrs).join(" ")}`.toLowerCase().includes(q)));
      $("#ds-count").textContent = `${shown.length} of ${all.length}`;
      mountGrid($("#ds-grid"), "ds-" + kind, dsColumns(kind, meta), shown, {
        caption: meta.label,
        emptyHtml: all.length ? empty("No records match these filters.")
          : empty(`No ${meta.label.toLowerCase()} yet.${meta.creatable ? ` Use “Add ${meta.singular}” to create the first one.` : ""}`),
      });
    };
    render();
    $("#ds-q").addEventListener("input", (e) => { dsFilter.q = e.target.value; render(); });
    if ($("#ds-st")) $("#ds-st").addEventListener("change", (e) => { dsFilter.status = e.target.value; render(); });
    if ($("#ds-add")) $("#ds-add").addEventListener("click", () => dsForm(kind, meta, null));
    $("#ds-grid").addEventListener("click", (e) => {
      const b = e.target.closest("[data-ds-edit],[data-ds-toggle],[data-ds-delete]");
      if (!b || b.disabled) return;
      const code = b.dataset.dsEdit ?? b.dataset.dsToggle ?? b.dataset.dsDelete;
      const r = all.find((x) => x.code === code);
      if (b.dataset.dsEdit !== undefined) dsForm(kind, meta, r);
      else if (b.dataset.dsToggle !== undefined) dsToggle(kind, meta, r);
      else dsDelete(kind, meta, r);
    });
  }

  // ── Delegated actions ─────────────────────────────────────────────────────
  document.addEventListener("click", (e) => {
    const sort = e.target.closest("[data-tsort]");
    if (sort) {
      const [id, key] = sort.dataset.tsort.split("|");
      const st = SORT[id] || (SORT[id] = { key: null, dir: 1 });
      st.dir = st.key === key ? -st.dir : 1; st.key = key;
      if (RERENDER[id]) RERENDER[id]();
      const again = document.querySelector(`[data-tsort="${CSS.escape(sort.dataset.tsort)}"]`);
      if (again) again.focus();
      return;
    }
    const der = e.target.closest("[data-derive]");
    if (der) { e.preventDefault(); const [dim, ...rest] = der.dataset.derive.split("|"); showDerivation(dim, rest.join("|")); return; }
    const ex = e.target.closest("[data-explain]");
    if (ex) { e.preventDefault(); const [topic, ...rest] = ex.dataset.explain.split("|"); explainWithAI(topic, rest.join("|")); return; }
    if (e.target.closest("[data-retry]")) { route(); return; }
    if (e.target.closest("#drawer a[href^='#/']")) { drawer.close(); return; }
    const row = e.target.closest("tr[data-href]");
    if (row && !e.target.closest("a,button,summary,details")) location.hash = row.dataset.href;
  });

  // ── Router ────────────────────────────────────────────────────────────────
  const ROUTES = {
    dashboard: viewDashboard, blotter: viewBlotter, trade: viewTrade, capture: viewCapture, monitoring: viewMonitoring,
    risk: viewRisk, exceptions: viewExceptions, audit: (arg, params) => viewAudit(params), "market-data": viewMarketData, system: viewSystem,
    "data-setup": viewDataSetup,
  };
  async function route() {
    const hash = location.hash.replace(/^#\/?/, "") || "dashboard";
    const [path, query] = hash.split("?");
    const [rawName, arg] = path.split("/");
    const name = ROUTES[rawName] ? rawName : rawName === "about" ? "system" : "dashboard";
    const navName = name === "trade" ? "blotter" : name;
    $$(".sidenav a").forEach((a) => {
      if (a.dataset.route === navName) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = `${SECTIONS[name]} · AgriRisk CTRM — Prototype`;
    $("#sidenav").classList.remove("open");
    $("#nav-toggle").setAttribute("aria-expanded", "false");
    try { await ROUTES[name](arg ? decodeURIComponent(arg) : undefined, new URLSearchParams(query || "")); }
    catch (e) { main.innerHTML = errorState(e); }
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  window.addEventListener("hashchange", route);
  (async function init() {
    try {
      REF = await api("/reference");
      $("#nav-store").textContent = REF.persistence.persistent ? "Stored in BigQuery." : "In-memory store — not saved.";
      initScope();
      await loadSession();
      refreshChrome();
      loadAiStatus();
      await route();
    } catch (e) { main.innerHTML = errorState(e); }
  })();
})();
