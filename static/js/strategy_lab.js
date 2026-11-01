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
  // One status badge per card (validation + what kind of number it is), the data kinds as plain text below it.
  // The label always carries the design verdict, e.g. "FAILED on design data · not validated".
  const badge = (v) => `<span class="tag ${v.status === "FORWARD TESTED: PASS" ? "live" : "nv"}" title="${esc(v.status)}">${esc(v.label || v.status)}</span>`;
  const kinds = (labels) => {
    const k = labels.filter((l) => l !== "NOT VALIDATED").map((l) => l.toLowerCase()).join(" · ");
    return k ? `<p class="strat-kinds small muted">${esc(k.charAt(0).toUpperCase() + k.slice(1))}</p>` : "";
  };
  const list = (items, cls = "") => (items && items.length ? `<ul class="ev ${cls}">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");

  function card(s) {
    const head = `<div class="strat-head"><h3>${esc(s.name)}</h3></div>`;
    const status = `<p class="strat-status"><b>${esc(s.validation.status)}</b><br><span class="muted small">${esc(s.validation.detail)}</span></p>`;
    if (!s.implemented) {
      return `<article class="strat-card planned" id="strat-${esc(s.id)}">${head}
        <div class="strat-tags">${badge(s.validation)}</div>
        <p class="muted small">${esc(s.purpose)}</p><p class="strat-planned">Planned for round ${s.planned_round}: not implemented yet, so no signal.</p>${status}</article>`;
    }
    if (!s.result) {
      return `<article class="strat-card" id="strat-${esc(s.id)}">${head}<p class="muted">Could not be evaluated: ${esc(s.error || "no result")}</p>${status}</article>`;
    }
    const r = s.result;
    const levels = r.entry_zone || r.stop !== null || (r.targets || []).length
      ? `<dl class="adv-dl"><div><dt>Entry zone</dt><dd>${r.entry_zone ? `$${esc(r.entry_zone.low)} to $${esc(r.entry_zone.high)}` : "Not applicable"}</dd></div>
          <div><dt>Invalidation</dt><dd>${r.stop !== null ? "$" + esc(r.stop) : "Not applicable"}</dd></div>
          <div><dt>Target</dt><dd>${(r.targets || []).length ? r.targets.map((t) => "$" + esc(t)).join(", ") : r.stop !== null ? "None (trailing exit)" : "Not applicable"}</dd></div>
          <div><dt>Reward/risk</dt><dd>${r.reward_risk !== null ? esc(r.reward_risk) : r.stop !== null ? "Unknown at entry (no fixed target)" : "Not applicable"}</dd></div></dl>`
      : `<p class="muted small">Entry, invalidation and reward/risk do not apply: ${esc(s.id === "dca" ? "DCA buys on a schedule and never sells." : "no setup.")}</p>`;
    const warn = [...(r.contradictory || []), ...((r.data || {}).warnings || [])];
    const bt = s.backtest ? `Design backtest: ${esc(s.backtest.verdict)} on its pre-registered criteria` : "Design backtest: not run yet";
    return `<article class="strat-card" id="strat-${esc(s.id)}">${head}
      <div class="strat-tags">${badge(s.validation)}</div>${kinds(r.labels)}
      <p class="muted small">${esc(s.purpose)}</p>
      ${r.design_warning ? `<p class="strat-design-warning">${esc(r.design_warning)}</p>` : ""}
      <div class="strat-signal sig-${esc(r.signal.toLowerCase().replace(/[^a-z]+/g, "-"))}">${esc(r.signal)}</div>
      <p class="small muted">${esc(r.asset)} · ${esc(r.timeframe)} · evaluated ${esc(when(r.evaluated_at))} · regime (context) ${esc(r.regime || "not available")}</p>
      <p class="small">${esc(r.explanation)}</p>
      ${r.rejection ? `<p class="small muted">${s.id === "dca" ? "Why no buy" : "Why no trade"}: ${esc(r.rejection)}</p>` : ""}
      <p class="small muted">Strength: ${r.strength === null ? "not applicable" : esc(r.strength) + " / 100"} · ${esc(r.strength_note)}</p>
      ${levels}
      <p class="small muted">Costs: fee ${esc(r.fees_pct)}% + slippage ${esc(r.slippage_pct)}% per ${s.id === "dca" ? "buy" : "side"} · data ${esc(r.data.source || "unknown")}, last close ${esc(when(r.data.last_candle_close))}${r.data.is_stale ? " (stale)" : ""}</p>
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

  const num = (v, d = 2) => (v === null || v === undefined ? "N/A" : Number(v).toFixed(d));
  const runCmd = (sid) => `<code>python scripts\\research_strategy.py ${esc(sid)}</code>`;

  function renderBacktest(sid, bt) {
    if (!bt) {
      $("stratBacktest").innerHTML = `<p class="muted small">No design backtest yet. It runs once, after the pre-registration is committed: ${runCmd(sid)}</p>`;
      return;
    }
    const m = bt.metrics;
    const head = `<p><b>${esc(bt.verdict)}</b> on the pre-registered criteria (design data ${esc(day(bt.period.first_day))} to ${esc(day(bt.period.last_day))}: evidence, not validation).</p>`;
    if (sid === "dca") {
      const rol = bt.rolling || {};
      $("stratBacktest").innerHTML = `${head}
        <dl class="adv-dl"><div><dt>Return on the budget</dt><dd>${pct(m.net_return_pct)}</dd></div>
          <div><dt>Lump sum, same budget</dt><dd>${pct(m.buy_hold_return_pct)}</dd></div>
          <div><dt>Max drawdown</dt><dd>${num(m.max_drawdown_pct)}%</dd></div><div><dt>Buys</dt><dd>${esc(m.trades)}</dd></div>
          <div><dt>Fees and slippage</dt><dd>$${num(m.costs_usd)}</dd></div>
          <div><dt>Rolling ${esc(rol.horizon_days)}-day windows</dt><dd>${esc(rol.windows)}</dd></div></dl>
        ${list((bt.criteria || []).map((c) => `${c.id} ${c.passed ? "passed" : "failed"}: ${c.rule} (DCA ${num(c.dca)}%, lump sum ${num(c.lump_sum)}%)`))}`;
      return;
    }
    const bh = bt.buy_and_hold || {}, h2 = bt.h2_rule || {}, rnd = bt.random_entries || {};
    const wf = (bt.walk_forward || []).map((w) => `<tr><td data-label="Window">${esc(day(w.start))} to ${esc(day(w.end))}</td><td data-label="Trades">${esc(w.trades)}</td>
      <td data-label="Return">${pct(w.return_pct)}</td><td data-label="Sum of R">${num(w.sum_r)}</td></tr>`).join("");
    // Sensitivity columns come from the result itself (each strategy varies its own pre-registered parameters).
    const SENS = { adx_min: "ADX min", adx_max: "ADX max", lookback: "Lookback", volume_mult: "Volume", target_r: "Target", target: "Target" };
    const sensKeys = Object.keys(SENS).filter((k) => (bt.sensitivity || []).some((x) => x[k] !== undefined));
    const sensVal = (k, v) => (k === "target_r" ? `${esc(v)}R` : k === "volume_mult" ? `${esc(v)}×` : k === "target" ? (v === "top" ? "range high (top)" : "midpoint") : esc(v));
    const sens = (bt.sensitivity || []).map((x) => `<tr>${sensKeys.map((k, j) => `<td data-label="${SENS[k]}">${sensVal(k, x[k])}${j === 0 && x.is_default ? " (default)" : ""}</td>`).join("")}
      <td data-label="Trades">${esc(x.trades)}</td><td data-label="Expectancy">${num(x.expectancy_r)}R</td>
      <td data-label="Profit factor">${num(x.profit_factor)}</td><td data-label="Max DD">${num(x.max_drawdown_pct)}%</td></tr>`).join("");
    $("stratBacktest").innerHTML = `${head}${bt.sample_warning ? `<p class="set-warn small">${esc(bt.sample_warning)}</p>` : ""}
      <dl class="adv-dl"><div><dt>Trades</dt><dd>${esc(m.trades)}</dd></div><div><dt>Expectancy</dt><dd>${num(m.expectancy_r)}R</dd></div>
        <div><dt>Profit factor</dt><dd>${num(m.profit_factor)}</dd></div><div><dt>Win rate</dt><dd>${num(m.win_rate_pct, 1)}%</dd></div>
        <div><dt>Net return</dt><dd>${pct(m.net_return_pct)}</dd></div><div><dt>Max drawdown</dt><dd>${num(m.max_drawdown_pct)}%</dd></div>
        <div><dt>Buy &amp; hold (reported)</dt><dd>${pct(bh.net_return_pct)} · max DD ${num(bh.max_drawdown_pct)}%</dd></div>
        ${bt.h2_rule ? `<div><dt>H2 rule (reported)</dt><dd>${pct(h2.net_return_pct)} · max DD ${num(h2.max_drawdown_pct)}%</dd></div>` : ""}
        ${bt.regime_share_pct !== undefined ? `<div><dt>Regime SIDEWAYS / valid range</dt><dd>${num(bt.regime_share_pct, 1)}% / ${num(bt.valid_range_share_pct, 1)}% of the bars</dd></div>` : ""}
        ${bt.trend_same_window ? `<div><dt>Trend, same window (reported)</dt><dd>${pct(bt.trend_same_window.net_return_pct)} · max DD ${num(bt.trend_same_window.max_drawdown_pct)}% · ${esc(bt.trend_same_window.trades)} trades</dd></div>` : ""}
        ${bt.diagnostics ? `<div><dt>Failed breakouts / retests (diagnostic)</dt><dd>${esc(bt.diagnostics.failed_breakouts)} / ${esc(bt.diagnostics.retests)} of ${esc(bt.diagnostics.trades)} trades</dd></div>` : ""}
        <div><dt>Beats random entries</dt><dd>${num(rnd.beaten_pct, 1)}% of ${esc(rnd.runs)} random runs</dd></div>
        <div><dt>Fees and slippage</dt><dd>${num(m.costs_pct)}% of the account</dd></div></dl>
      ${list((bt.criteria || []).map((c) => `${c.id} ${c.passed ? "passed" : "failed"}: ${c.rule} (value ${num(c.value)})`))}
      <h4>Walk-forward windows (182 days, fixed rules, reported only)</h4>
      <div class="table-wrap"><table class="table stack"><thead><tr><th>Window</th><th>Trades</th><th>Return</th><th>Sum of R</th></tr></thead><tbody>${wf}</tbody></table></div>
      <h4>Sensitivity (reported only, never used to choose parameters)</h4>
      <div class="table-wrap"><table class="table stack"><thead><tr>${sensKeys.map((k) => `<th>${SENS[k]}</th>`).join("")}<th>Trades</th><th>Expectancy</th><th>Profit factor</th><th>Max DD</th></tr></thead><tbody>${sens}</tbody></table></div>`;
  }

  // Deposit-neutral views (addendum 2026-11-01): reported only, never a criterion.
  function depositNeutral(m, budgetDd) {
    if (!m || m.twr_max_drawdown_pct === null || m.twr_max_drawdown_pct === undefined) return "";
    return `<div class="strat-supp"><p class="small"><b>Drawdown of the invested money</b> <span class="tag hyp">Reported only</span></p>
      <dl class="adv-dl"><div><dt>Time-weighted max drawdown</dt><dd>${num(m.twr_max_drawdown_pct)}%</dd></div>
        ${budgetDd !== undefined ? `<div><dt>Budget-basis max drawdown (C1 basis)</dt><dd>${num(budgetDd)}%</dd></div>` : ""}
        <div><dt>Time-weighted return</dt><dd>${pct(m.twr_return_pct)}</dd></div>
        <div><dt>Return on invested: worst</dt><dd>${pct(m.roi_worst_pct)} (${esc(day(m.roi_worst_day))})</dd></div>
        <div><dt>Return on invested: last</dt><dd>${pct(m.roi_final_pct)}</dd></div></dl>
      <p class="small muted">${esc(m.note)}</p></div>`;
  }

  function renderForward(sid, f) {
    const state = { not_started: "Not started yet: the background monitor registers it once the app runs.",
      waiting: `Starts ${f.start_day ? day(f.start_day) : "soon"} (00:00 UTC); no completed ${f.timeframe === "1d" ? "day" : "bar"} yet.`,
      running: `Running: day ${f.days_elapsed} of ${f.target_days}.`, done: `Done: ${f.verdict} on the pre-registered forward criteria.` }[f.state];
    let nums = "";
    if (f.days_elapsed || f.bars_processed) {
      nums = sid === "dca"
        ? `<p class="small">DCA ${pct(f.dca_return_pct)} (max drawdown ${num(f.dca_max_dd_pct)}%) vs lump sum ${pct(f.lump_return_pct)} (max drawdown ${num(f.lump_max_dd_pct)}%).</p>`
        : `<p class="small">${esc(f.trades)} trades · net return ${pct(f.net_return_pct)} · expectancy ${num(f.expectancy_r)}R · max drawdown ${num(f.max_drawdown_pct)}%.</p>`;
    }
    const rebased = f.rebased ? `<p class="small muted">Fingerprint re-based on ${esc(day(f.rebased.created_at))}, before the first processed day: ${esc(f.rebased.reason)}.</p>` : "";
    $("stratForward").innerHTML = `<p class="small">${esc(state)}</p>${nums}
      <p class="small muted">Own tracker, own fingerprint <code>${esc(String(f.fingerprint_now).slice(0, 12))}</code>: <span class="fp-badge ${f.code_changed ? "bad" : "ok"}">${f.code_changed ? "code changed since the start" : "unchanged since the start"}</span></p>${rebased}`;
  }

  function field(key, f, value) {
    const label = `<label>${esc(key.replace(/_/g, " "))}<span class="help">${esc(f.help)}</span></label>`;
    const input = f.kind === "choice"
      ? `<select name="${esc(key)}" aria-label="${esc(key)}">${f.choices.map((c) => `<option${c === value ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`
      : `<input type="number" name="${esc(key)}" aria-label="${esc(key)}" value="${esc(value)}" min="${f.lo}" max="${f.hi}" step="${f.kind === "int" ? 1 : "any"}">`;
    return `<div class="set-row">${label}<div>${input}</div></div>`;
  }

  function renderConfig(sid, cfg) {
    const box = document.querySelector("#stratDetail .strat-cfg");
    box.id = `stratCfg-${sid}`;
    // in the documented order (JSON objects arrive with sorted keys)
    box.querySelector(".strat-cfg-fields").innerHTML = cfg.order.map((k) => field(k, cfg.fields[k], cfg.config[k])).join("");
    box.querySelector(".strat-cfg-version").textContent = `Configuration version ${cfg.version} (${cfg.settings_changed
      ? "differs from the pre-registered settings: NOT VALIDATED (settings changed)" : "the pre-registered settings"}). Pre-registration: ${cfg.preregistration}.`;
    box.dataset.version = cfg.version;
    box.dataset.sid = sid;
  }

  function renderHistory(rows) {
    $("stratHistory").querySelector("tbody").innerHTML = rows.length ? rows.map((h) => `<tr><td data-label="Day (UTC)">${esc(day(h.day))}</td>
      <td data-label="Signal"><b>${esc(h.signal)}</b></td><td data-label="Config">v${esc(h.config_version)}</td><td data-label="Why">${esc(h.explanation)}</td></tr>`).join("")
      : '<tr><td colspan="4" class="empty">No stored records yet: the background monitor stores one per UTC day.</td></tr>';
  }

  const store = { get(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } } };
  let detailId = store.get("btc-strategy-detail", "dca");
  let activeTab = store.get("btc-strategy-tab", "overview") === "chart" ? "chart" : "overview";

  function showTab(tab) {
    activeTab = tab;
    store.set("btc-strategy-tab", tab);
    document.querySelectorAll("#stratTabs button").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", String(on));
    });
    $("stratTabOverview").hidden = tab !== "overview";
    $("stratTabChart").hidden = tab !== "chart";
  }

  async function load() {
    try {
      const all = await call("GET", "/api/strategies");
      const live = all.strategies.filter((s) => s.implemented);
      if (!live.some((s) => s.id === detailId)) detailId = live[0].id;
      const [det, cfg] = await Promise.all([call("GET", `/api/strategies/${detailId}`), call("GET", `/api/strategies/${detailId}/config`)]);
      $("stratCards").innerHTML = all.strategies.map(card).join("");
      renderCompare(all.comparison);
      $("stratDetailPick").innerHTML = live.map((s) => `<option value="${esc(s.id)}"${s.id === detailId ? " selected" : ""}>${esc(s.name)}</option>`).join("");
      $("stratDetailTitle").textContent = `${det.name} details`;
      renderBacktest(detailId, det.backtest);
      if (det.backtest_supplement) $("stratBacktest").insertAdjacentHTML("beforeend",
        depositNeutral(det.backtest_supplement, det.backtest_supplement.budget_basis_max_drawdown_pct));
      renderForward(detailId, det.forward);
      if (det.forward_deposit_neutral) $("stratForward").insertAdjacentHTML("beforeend", depositNeutral(det.forward_deposit_neutral));
      renderConfig(detailId, cfg);
      renderHistory(det.history || []);
      $("stratDetail").dataset.sid = detailId;
      $("stratLab").dataset.state = "ready";
      const charts = window.BTCStrategyCharts;
      if (charts) {
        charts.compare();
        if (activeTab === "chart") charts.render(detailId);
      }
    } catch (err) {
      $("stratCards").innerHTML = `<p class="empty">Strategy Lab could not load: ${esc(err.message)}</p>`;
      $("stratLab").dataset.state = "error";
    }
  }

  function initForm() {
    const box = document.querySelector("#stratDetail .strat-cfg");
    if (!box || box.dataset.ready) return;
    box.dataset.ready = "1";
    const form = box.querySelector("form"), msg = box.querySelector(".set-msg");
    showTab(activeTab);
    $("stratTabs").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (!b) return;
      showTab(b.dataset.tab);
      if (b.dataset.tab === "chart" && window.BTCStrategyCharts) window.BTCStrategyCharts.render(detailId);
    });
    $("stratDetailPick").addEventListener("change", (e) => {
      detailId = e.target.value;
      store.set("btc-strategy-detail", detailId);
      msg.textContent = "";
      load();
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = {};
      form.querySelectorAll("[name]").forEach((el) => { body[el.name] = el.tagName === "SELECT" ? el.value : Number(el.value); });
      const next = Number(box.dataset.version || 0) + 1;
      if (!window.confirm(`Saving creates configuration version ${next}. Settings that differ from the pre-registered ones are `
        + "NOT VALIDATED (settings changed) and inherit no backtest or forward result. Continue?")) return;
      try { await call("PUT", `/api/strategies/${box.dataset.sid}/config`, body); msg.textContent = "Saved."; await load(); }
      catch (err) { msg.textContent = "Not saved: " + err.message; }
    });
    box.querySelector("[data-reset]").addEventListener("click", async () => {
      try { await call("POST", `/api/strategies/${box.dataset.sid}/config/reset`); msg.textContent = "Reset to the pre-registered defaults."; await load(); }
      catch (err) { msg.textContent = "Reset failed: " + err.message; }
    });
  }

  document.addEventListener("DOMContentLoaded", initForm);
  if (document.readyState !== "loading") initForm();
  window.BTCStrategyLab = { load };
})();
