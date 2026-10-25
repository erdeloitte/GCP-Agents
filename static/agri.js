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
  const hhmm = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(11, 16) + " UTC"; };
  const commodityName = (k) => (REF && REF.commodities[k] ? REF.commodities[k].name : k);
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
    const th = (c) => {
      const cls = c.num ? "num" : "";
      if (c.nosort) return `<th scope="col" class="${cls}">${c.label}</th>`;
      const on = st.key === c.key;
      return `<th scope="col" class="${cls}" aria-sort="${on ? (st.dir > 0 ? "ascending" : "descending") : "none"}"><button class="sort" data-tsort="${id}|${c.key}">${c.label}<span class="ind" aria-hidden="true">${on ? (st.dir > 0 ? "▲" : "▼") : "↕"}</span></button></th>`;
    };
    const td = (c, r) => `<td class="${c.num ? "num" : ""} ${c.cls ? c.cls(r) : ""}">${c.render ? c.render(r) : esc(r[c.key])}</td>`;
    return `<div class="table-wrap"><table><caption class="sr-only">${esc(o.caption || "")}</caption>
      <thead><tr>${cols.map(th).join("")}</tr></thead>
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
    risk: "Exposure & risk", exceptions: "Exceptions", audit: "Audit trail", "market-data": "Market data", system: "System & AI" };

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
    sel.innerHTML = `<option value="">All portfolios</option>` + REF.portfolios.map((p) => `<option ${p === SCOPE ? "selected" : ""}>${esc(p)}</option>`).join("");
    if (SCOPE && !REF.portfolios.includes(SCOPE)) SCOPE = "";
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
  const limitName = (l) => `${label(l.dimension)}: ${l.dimension === "commodity" ? commodityName(l.key) : l.key}`;
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
  async function viewDashboard() {
    main.innerHTML = loading();
    const d = await api("/dashboard" + (SCOPE ? `?portfolio=${encodeURIComponent(SCOPE)}` : ""));
    const k = d.kpis, f = d.freshness;
    const commodityLimit = (c) => d.limits.filter((l) => l.dimension === "commodity" && l.key === c).sort((a, b) => b.utilisation - a.utilisation)[0];
    const item = (dt_, dd) => `<div><dt>${dt_}</dt><dd>${dd}</dd></div>`;
    main.innerHTML = `
      <div class="page-head"><div><h1>Portfolio overview</h1>
        <p>${esc(SCOPE || "All portfolios")} · valued ${ts(d.valued_at)} · USD ${DET} ${DEMO}</p></div>
        <div class="page-actions"><a class="btn" href="#/exceptions">Exceptions queue</a><a class="btn" href="#/blotter">Trade blotter</a></div></div>
      <dl class="summary" aria-label="Portfolio summary">
        ${item("Open trades", `${num(k.open_trades)}${k.pending_review ? ` <span class="muted">· ${k.pending_review} pending review</span>` : ""}`)}
        ${item("Unrealised MtM, USD", `<span class="${signCls(k.mtm_usd)}">${usd(k.mtm_usd)}</span>`)}
        ${item("Valuation coverage", `${k.mtm_included} / ${k.open_trades} trades${k.mtm_excluded ? ` <span class="neg">· ${k.mtm_excluded} excluded</span>` : ""}`)}
        ${item("Limits (firm-wide)", k.limit_breaches ? `<span class="neg">${k.limit_breaches} breach</span> · ${k.limit_warnings} warning` : `${k.limit_warnings} warning`)}
        ${item("Open exceptions", `${k.open_exceptions}${k.critical_exceptions ? ` <span class="neg">· ${k.critical_exceptions} critical</span>` : ""}`)}
        ${item("Market inputs", `${f.marks_in_use} marks${f.stale_marks || f.missing_marks ? ` <span class="neg">· ${f.stale_marks} stale · ${f.missing_marks} missing</span>` : " · all current"}`)}
      </dl>
      <div class="cols cols-main">
        <div class="stack">
          <section class="panel core" aria-labelledby="h-pos"><div class="panel-head"><h2 id="h-pos">Positions by commodity<span class="sub">delta-equivalent tonnes · select a row for derivation</span></h2>${DET}</div>
            <div id="pos-grid"></div>
            <div class="panel-note"><p>Notional and MtM include only trades with a current or explicitly accepted mark. Options: delta 1 if in-the-money, else 0.</p>${basis()}</div></section>
          <section class="panel" aria-labelledby="h-mat"><div class="panel-head"><h2 id="h-mat">Exposure by maturity bucket</h2>${DET}</div><div id="bucket-grid"></div></section>
        </div>
        <div class="stack">
          <section class="panel" aria-labelledby="h-attn"><div class="panel-head"><h2 id="h-attn">Requires attention</h2><a class="btn btn-sm" href="#/exceptions">Queue (${k.open_exceptions})</a></div>
            ${d.exceptions.length ? `<ul class="attn">${d.exceptions.map((e) => `<li><span>${sevTag(e.severity)}</span><span class="what">${esc(e.title)} · ${entityLink(e)}</span><span class="detail" title="${esc(e.detail)}">${esc(e.detail)}</span></li>`).join("")}</ul>` : empty("No open exceptions — all monitoring rules pass.")}
          </section>
          <section class="panel" aria-labelledby="h-fresh"><div class="panel-head"><h2 id="h-fresh">Market inputs</h2>${DEMO}</div>
            <div class="panel-body"><dl class="kv">
              <div><dt>Marks in use</dt><dd>${f.marks_in_use}</dd></div>
              <div><dt>Stale (&gt; ${f.stale_threshold_hours}h)</dt><dd class="${f.stale_marks ? "neg" : ""}">${f.stale_marks}</dd></div>
              <div><dt>Missing</dt><dd class="${f.missing_marks ? "neg" : ""}">${f.missing_marks}</dd></div>
              <div><dt>Oldest mark</dt><dd>${ts(f.oldest_mark)}</dd></div></dl></div>
            <div class="panel-note">Trades on stale or missing marks are flagged and excluded from MtM totals. <a href="#/market-data">View marks</a></div></section>
        </div>
      </div>
      <div class="cols cols-2">
        <section class="panel core" aria-labelledby="h-lim"><div class="panel-head"><h2 id="h-lim">Limit utilisation<span class="sub">firm-wide</span></h2><a class="btn btn-sm" href="#/risk">All limits</a></div><div id="lim-grid"></div></section>
        <section class="panel" aria-labelledby="h-due"><div class="panel-head"><h2 id="h-due">Maturing within ${d.maturity_window_days} days</h2></div><div id="due-grid"></div></section>
      </div>`;

    mountGrid($("#pos-grid"), "dash-pos", [
      { key: "commodity", label: "Commodity", sort: (r) => commodityName(r.commodity), render: (r) => `<button class="btn-link" data-derive="commodity|${esc(r.commodity)}">${esc(commodityName(r.commodity))}</button><span class="sub">${esc(REF.commodities[r.commodity].exchange)} ${esc(REF.commodities[r.commodity].code)}</span>` },
      { key: "contract_months", label: "Months", nosort: true, render: (r) => `${r.contract_months.length} <span class="sub">${mon(r.contract_months[0])}${r.contract_months.length > 1 ? `–${mon(r.contract_months[r.contract_months.length - 1])}` : ""}</span>` },
      { key: "long_t", label: "Long t", num: true, render: (r) => tonnes(r.long_t) },
      { key: "short_t", label: "Short t", num: true, render: (r) => tonnes(r.short_t) },
      { key: "net_t", label: "Net t", num: true, render: (r) => `<strong>${tonnes(r.net_t)}</strong>` },
      { key: "gross_notional_usd", label: "Notional USD", num: true, render: (r) => usd(r.gross_notional_usd) },
      { key: "mtm_usd", label: "MtM USD", num: true, cls: (r) => signCls(r.mtm_usd), render: (r) => usd(r.mtm_usd) },
      { key: "valued", label: "Valued", num: true, sort: (r) => r.valued / r.trades, render: (r) => `<span class="${r.data_complete ? "" : "neg"}">${r.valued} / ${r.trades}</span>` },
      { key: "limit", label: "Limit", sort: (r) => (commodityLimit(r.commodity) || {}).utilisation ?? null, render: (r) => { const l = commodityLimit(r.commodity); return l ? `${limTag(l.status)} <span class="muted">${pct(l.utilisation)}</span>` : '<span class="muted">—</span>'; } },
    ], d.positions, { caption: "Positions by commodity", empty: "No open positions in this scope.",
      foot: d.positions.length ? `<tr><td colspan="5">Total · ${k.open_trades} open trades</td><td class="num">${usd(k.gross_notional_usd)}</td><td class="num ${signCls(k.mtm_usd)}">${usd(k.mtm_usd)}</td><td class="num">${k.mtm_included} / ${k.open_trades}</td><td></td></tr>` : "" });

    mountGrid($("#bucket-grid"), "dash-bucket", [
      { key: "key", label: "Bucket", nosort: true, render: (a) => `<button class="btn-link" data-derive="maturity_bucket|${esc(a.key)}">${esc(a.key)}</button>` },
      { key: "net_tonnes", label: "Net t", num: true, nosort: true, render: (a) => tonnes(a.net_tonnes) },
      { key: "gross_tonnes", label: "Gross t", num: true, nosort: true, render: (a) => tonnes(a.gross_tonnes) },
      { key: "gross_notional_usd", label: "Notional USD", num: true, nosort: true, render: (a) => usd(a.gross_notional_usd) },
      { key: "mtm_usd", label: "MtM USD", num: true, nosort: true, cls: (a) => signCls(a.mtm_usd), render: (a) => usd(a.mtm_usd) },
      { key: "trades", label: "Trades", num: true, nosort: true, render: (a) => `${a.trade_ids.length}${a.data_complete ? "" : ` <span class="tag tag-warn" title="${a.incomplete.length} trade(s) without a usable mark">${a.incomplete.length} excl.</span>`}` },
    ], d.by_maturity, { caption: "Exposure by maturity bucket", empty: "No open trades." });

    mountGrid($("#lim-grid"), "dash-lim", [
      { key: "name", label: "Limit", sort: limitName, render: (l) => `<button class="btn-link" data-derive="${esc(l.dimension)}|${esc(l.key)}">${esc(limitName(l))}</button><span class="sub">${esc(l.metric_label)}</span>` },
      { key: "utilisation", label: "Utilisation", render: meter },
      { key: "status", label: "Status", sort: (l) => ({ BREACH: 0, WARN: 1, OK: 2 }[l.status]), render: (l) => limTag(l.status) + (l.data_complete ? "" : ' <span class="tag tag-warn" title="Some contributing trades lack valid inputs">Incomplete</span>') },
    ], d.limits, { caption: "Limit utilisation", sortKey: "utilisation", sortDir: -1 });

    mountGrid($("#due-grid"), "dash-due", [
      { key: "id", label: "Trade", render: (t) => tradeLink(t.id) },
      { key: "commodity", label: "Commodity", sort: (t) => commodityName(t.commodity), render: (t) => `${esc(commodityName(t.commodity))}<span class="sub">${esc(label(t.instrument_type))} · ${esc(t.direction)}</span>` },
      { key: "quantity", label: "Quantity", num: true, render: (t) => `${num(t.quantity)} ${esc(t.quantity_unit)}` },
      { key: "maturity_date", label: "Maturity", render: (t) => dt(t.maturity_date) },
      { key: "days", label: "Days", num: true, cls: (t) => (t.days < 0 ? "neg" : ""), render: (t) => t.days },
      { key: "status", label: "Status", render: (t) => tradeTag(t.status) },
    ], d.maturities, { caption: "Upcoming maturities", sortKey: "days", empty: `No open trades mature within ${d.maturity_window_days} days.` });
  }

  // ── Blotter ───────────────────────────────────────────────────────────────
  const blotterState = { filters: {} };
  async function viewBlotter() {
    if (blotterState.filters.portfolio === undefined) blotterState.filters.portfolio = SCOPE;
    const opt = (name, values, lbl, fmt = (v) => v) => `<div class="field"><label for="f-${name}">${lbl}</label><select id="f-${name}" data-filter="${name}"><option value="">All</option>
      ${values.map((v) => `<option value="${esc(v)}" ${blotterState.filters[name] === v ? "selected" : ""}>${esc(fmt(v))}</option>`).join("")}</select></div>`;
    main.innerHTML = `<div class="page-head"><div><h1>Trade blotter</h1><p>All captured trades with current valuation status ${DET} ${DEMO}</p></div>
      <div class="page-actions"><a class="btn btn-primary" href="#/capture">Capture trade</a></div></div>
      <section class="panel"><div class="filters" role="search">
        <div class="field" style="flex:1 1 220px"><label for="f-q">Search</label><input id="f-q" type="search" placeholder="Trade ID, counterparty, execution ref…" value="${esc(blotterState.filters.q || "")}" data-filter="q"></div>
        ${opt("commodity", Object.keys(REF.commodities), "Commodity", commodityName)}
        ${opt("instrument_type", REF.instrument_types, "Instrument", label)}
        ${opt("direction", REF.directions, "Direction", label)}
        ${opt("status", REF.statuses, "Status", label)}
        ${opt("portfolio", REF.portfolios, "Portfolio")}
        <button class="btn" id="f-clear" type="button">Clear</button></div>
        <div id="blotter-out">${loading()}</div>
        <div class="panel-note"><span id="blotter-count" aria-live="polite"></span>${basis()}</div></section>`;
    let timer;
    $$("[data-filter]").forEach((el) => el.addEventListener(el.tagName === "INPUT" ? "input" : "change", () => {
      blotterState.filters[el.dataset.filter] = el.value;
      clearTimeout(timer); timer = setTimeout(loadBlotter, el.tagName === "INPUT" ? 250 : 0);
    }));
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
        { key: "portfolio", label: "Portfolio" },
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
    $("#crumb-item").textContent = t.id;
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

  async function viewCapture(id) {
    main.innerHTML = loading();
    const editing = Boolean(id);
    let t = null;
    if (editing) {
      t = (await api("/trades/" + encodeURIComponent(id))).trade;
      $("#crumb-item").textContent = t.id;
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
          ${sel("counterparty", REF.counterparties, "Counterparty")}
          ${sel("portfolio", REF.portfolios, "Portfolio / business unit")}</div></fieldset>
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
          ${inp("maturity_date", "Maturity / last trade / delivery", "date", true, "", "Suggested from instrument and month")}</div></fieldset>
        <fieldset><legend>Execution &amp; source</legend><div class="form-grid">
          ${sel("broker", REF.brokers, "Broker", false)}
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
        ${row("Counterparty", esc(f.counterparty.value))}${row("Portfolio", esc(f.portfolio.value))}</dl>`;
    };
    const sync = (changed) => {
      const c = REF.commodities[f.commodity.value];
      const inst = f.instrument_type.value;
      const isOpt = inst === "OPTION";
      $("[data-field=option_type]").hidden = !isOpt;
      $("[data-field=strike]").hidden = !isOpt;
      if (!isOpt) { f.option_type.value = ""; f.strike.value = ""; }
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
        "quantity", "price", "contract_month", "trade_date", "effective_date", "maturity_date", "broker", "execution_ref", "source", "notes"]
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
    const keyLabel = (k) => riskDim === "commodity" ? commodityName(k) : k;
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
    openDrawer(`Derivation — ${label(dim)}: ${dim === "commodity" ? commodityName(key) : key}`, loading());
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
        { key: "action", label: "Action", render: (e) => esc(label(e.action)) },
        { key: "entity_id", label: "Entity", render: (e) => (e.entity_type === "trade" ? tradeLink(e.entity_id) : `<span class="mono">${esc(e.entity_id)}</span>`) },
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
    $("#crumb-section").textContent = SECTIONS[name];
    $("#crumb-item").textContent = "";
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
