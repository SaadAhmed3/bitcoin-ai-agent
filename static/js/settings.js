/* BTC Signal Lab settings page (Phase 8). Vanilla JS. */
(() => {
  window.APP = JSON.parse(document.getElementById("appConfig").textContent);
  const fetch = (url, opts = {}) => window.fetch(url, { ...opts, headers: { "X-Requested-With": "BTCSignalLab", ...(opts.headers || {}) } });
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SOURCE = { env: "env override (read-only)", settings: "settings DB", default: "default" };
  const SIGNAL_FIELDS = ["entry_mode", "strong_threshold", "buy_threshold", "sell_threshold", "min_agreement", "strong_min_agreement",
    "counter_trend_limit", "min_rr", "fee_pct", "slippage_pct", "max_source_divergence_pct", "require_validated_edge"];
  const RISK_FIELDS = ["risk_pct", "max_exposure_pct", "daily_loss_limit_pct", "fee_pct", "slippage_pct", "loss_streak_reduce", "max_risk_pct"];
  let data = null;

  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  function theme() {
    let saved = null;
    try { saved = localStorage.getItem("btc-theme"); } catch (_) {}
    if (window.APP.theme === "dark" || window.APP.theme === "light") saved = window.APP.theme;
    else if (!saved && window.matchMedia) saved = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    if (saved) document.documentElement.dataset.theme = saved;
    $("themeBtn").onclick = () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("btc-theme", next); } catch (_) {}
    };
    $("menuBtn").onclick = () => $("sidebar").classList.toggle("open");
  }

  function input(key, f) {
    const ro = (f.read_only ? " disabled" : "") + ` aria-label="${esc(key.replace(/_/g, " "))}"`;
    const v = f.value;
    if (f.kind === "secret") return `<span class="mono">${esc(v)}</span>`;
    if (f.kind === "bool") return `<input type="checkbox" name="${esc(key)}"${v ? " checked" : ""}${ro}>`;
    if (f.kind === "choice") return `<select name="${esc(key)}"${ro}>${f.choices.map((c) => `<option${c === v ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
    if (f.kind === "int" || f.kind === "float") {
      return `<input type="number" name="${esc(key)}" value="${esc(v)}" step="${f.kind === "int" ? 1 : "any"}"${f.min != null ? ` min="${f.min}"` : ""}${f.max != null ? ` max="${f.max}"` : ""}${ro}>`;
    }
    return `<input type="text" name="${esc(key)}" value="${esc(Array.isArray(v) ? v.join(", ") : v)}"${ro}>`;
  }

  function row(key, f) {
    const range = f.min != null ? ` (${f.min}-${f.max})` : "";
    const restart = f.restart_required ? ' <span class="src-badge">restart required</span>' : "";
    return `<div class="set-row"><label>${esc(key.replace(/_/g, " "))}<span class="help">${esc(f.help || "")}${esc(range)}</span></label>
      <div>${input(key, f)}<div class="muted small">Source: <span class="src-badge ${esc(f.source)}">${esc(SOURCE[f.source] || f.source)}</span>${f.env ? (f.source === "env" ? ` from env ${esc(f.env)}` : ` · env ${esc(f.env)} would override`) : ""}${restart}</div></div></div>`;
  }

  // Allowed range from the server (the server enforces it; this is only the hint next to the label).
  function rangeHelp(r) {
    if (!r) return "";
    return `${r.min_exclusive ? ">" : ""}${r.min} to ${r.max}: ${r.help}`;
  }

  function plainRow(key, value, help) {
    const isBool = typeof value === "boolean";
    const name = `name="${esc(key)}" aria-label="${esc(key.replace(/_/g, " "))}"`;
    const field = isBool ? `<input type="checkbox" ${name}${value ? " checked" : ""}>`
      : key === "entry_mode" ? `<select ${name}>${["standard", "breakout", "pullback"].map((m) => `<option${m === value ? " selected" : ""}>${m}</option>`).join("")}</select>`
      : `<input type="number" step="any" ${name} value="${esc(value)}">`;
    return `<div class="set-row"><label>${esc(key.replace(/_/g, " "))}<span class="help">${esc(help || "")}</span></label><div>${field}</div></div>`;
  }

  function render() {
    document.querySelectorAll("form.set-form").forEach((form) => {
      const sec = form.dataset.section;
      const box = form.querySelector(".set-fields");
      if (sec === "signal") {
        const c = data.signal.config;
        const v = data.signal.validation || {};
        box.innerHTML = (data.signal.notice ? `<p class="set-warn" id="signalNotice">${esc(data.signal.notice)}</p>` : "")
          + `<p class="muted small">Validation now: <b>${v.validated ? "validated" : v.stale ? "STALE (settings changed since the last backtest)" : "not validated"}</b>${v.note ? " - " + esc(v.note) : ""}</p>`
          + SIGNAL_FIELDS.map((k) => plainRow(k, c[k], rangeHelp((data.signal.ranges || {})[k]))).join("")
          + `<div class="set-row"><label>weights<span class="help">JSON object; normalised to sum 1</span></label><div><input type="text" name="weights" aria-label="weights" value="${esc(JSON.stringify(c.weights))}"></div></div>`;
      } else if (sec === "risk") {
        const s = data.risk.settings;
        box.innerHTML = `<p class="sim-banner">SIMULATED ACCOUNT · no real money · no real orders</p>`
          + RISK_FIELDS.map((k) => plainRow(k, s[k], (data.risk.ranges || {})[k])).join("");
      } else {
        box.innerHTML = Object.entries(data[sec]).map(([k, f]) => row(k, f)).join("");
      }
      form.closest("section").dataset.state = "ready";
    });
  }

  function collect(form) {
    const sec = form.dataset.section;
    const out = {};
    form.querySelectorAll("input[name], select[name]").forEach((el) => {
      if (el.disabled) return;
      const k = el.name;
      if (el.type === "checkbox") out[k] = el.checked;
      else if (k === "weights") out[k] = JSON.parse(el.value);
      else if (el.type === "number") out[k] = el.value === "" ? null : Number(el.value);
      else if (sec !== "signal" && Array.isArray((data[sec][k] || {}).value)) out[k] = el.value.split(",").map((x) => x.trim()).filter(Boolean);
      else out[k] = el.value;
    });
    return out;
  }

  async function save(form) {
    const sec = form.dataset.section;
    const msg = form.querySelector(".set-msg");
    msg.textContent = "Saving…";
    try {
      const body = collect(form);
      if (sec === "signal") await call("PUT", "/api/signal/config", body);
      else if (sec === "risk") await call("POST", "/api/paper/settings", body);
      else await call("PUT", `/api/settings/${sec}`, body);
      await load();
      const restart = sec !== "signal" && sec !== "risk" && Object.values(data[sec]).some((f) => f.restart_required && f.source === "settings");
      msg.textContent = "Saved." + (restart ? " Restart the app for 'restart required' values." : sec === "signal" ? " " + data.signal.warning : "");
    } catch (err) {
      msg.textContent = "Not saved: " + err.message;
    }
  }

  async function reset(sec, form) {
    const msg = form.querySelector(".set-msg");
    try {
      if (sec === "signal") await call("PUT", "/api/signal/config", { reset: true });
      else if (sec === "risk") await call("POST", "/api/paper/settings", data.risk.defaults);
      else await call("POST", `/api/settings/${sec}/reset`);
      await load();
      msg.textContent = "Reset to defaults.";
    } catch (err) { msg.textContent = "Reset failed: " + err.message; }
  }

  async function load() {
    try {
      data = await call("GET", "/api/settings?ack_notice=1");
      render();
      $("loadState").textContent = "";
    } catch (err) {
      $("loadState").textContent = "Could not load settings: " + err.message;
      document.querySelectorAll("section.set-section").forEach((s) => { s.dataset.state = "error"; });
    }
  }

  theme();
  document.querySelectorAll("form.set-form").forEach((form) => {
    form.addEventListener("submit", (e) => { e.preventDefault(); save(form); });
    form.querySelector("[data-reset]").onclick = () => reset(form.dataset.section, form);
  });
  load();
})();
