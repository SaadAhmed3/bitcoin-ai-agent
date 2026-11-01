/* Strategy Lab view: five research strategy cards, the comparison table and the DCA details (round 1).
 * Read-only except the strategy settings form, which saves a NEW configuration version. Loaded by app.js's view
 * loader (window.BTCStrategyLab.load). Every value comes from /api/strategies*: nothing is calculated here. */
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const call = async (method, url, body) => {
    const r = await fetch(url, { method, headers: { "X-Requested-With": "BTCSignalLab", Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }) }, body: body === undefined ? undefined : JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  };
  const tz = () => (window.APP && window.APP.displayTz) || undefined;
  const when = (ms) => (ms ? new Date(ms).toLocaleString(undefined, { timeZone: tz(), dateStyle: "medium", timeStyle: "short" }) : "Unknown time");
  const day = (ms) => new Date(ms).toISOString().slice(0, 10);
  const pct = (v) => (v === null || v === undefined ? "N/A" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
  const TAG = { "LIVE DATA": "live", CALCULATED: "calc", "MODEL ESTIMATE": "model", HYPOTHETICAL: "hyp", SIMULATED: "sim", "NOT VALIDATED": "nv" };
  const tags = (labels) => labels.map((l) => `<span class="tag ${TAG[l] || "calc"}">${esc(l)}</span>`).join(" ");
  const list = (items, cls = "") => (items && items.length ? `<ul class="ev ${cls}">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");

  function card(s) {
    const head = `<div class="strat-head"><h3>${esc(s.name)}</h3></div>`;
    const status = `<p class="strat-status"><b>${esc(s.validation.status)}</b><br><span class="muted small">${esc(s.validation.detail)}</span></p>`;
    if (!s.implemented) {
      return `<article class="strat-card planned" id="strat-${esc(s.id)}">${head}
        <div class="strat-tags">${tags(["NOT VALIDATED"])}</div>
        <p class="muted small">${esc(s.purpose)}</p><p class="strat-planned">Planned for round ${s.planned_round}: not implemented yet, so no signal.</p>${status}</article>`;
    }
    if (!s.result) {
      return `<article class="strat-card" id="strat-${esc(s.id)}">${head}<p class="muted">Could not be evaluated: ${esc(s.error || "no result")}</p>${status}</article>`;
    }
    const r = s.result;
    const levels = r.entry_zone || r.stop !== null || (r.targets || []).length
      ? `<dl class="adv-dl"><div><dt>Entry zone</dt><dd>${r.entry_zone ? `$${esc(r.entry_zone.low)} to $${esc(r.entry_zone.high)}` : "Not applicable"}</dd></div>
          <div><dt>Invalidation</dt><dd>${r.stop !== null ? "$" + esc(r.stop) : "Not applicable"}</dd></div>
          <div><dt>Reward/risk</dt><dd>${r.reward_risk !== null ? esc(r.reward_risk) : "Not applicable"}</dd></div></dl>`
      : `<p class="muted small">Entry, invalidation and reward/risk do not apply: ${esc(s.id === "dca" ? "DCA buys on a schedule and never sells." : "no setup.")}</p>`;
    const warn = [...(r.contradictory || []), ...((r.data || {}).warnings || [])];
    const bt = s.backtest ? `Design backtest: ${esc(s.backtest.verdict)} on its pre-registered criteria` : "Design backtest: not run yet";
    return `<article class="strat-card" id="strat-${esc(s.id)}">${head}
      <div class="strat-tags">${tags(r.labels)}</div>
      <div class="strat-signal sig-${esc(r.signal.toLowerCase().replace(/[^a-z]+/g, "-"))}">${esc(r.signal)}</div>
      <p class="small muted">${esc(r.asset)} · ${esc(r.timeframe)} · evaluated ${esc(when(r.evaluated_at))} · regime (context) ${esc(r.regime || "not available")}</p>
      <p class="small">${esc(r.explanation)}</p>
      ${r.rejection ? `<p class="small muted">Why no buy: ${esc(r.rejection)}</p>` : ""}
      <p class="small muted">Strength: ${r.strength === null ? "not applicable" : esc(r.strength) + " / 100"} · ${esc(r.strength_note)}</p>
      ${levels}
      <p class="small muted">Costs: fee ${esc(r.fees_pct)}% + slippage ${esc(r.slippage_pct)}% per buy · data ${esc(r.data.source || "unknown")}, last close ${esc(when(r.data.last_candle_close))}${r.data.is_stale ? " (stale)" : ""}</p>
      ${list(r.supporting)}${warn.length ? `<h4>Warnings</h4>${list(warn)}` : ""}
      <p class="small">${bt}. Config version ${esc(s.config.version)}${s.config.settings_changed ? " (changed from the pre-registered settings)" : ""}.</p>
      ${status}</article>`;
  }

  function renderCompare(cmp) {
    const ids = ["dca", "trend", "range", "breakout", "rotation"];
    const heads = ["DCA", "Trend", "Range", "Breakout", "Rotation"];
    $("stratCompare").querySelector("tbody").innerHTML = cmp.rows.map((row) => `<tr><td><b>${esc(row.metric)}</b></td>${ids.map((id, k) =>
      `<td data-label="${heads[k]}">${esc(row.values[id])}</td>`).join("")}</tr>`).join("");
    $("stratCompare").querySelectorAll("tbody td:first-child").forEach((td) => td.setAttribute("data-label", "Metric"));
    $("stratCompareNote").textContent = cmp.note;
  }

  function renderBacktest(bt) {
    if (!bt) {
      $("stratBacktest").innerHTML = `<p class="muted small">No design backtest yet. It runs once, after the pre-registration is committed:
        <code>python scripts\\research_strategy.py dca</code></p>`;
      return;
    }
    const m = bt.metrics, rol = bt.rolling || {};
    $("stratBacktest").innerHTML = `<p><b>${esc(bt.verdict)}</b> on the pre-registered criteria (design data ${esc(day(bt.period.first_day))} to ${esc(day(bt.period.last_day))}: evidence, not validation).</p>
      <dl class="adv-dl"><div><dt>Return on the budget</dt><dd>${pct(m.net_return_pct)}</dd></div>
        <div><dt>Lump sum, same budget</dt><dd>${pct(m.buy_hold_return_pct)}</dd></div>
        <div><dt>Max drawdown</dt><dd>${m.max_drawdown_pct.toFixed(2)}%</dd></div><div><dt>Buys</dt><dd>${esc(m.trades)}</dd></div>
        <div><dt>Fees and slippage</dt><dd>$${Number(m.costs_usd).toFixed(2)}</dd></div>
        <div><dt>Rolling ${esc(rol.horizon_days)}-day windows</dt><dd>${esc(rol.windows)}</dd></div></dl>
      ${list((bt.criteria || []).map((c) => `${c.id} ${c.passed ? "passed" : "failed"}: ${c.rule} (DCA ${Number(c.dca).toFixed(2)}%, lump sum ${Number(c.lump_sum).toFixed(2)}%)`))}`;
  }

  function renderForward(f) {
    const state = { not_started: "Not started yet: the background monitor registers it once the app runs.",
      waiting: `Starts ${f.start_day ? day(f.start_day) : "soon"} (00:00 UTC); no completed day yet.`,
      running: `Running: day ${f.days_elapsed} of ${f.target_days}.`, done: `Done: ${f.verdict} on the pre-registered forward criterion.` }[f.state];
    const nums = f.days_elapsed ? `<p class="small">DCA ${pct(f.dca_return_pct)} (max drawdown ${Number(f.dca_max_dd_pct).toFixed(2)}%) vs lump sum ${pct(f.lump_return_pct)} (max drawdown ${Number(f.lump_max_dd_pct).toFixed(2)}%).</p>` : "";
    $("stratForward").innerHTML = `<p class="small">${esc(state)}</p>${nums}
      <p class="small muted">Own tracker, own fingerprint <code>${esc(String(f.fingerprint_now).slice(0, 12))}</code>: <span class="fp-badge ${f.code_changed ? "bad" : "ok"}">${f.code_changed ? "code changed since the start" : "unchanged since the start"}</span></p>`;
  }

  function field(key, f, value) {
    const label = `<label>${esc(key.replace(/_/g, " "))}<span class="help">${esc(f.help)}</span></label>`;
    const input = f.kind === "choice"
      ? `<select name="${esc(key)}" aria-label="${esc(key)}">${f.choices.map((c) => `<option${c === value ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`
      : `<input type="number" name="${esc(key)}" aria-label="${esc(key)}" value="${esc(value)}" min="${f.lo}" max="${f.hi}" step="${f.kind === "int" ? 1 : "any"}">`;
    return `<div class="set-row">${label}<div>${input}</div></div>`;
  }

  function renderConfig(cfg) {
    const box = $("stratCfg-dca");
    // in the documented order (JSON objects arrive with sorted keys)
    box.querySelector(".strat-cfg-fields").innerHTML = cfg.order.map((k) => field(k, cfg.fields[k], cfg.config[k])).join("");
    box.querySelector(".strat-cfg-version").textContent = `Configuration version ${cfg.version} (${cfg.settings_changed
      ? "differs from the pre-registered settings: NOT VALIDATED (settings changed)" : "the pre-registered settings"}). Pre-registration: ${cfg.preregistration}.`;
    box.dataset.version = cfg.version;
  }

  function renderHistory(rows) {
    $("stratHistory").querySelector("tbody").innerHTML = rows.length ? rows.map((h) => `<tr><td data-label="Day (UTC)">${esc(day(h.day))}</td>
      <td data-label="Signal"><b>${esc(h.signal)}</b></td><td data-label="Config">v${esc(h.config_version)}</td><td data-label="Why">${esc(h.explanation)}</td></tr>`).join("")
      : '<tr><td colspan="4" class="empty">No stored records yet: the background monitor stores one per UTC day.</td></tr>';
  }

  async function load() {
    try {
      const [all, dca, cfg] = await Promise.all([call("GET", "/api/strategies"), call("GET", "/api/strategies/dca"),
        call("GET", "/api/strategies/dca/config")]);
      $("stratCards").innerHTML = all.strategies.map(card).join("");
      renderCompare(all.comparison);
      renderBacktest(dca.backtest);
      renderForward(dca.forward);
      renderConfig(cfg);
      renderHistory(dca.history || []);
      $("stratLab").dataset.state = "ready";
    } catch (err) {
      $("stratCards").innerHTML = `<p class="empty">Strategy Lab could not load: ${esc(err.message)}</p>`;
      $("stratLab").dataset.state = "error";
    }
  }

  function initForm() {
    const box = $("stratCfg-dca");
    if (!box) return;
    const form = box.querySelector("form"), msg = box.querySelector(".set-msg");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = {};
      form.querySelectorAll("[name]").forEach((el) => { body[el.name] = el.tagName === "SELECT" ? el.value : Number(el.value); });
      const next = Number(box.dataset.version || 0) + 1;
      if (!window.confirm(`Saving creates configuration version ${next}. Settings that differ from the pre-registered ones are `
        + "NOT VALIDATED (settings changed) and inherit no backtest or forward result. Continue?")) return;
      try { await call("PUT", "/api/strategies/dca/config", body); msg.textContent = "Saved."; await load(); }
      catch (err) { msg.textContent = "Not saved: " + err.message; }
    });
    box.querySelector("[data-reset]").addEventListener("click", async () => {
      try { await call("POST", "/api/strategies/dca/config/reset"); msg.textContent = "Reset to the pre-registered defaults."; await load(); }
      catch (err) { msg.textContent = "Reset failed: " + err.message; }
    });
  }

  document.addEventListener("DOMContentLoaded", initForm);
  if (document.readyState !== "loading") initForm();
  window.BTCStrategyLab = { load };
})();
