/* BTC Signal Lab dashboard (Phases 1-9, UI polish 0.9.1). Vanilla JS + Plotly. */
(() => {
  // Page config comes from a JSON data block (no inline script, so the CSP can stay script-src 'self').
  window.APP = JSON.parse(document.getElementById("appConfig").textContent);
  // Every request carries the app's own header; the server rejects cross-site writes without it (CSRF guard).
  const fetch = (url, opts = {}) => window.fetch(url, { ...opts, headers: { "X-Requested-With": "BTCSignalLab", ...(opts.headers || {}) } });
  const state = { tf: "1h", view: null, lastSeries: null, loaded: {}, ran: new Map(), priceMs: 15000, lastUpdate: 0,
    priceStatus: "wait", sourceErrors: [], analysis: {}, chartExtras: {} };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Links from data (feeds, alerts) only ever become http(s) or same-site paths; anything else is inert.
  const safeUrl = (u) => { const s = String(u ?? "").trim(); return /^https?:\/\//i.test(s) || /^\/(?!\/)/.test(s) ? s : "#"; };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode: not remembered */ } },
  };

  // ---------------------------------------------------------------- missing values: ONE helper
  // Never show a bare "—", "NaN", "null" or "undefined": every formatter routes a missing value through show().
  const MISSING = { wait: "Waiting for first refresh…", data: "Not enough data", source: "Not available from this source",
    none: "None", trades: "No trades yet", never: "Never", unknown: "Unknown time", nodata: "No data" };
  const has = (v) => v !== null && v !== undefined && v !== "" && !(typeof v === "number" && !Number.isFinite(v));
  const show = (v, fmt = (x) => x, missing = MISSING.data) => (has(v) ? fmt(v) : missing);
  const fmtNum = (v, d = 2) => Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtUsd = (v, d = 2, missing = MISSING.data) => show(v, (x) => fmtNum(x, d), missing);
  const usd = (v, d = 2, missing = MISSING.data) => show(v, (x) => "$" + fmtNum(x, d), missing);
  const fmtBig = (v, missing = MISSING.data) => show(v, (x) => {
    const a = Math.abs(x);
    if (a >= 1e12) return (x / 1e12).toFixed(2) + "T";
    if (a >= 1e9) return (x / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return (x / 1e6).toFixed(2) + "M";
    return fmtNum(x, 0);
  }, missing);
  const money = (v, missing = MISSING.none) => show(v, (x) => `${x < 0 ? "−" : ""}$${fmtNum(Math.abs(x), 2)}`, missing);
  const fmtPct = (v, missing = MISSING.data) => show(v, (x) => `${x > 0 ? "+" : ""}${Number(x).toFixed(2)}%`, missing);
  const signed = (v, d = 2, missing = MISSING.data) => show(v, (x) => `${x >= 0 ? "+" : ""}${Number(x).toFixed(d)}`, missing);
  const plain = (v, missing = MISSING.data) => show(v, (x) => String(x), missing);
  const localTime = (ms) => show(ms, (x) => new Date(x).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" }), MISSING.unknown);
  const utcTime = (ms) => (has(ms) ? new Date(ms).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "");
  const isoUtc = (ms) => new Date(ms).toISOString().slice(0, 19) + "+00:00";     // the chart's own time format
  const ago = (ms) => {
    if (!has(ms)) return MISSING.never;
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  };
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const alpha = (hex, a) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };

  async function getJson(url) {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    return body;
  }

  // Mobile tables: stacked cards below 600 px (CSS only) read their labels from data-label, set from the <th> row.
  function labelCells(table) {
    if (!table || !table.tHead || !table.tHead.rows.length) return;
    const heads = [...table.tHead.rows[0].cells].map((th) => th.textContent.trim());
    [...table.tBodies].forEach((tb) => [...tb.rows].forEach((tr) => {
      let i = 0;
      [...tr.cells].forEach((td) => { if (td.colSpan === 1 && heads[i]) td.dataset.label = heads[i]; i += td.colSpan; });
    }));
  }

  // <details class="fold" id=...>: closed by default, the open/closed state is remembered per id.
  function applyFolds(root = document) {
    root.querySelectorAll("details.fold[id]").forEach((d) => {
      if (d.dataset.foldInit) return;
      d.dataset.foldInit = "1";
      if (store.get("btc-fold-" + d.id, false)) d.open = true;
      d.addEventListener("toggle", () => store.set("btc-fold-" + d.id, d.open));
    });
  }

  // ---------------------------------------------------------------- views (hash routes)
  const VIEWS = { overview: "Overview", market: "Market & chart", news: "News & calendar", signal: "Signal details",
    research: "Research (backtest)", paper: "Risk & paper", alerts: "Alerts", assistant: "Ask the data", system: "System & sources" };

  function parseHash() {
    const h = decodeURIComponent(location.hash || "");
    if (h.startsWith("#/")) {
      const v = h.slice(2);
      return { view: VIEWS[v] ? v : "overview" };
    }
    if (h.length > 1) {                                   // old anchors: #backtest, #news, #signal, ...
      const el = document.getElementById(h.slice(1));
      const holder = el && el.closest("[data-views]");
      if (holder) return { view: holder.dataset.views.split(" ")[0], anchor: el };
    }
    return { view: "overview" };
  }

  function runOnce(fn) {
    if (!state.ran.has(fn)) state.ran.set(fn, Promise.resolve().then(fn).catch(() => {}));
    return state.ran.get(fn);
  }

  function ensureLoaded(view) {
    if (!state.loaded[view]) state.loaded[view] = Promise.all((LOADERS[view] || []).map(runOnce));
    return state.loaded[view];
  }

  function showView(view, anchor) {
    const main = $("main");
    state.view = view;
    main.dataset.view = view;
    document.querySelectorAll("[data-views]").forEach((el) => { el.hidden = !el.dataset.views.split(" ").includes(view); });
    document.querySelectorAll("#viewNav a[data-view]").forEach((a) => {
      const on = a.dataset.view === view;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    $("viewTitle").textContent = VIEWS[view];
    document.title = `${VIEWS[view]} · BTC Signal Lab`;
    closeMenu();
    main.dataset.viewReady = "0";
    ensureLoaded(view).then(() => {
      if (state.view !== view) return;
      main.dataset.viewReady = "1";
      resizeCharts();
      if (anchor) anchor.scrollIntoView({ block: "start" });   // again: lazily loaded panels above it may have grown
    });
    requestAnimationFrame(() => {
      resizeCharts();
      if (anchor) anchor.scrollIntoView({ block: "start" }); else window.scrollTo(0, 0);
    });
  }

  function resizeCharts() {
    if (typeof Plotly === "undefined") return;
    document.querySelectorAll(".js-plotly-plot").forEach((p) => { if (p.offsetParent) Plotly.Plots.resize(p); });
  }

  const route = () => { const r = parseHash(); showView(r.view, r.anchor); };

  // ---------------------------------------------------------------- theme / chrome
  function closeMenu() {
    $("sidebar").classList.remove("open");
    $("menuBtn").setAttribute("aria-expanded", "false");
    $("menuBtn").setAttribute("aria-label", "Open menu");
  }

  function initChrome() {
    let saved = null;
    try { saved = localStorage.getItem("btc-theme"); } catch (_) {}
    const preset = window.APP.theme;                       // Settings → General → Theme ("system" = browser/OS)
    if (preset === "dark" || preset === "light") saved = preset;
    else if (!saved && window.matchMedia) saved = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    if (saved) document.documentElement.dataset.theme = saved;
    const syncIcon = () => { $("themeBtn").textContent = document.documentElement.dataset.theme === "dark" ? "☀" : "☾"; };
    syncIcon();
    $("themeBtn").onclick = () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      syncIcon();
      try { localStorage.setItem("btc-theme", next); } catch (_) {}
      if (state.lastSeries) drawChart(state.lastSeries);
    };
    $("menuBtn").onclick = (e) => {
      e.stopPropagation();
      const open = !$("sidebar").classList.contains("open");
      $("sidebar").classList.toggle("open", open);
      $("menuBtn").setAttribute("aria-expanded", String(open));
      $("menuBtn").setAttribute("aria-label", open ? "Close menu" : "Open menu");
      if (open) { const cur = document.querySelector("#viewNav a[aria-current]"); if (cur) cur.focus(); }
    };
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("sidebar").classList.contains("open")) { closeMenu(); $("menuBtn").focus(); } });
    document.addEventListener("click", (e) => { if ($("sidebar").classList.contains("open") && !e.target.closest("#sidebar")) closeMenu(); });
    $("viewNav").addEventListener("click", (e) => { if (e.target.closest("a")) closeMenu(); });
    $("tfSeg").addEventListener("click", (e) => {
      const tf = e.target.dataset.tf;
      if (!tf || tf === state.tf) return;
      state.tf = tf;
      document.querySelectorAll("#tfSeg button").forEach((b) => b.classList.toggle("on", b.dataset.tf === tf));
      loadChart(); loadAnalysis();
    });
    const mobile = window.innerWidth < 700;
    const defaults = { ema: true, bb: true, vwap: false, levels: !mobile, alerts: !mobile, markers: !mobile };
    state.overlays = { ...defaults, ...store.get("btc-overlays", {}) };
    document.querySelectorAll("#overlayToggles input[data-ov]").forEach((i) => { i.checked = Boolean(state.overlays[i.dataset.ov]); });
    $("overlayToggles").addEventListener("change", (e) => {
      state.overlays[e.target.dataset.ov] = e.target.checked;
      store.set("btc-overlays", state.overlays);
      if (state.lastSeries) drawChart(state.lastSeries);
    });
    $("refreshBtn").onclick = () => refreshView(true);
    const tick = () => {
      $("clock").textContent = new Date().toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " (local)";
      renderFreshness();
    };
    tick(); setInterval(tick, 1000);
  }

  function setStatus(kind, text) {
    const p = $("statusPill");
    p.className = "pill " + kind;
    p.querySelector("span").textContent = text;
  }

  // ---------------------------------------------------------------- freshness (top bar)
  function renderFreshness() {
    const f = $("freshness");
    if (!state.lastUpdate) { f.textContent = MISSING.wait; f.className = "fresh"; return; }
    const age = Math.max(0, Math.round((Date.now() - state.lastUpdate) / 1000));
    const next = Math.max(0, Math.round((state.lastUpdate + state.priceMs - Date.now()) / 1000));
    const ageTxt = age < 120 ? `${age} s` : `${Math.round(age / 60)} min`;
    f.textContent = `Updated ${ageTxt} ago · next refresh in ${next} s`;
    const problems = [];
    if (state.priceStatus === "missing") problems.push("no price source reachable");
    if (state.priceStatus === "stale") problems.push("price is stale");
    if (state.sourceErrors.length) problems.push("failing: " + state.sourceErrors.join(", "));
    const level = state.priceStatus === "missing" ? "err" : problems.length ? "warn" : "ok";
    f.className = "fresh " + level;
    f.title = problems.length ? `Problem: ${problems.join("; ")}. Open System & sources.` : "All sources OK. Opens System & sources.";
  }

  // ---------------------------------------------------------------- price / overview
  async function loadPrice(force) {
    try {
      const p = await getJson("/api/price" + (force ? "?force=1" : ""));
      state.lastUpdate = Date.now();
      if (p.price == null) {
        state.priceStatus = "missing";
        $("price").textContent = "Unavailable";
        $("priceMeta").textContent = "No price source reachable";
        $("priceWarn").textContent = (p.warnings || []).join(" ");
        setStatus("err", "No price data");
        return;
      }
      state.priceStatus = p.is_stale ? "stale" : "ok";
      $("price").textContent = usd(p.price);
      const badge = p.is_stale ? '<span class="badge stale">stale</span>' : '<span class="badge fresh">fresh</span>';
      $("priceMeta").innerHTML = `${badge} ${esc(p.provider)} · BTC/${esc(p.quote)} · observed <span title="${esc(utcTime(p.observed_at || p.fetched_at))}">${esc(ago(p.observed_at || p.fetched_at))}</span>`;
      $("priceWarn").textContent = (p.warnings || []).join(" ");
      const ch = p.change_24h_pct;
      $("change").textContent = fmtPct(ch, "Not provided");
      $("change").className = "kpi-value mono " + (!has(ch) ? "" : ch >= 0 ? "up" : "down");
      $("changeMeta").textContent = has(ch) ? `${p.provider} · rolling 24h` : MISSING.source;
      setStatus(p.is_stale ? "warn" : "ok", p.is_stale ? "Data delayed" : "Data fresh");
    } catch (err) {
      setStatus("err", "Server error");
      $("priceWarn").textContent = err.message;
    } finally { renderFreshness(); }
  }

  async function loadOverview() {
    try {
      const o = await getJson("/api/overview");
      if (o.status === "unavailable") {
        $("mcap").textContent = "Unavailable";
        $("mcapMeta").textContent = "CoinGecko unavailable";
        return;
      }
      const dom = has(o.btc_dominance_pct) ? o.btc_dominance_pct.toFixed(1) + "% dom." : "dominance not provided";
      $("mcap").innerHTML = `$${esc(fmtBig(o.market_cap_usd))} <small class="muted kpi-small">· ${esc(dom)}</small>`;
      $("mcapMeta").innerHTML = `Data from CoinGecko · updated ${esc(ago(o.observed_at || o.fetched_at))}${o.from_cache ? " (cached)" : ""}`;
    } catch (err) {
      $("mcap").textContent = "Unavailable";
      $("mcapMeta").textContent = err.message;
    }
  }

  // ---------------------------------------------------------------- chart
  function getAnalysis(tf) {
    const c = state.analysis[tf];
    if (c && Date.now() - c.at < 30000) return c.promise;
    const promise = getJson(`/api/analysis?tf=${tf}`);
    state.analysis[tf] = { at: Date.now(), promise };
    promise.catch(() => { delete state.analysis[tf]; });
    return promise;
  }

  async function loadChart() {
    if (!state.lastSeries) $("chartBox").innerHTML = '<div class="empty">Loading chart…</div>';
    $("chartWarn").textContent = "";
    try {
      const data = await getJson(`/api/candles?tf=${state.tf}&limit=300`);
      const m = data.meta;
      $("chartMeta").innerHTML = m.count
        ? `${esc(m.provider)} · BTC/${esc(m.quote)} · ${m.count} closed candles · last close <span title="${esc(utcTime(m.last_closed_candle_close))}">${esc(localTime(m.last_closed_candle_close))}</span>${m.from_cache ? " · cached" : ""}`
        : "No candle data yet";
      $("chartWarn").textContent = (m.warnings || []).join(" ");
      if (!data.series.time.length) {
        $("chartBox").innerHTML = '<div class="empty">No candle data available. Check System &amp; sources.</div>';
        state.lastSeries = null;
        return;
      }
      const [ana, rules, hist] = await Promise.allSettled([getAnalysis(state.tf), getJson("/api/alerts/rules"),
        getJson("/api/signal/history?limit=200")]);
      state.chartExtras = {
        levels: ana.status === "fulfilled" ? ana.value.levels : null,
        rules: rules.status === "fulfilled" ? rules.value.rules : [],
        history: hist.status === "fulfilled" ? hist.value.history : [],
      };
      state.lastSeries = data.series;
      drawChart(data.series);
    } catch (err) {
      $("chartBox").innerHTML = `<div class="empty">Chart failed to load: ${esc(err.message)}</div>`;
    }
  }

  const SETUP_GROUP = (s) => (/BUY/.test(s || "") ? "BUY" : /SELL/.test(s || "") ? "SELL" : "HOLD");

  function overlayTraces(s, lo, hi, colors) {
    const traces = [], shapes = [], annotations = [];
    let outside = 0;
    const flat = (y) => s.time.map(() => y);
    const ex = state.chartExtras || {};
    if (state.overlays.levels && ex.levels) {
      for (const [kind, arr, color] of [["Resistance", ex.levels.resistance || [], colors.down], ["Support", ex.levels.support || [], colors.up]]) {
        for (const l of arr) {
          if (!(l.price >= lo && l.price <= hi)) { outside += 1; continue; }
          const major = l.touches >= 2;
          const label = `${kind} $${fmtNum(l.price, 0)} · ${l.touches} touch${l.touches > 1 ? "es" : ""}${major ? "" : " (minor)"}`;
          if (major) {
            shapes.push({ type: "rect", xref: "paper", x0: 0, x1: 1, yref: "y", layer: "below", line: { width: 0 },
              y0: Math.min(l.low, l.price * 0.999), y1: Math.max(l.high, l.price * 1.001), fillcolor: alpha(color, 0.13) });
          }
          traces.push({ type: "scatter", mode: "lines", x: s.time, y: flat(l.price), name: label, showlegend: false, xaxis: "x", yaxis: "y",
            line: { color: alpha(color, major ? 0.55 : 0.85), width: 1, dash: major ? "solid" : "dash" }, hovertemplate: `${esc(label)}<extra></extra>` });
        }
      }
    }
    if (state.overlays.alerts) {
      for (const r of ex.rules || []) {
        const p = r.params && Number(r.params.price);
        if (!r.enabled || !(r.kind === "price_above" || r.kind === "price_below") || !has(p)) continue;
        if (!(p >= lo && p <= hi)) { outside += 1; continue; }
        const label = `Alert ${r.kind === "price_above" ? "above" : "below"} $${fmtNum(p, 0)}`;
        traces.push({ type: "scatter", mode: "lines", x: s.time, y: flat(p), name: label, showlegend: false, xaxis: "x", yaxis: "y",
          line: { color: colors.info, width: 1.2, dash: "dot" }, hovertemplate: `${esc(label)}<extra></extra>` });
        annotations.push({ xref: "paper", x: 0.01, xanchor: "left", yref: "y", y: p, yanchor: "bottom", text: esc(label),
          showarrow: false, font: { size: 10, color: colors.info } });
      }
    }
    if (state.overlays.markers && (ex.history || []).length) {
      const t0 = Date.parse(s.time[0]), t1 = Date.parse(s.time[s.time.length - 1]);
      const groups = { BUY: [], SELL: [], HOLD: [] };
      for (const h of ex.history) {
        if (!(h.created_at >= t0 && h.created_at <= t1 + 86400000) || !has(h.price)) continue;
        groups[SETUP_GROUP(h.setup)].push(h);
      }
      const look = { BUY: ["triangle-up", colors.up], SELL: ["triangle-down", colors.down], HOLD: ["circle-open", colors.muted] };
      for (const [g, rows] of Object.entries(groups)) {
        if (!rows.length) continue;
        traces.push({ type: "scatter", mode: "markers", name: `Signal: ${g} setup`, showlegend: false, xaxis: "x", yaxis: "y",
          x: rows.map((h) => isoUtc(h.created_at)), y: rows.map((h) => h.price),
          marker: { symbol: look[g][0], color: look[g][1], size: 10, line: { color: look[g][1], width: 1.5 } },
          hovertext: rows.map((h) => esc(`${localTime(h.created_at)} · ${h.signal} (${h.setup}) · score ${signed(h.score)}`)), hoverinfo: "text" });
      }
    }
    return { traces, shapes, annotations, outside };
  }

  function drawChart(s) {
    if (typeof Plotly === "undefined") {
      $("chartBox").innerHTML = '<div class="empty">Plotly could not be loaded (offline?). Data is still available via the API.</div>';
      return;
    }
    $("chartBox").innerHTML = "";
    const up = css("--up"), down = css("--down"), text = css("--text"), muted = css("--muted"), line = css("--line"), info = css("--info");
    const lows = s.low.filter(has), highs = s.high.filter(has);
    const pad = (Math.max(...highs) - Math.min(...lows)) * 0.04 || 1;
    const lo = Math.min(...lows) - pad, hi = Math.max(...highs) + pad;
    const traces = [{
      type: "candlestick", x: s.time, open: s.open, high: s.high, low: s.low, close: s.close, name: "BTC",
      increasing: { line: { color: up }, fillcolor: up }, decreasing: { line: { color: down }, fillcolor: down },
      xaxis: "x", yaxis: "y", hoverinfo: "x+y",
    }];
    const lineTrace = (y, name, color, extra = {}) => ({ type: "scatter", mode: "lines", x: s.time, y, name, line: { color, width: 1.3, ...extra }, xaxis: "x", yaxis: "y", hoverinfo: "skip" });
    if (state.overlays.ema) {
      traces.push(lineTrace(s.ema9, "EMA 9", "#f7c948"), lineTrace(s.ema21, "EMA 21", "#60a5fa"),
        lineTrace(s.ema50, "EMA 50", "#c084fc"), lineTrace(s.ema200, "EMA 200", "#f97316", { width: 2 }));
    }
    if (state.overlays.bb) {
      traces.push(lineTrace(s.bb_upper, "BB upper", muted, { dash: "dot", width: 1 }),
        lineTrace(s.bb_lower, "BB lower", muted, { dash: "dot", width: 1 }));
    }
    if (state.overlays.vwap) traces.push(lineTrace(s.vwap, "VWAP", "#2dd4bf", { dash: "dash" }));
    const ov = overlayTraces(s, lo, hi, { up, down, info, muted });
    traces.push(...ov.traces);
    traces.push({
      type: "bar", x: s.time, y: s.volume, name: "Volume", xaxis: "x", yaxis: "y2", hoverinfo: "y",
      marker: { color: s.close.map((c, i) => (c >= s.open[i] ? up : down)), opacity: 0.45 },
    });
    traces.push({ type: "scatter", mode: "lines", x: s.time, y: s.rsi14, name: "RSI 14", line: { color: "#a78bfa", width: 1.3 }, xaxis: "x", yaxis: "y3" });
    const narrow = window.innerWidth <= 700;
    const layout = {
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: text, size: 11 },
      margin: { l: 10, r: narrow ? 48 : 60, t: 10, b: 30 }, showlegend: !narrow,
      legend: { orientation: "h", y: 1.06, x: 0, font: { size: 10, color: muted } },
      hovermode: "x unified", dragmode: "pan",
      xaxis: { rangeslider: { visible: false }, gridcolor: line, linecolor: line, type: "date" },
      yaxis: { domain: [0.38, 1], side: "right", gridcolor: line, tickformat: ",.0f", range: [lo, hi] },
      yaxis2: { domain: [0.2, 0.34], side: "right", gridcolor: line, showticklabels: false },
      yaxis3: { domain: [0, 0.16], side: "right", gridcolor: line, range: [0, 100], tickvals: [30, 50, 70] },
      shapes: [...ov.shapes, ...[30, 70].map((y) => ({ type: "line", xref: "paper", x0: 0, x1: 1, yref: "y3", y0: y, y1: y, line: { color: muted, width: 1, dash: "dot" } }))],
      annotations: ov.annotations,
    };
    Plotly.newPlot("chartBox", traces, layout, { responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ["select2d", "lasso2d"] });
    const on = ["levels", "alerts", "markers"].filter((k) => state.overlays[k]);
    $("chartNote").textContent = (on.length ? "Overlays: support/resistance bands are levels with 2+ touches, dashed lines are minor levels; dotted lines are your price alerts; markers are stored signal records. " : "")
      + (ov.outside ? `${ov.outside} level${ov.outside > 1 ? "s" : ""} or alert price${ov.outside > 1 ? "s are" : " is"} outside the visible price range.` : "");
    $("chartBox").dataset.overlays = on.join(" ");
  }

  // ---------------------------------------------------------------- analysis
  async function loadAnalysis() {
    if (!$("readings").querySelector(".name")) $("readings").innerHTML = '<li class="empty">Loading…</li>';
    try {
      const a = await getAnalysis(state.tf);
      $("anaMeta").innerHTML = a.status === "ok"
        ? `${esc(state.tf)} · ${a.bars} closed candles · as of ${esc(localTime(a.as_of_candle_close))}`
        : "Insufficient data";
      const warns = [...(a.warnings || []), ...((a.data && a.data.warnings) || [])];
      $("anaWarn").textContent = warns.join(" ");
      if (a.status !== "ok") {
        $("readings").innerHTML = `<li class="empty">${esc(warns.join(" ") || "Not enough data.")}</li>`;
        $("tally").innerHTML = ""; $("facts").innerHTML = ""; $("resList").innerHTML = ""; $("supList").innerHTML = "";
        $("volRegime").textContent = MISSING.data;
        return;
      }
      const t = a.tally;
      $("tally").innerHTML = `<span class="up">▲ ${t.bullish} bullish</span><span class="down">▼ ${t.bearish} bearish</span><span>● ${t.neutral} neutral</span>`;
      $("readings").innerHTML = a.readings.map((r) => `
        <li><span class="dot b${r.bias}"></span><span class="name">${esc(r.name)}</span>
        <span class="val mono">${esc(typeof r.value === "number" ? fmtUsd(r.value, r.value < 10 ? 3 : 2) : plain(r.value, MISSING.nodata))}</span>
        <span class="txt">${esc(r.interpretation)}</span></li>`).join("") || '<li class="empty">No readings.</li>';
      const v = a.volatility, b = a.breakout, st = a.structure;
      const brk = (b.state || "unknown").replaceAll("_", " ") + (b.volume_confirmed ? " (volume confirmed)" : "");
      const steps = [st.last_high_step, st.last_low_step].filter(Boolean).join(" · ").replaceAll("_", " ");
      $("facts").innerHTML = `
        <div class="fact"><small>Structure (last 4 swings)</small><b>${esc(st.trend)}${has(st.score) ? ` (${signed(st.score)})` : ""}</b><div class="muted">last step: ${esc(steps || "no confirmed swing yet")}</div></div>
        <div class="fact"><small>Range state (20 bars)</small><b>${esc(brk)}</b></div>
        <div class="fact"><small>ATR 14</small><b class="mono">${esc(usd(a.indicators.atr14))}</b><div class="muted">${esc(show(a.indicators.atr_pct, (x) => x + "% of price"))}</div></div>
        <div class="fact"><small>Divergence</small><b>${a.divergence.bullish ? "Bullish" : a.divergence.bearish ? "Bearish" : "None"}</b><div class="muted">experimental</div></div>`;
      $("volRegime").textContent = v.regime === "insufficient_data" ? MISSING.data : v.regime;
      $("volRegime").className = "kpi-value " + ({ extreme: "down", high: "down", low: "up" }[v.regime] || "");
      $("volMeta").textContent = has(v.percentile) ? `${state.tf} ATR% at ${v.percentile}th percentile` : "Not enough history";
      const lvl = (arr) => arr.length ? arr.map((l) => `<li><span class="mono">${esc(usd(l.price))}</span><small>${l.touches} touch${l.touches > 1 ? "es" : ""}</small></li>`).join("") : '<li class="muted">None found</li>';
      $("resList").innerHTML = lvl(a.levels.resistance);
      $("supList").innerHTML = lvl(a.levels.support);
    } catch (err) {
      $("readings").innerHTML = `<li class="empty">Analysis failed: ${esc(err.message)}</li>`;
    }
  }

  // ---------------------------------------------------------------- mtf & sources
  async function loadMtf() {
    const body = $("mtfTable").querySelector("tbody");
    try {
      const d = await getJson("/api/mtf");
      body.innerHTML = d.timeframes.map((r) => {
        if (r.status !== "ok") return `<tr><td><b>${esc(r.timeframe)}</b></td><td colspan="8" class="muted">${esc((r.warnings || []).join(" ") || "Insufficient data")}</td></tr>`;
        const rsiCls = r.rsi14 >= 70 ? "down" : r.rsi14 <= 30 ? "up" : "";
        return `<tr><td><b>${esc(r.timeframe)}</b></td><td class="mono">${esc(usd(r.price))}</td>
          <td class="mono ${rsiCls}">${esc(plain(r.rsi14, MISSING.nodata))}</td><td class="mono">${esc(plain(r.adx, MISSING.nodata))}</td>
          <td>${esc(plain(r.trend, MISSING.nodata))}</td><td>${esc((r.breakout || "unknown").replaceAll("_", " "))}</td><td>${esc(plain(r.volatility, MISSING.nodata))}</td>
          <td><span class="up">${r.tally.bullish}</span> / <span class="down">${r.tally.bearish}</span></td>
          <td>${r.is_stale ? '<span class="badge stale">stale</span>' : '<span class="badge fresh">ok</span>'} <span class="muted">${esc(r.provider || "")}</span></td></tr>`;
      }).join("");
      labelCells($("mtfTable"));
    } catch (err) {
      body.innerHTML = `<tr><td colspan="9" class="empty">${esc(err.message)}</td></tr>`;
    }
  }

  async function loadSources() {
    try {
      const d = await getJson("/api/sources");
      state.sourceErrors = d.health.filter((h) => h.status !== "ok").map((h) => h.source);
      renderFreshness();
      const body = $("srcTable").querySelector("tbody");
      body.innerHTML = d.health.length ? d.health.map((h) => `<tr>
          <td><b>${esc(h.source)}</b></td><td class="status-${esc(h.status)}">${h.status === "ok" ? "● OK" : "● Error"}</td>
          <td title="${esc(utcTime(h.last_success))}">${esc(ago(h.last_success))}</td>
          <td class="mono">${has(h.latency_ms) ? Math.round(h.latency_ms) + " ms" : "Not measured"}</td>
          <td class="muted">${esc(h.last_error || "None")}</td></tr>`).join("")
        : '<tr><td colspan="5" class="empty">No requests yet.</td></tr>';
      labelCells($("srcTable"));
      $("docTable").querySelector("tbody").innerHTML =
        "<tr><th>Source</th><th>Provides</th><th>API key</th><th>Limits</th><th>Delay</th><th>On failure</th><th>Notes</th></tr>" +
        d.docs.map((x) => `<tr><td>${esc(x.source)}</td><td>${esc(x.provides)}</td><td>${esc(x.api_key)}</td><td>${esc(x.limits)}</td><td>${esc(x.delay)}</td><td>${esc(x.failure)}</td><td>${esc(x.notes)}</td></tr>`).join("");
    } catch (_) { /* panel stays in its empty state */ }
  }

  async function loadSystem() {
    try {
      const h = await getJson("/api/health");
      $("sysHealth").textContent = `${h.status === "ok" ? "OK" : h.status} · database ${h.db ? "OK" : "unavailable"} · server time ${utcTime(h.server_time)}`;
    } catch (err) { $("sysHealth").textContent = "Unavailable: " + err.message; }
    if (state.lastSignal) $("sysEngine").textContent = state.lastSignal.engine;
  }

  // ---------------------------------------------------------------- news
  const CAT_LABEL = (c) => c.replaceAll("_", " ");
  const FRESH_LABEL = { new: "new", recent: "recent", likely_priced_in: "likely priced in", superseded: "superseded", too_old: "> 72h old" };
  const isStale = (e) => ["too_old", "likely_priced_in", "superseded"].includes(e.freshness);
  state.news = null;

  async function loadNews() {
    try {
      const r = await getJson("/api/news?hours=168");
      state.news = r;
      renderNewsSummary(r);
      renderNewsList();
    } catch (err) {
      $("newsList").innerHTML = `<li class="empty">News failed to load: ${esc(err.message)}</li>`;
    }
  }

  function renderNewsSummary(r) {
    const s = r.summary;
    const pos = Math.max(0, Math.min(100, (s.bias_score + 1) * 50));
    const cls = s.bias_label === "bullish" ? "up" : s.bias_label === "bearish" ? "down" : "";
    if (s.bias_label === "insufficient_news") {
      $("newsBias").innerHTML = '<div class="empty">Not enough recent, weighted news to estimate a bias.</div>';
    } else {
      $("newsBias").innerHTML = `<span class="big ${cls}">${esc(s.bias_label)}</span><span class="mono muted">score ${esc(signed(s.bias_score))}</span>
         <span class="muted small">${s.events_active} active events · ${s.high_impact_active} high impact</span>
         <div class="meter"><i></i></div><div class="scale"><span>bearish −1</span><span>0</span><span>+1 bullish</span></div>`;
      $("newsBias").querySelector(".meter i").style.left = pos + "%";
    }
    const c = s.counts;
    $("sentBars").innerHTML = `<div class="up"><b>${c.bullish}</b>bullish</div><div class="down"><b>${c.bearish}</b>bearish</div>
      <div><b>${c.uncertain}</b>uncertain</div><div><b>${c.neutral}</b>neutral</div>`;
    const cats = Object.entries(s.by_category);
    $("catList").innerHTML = cats.length ? cats.map(([k, v]) =>
      `<li><span>${esc(CAT_LABEL(k))}</span><span>${v.events} · <span class="up">${v.bullish}▲</span> <span class="down">${v.bearish}▼</span></span></li>`).join("")
      : '<li class="muted">No events yet.</li>';
    $("conflicts").innerHTML = s.conflicts.length ? s.conflicts.map((x) =>
      `<li><b>${esc(CAT_LABEL(x.category))}:</b> “${esc(x.a.title)}” (${esc(x.a.sentiment)}) vs “${esc(x.b.title)}” (${esc(x.b.sentiment)})</li>`).join("")
      : '<li class="muted">None detected in the last 24h.</li>';
    $("newsMethod").textContent = s.method + " " + s.disclaimer;
    const ex = s.excluded || {};
    const dropped = (ex.off_topic || 0) + (ex.too_short || 0);
    $("newsMeta").textContent = `Updated ${localTime(s.generated_at)} · ${s.events_total} events from ${r.events.reduce((n, e) => n + e.sources.length, 0)} articles` +
      (dropped ? ` · ${dropped} off-topic/too-short dropped` : "") + ` · ${s.directional_active ?? 0} directional`;
    $("newsWarn").textContent = (r.warnings || []).join(" ");
    const sel = $("fCat"), cur = sel.value;
    const allCats = [...new Set(r.events.flatMap((e) => e.categories))].sort();
    sel.innerHTML = '<option value="">All categories</option>' + allCats.map((k) => `<option value="${esc(k)}">${esc(CAT_LABEL(k))}</option>`).join("");
    sel.value = allCats.includes(cur) ? cur : "";
  }

  function renderNewsList() {
    const r = state.news;
    if (!r) return;
    const fs = $("fSent").value, fi = $("fImpact").value, fc = $("fCat").value, showOld = $("fOld").checked;
    const list = r.events.filter((e) => (!fs || e.sentiment === fs) && (!fi || e.impact === fi) &&
      (!fc || e.categories.includes(fc)) && (showOld || !isStale(e)));
    if (!list.length) {
      $("newsList").innerHTML = `<li class="empty">${r.events.length ? "No stories match these filters." : "No news collected yet."}</li>`;
      return;
    }
    $("newsList").innerHTML = list.slice(0, 120).map((e) => {
      const when = e.published_at || e.first_seen_at;
      const extra = e.sources.length > 1 ? ` <span class="chip">+${e.sources.length - 1} more · ${e.independent_sources} publishers</span>` : "";
      const others = e.sources.slice(1).map((x) => `<li><a href="${esc(safeUrl(x.link))}" target="_blank" rel="noopener noreferrer">${esc(x.publisher)}</a>: ${esc(x.title)}</li>`).join("");
      const dim = isStale(e) ? "dim" : "";
      return `<li class="${dim}">
        <div class="news-top"><span class="sent-dot ${esc(e.sentiment)}" title="${esc(e.sentiment)}"></span>
          <a href="${esc(safeUrl(e.link))}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a></div>
        <div class="news-meta">
          <span>${esc(e.publisher)}</span>·<span title="${esc(utcTime(when))}">${esc(ago(when))}${e.publish_time_known ? "" : " (publish time unknown)"}</span>
          <span class="chip ${esc(e.sentiment)}">${esc(e.sentiment)}</span>
          <span class="chip ${e.impact === "high" ? "high" : ""}">${esc(e.impact)} impact</span>
          <span class="chip conf-${esc(e.verification)}" title="${esc(e.verification_reason)}">${esc(e.verification)}</span>
          <span class="chip" title="${esc(e.freshness_reason)}">${esc(FRESH_LABEL[e.freshness] || e.freshness)}</span>
          ${e.conflicting_reports ? '<span class="chip uncertain">conflicting reports</span>' : ""}${extra}
          ${e.relevance < 1 ? `<span class="chip" title="BTC relevance x${e.relevance}">not BTC-specific</span>` : ""}
          ${e.categories.slice(0, 3).map((c) => `<span class="chip cat">${esc(CAT_LABEL(c))}</span>`).join("")}
        </div>
        <details class="news-why"><summary>Why ${esc(e.sentiment)} · possible BTC impact</summary>
          <ul>${e.btc_impact.map((w) => `<li>${esc(w)}</li>`).join("")}
            <li>Impact: ${esc(e.impact_reason)}.</li><li>${esc(e.verification_reason)}</li><li>${esc(e.freshness_reason)}</li></ul>
          ${others ? `<div class="srcs"><p class="small">Also reported by:</p><ul>${others}</ul></div>` : ""}
        </details></li>`;
    }).join("");
  }

  function initNewsControls() {
    ["fSent", "fImpact", "fCat", "fOld"].forEach((id) => $(id).addEventListener("change", renderNewsList));
    $("newsRefresh").onclick = async () => {
      const b = $("newsRefresh");
      b.disabled = true; b.textContent = "Fetching…";
      try { await fetch("/api/news/refresh", { method: "POST" }); await loadNews(); loadSources(); }
      finally { b.disabled = false; b.textContent = "Fetch now"; }
    };
  }

  // ---------------------------------------------------------------- signal
  const SIG_CLASS = (s) => s === "STRONG BUY SETUP" ? "strong" : s === "BUY SETUP" ? "buy" : s === "SELL / REDUCE EXPOSURE" ? "sell" : s === "NO TRADE" ? "notrade" : "hold";
  const pct = (x) => fmtPct(x, "n/a");

  // One plain-language line from the first NO TRADE reason (the full reasons stay in the Signal view).
  const PLAIN_REASONS = [
    [/^Not validated: no backtest/i, "No proven edge yet: no validation backtest has been run"],
    [/^Not validated: no holdout run yet/i, "No proven edge yet: no holdout run has been made"],
    [/^Not validated: the rules changed/i, "No proven edge for the current rules: they changed since the holdout run"],
    [/forward observation/i, "Validated, but still in the forward-observation period"],
    [/^Not validated/i, "No proven edge: the validation backtest didn't pass"],
    [/^No current BTC price/i, "No live price, so no decision can be made"],
    [/price is stale/i, "The price is stale: waiting for fresh data"],
    [/too little data|could not be calculated/i, "Not enough data to judge the market"],
    [/candles are stale/i, "Chart data is stale: waiting for fresh candles"],
    [/missing candles/i, "Gaps in the price history"],
    [/^Price sources disagree/i, "Exchanges disagree on the price: waiting until they agree"],
    [/volatility is extreme/i, "Volatility is extreme: too risky to judge"],
    [/contradictory/i, "The evidence is mixed: indicators disagree"],
    [/fights the daily/i, "The setup fights the daily trend"],
    [/^(Fresh|Recent) high-impact (bearish|bullish) news/i, "High-impact news against this setup: letting the market digest it"],
  ];
  const plainReason = (r) => {
    for (const [re, text] of PLAIN_REASONS) if (re.test(r)) return text;
    return String(r).split(/(?<=\.)\s/)[0];
  };

  async function loadSignal() {
    try {
      const s = await getJson("/api/signal");
      renderSignal(s);
      renderSummary(s);
      loadSignalHistory();
    } catch (err) {
      $("sigBadge").textContent = "Unavailable"; $("sumBadge").textContent = "Unavailable";
      $("sigSub").textContent = err.message; $("sumReason").textContent = "The signal could not be calculated: " + err.message;
    }
  }

  function renderSummary(s) {
    const b = $("sumBadge");
    b.textContent = s.signal;
    b.className = "sig-badge " + SIG_CLASS(s.signal);
    const reasons = s.no_trade_reasons || [];
    $("sumReason").textContent = reasons.length
      ? plainReason(reasons[0]) + (reasons.length > 1 ? ` (+${reasons.length - 1} more reason${reasons.length > 2 ? "s" : ""})` : "") + "."
      : "All safeguards passed. Still a research signal, not advice.";
    $("sumReason").title = reasons.join(" ");
    const pr = s.price || {};
    const rows = [
      ["Setup", esc(s.setup)],
      ["Score", `<span class="mono">${esc(signed(s.score))}</span> <span class="muted">on −1…+1, not a probability</span>`],
      ["BTC price", `<span class="mono">${esc(usd(pr.value))}</span> <span class="muted">${esc(pr.provider || "unknown source")}${pr.quote ? " · " + esc(pr.quote) : ""}</span>`],
      ["Generated", `<span title="${esc(utcTime(s.generated_at))}">${esc(ago(s.generated_at))}</span>`],
    ];
    $("sumFacts").innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
    const po = s.pending_order;
    $("sumPending").innerHTML = po
      ? `<p class="pending-note"><b>Pending order (research only, not actionable):</b> ${esc(po.type)} · ${esc(po.condition)} <span class="mono">${esc(usd(po.price, 0))}</span>, stop <span class="mono">${esc(usd(po.stop, 0))}</span>, target <span class="mono">${esc(usd(po.target, 0))}</span></p>`
      : "";
    $("sigSummary").dataset.state = "ready";
  }

  function renderSignal(s) {
    state.lastSignal = s;
    const cfg = s.config || {};
    const badge = $("sigBadge");
    badge.textContent = s.signal;
    badge.className = "sig-badge " + SIG_CLASS(s.signal);
    const pr = s.price || {};
    $("sigSub").innerHTML = `Setup: <b>${esc(s.setup)}</b> · score <b class="mono">${esc(signed(s.score))}</b> <span class="muted">(−1…+1, not a probability)</span>
      · ${esc(s.direction)}, ${esc(s.strength)}<br>
      Horizon: ${esc(s.horizon)}<br>
      BTC <b class="mono">${esc(usd(pr.value))}</b> (${esc(pr.provider || "unknown source")}${pr.quote ? ", " + esc(pr.quote) : ""}${pr.status ? ", " + esc(pr.status) : ""})
      · generated <span title="${esc(utcTime(s.generated_at))}">${esc(localTime(s.generated_at))}</span>`;
    const pos = (v) => Math.max(0, Math.min(100, (v + 1) * 50));
    $("sigDot").style.left = pos(s.score) + "%";
    document.querySelector(".score-meter .mark.sell").style.left = pos(cfg.sell_threshold ?? -0.25) + "%";
    document.querySelector(".score-meter .mark.buy").style.left = pos(cfg.buy_threshold ?? 0.25) + "%";
    document.querySelector(".score-meter .mark.strong").style.left = pos(cfg.strong_threshold ?? 0.5) + "%";

    const g = $("sigGates");
    const notes = (s.setup_notes || []).length
      ? `<h4 class="note-head">Setup note</h4><ul>${s.setup_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
    if (s.no_trade_reasons.length) {
      g.className = "sig-gates";
      g.innerHTML = `<h4>NO TRADE because</h4><ul>${s.no_trade_reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>${notes}`;
    } else {
      g.className = "sig-gates clear";
      g.innerHTML = `<h4>All safeguards passed</h4><ul><li>Data fresh, volatility not extreme, evidence consistent, no opposing fresh high-impact news.</li></ul>${notes}`;
    }
    const li = (arr, empty) => arr.length ? arr.map((x) => `<li>${esc(x)}</li>`).join("") : `<li class="muted">${esc(empty)}</li>`;
    $("sigFor").innerHTML = li(s.evidence_bullish || [], "No bullish evidence");
    $("sigAgainst").innerHTML = li(s.evidence_bearish || [], "No bearish evidence");

    const p = s.plan;
    if (p) {
      const ok = s.plan_status === "actionable";
      const rr = (v, label) => (v <= 0 ? "no room" : `${v} ${label}`);
      $("sigPlan").innerHTML = `<div class="status ${ok ? "ok" : ""}">${esc(s.plan_status)}</div>
        <div class="row"><span>Entry zone</span><span class="mono">${esc(usd(p.entry_zone[0], 0))} – ${esc(usd(p.entry_zone[1], 0))}</span></div>
        <div class="row"><span>Stop reference</span><span class="mono down">${esc(usd(p.stop, 0))} (${esc(pct(p.stop_pct))})</span></div>
        ${p.targets.map((t, i) => `<div class="row"><span>Target ${i + 1}</span><span class="mono up">${esc(usd(t.price, 0))} (${esc(pct(t.pct))}) · ${t.rr_after_costs <= 0 ? "no room" : "R/R " + t.rr_after_costs + " now"} / ${esc(rr(t.rr_mid_zone, "mid-zone"))}</span></div>`).join("")}
        ${p.room_note ? `<div class="note down">${esc(p.room_note)}</div>` : ""}
        ${(p.obstacles_before_t2 || []).length ? `<div class="note">Between target 1 and target 2: ${p.obstacles_before_t2.map(esc).join("; ")}.</div>` : ""}
        ${(p.obstacles_before_t1 || []).length ? `<div class="note">Before target 1: ${p.obstacles_before_t1.map(esc).join("; ")}.</div>` : ""}
        <div class="note">R/R "now" = buying at the live price (top of the zone), used for the HOLD check; "mid-zone" = a fill in the middle of the entry zone.<br>Stop: ${esc(p.stop_basis)}. Targets: ${p.targets.map((t) => esc(t.basis)).join("; ")}.<br>Costs assumed: ${esc(p.assumed_costs)}. ${esc(p.stop_warning)}</div>`;
    } else {
      $("sigPlan").innerHTML = `<p class="muted">${s.setup === "SELL / REDUCE EXPOSURE"
        ? "Spot only: no short positions. A sell setup means reducing existing exposure; see invalidation levels."
        : "No entry plan: plans are only built for buy setups with reward/risk of at least " + esc(cfg.min_rr ?? 1.5) + " after costs."}</p>`;
    }
    const po = s.pending_order;
    if (po) {
      $("sigPlan").innerHTML += `<div class="pending-order">
        <div class="status">Pending order (research only, not actionable)</div>
        <div class="row"><span>${esc(po.type)} · ${esc(po.condition)}</span><span class="mono">${esc(usd(po.price, 0))}</span></div>
        <div class="row"><span>Stop</span><span class="mono down">${esc(usd(po.stop, 0))}</span></div>
        <div class="row"><span>Target</span><span class="mono up">${esc(usd(po.target, 0))} · R/R ${esc(plain(po.rr_t1))} after costs</span></div>
        <div class="note">Valid ${esc(po.valid_bars)} 4h bars. ${esc(po.basis)}. The signal stays ${esc(s.signal)} while the strategy is not validated.</div></div>`;
    }

    $("sigComp").querySelector("tbody").innerHTML = s.components.map((c) => {
      const v = c.score;
      const bar = has(v) ? `<span class="bar"><i class="${v >= 0 ? "pos" : "neg"}" data-l="${v >= 0 ? 50 : 50 + v * 50}" data-w="${Math.abs(v) * 50}"></i></span>` : "";
      return `<tr><td><b>${esc(c.name)}</b></td><td class="mono">${bar}${has(v) ? esc(signed(v)) : "no data"}</td>
        <td class="mono">${(c.weight * 100).toFixed(0)}%</td><td class="mono">${has(c.contribution) ? esc(signed(c.contribution, 3)) : "no data"}</td>
        <td title="${esc(c.rationale)}"><ul class="facts-list">${(c.facts || c.reasons.map((t) => ({ text: t, polarity: 0 })))
          .map((f) => `<li class="${f.polarity > 0 ? "up" : f.polarity < 0 ? "down" : "muted"}">${f.polarity > 0 ? "▲" : f.polarity < 0 ? "▼" : "•"} ${esc(f.text)}</li>`).join("")}</ul></td></tr>`;
    }).join("");
    $("sigComp").querySelectorAll(".bar i").forEach((i) => { i.style.left = i.dataset.l + "%"; i.style.width = i.dataset.w + "%"; });
    labelCells($("sigComp"));

    $("sigInval").innerHTML = li([...s.invalidation, ...s.would_change], "Nothing listed");
    const kl = s.key_levels || {};
    const fmtL = (l) => l ? `${usd(l.price, 0)} (${l.touches} touch${l.touches > 1 ? "es" : ""}${l.minor ? ", minor" : ""})` : "none found";
    $("sigLevels").innerHTML = li([`Nearest support: ${fmtL((kl.support || [])[0])}`, `Nearest resistance: ${fmtL((kl.resistance || [])[0])}`,
      ...(kl.current_zone ? [`Price is inside the zone ${usd(kl.current_zone.low, 0)}-${usd(kl.current_zone.high, 0)}`] : [])], "No levels");
    const r = s.risk || {};
    const volTxt = (tf) => `${tf} ${plain(r["volatility_" + tf], "n/a")} (${show(r["volatility_pct_" + tf], (x) => x + "th pct", "no percentile")})`;
    $("sigRisk").innerHTML = li([
      `Volatility regime: ${volTxt("1h")}, ${volTxt("4h")}, ${volTxt("1d")} - each vs its own history`,
      `Typical daily move (1d ATR): ${show(r.atr_pct_1d, (x) => "±" + x.toFixed(2) + "%", "n/a")}`,
      `Downside to nearest 4h support with 2+ touches: ${pct(r.downside_to_support_pct)}`,
      `Room to nearest resistance: ${has(r.room_to_resistance_pct) ? pct(r.room_to_resistance_pct) + " (" + r.room_to_resistance_atr + " ATR)" : "n/a"}${has(r.room_to_resistance_atr) && r.room_to_resistance_atr < 0.5 ? " - price is pressing into resistance; buying here means buying into it" : ""}`,
      `News bias: ${plain(s.news.bias_label, "n/a")}${has(s.news.bias_score) ? " (" + signed(Number(s.news.bias_score)) + ")" : ""}`,
    ], "No risk data");
    $("sigNews").innerHTML = s.news.top_events.length
      ? s.news.top_events.map((e) => `<li><a href="${esc(safeUrl(e.link))}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a> <span class="muted">(${esc(e.publisher)}, ${esc(e.sentiment)}, ${esc(e.impact)})</span></li>`).join("")
      : '<li class="muted">No scored news events in the last 72 hours.</li>';
    const rec = s.record || {};
    const recTxt = !s.id ? "not stored yet" : rec.stored ? `stored as record #${s.id}`
      : `unchanged since record #${s.id} (${localTime(rec.stored_at)}); live numbers may differ slightly`;
    const vnote = (s.validation.note || (s.validation.validated ? "validated" : "not validated")).replace(/\.+$/, "");
    $("sigDiag").innerHTML = li([
      `Engine ${s.engine} · ${recTxt}`,
      `Agreement ${Math.round(s.agreement * 100)}% of directional evidence · directional weight ${Math.round((s.directional_weight ?? 0) * 100)}% of total (agreement only counts components with a direction)`,
      `Thresholds: buy ${signed(cfg.buy_threshold)}, strong ${signed(cfg.strong_threshold)}, sell ${signed(cfg.sell_threshold)} · minimum reward/risk ${plain(cfg.min_rr)} · entry rule ${plain(cfg.entry_mode, "standard")}`,
      `Indicators use the last closed candles (4h close ${usd((s.price_basis || {}).close_4h, 0)}, 1d close ${usd((s.price_basis || {}).close_1d, 0)}), not the live price`,
      `Validation: ${vnote}`,
    ], "No diagnostics");
    const warns = s.data_warnings || [];
    $("sigWarn").innerHTML = li(warns.map((w) => "⚠ " + w), "No data warnings.");
    $("sigWarnCount").textContent = `(${warns.length})`;
    $("sigConf").textContent = `${s.confidence} Validation: ${vnote}. Engine ${s.engine}.`;
    $("sigMeta").textContent = `Research signal · spot only · never executes trades · ${recTxt}`;
    if ($("sysEngine")) $("sysEngine").textContent = s.engine;
    $("signal").dataset.state = "ready";
  }

  async function loadSignalHistory() {
    try {
      const { history } = await getJson("/api/signal/history?limit=30");
      $("histTable").querySelector("tbody").innerHTML = history.length ? history.map((h) => `<tr>
        <td title="${esc(utcTime(h.created_at))}">${esc(localTime(h.created_at))}</td>
        <td><span class="sig-pill ${SIG_CLASS(h.signal)}">${esc(h.signal)}</span></td>
        <td><span class="sig-pill ${SIG_CLASS(h.setup)}">${esc(h.setup)}</span></td>
        <td class="mono">${esc(signed(Number(h.score)))}</td>
        <td class="mono">${esc(usd(h.price, 0))}</td><td class="muted">${esc(h.reason)}</td></tr>`).join("")
        : '<tr><td colspan="6" class="empty">No signals yet.</td></tr>';
      labelCells($("histTable"));
    } catch (_) { /* keep empty state */ }
  }

  // ---------------------------------------------------------------- backtest (Research view)
  let btTimer = null;
  const fmtD = (ms) => (has(ms) ? new Date(ms).toISOString().slice(0, 10) : "unknown date");

  // The holdout is single-use (0.9.3): the first counting run of ANY rule set spends it; a later rule set needs a new
  // holdout start after it (forward data only).
  function renderHoldout(h) {
    if (!h) return;
    const spent = h.status === "spent";
    $("btHoldoutStatus").textContent = `Holdout status: ${h.label}` + (spent ? "" : ` (reserved period from ${fmtD(h.start)})`);
    $("btHoldout").textContent = `Holdout runs made: ${h.made}` + (h.made
      ? ` · ${h.counted} counted for validation (only the first run in a holdout period counts, whatever its rules; later runs are stored, not valid for validation)`
      : " · the reserved holdout period is untouched");
    $("btPeriod").querySelector("option[value='holdout']").textContent = `holdout (from ${fmtD(h.start)})`;
    $("btPeriod").querySelector("option[value='design']").textContent = `design (to ${fmtD(h.start)})`;
    const box = $("btNewHoldout");
    box.hidden = !spent;
    if (spent) {
      const earliest = fmtD(h.earliest_new_start);
      $("btNewHoldoutText").textContent = `The holdout from ${fmtD(h.start)} is spent (${h.label}). To validate a different `
        + `rule set you need fresh data: set a new holdout start on or after ${earliest}. Only data from that day on is `
        + "used for the holdout; design runs then extend up to the new start.";
      const input = $("btNewHoldoutDate");
      input.min = earliest;
      if (!input.value || input.value < earliest) input.value = earliest;
    }
  }

  // Forward paper tests (research-1): read-only; the background monitor starts and advances them.
  async function loadForward() {
    let st;
    try { st = await getJson("/api/forward"); } catch (err) { $("fwdProgress").textContent = "Forward tests unavailable: " + err.message; return; }
    const sec = $("forward");
    if (st.state === "not_started") {
      $("fwdProgress").textContent = "Not started yet: starts at the next UTC midnight after the app's background monitor first runs (python app.py).";
      $("fwdBody").innerHTML = "";
      $("fwdFingerprint").textContent = "";
      sec.dataset.state = "ready";
      return;
    }
    const day = `Day ${st.days_elapsed} of ${st.target_days}`;
    $("fwdProgress").textContent = `${day} · started ${st.start_date} (UTC)` + (st.waiting_for ? ` · ${st.waiting_for}` : "");
    const pct = (x) => (x == null ? "no data yet" : fmtPct(x));
    $("fwdBody").innerHTML = st.strategies.map((s) => {
      const d = s.last_decision;
      const last = d ? `${esc(d.date)}: ${d.in_market ? "in" : "out"}` : "none yet";
      const ev = s.evaluation ? ` · ${esc(s.evaluation.verdict === "NOT YET" ? `verdict after ${st.target_days} days` : s.evaluation.verdict)}` : "";
      return `<tr data-rule="${esc(s.name)}"><td><b>${esc(s.label)}</b></td><td>${esc(s.role)}${ev}</td>
        <td>${esc(s.position)}${s.pending ? ` · ${esc(s.pending)}` : ""}</td><td class="mono">${esc(pct(s.net_return_pct))}</td>
        <td class="mono">${esc(s.max_drawdown_pct == null ? "no data yet" : fmtPct(-s.max_drawdown_pct))}</td>
        <td class="mono">${esc(s.trades)}</td><td class="mono">${esc(s.time_in_market_pct == null ? "no data yet" : s.time_in_market_pct + "%")}</td>
        <td>${last}</td></tr>`;
    }).join("");
    labelCells($("fwdTable"));
    const changed = st.strategies.some((s) => s.code_changed);
    const fp = (st.strategies[0] || {}).fingerprint_at_start || "";
    $("fwdFingerprint").textContent = `Rule code fingerprint at start: ${fp.slice(0, 16)}…`
      + (changed ? " · WARNING: the rule code changed since the start; results are no longer a clean test." : " · unchanged since the start.");
    sec.dataset.state = "ready";
  }

  async function loadBacktest() {
    const st = await loadBacktestStatus();
    if (st && !["starting", "downloading", "running"].includes(st.state)) await loadBacktestResult();
  }

  async function loadBacktestStatus() {
    try {
      const st = await getJson("/api/backtest/status");
      const h = st.history || {};
      $("btHistory").textContent = "History stored: " + ["1d", "4h", "1h"].map((tf) => `${tf} ${(h[tf] || {}).count || 0}`).join(" · ") + " candles";
      renderHoldout(st.holdout);
      if (!state.btDownloadSet) {
        // Tick "download" only when history is missing or more than a day old.
        const last = Math.min(...["1d", "4h", "1h"].map((tf) => (h[tf] || {}).last || 0));
        const fresh = last && Date.now() - last < 36 * 3600 * 1000 && ((h["1d"] || {}).count || 0) > 1500;
        $("btDownload").checked = !fresh;
        state.btDownloadSet = true;
      }
      const running = ["starting", "downloading", "running"].includes(st.state);
      $("btRun").disabled = running;
      $("btCancel").hidden = !running;
      $("btProgress").hidden = !running;
      $("btProgress").querySelector("i").style.width = `${Math.round((st.progress || 0) * 100)}%`;
      $("btMsg").textContent = st.state === "error" ? `Error: ${st.error}` : (st.message || "");
      $("btMsg").className = st.state === "error" ? "kpi-warn" : "muted small";
      $("backtest").dataset.state = st.state;
      if (running) {
        clearTimeout(btTimer);
        btTimer = setTimeout(loadBacktestStatus, 2000);
      } else if (st.state === "done") {
        loadBacktestResult();
        loadSignal();
      }
      return st;
    } catch (err) { $("btMsg").textContent = err.message; }
  }

  function renderBacktestEmpty() {
    $("btResult").innerHTML = `<div class="empty cta">
      <p><b>No backtest yet.</b> Download history, then run a 4-year full/standard backtest (about 8 min).</p>
      <button class="btn" type="button" id="btCta">Download history and run (4 years · full · standard)</button></div>`;
    $("btCta").onclick = () => {
      $("btYears").value = "4"; $("btEntryMode").value = "standard"; $("btPeriod").value = "full"; $("btDownload").checked = true;
      $("btRun").click();
    };
    $("btExport").hidden = true;
  }

  async function loadBacktestResult() {
    let r;
    try {
      // A first start has no run yet: ask the list first so the console shows no 404.
      const { runs } = await getJson("/api/backtest/runs");
      if (!runs.length) { renderBacktestEmpty(); return; }
      r = await getJson(`/api/backtest/${runs[0].id}`);
    } catch (_) { return; }
    const s = r.strategy, b = r.baselines, wf = r.walk_forward;
    const row = (name, m) => `<tr><td><b>${esc(name)}</b></td><td class="mono">${esc(fmtPct(m.net_return_pct))}</td>
      <td class="mono">${esc(fmtPct(has(m.max_drawdown_pct) ? -m.max_drawdown_pct : null))}</td><td class="mono">${esc(plain(m.return_over_max_dd))}</td>
      <td class="mono">${esc(plain(m.trades, "0"))}</td><td class="mono">${esc(show(m.exposure_pct, (x) => x + "%"))}</td></tr>`;
    const rnd = b.random_entries || {};
    const tooFew = r.criteria.some((c) => c.name === "Enough trades" && !c.passed);
    const passed = r.criteria.filter((c) => c.passed).length;
    const crit = r.criteria.map((c) => {
      const moot = tooFew && c.passed && c.name !== "Enough trades";
      return `<li class="${moot ? "moot" : c.passed ? "pass" : "fail"}"><b>${moot ? "–" : c.passed ? "✓" : "✗"} ${esc(c.name)}</b>${esc(c.detail)}${moot ? "<br><i>Not meaningful: too few trades to judge.</i>" : ""}</li>`;
    }).join("");
    const hr = r.hit_rate || {};
    if (r.id) { const ex = $("btExport"); ex.dataset.query = `run=${r.id}`; ex.hidden = false; renderExport(ex); }
    const bh = b.buy_and_hold || {};
    const settings = r.settings || {};
    const kv = (o) => Object.entries(o || {}).filter(([, v]) => typeof v !== "object" || v === null)
      .map(([k, v]) => `<li><b>${esc(k.replaceAll("_", " "))}</b>: ${esc(plain(v, "not set"))}</li>`).join("");
    $("btResult").innerHTML = `
      ${r.period_badge || r.design_badge ? `<div class="verdict design">${esc(r.period_badge || r.design_badge)}</div>` : ""}
      <div class="verdict ${r.validated ? "ok" : "no"}">${esc(r.verdict)}
        <small>Run #${r.id} · entry rule <b>${esc(r.entry_mode || "standard")}</b> · period <b>${esc(r.period_kind || (r.period && r.period.kind) || "full")}</b> · ${fmtD(r.period.start)} → ${fmtD(r.period.end)} (${r.period.years} years, ${Number(r.period.bars).toLocaleString()} bars)</small></div>
      <div class="bt-tiles">
        <div class="tile"><small>Net return (after costs)</small><b class="mono ${s.net_return_pct >= 0 ? "up" : "down"}">${esc(fmtPct(s.net_return_pct))}</b><span class="muted">buy &amp; hold ${esc(fmtPct(bh.net_return_pct))}</span></div>
        <div class="tile"><small>Max drawdown</small><b class="mono">${esc(fmtPct(has(s.max_drawdown_pct) ? -s.max_drawdown_pct : null))}</b><span class="muted">buy &amp; hold ${esc(fmtPct(has(bh.max_drawdown_pct) ? -bh.max_drawdown_pct : null))}</span></div>
        <div class="tile"><small>Trades</small><b class="mono">${s.trades ? esc(s.trades) : MISSING.trades}</b><span class="muted">win rate ${esc(show(s.win_rate_pct, (x) => x + "%", "n/a"))}</span></div>
        <div class="tile"><small>Criteria passed</small><b class="mono ${r.validated ? "up" : "down"}">${passed}/${r.criteria.length}</b><span class="muted">${tooFew ? "too few trades to judge" : r.validated ? "all passed" : "not validated"}</span></div>
      </div>
      <h3>Equity curves (hypothetical, after costs)</h3><div id="btChart" class="bt-chart" role="figure" aria-label="Hypothetical equity curves: signal engine, buy and hold, EMA 50/200"></div>
      <details class="fold" id="fdBtCriteria"><summary>Criteria &amp; baselines (${passed}/${r.criteria.length} passed)</summary>
        <ul class="criteria">${crit}</ul>
        <div class="table-wrap"><table class="table stack"><thead><tr><th>Out-of-sample</th><th>Net return</th><th>Max drawdown</th><th>Return / DD</th><th>Trades</th><th>Exposure</th></tr></thead><tbody>
          ${row("Signal engine (default weights)", s)}
          ${wf.chosen_on_train ? row("Weights chosen on prior year", wf.chosen_on_train) : ""}
          ${row("Buy & hold", b.buy_and_hold)}${row("EMA 50/200 crossover", b.ema_50_200)}
          <tr><td><b>Random entries</b> <span class="muted">(${rnd.runs || 0} runs)</span></td><td class="mono" colspan="5">median ${esc(fmtPct(rnd.median_return_pct))} · 10th–90th pct ${esc(fmtPct(rnd.p10_return_pct))} to ${esc(fmtPct(rnd.p90_return_pct))}</td></tr>
        </tbody></table></div>
        <div class="facts">
          <div class="fact"><small>Profit factor · win rate</small><b class="mono">${esc(plain(s.profit_factor, "n/a"))} · ${esc(show(s.win_rate_pct, (x) => x + "%", "n/a"))}</b></div>
          <div class="fact"><small>Avg trade · avg R</small><b class="mono">${esc(fmtPct(s.avg_trade_pct, "n/a"))} · ${esc(show(s.avg_r_multiple, (x) => x + "R", "n/a"))}</b></div>
          <div class="fact"><small>Costs paid (sum)</small><b class="mono">${esc(show(s.costs_paid_pct, (x) => x + "%", "n/a"))}</b><div class="muted">${esc(Object.entries(s.exit_reasons || {}).map(([k, v]) => k + " " + v).join(", ") || "no exits")}</div></div>
          <div class="fact"><small>BUY hit rate (precision · base rate)</small><b class="mono">${esc(plain(hr.precision, "n/a"))} · ${esc(plain(hr.base_rate, "n/a"))}</b><div class="muted">balanced accuracy ${esc(plain(hr.balanced_accuracy, "n/a"))}</div></div>
        </div>
      </details>
      ${r.funnel ? `<details class="fold" id="fdBtFunnel"><summary>Signal funnel: why so few trades?</summary>${funnelHtml(r.funnel)}</details>` : ""}
      <details class="fold" id="fdBtWindows"><summary>Walk-forward windows (${wf.windows_with_trades}/${wf.windows_total} with trades)</summary>
        <div class="table-wrap"><table class="table stack"><thead><tr><th>Window</th><th>Default</th><th>Trades</th><th>Chosen weights</th><th>Chosen</th><th>Buy &amp; hold</th></tr></thead><tbody>
          ${wf.windows.map((w) => `<tr><td>${fmtD(w.start)} → ${fmtD(w.end)}</td><td class="mono">${esc(fmtPct(w.default_return_pct))}</td><td class="mono">${w.default_trades}</td>
            <td>${esc(w.chosen_weights)}${w.chosen_reason ? `<div class="muted small">${esc(w.chosen_reason)}</div>` : ""}</td><td class="mono">${esc(fmtPct(w.chosen_return_pct))}</td><td class="mono">${esc(fmtPct(w.buy_hold_return_pct))}</td></tr>`).join("") || '<tr><td colspan="6" class="empty">No windows</td></tr>'}
        </tbody></table></div>
        <p class="muted small">${esc(wf.note)}</p></details>
      <details class="fold" id="fdBtTrades"><summary>Simulated trades (last ${Math.min(30, r.trades.length)})</summary><div class="table-wrap"><table class="table stack"><thead><tr><th>Entry</th><th>Type</th><th>Exit</th><th>Entry px</th><th>Exit px</th><th>Reason</th><th>Net</th><th>R</th></tr></thead><tbody>
        ${r.trades.slice(-30).reverse().map((t) => `<tr><td>${fmtD(t.entry_time)}</td><td>${esc(t.entry_type || "standard")}</td><td>${fmtD(t.exit_time)}</td><td class="mono">${esc(fmtUsd(t.entry, 0))}</td><td class="mono">${esc(fmtUsd(t.exit, 0))}</td>
          <td>${esc(t.reason)}</td><td class="mono ${t.ret >= 0 ? "up" : "down"}">${esc(fmtPct(t.ret * 100))}</td><td class="mono">${esc(show(t.r_multiple, (x) => x.toFixed(2)))}</td></tr>`).join("") || `<tr><td colspan="8" class="empty">${MISSING.trades}</td></tr>`}
      </tbody></table></div></details>
      <details class="fold" id="fdBtLimits"><summary>Limitations of this test</summary><ul class="ev">${r.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>
      <details class="fold" id="fdBtSettings"><summary>Run settings</summary>
        <ul class="ev"><li><b>engine</b>: ${esc(r.engine)}</li><li><b>settings fingerprint</b>: ${esc(r.config_hash)}</li>${kv(settings.backtest)}</ul>
        ${settings.criteria ? `<h3 class="sub">Validation criteria</h3><ul class="ev">${kv(settings.criteria)}</ul>` : ""}
        ${settings.engine ? `<h3 class="sub">Engine settings</h3><ul class="ev">${kv(settings.engine)}</ul>` : ""}
      </details>`;
    $("btResult").querySelectorAll("table.stack").forEach(labelCells);
    applyFolds($("btResult"));
    if (typeof Plotly !== "undefined" && r.equity && r.equity.time.length) {
      const x = r.equity.time.map((t) => new Date(t));
      const line = (y, name, color, dash) => ({ type: "scatter", mode: "lines", x, y, name, line: { color, width: 1.6, dash } });
      Plotly.newPlot("btChart", [line(r.equity.strategy, "Signal engine", css("--accent")),
        line(r.equity.buy_and_hold, "Buy & hold", css("--muted"), "dot"), line(r.equity.ema_50_200, "EMA 50/200", css("--info"), "dash")],
        { paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: css("--text"), size: 11 },
          margin: { l: 40, r: 10, t: 10, b: 30 }, legend: { orientation: "h", y: 1.1 },
          yaxis: { type: "log", gridcolor: css("--line"), title: "growth of 1" }, xaxis: { gridcolor: css("--line") } },
        { responsive: true, displaylogo: false });
    } else {
      $("btChart").innerHTML = '<div class="empty">No equity curve stored for this run.</div>';
    }
  }

  function funnelHtml(f) {
    const n = (v) => show(v, (x) => Number(x).toLocaleString(), "n/a");
    const blocked = Object.entries(f.blocked_by_safeguard || {}).map(([k, v]) => `${k.replaceAll("_", " ")} ${n(v)}`).join(", ") || "none";
    const wi = f.what_if_min_rr || {};
    const lr = f.low_rr_values || {};
    const sp = f.score_percentiles || {};
    const share = (v) => show(v, (x) => Math.round(x * 100) + "%", "n/a");
    return `<div class="table-wrap"><table class="table funnel"><tbody>
        <tr><td>4h bars replayed</td><td class="mono">${n(f.bars)}</td><td class="muted">score median ${esc(plain(sp.p50, "n/a"))}, 90th pct ${esc(plain(sp.p90, "n/a"))}, 99th pct ${esc(plain(sp.p99, "n/a"))}, max ${esc(plain(sp.max, "n/a"))}</td></tr>
        <tr><td>Bars with score at or above the BUY threshold</td><td class="mono">${n(f.bars_score_at_or_above_buy)}</td><td class="muted">sell side: ${n(f.bars_score_at_or_below_sell)}</td></tr>
        <tr><td>Raw buy setups</td><td class="mono">${n(f.raw_buy_setups)}</td><td></td></tr>
        <tr><td>– dropped: reward/risk below the minimum</td><td class="mono down">${n(f.downgraded_low_rr)}</td><td class="muted">their R/R: median ${esc(plain(lr.median, "n/a"))}, 75th pct ${esc(plain(lr.p75, "n/a"))}, ${share(lr["share_at_or_above_1.0"])} at or above 1.0, ${share(lr.share_no_room)} no room at all (R/R ≤ 0)</td></tr>
        <tr><td>– dropped: no plan possible</td><td class="mono">${n(f.downgraded_no_plan)}</td><td></td></tr>
        <tr><td>Buy setups after the reward/risk check</td><td class="mono">${n(f.buy_setups_after_rr)}</td><td class="muted">blocked by safeguards: ${esc(blocked)}</td></tr>
        <tr><td>Tradable buy bars</td><td class="mono up">${n(f.tradable_buy_bars)}</td><td class="muted">${esc(f.note)}</td></tr>
        ${f.pending_orders && (f.pending_order_bars || f.pending_orders.created) ? `<tr><td>Clean pending-order bars</td><td class="mono">${n(f.pending_order_bars)}</td><td class="muted">pending orders: ${
          Object.entries(f.pending_orders).map(([k, v]) => `${k.replaceAll("_", " ")} ${n(v)}`).join(", ")}</td></tr>` : ""}
        <tr><td>Trades taken</td><td class="mono">${n(f.trades)}</td><td class="muted">${f.trades_by_entry ? Object.entries(f.trades_by_entry).map(([k, v]) => `${esc(k)} ${n(v)}`).join(", ") : ""}</td></tr>
      </tbody></table></div>
      <p class="muted small">Diagnostic only, not a backtest: raw buy bars that would pass reward/risk at a lower minimum with no safeguard flagged —
        ≥1.5: ${n(wi["min_rr_1.5"])}, ≥1.2: ${n(wi["min_rr_1.2"])}, ≥1.0: ${n(wi["min_rr_1.0"])}, ≥0.8: ${n(wi["min_rr_0.8"])}.</p>`;
  }

  // ---------------------------------------------------------------- risk calculator
  async function calcRisk(e) {
    if (e) e.preventDefault();
    try {
      const r = await fetch("/api/risk/size", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ portfolio_value: $("rkPortfolio").value, entry: $("rkEntry").value, stop: $("rkStop").value,
          settings: { risk_pct: Number($("rkRisk").value) } }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Calculation failed");
      $("riskOut").innerHTML = `<div class="big mono">${esc(d.quantity_btc)} BTC</div>
        <div class="muted">≈ ${esc(usd(d.notional))} (${esc(d.exposure_pct)}% of portfolio) · stop ${esc(d.stop_distance_pct)}% below entry</div>
        <div class="risk-loss">Max estimated loss at the stop: <b class="down mono">${esc(usd(d.max_loss_at_stop))}</b> (${esc(d.max_loss_pct)}% of portfolio, ${esc(d.costs_included)})</div>
        <ul>${[...d.notes, ...d.warnings].map((w) => `<li>${esc(w)}</li>`).join("")}</ul><p class="disclaimer">${esc(d.disclaimer)}</p>`;
    } catch (err) { $("riskOut").innerHTML = `<p class="kpi-warn">${esc(err.message)}</p>`; }
  }

  function riskFromPlan() {
    const s = state.lastSignal;
    const price = s && s.price && s.price.value;
    if (price) $("rkEntry").value = Math.round(price);
    if (s && s.plan) {
      $("rkStop").value = Math.round(s.plan.stop);
      $("ppStop").value = Math.round(s.plan.stop);
      $("ppTarget").value = Math.round(s.plan.targets[0].price);
    } else if (price) {
      const atrPct = (s.risk && s.risk.atr_pct_4h) || 1;
      $("rkStop").value = Math.round(price * (1 - 2 * atrPct / 100));
    }
  }

  // ---------------------------------------------------------------- paper trading
  async function loadPaper() {
    try { renderPaper(await getJson("/api/paper")); } catch (err) { $("paperMsg").textContent = err.message; }
  }

  function renderPaper(st) {
    $("paperFacts").innerHTML = `
      <div class="fact"><small>Simulated equity</small><b class="mono">${esc(usd(st.equity, 2, "No price yet"))}</b><div class="${(st.total_pnl || 0) >= 0 ? "up" : "down"}">${esc(fmtPct(st.total_pnl_pct, "0.00%"))} since start (${esc(usd(st.start_balance, 0))})</div></div>
      <div class="fact"><small>Cash · BTC</small><b class="mono">${esc(usd(st.cash))} · ${esc(st.btc)}</b><div class="muted">exposure ${esc(st.exposure_pct)}%</div></div>
      <div class="fact"><small>Position</small><b class="mono">${st.btc > 0 ? "avg fill " + esc(usd(st.avg_fill ?? st.avg_entry)) : "flat"}</b>${st.btc > 0 ? `<div class="muted">cost basis incl. fees ${esc(usd(st.cost_basis ?? st.avg_entry))}</div>` : ""}<div class="muted">${st.btc > 0 ? `stop ${esc(usd(st.stop, 0, "none"))} · target ${esc(usd(st.target, 0, "none"))} · unrealised ${esc(money(st.unrealized_pnl))}` : "no open position"}</div></div>
      <div class="fact"><small>Today · limits</small><b class="mono ${st.daily_limit_reached ? "down" : ""}">loss ${esc(st.daily_loss_pct)}% / ${esc(st.settings.daily_loss_limit_pct)}%</b><div class="muted">${st.daily_limit_reached ? "Daily limit reached: buys blocked" : `max exposure ${esc(st.settings.max_exposure_pct)}%`}${st.loss_streak ? ` · ${st.loss_streak} losses in a row` : ""}${st.daily_loss_basis ? ` · loss measured ${esc(st.daily_loss_basis)}` : ""}</div></div>`;
    $("paperOrders").querySelector("tbody").innerHTML = st.orders.length ? st.orders.slice(0, 20).map((o) => `<tr>
      <td>${esc(localTime(o.created_at))}</td><td><span class="sig-pill ${o.side === "BUY" ? "buy" : "sell"}">SIM ${esc(o.side)}</span></td>
      <td class="mono">${Number(o.qty).toFixed(6)}</td><td class="mono">${esc(usd(o.price))}</td><td class="mono">${esc(usd(o.fee))}</td>
      <td class="mono ${!has(o.realized_pnl) ? "" : o.realized_pnl >= 0 ? "up" : "down"}">${esc(money(o.realized_pnl, "open"))}</td>
      <td class="muted">${esc(o.reason)}</td></tr>`).join("") : '<tr><td colspan="7" class="empty">No simulated orders yet.</td></tr>';
    labelCells($("paperOrders"));
    if (st.last_background_exit && !$("paperMsg").textContent) $("paperMsg").textContent = "Background check: " + st.last_background_exit.message;
  }

  async function paperPost(url, body) {
    $("paperMsg").textContent = "";
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Request failed");
      $("paperMsg").textContent = d.message || "";
      if (d.state) renderPaper(d.state); else loadPaper();
    } catch (err) { $("paperMsg").textContent = err.message; }
  }

  function initPhase6() {
    $("btRun").onclick = async () => {
      try {
        const r = await fetch("/api/backtest/run", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ years: Number($("btYears").value), download: $("btDownload").checked,
            entry_mode: $("btEntryMode").value, period: $("btPeriod").value }) });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || "Could not start");
        loadBacktestStatus();
      } catch (err) { $("btMsg").textContent = err.message; }
    };
    $("btCancel").onclick = () => fetch("/api/backtest/cancel", { method: "POST" }).then(loadBacktestStatus);
    $("btNewHoldoutSet").onclick = async () => {
      const msg = $("btNewHoldoutMsg");
      try {
        const d = await sendJson("POST", "/api/backtest/holdout_start", { start: $("btNewHoldoutDate").value });
        msg.textContent = `New holdout period from ${fmtD(d.holdout.start)}.`;
        renderHoldout(d.holdout);
      } catch (err) { msg.textContent = "Not set: " + err.message; }
    };
    $("riskForm").addEventListener("submit", calcRisk);
    $("rkFromPlan").onclick = riskFromPlan;
    $("paperForm").addEventListener("submit", (e) => {
      e.preventDefault();
      paperPost("/api/paper/order", { side: "BUY", usd: $("ppUsd").value, stop: $("ppStop").value, target: $("ppTarget").value });
    });
    $("ppSell").onclick = () => paperPost("/api/paper/order", { side: "SELL" });
    $("ppReset").onclick = () => { if (confirm("Reset the SIMULATED account to 10,000 USDT and delete its order history?")) paperPost("/api/paper/reset", { start_balance: 10000 }); };
  }

  // ---------------------------------------------------------------- assistant (Phase 7)
  // Model text is untrusted: it is always escaped and rendered as plain paragraphs and bullets, never as HTML.
  function plainTextHtml(text) {
    const out = [];
    let list = [];
    const flush = () => { if (list.length) { out.push(`<ul>${list.join("")}</ul>`); list = []; } };
    String(text || "").split(/\r?\n/).forEach((line) => {
      const m = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (m) { list.push(`<li>${esc(m[1])}</li>`); return; }
      flush();
      if (line.trim()) out.push(`<p>${esc(line)}</p>`);
    });
    flush();
    return out.join("");
  }

  function chatMsgHtml(m) {
    if (m.role === "user") {
      return `<div class="msg user"><div class="msg-meta">You · ${esc(localTime(m.created_at))}</div><div class="msg-body">${plainTextHtml(m.content)}</div></div>`;
    }
    const withheld = m.withheld || (m.error && String(m.error).startsWith("withheld"));
    const mode = m.label ? esc(m.label)
      : m.mode === "ai" ? `AI rewrite (local ${esc(m.model || "model")}) - may contain mistakes; check the Sources`
      : withheld ? "AI withheld - rule answer" : "Rule-based (instant)";
    const src = (m.sources || []).map((s) => {
      const link = s.link && /^https?:\/\//i.test(s.link) ? ` <a href="${esc(s.link)}" target="_blank" rel="noopener noreferrer">open</a>` : "";
      return `<li><b>[${esc(s.id)}]</b> ${esc(s.text)} <span class="muted">· ${esc(s.source)}${s.time ? ", " + esc(s.time) : ""}</span>${link}</li>`;
    }).join("");
    return `<div class="msg bot"><div class="msg-meta"><span class="mode ${m.mode === "ai" ? "ai" : withheld ? "withheld" : "rule"}">${mode}</span>${m.title ? " · " + esc(m.title) : ""} · ${esc(localTime(m.created_at))}</div>
      <div class="msg-body">${plainTextHtml(m.content)}</div>
      ${m.warning ? `<div class="msg-warn">⚠ ${esc(m.warning)}</div>` : ""}
      ${src ? `<details class="msg-src" open><summary>Sources (${(m.sources || []).length})</summary><ol>${src}</ol></details>` : ""}</div>`;
  }

  function appendChat(m) {
    const log = $("chatLog");
    if (log.querySelector(".empty")) log.innerHTML = "";
    log.insertAdjacentHTML("beforeend", chatMsgHtml(m));
    log.scrollTop = log.scrollHeight;
  }

  async function loadAssistantStatus() {
    try {
      const st = await getJson("/api/assistant/status");
      state.aiReady = st.mode === "ai";
      const badge = $("aiBadge");
      badge.textContent = state.aiReady ? `Local AI: ${st.model} connected` : "Local AI offline - rule-based answers";
      badge.className = "tag ai-badge " + (state.aiReady ? "on" : "off");
      const hint = $("aiHint");
      hint.hidden = state.aiReady;
      hint.textContent = !st.available ? `Optional free local AI: ${st.install_hint}` : !st.model_installed ? `Model missing: ollama pull ${st.model}` : "";
    } catch (_) {
      state.aiReady = false;
      $("aiBadge").textContent = "Local AI offline - rule-based answers";
    }
    $("assistant").dataset.state = "ready";
  }

  async function loadChatHistory() {
    try {
      const { messages } = await getJson("/api/assistant/history?limit=50");
      $("chatLog").innerHTML = messages.length ? "" : '<div class="empty">Ask a question about the app\'s data, or pick a suggestion.</div>';
      messages.forEach((m) => appendChat(m));
    } catch (_) { /* keep empty state */ }
  }

  // Long loader (live seconds counter + Cancel) only when the question is routed to the local model.
  function chatBusy(on, longWait) {
    const sec = $("assistant");
    ["chatSend", "chatExplain", "chatClear"].forEach((id) => { $(id).disabled = on; });
    document.querySelectorAll("#chatChips button").forEach((b) => { b.disabled = on; });
    clearInterval(state.chatTimer);
    const wait = $("chatWait"), st = $("chatStatus");
    st.className = "muted small chat-status";
    wait.hidden = !(on && longWait);
    $("chatCancel").hidden = !(on && longWait);
    if (!on) return;
    sec.dataset.chat = "thinking";
    if (longWait) {
      const t0 = Date.now();
      const tick = () => { st.textContent = `Thinking locally… usually 1-3 min on CPU (${Math.round((Date.now() - t0) / 1000)} s)`; };
      tick();
      state.chatTimer = setInterval(tick, 1000);
    }
  }

  function chatError(msg) {
    clearInterval(state.chatTimer);
    $("chatWait").hidden = false;
    $("chatCancel").hidden = true;
    const st = $("chatStatus");
    st.className = "chat-status kpi-warn";
    st.textContent = msg;
    $("assistant").dataset.chat = "error";
  }

  async function routeOf(msg) {
    if (!state.aiReady) return { uses_ai: false };
    try {
      const r = await fetch("/api/assistant/route", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: msg }) });
      return await r.json();
    } catch (_) { return { uses_ai: true }; }
  }

  async function askServer(url, options, title) {
    state.chatAbort = new AbortController();
    try {
      const r = await fetch(url, { ...options, signal: state.chatAbort.signal });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      appendChat({ role: "assistant", title, content: d.answer, mode: d.mode, model: d.model, sources: d.sources, warning: d.warning,
        withheld: d.withheld, label: d.label, created_at: d.created_at });
      chatBusy(false);
      $("assistant").dataset.chat = "answered";
    } catch (err) {
      chatBusy(false);
      chatError(err.name === "AbortError" ? "Cancelled. The local model may still finish in the background; if the next question says busy, wait a moment." : err.message);
    }
  }

  async function sendChat(text) {
    const msg = String(text || "").trim();
    if (!msg) return;
    appendChat({ role: "user", content: msg, created_at: Date.now() });
    $("chatInput").value = ""; updateCount();
    const route = await routeOf(msg);
    chatBusy(true, Boolean(route.uses_ai));
    await askServer("/api/assistant/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: msg }) });
  }

  async function explainSignal() {
    // The server decides: AI rewrite only when Settings → Assistant allows it and no AI request is still running.
    const route = await routeOf("Explain the signal");
    chatBusy(true, Boolean(route.uses_ai));
    await askServer(`/api/assistant/explain?ai=${state.aiReady ? 1 : 0}`, { headers: { Accept: "application/json" } }, "Explain current signal");
  }

  function updateCount() {
    const max = Number($("chatInput").getAttribute("maxlength")) || 1000;
    $("chatCount").textContent = `${$("chatInput").value.length} / ${max}`;
  }

  function initAssistant() {
    $("chatForm").addEventListener("submit", (e) => { e.preventDefault(); sendChat($("chatInput").value); });
    $("chatInput").addEventListener("input", updateCount);
    $("chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat($("chatInput").value); } });
    $("chatExplain").onclick = explainSignal;
    $("chatCancel").onclick = () => { if (state.chatAbort) state.chatAbort.abort(); };
    $("chatClear").onclick = async () => {
      await fetch("/api/assistant/clear", { method: "POST" });
      $("chatLog").innerHTML = '<div class="empty">Ask a question about the app\'s data, or pick a suggestion.</div>';
      $("assistant").dataset.chat = "idle";
    };
    $("chatChips").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.explain) explainSignal(); else sendChat(b.dataset.q);
    });
    updateCount();
  }

  // ---------------------------------------------------------------- exports
  function renderExport(el) {
    const q = el.dataset.query ? "&" + el.dataset.query : "";
    const ds = el.dataset.dataset;
    el.innerHTML = `<span class="muted small">Export</span>
      <a class="btn ghost small-btn" href="/api/export/${ds}?format=csv${esc(q)}" download>CSV</a>
      <a class="btn ghost small-btn" href="/api/export/${ds}?format=json${esc(q)}" download>JSON</a>`;
  }

  // ---------------------------------------------------------------- watchlist
  async function loadWatchlist() {
    const card = $("watchlist");
    try {
      const w = await getJson("/api/watchlist");
      const rows = w.rows || [];
      $("wlTable").querySelector("tbody").innerHTML = rows.length ? rows.map((r) => {
        const ch = r.change_24h_pct;
        const cls = !has(ch) ? "" : ch >= 0 ? "up" : "down";
        return `<tr><td><b>${esc(r.symbol)}</b></td>
          <td class="mono">${esc(fmtUsd(r.price, r.price < 10 ? 4 : 2, MISSING.nodata))}</td>
          <td class="mono ${cls}">${esc(fmtPct(ch, MISSING.nodata))}</td>
          <td class="small">${esc(r.provider)} · ${r.observed_at ? esc(localTime(r.observed_at)) : esc(r.error || "no data")}${r.is_stale ? ' <span class="tag stale-tag">stale</span>' : ""}</td></tr>`;
      }).join("") : `<tr><td colspan="4" class="empty">${esc((w.warnings || [])[0] || "No watchlist data.")}</td></tr>`;
      labelCells($("wlTable"));
      $("wlMeta").textContent = `Binance spot · display only, no signals · updated ${ago(w.fetched_at)}`;
      $("wlWarn").textContent = (w.warnings || []).join(" ") + (w.is_stale ? " Data is stale." : "");
      card.dataset.state = w.status === "unavailable" ? "error" : "ready";
    } catch (err) {
      $("wlTable").querySelector("tbody").innerHTML = `<tr><td colspan="4" class="empty">Watchlist unavailable: ${esc(err.message)}</td></tr>`;
      card.dataset.state = "error";
    }
  }

  // ---------------------------------------------------------------- economic calendar
  async function loadCalendar() {
    const card = $("calendar");
    try {
      const c = await getJson("/api/calendar");
      const tz = window.APP.displayTz;
      const localIn = (ms) => new Date(ms).toLocaleString(undefined, { timeZone: tz, dateStyle: "medium", timeStyle: "short" });
      const link = (href, text) => `<a href="${esc(safeUrl(href))}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
      $("calList").innerHTML = c.events.length ? c.events.map((e) => `<li><div><b>${esc(e.event)}</b> <span class="tag">${esc(e.kind)}</span></div>
          <div class="mono small">${esc(utcTime(e.at))} · ${esc(localIn(e.at))} (${esc(tz)})</div>
          <div class="muted small">${esc(e.time_note || "")} · ${link(e.link, e.source)}</div></li>`).join("")
        : `<li class="empty">${esc(c.empty_reason || "No events.")}</li>`;
      const n = c.next_fomc;
      $("calNext").textContent = n && !c.events.some((e) => e.id === n.id)
        ? `Next FOMC decision: ${utcTime(n.at)} · ${localIn(n.at)} (${tz}) · meeting ${n.meeting}. ${n.time_note}.` : "";
      $("calSources").innerHTML = c.sources.map((s) => `<li>${link(s.link, s.source)} - <b>${esc(s.status)}</b>${s.fetched_at ? `, fetched ${esc(ago(s.fetched_at))}` : ""}${s.error ? `<br><span class="muted small">${esc(s.error)}</span>` : ""}</li>`).join("")
        + `<li class="muted small">${esc(c.note)}</li>`;
      $("calMeta").textContent = `Verified US events · next ${c.window_days} days` + ((c.warnings || []).length ? " · " + c.warnings.join(" ") : "");
      const next = c.events[0] || n;
      $("ovNextEvent").innerHTML = next
        ? `<p class="next-name"><b>${esc(next.event || "FOMC rate decision")}</b></p>
           <p class="mono small">${esc(utcTime(next.at))}</p><p class="small">${esc(localIn(next.at))} (${esc(tz)}) · ${esc(ago(next.at).replace(" ago", "").replace(/^0s$/, "now"))}${next.at > Date.now() ? " from now" : ""}</p>
           <p class="muted small">${esc(next.time_note || "")}${next.link ? " · " + link(next.link, next.source || "source") : ""}</p>`
        : `<p class="empty">${esc(c.empty_reason || "No verified events in the calendar window.")}</p>`;
      card.dataset.state = "ready";
    } catch (err) {
      $("calList").innerHTML = `<li class="empty">Calendar unavailable: ${esc(err.message)}</li>`;
      $("ovNextEvent").innerHTML = `<p class="empty">Calendar unavailable: ${esc(err.message)}</p>`;
      card.dataset.state = "error";
    }
  }

  // ---------------------------------------------------------------- alert centre
  const RULE_NAMES = { price_above: "Price above", price_below: "Price below", big_move: "Big move", significant_news: "Significant news",
    high_volatility: "High volatility", signal_change: "Signal change", source_failure: "Source failure", risk_limit: "Risk limit (SIMULATED)" };
  const notifyOk = () => "Notification" in window && Notification.permission === "granted" && state.browserNotify;

  function alertHtml(a) {
    const links = (a.links || []).map((l) => {
      const ext = /^https?:\/\//i.test(l);
      let text = l;
      if (ext) { try { text = new URL(l).hostname; } catch (_) { /* keep raw */ } }
      return `<a href="${esc(safeUrl(l))}"${ext ? ' target="_blank" rel="noopener noreferrer"' : ""}>${esc(text)}</a>`;
    }).join(" · ");
    const nums = has(a.value) ? ` · value ${esc(a.value)}${has(a.threshold) ? " / threshold " + esc(a.threshold) : ""}` : "";
    return `<li class="alert ${esc(a.severity)}${a.acknowledged_at ? " acked" : ""}">
      <div class="al-head"><span class="sev">${esc(a.severity)}</span><b>${esc(a.title)}</b>
        ${a.acknowledged_at ? '<span class="muted small">acknowledged</span>' : `<button class="btn ghost small-btn" data-ack="${a.id}">Acknowledge</button>`}</div>
      <div>${esc(a.message)}</div>
      <div class="muted small">Why: ${esc(a.reason)}${nums} · source ${esc(a.source)}</div>
      <div class="muted small">${esc(utcTime(a.created_at))} · ${esc(localTime(a.created_at))}${links ? " · " + links : ""}</div></li>`;
  }

  async function loadAlerts() {
    const card = $("alerts");
    try {
      const d = await getJson(`/api/alerts?limit=50&unread=${$("alUnread").checked ? 1 : 0}`);
      $("alList").innerHTML = d.alerts.length ? d.alerts.map(alertHtml).join("") : '<li class="empty">No alerts yet. Rules are checked every 60 s.</li>';
      const latest = d.alerts.slice(0, 3);
      $("ovAlertList").innerHTML = latest.length ? latest.map((a) => `<li class="alert ${esc(a.severity)}${a.acknowledged_at ? " acked" : ""}">
          <div class="al-head"><span class="sev">${esc(a.severity)}</span><b>${esc(a.title)}</b></div>
          <div class="muted small">${esc(ago(a.created_at))}${a.acknowledged_at ? " · acknowledged" : ""}</div></li>`).join("")
        : '<li class="empty">No alerts yet.</li>';
      $("ovAlertsMeta").textContent = `${d.unread} unread · newest first`;
      const badge = $("bellBadge"), nav = $("navAlertBadge");
      badge.hidden = !d.unread; nav.hidden = !d.unread;
      badge.textContent = d.unread > 99 ? "99+" : d.unread;
      nav.textContent = badge.textContent;
      $("bellBtn").setAttribute("aria-label", `Alerts: ${d.unread} unread`);
      $("alMeta").textContent = `${d.unread} unread · ` + (d.monitor_running ? "monitor running, checks every 60 s" : "monitor not running (it starts with python app.py)");
      const newest = d.alerts[0];
      if (newest && state.lastAlertId != null && newest.id > state.lastAlertId && notifyOk()) {
        d.alerts.filter((a) => a.id > state.lastAlertId && !a.acknowledged_at).slice(0, 3).forEach((a) => {
          try { new Notification(a.title, { body: a.message, tag: "btc-alert-" + a.id }); } catch (_) { /* never break the page */ }
        });
      }
      if (newest) state.lastAlertId = Math.max(state.lastAlertId ?? 0, newest.id);
      else if (state.lastAlertId == null) state.lastAlertId = 0;
      card.dataset.state = "ready";
    } catch (err) {
      $("alList").innerHTML = `<li class="empty">Alerts unavailable: ${esc(err.message)}</li>`;
      card.dataset.state = "error";
    }
  }

  async function loadAlertRules() {
    try {
      const { rules } = await getJson("/api/alerts/rules");
      $("alRules").querySelector("tbody").innerHTML = rules.map((r) => `<tr><td>${r.id}</td>
        <td>${esc(RULE_NAMES[r.kind] || r.kind)}${r.params.price ? " " + esc(fmtUsd(r.params.price)) : ""}</td>
        <td class="mono">${r.cooldown_s ? Math.round(r.cooldown_s / 60) + " min" : "default"}</td>
        <td><input type="checkbox" data-toggle="${r.id}" ${r.enabled ? "checked" : ""} aria-label="Rule ${r.id} enabled"></td>
        <td><button class="btn ghost small-btn" data-del="${r.id}">Delete</button></td></tr>`).join("") || '<tr><td colspan="5" class="empty">No rules.</td></tr>';
      labelCells($("alRules"));
    } catch (err) { $("alRuleMsg").textContent = err.message; }
  }

  async function sendJson(method, url, body) {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  function syncNotifyUi() {
    const supported = "Notification" in window;
    $("alNotifyState").textContent = !supported ? "Browser notifications are not supported here."
      : Notification.permission === "denied" ? "Browser notifications are blocked in this browser's site settings."
      : notifyOk() ? "Browser notifications on." : "Browser notifications off.";
    $("alNotify").textContent = notifyOk() ? "Disable browser notifications" : "Enable browser notifications";
    $("alNotify").disabled = !supported || Notification.permission === "denied";
  }

  function initAlerts() {
    try { state.browserNotify = localStorage.getItem("btc-notify") === "1"; } catch (_) { state.browserNotify = false; }
    syncNotifyUi();
    $("alNotify").onclick = async () => {
      if (notifyOk()) state.browserNotify = false;
      else state.browserNotify = (await Notification.requestPermission()) === "granted";   // only after this click
      try { localStorage.setItem("btc-notify", state.browserNotify ? "1" : "0"); } catch (_) {}
      sendJson("PUT", "/api/settings/alerts", { channel_browser: state.browserNotify }).catch(() => {});
      syncNotifyUi();
    };
    $("alTest").onclick = async () => { await sendJson("POST", "/api/alerts/test"); loadAlerts(); };
    $("alAckAll").onclick = async () => { await sendJson("POST", "/api/alerts/ack_all"); loadAlerts(); };
    $("alUnread").onchange = loadAlerts;
    $("alList").addEventListener("click", async (e) => {
      const id = e.target.dataset.ack;
      if (id) { await sendJson("POST", `/api/alerts/${id}/ack`); loadAlerts(); }
    });
    const syncKind = () => { $("alPriceLbl").hidden = !$("alKind").value.startsWith("price_"); };
    $("alKind").onchange = syncKind; syncKind();
    $("alNewForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const kind = $("alKind").value;
      const body = { kind, params: kind.startsWith("price_") ? { price: Number($("alPrice").value) } : {} };
      if ($("alCooldown").value) body.cooldown_s = Number($("alCooldown").value) * 60;
      try { await sendJson("POST", "/api/alerts/rules", body); $("alRuleMsg").textContent = "Rule added."; loadAlertRules(); if (state.lastSeries) loadChart(); }
      catch (err) { $("alRuleMsg").textContent = err.message; }
    });
    $("alRules").addEventListener("click", async (e) => {
      try {
        if (e.target.dataset.del) { await sendJson("DELETE", `/api/alerts/rules/${e.target.dataset.del}`); loadAlertRules(); }
        else if (e.target.dataset.toggle) await sendJson("PUT", `/api/alerts/rules/${e.target.dataset.toggle}`, { enabled: e.target.checked });
      } catch (err) { $("alRuleMsg").textContent = err.message; }
    });
  }

  // ---------------------------------------------------------------- per-view loading and refresh
  // Each view loads its data the first time it is opened; the overview loads first so the app starts fast.
  const LOADERS = {
    overview: [() => loadPrice(false), loadOverview, loadChart, loadAnalysis, loadSignal, loadAlerts, loadWatchlist, loadCalendar],
    market: [loadChart, loadAnalysis, loadMtf],
    news: [loadNews, loadCalendar],
    signal: [loadSignal],
    research: [loadBacktest, loadForward],
    paper: [loadPaper, loadSignal],
    alerts: [loadAlerts, loadAlertRules],
    assistant: [loadAssistantStatus, loadChatHistory],
    system: [loadSources, loadSystem, loadSignal],
  };

  async function refreshView(force) {
    await loadPrice(force);
    state.analysis = {};
    await Promise.all((LOADERS[state.view] || []).map((f) => f()));
    loadSources();
  }

  // Timers run only for the visible view; the price, the alert badge and source health update everywhere.
  const every = (ms, views, fn) => setInterval(() => { if (!views || views.includes(state.view)) fn(); }, ms);
  const startTimers = (g) => {
    const sec = (k, d) => Math.max(10, Number((g[k] || {}).value) || d) * 1000;
    state.priceMs = sec("refresh_price_s", 15);
    every(state.priceMs, null, () => loadPrice(false));
    every(30000, null, loadAlerts);
    every(60000, null, loadSources);
    every(sec("refresh_analysis_s", 60), ["overview", "market"], loadAnalysis);
    every(120000, ["overview", "market"], loadChart);
    every(120000, ["market"], loadMtf);
    every(sec("refresh_news_s", 300), ["news"], loadNews);
    every(sec("refresh_news_s", 300), ["overview", "signal", "paper"], loadSignal);
    every(30000, ["paper"], loadPaper);
    every(60000, ["overview"], loadWatchlist);
    every(3600000, ["overview", "news"], loadCalendar);
    every(60000, ["assistant"], loadAssistantStatus);
  };

  initChrome();
  initNewsControls();
  initPhase6();
  initAssistant();
  initAlerts();
  applyFolds();
  document.querySelectorAll(".export[data-dataset]").forEach((el) => { if (el.id !== "btExport") renderExport(el); });
  $("sigRefresh").onclick = async () => {
    const b = $("sigRefresh");
    b.disabled = true; b.textContent = "Calculating…";
    try { await loadSignal(); } finally { b.disabled = false; b.textContent = "Recalculate"; }
  };
  window.addEventListener("hashchange", route);
  route();
  runOnce(loadSources);
  // Refresh intervals come from Settings → General (minimums there respect the free rate limits).
  getJson("/api/settings").then((s) => startTimers(s.general || {})).catch(() => startTimers({}));
})();
