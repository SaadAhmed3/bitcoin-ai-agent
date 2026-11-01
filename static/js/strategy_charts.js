/* Strategy Lab charts (Plotly): live chart, design-backtest history with a trade list, forward test, comparison.
 * Every number comes from /api/strategies/*: the server uses the strategy's own rule code and the stored results.
 * window.BTCStrategyCharts.render(sid) draws the Chart tab, .compare() the comparison charts. */
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const get = async (url) => {
    const r = await fetch(url, { headers: { "X-Requested-With": "BTCSignalLab", Accept: "application/json" } });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  };
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const alpha = (hex, a) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };
  const iso = (ms) => new Date(ms).toISOString();
  const day = (ms) => new Date(ms).toISOString().slice(0, 10);
  const when = (ms) => new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";
  const usd = (v) => (v === null || v === undefined ? "N/A" : "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  const pct = (v) => (v === null || v === undefined ? "N/A" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
  const BAR = { "4h": 4 * 3600000, "1d": 86400000 };
  const colors = () => ({ up: css("--up"), down: css("--down"), text: css("--text"), muted: css("--muted"), line: css("--line"),
    info: css("--info"), accent: css("--accent"), warn: css("--warn") });
  const narrow = () => window.innerWidth <= 700;
  const layout = (c, extra) => ({ paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: c.text, size: 11 },
    margin: { l: 10, r: narrow() ? 44 : 60, t: 10, b: 30 }, showlegend: !narrow(),
    legend: { orientation: "h", y: 1.1, x: 0, font: { size: 10, color: c.muted } }, hovermode: "closest", dragmode: "pan", ...extra });
  const axis = (c, extra) => ({ gridcolor: c.line, linecolor: c.line, side: "right", ...extra });
  const PCFG = { responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ["select2d", "lasso2d"] };
  const ok = () => typeof Plotly !== "undefined";
  const empty = (id, text) => {
    const el = $(id);
    if (ok()) Plotly.purge(el);
    el.classList.add("is-empty");
    el.innerHTML = `<div class="empty">${esc(text)}</div>`;
  };
  const plot = (id, traces, L) => { const el = $(id); el.classList.remove("is-empty"); el.innerHTML = ""; Plotly.newPlot(id, traces, L, PCFG); };
  const how = (id, text) => { $(id).textContent = text ? `How to read this chart: ${text}` : ""; };
  const line = (x, y, name, color, extra = {}) => ({ type: "scatter", mode: "lines", x, y, name, line: { color, width: 1.3, ...(extra.line || {}) },
    xaxis: "x", yaxis: extra.yaxis || "y", hoverinfo: extra.hoverinfo || "x+y+name", connectgaps: false });

  // ---------------------------------------------------------------- A. live chart
  function drawLive(sid, d) {
    const banner = $("stratLiveBanner");
    banner.hidden = !d.banner;
    banner.textContent = d.banner || "";
    how("stratLiveHow", d.how_to);
    $("stratChecklist").innerHTML = (d.checklist || []).map((c) => `<li class="${c.met ? "met" : "not"}">${c.met ? "✓" : "✗"} ${esc(c.rule)}</li>`).join("");
    if (d.status !== "ok") { empty("stratLiveChart", d.note || "No chart data."); return; }
    if (!ok()) { empty("stratLiveChart", "Plotly could not be loaded."); return; }
    const c = colors(), s = d.series, x = s.time.map(iso), last = x[x.length - 1];
    const traces = [{ type: "candlestick", name: "BTC", x, open: s.open, high: s.high, low: s.low, close: s.close,
      increasing: { line: { color: c.up }, fillcolor: c.up }, decreasing: { line: { color: c.down }, fillcolor: c.down }, xaxis: "x", yaxis: "y" },
    { type: "bar", name: "Volume", x, y: s.volume, marker: { color: alpha(c.muted, 0.35) }, xaxis: "x", yaxis: "y2", hoverinfo: "skip", showlegend: false }];
    const shapes = [], ann = [];
    let adx = false;
    if (sid === "trend") {
      const n = d.names || {};
      traces.push(line(x, s.ema_f, n.ema_f || "EMA fast", "#60a5fa"), line(x, s.ema_m, n.ema_m || "EMA mid", "#c084fc"),
        line(x, s.ema_s, n.ema_s || "EMA slow", "#f97316", { line: { width: 2 } }),
        line(x, s.daily_ema, n.daily_ema || "Daily EMA", c.warn, { line: { dash: "dot" } }));
      adx = true;
    } else if (sid === "range") {
      const b = d.box || {};
      if (b.high !== null && d.box_from !== null) {
        const bx = [iso(d.box_from), last];
        shapes.push({ type: "rect", xref: "x", yref: "y", x0: bx[0], x1: bx[1], y0: b.low, y1: b.high, layer: "below",
          line: { width: 1, color: alpha(c.info, 0.6) }, fillcolor: alpha(c.info, 0.06) });
        traces.push(line(bx, [b.high, b.high], "Range high", c.info), line(bx, [b.low, b.low], "Range low", c.info),
          line(bx, [b.mid, b.mid], "Midpoint", c.muted, { line: { dash: "dash" } }));
      }
      const t = d.touches || { top: [], bottom: [] };
      traces.push({ type: "scatter", mode: "markers", name: "Touches", xaxis: "x", yaxis: "y",
        x: [...t.top, ...t.bottom].map((p) => iso(p.time)), y: [...t.top, ...t.bottom].map((p) => p.price),
        marker: { symbol: "circle-open", size: 9, color: c.accent, line: { width: 1.5 } } });
      const lab = d.regime.label;                       // sideways bars shaded
      let k = 0;
      while (k < lab.length) {
        if (lab[k] === "SIDEWAYS / RANGING") {
          let j = k;
          while (j + 1 < lab.length && lab[j + 1] === "SIDEWAYS / RANGING") j += 1;
          shapes.push({ name: "sideways", type: "rect", xref: "x", yref: "paper", x0: x[k], x1: x[Math.min(j + 1, x.length - 1)],
            y0: 0.25, y1: 1, layer: "below", line: { width: 0 }, fillcolor: alpha(c.up, 0.07) });
          k = j + 1;
        } else k += 1;
      }
      adx = true;
    } else if (sid === "dca") {
      const lowBy = Object.fromEntries(s.time.map((t, i) => [t, s.low[i]]));
      const days = (d.buy_days || []).filter((t) => lowBy[t] !== undefined);
      traces.push({ type: "scatter", mode: "markers", name: "Scheduled buy days", xaxis: "x", yaxis: "y",
        x: days.map(iso), y: days.map((t) => lowBy[t] * 0.985), marker: { symbol: "triangle-up", size: 9, color: c.info } });
      if (d.avg_price) traces.push(line([x[0], last], [d.avg_price, d.avg_price], "Average buy price", c.accent, { line: { dash: "dash" } }));
    }
    if (adx) {
      traces.push(line(x, s.adx, "ADX(14)", "#2dd4bf", { yaxis: "y3" }));
      if (d.adx_line) shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, yref: "y3", y0: d.adx_line, y1: d.adx_line, line: { color: c.muted, width: 1, dash: "dot" } });
    }
    if (d.plan) {                                    // entry band, stop (red), targets (green), R:R
      const e = d.plan.entry || {}, lo = e.low * 0.999, hi = e.high * 1.001;
      shapes.push({ type: "rect", xref: "paper", x0: 0.8, x1: 1, yref: "y", y0: lo, y1: hi, line: { width: 0 }, fillcolor: alpha(c.info, 0.3) });
      traces.push(line([x[Math.max(0, x.length - 30)], last], [d.plan.stop, d.plan.stop], `Stop ${usd(d.plan.stop)}`, c.down, { line: { width: 2 } }));
      (d.plan.targets || []).forEach((t, i) => traces.push(line([x[Math.max(0, x.length - 30)], last], [t, t], `Target ${i + 1} ${usd(t)}`, c.up, { line: { width: 2 } })));
      ann.push({ xref: "paper", x: 0.99, xanchor: "right", yref: "y", y: hi, yanchor: "bottom", showarrow: false,
        text: `Entry ${usd(e.low)} · R:R ${d.plan.rr}`, font: { size: 11, color: c.text }, bgcolor: alpha(c.info, 0.2) });
    } else if ((d.checklist || []).length && !narrow()) {   // no setup: why, rule by rule (phones: the list below)
      ann.push({ xref: "paper", yref: "paper", x: 0.01, y: 0.99, xanchor: "left", yanchor: "top", align: "left", showarrow: false,
        text: ["<b>No setup</b>", ...d.checklist.map((r) => `${r.met ? "✓" : "✗"} ${esc(r.rule)}`)].join("<br>"),
        font: { size: 10, color: c.text }, bgcolor: alpha(css("--card") || "#141922", 0.85), bordercolor: c.line, borderwidth: 1, borderpad: 6 });
    }
    const L = layout(c, { shapes, annotations: ann, hovermode: "x unified",
      xaxis: { type: "date", rangeslider: { visible: false }, gridcolor: c.line, linecolor: c.line },
      yaxis: axis(c, { domain: adx ? [0.36, 1] : [0.24, 1], tickformat: ",.0f" }),
      yaxis2: axis(c, { domain: adx ? [0.22, 0.32] : [0, 0.18], showticklabels: false }),
      ...(adx ? { yaxis3: axis(c, { domain: [0, 0.18], range: [0, 60] }) } : {}) });
    plot("stratLiveChart", traces, L);
  }

  // ---------------------------------------------------------------- B. history chart
  let histTimer = null;
  function drawHistory(sid, h, retry) {
    clearTimeout(histTimer);
    const tbody = $("stratTradeList").querySelector("tbody");
    $("stratTradeList").closest(".strat-trades").hidden = sid === "dca";
    if (h.status === "preparing") {
      empty("stratHistChart", h.note);
      tbody.innerHTML = "";
      histTimer = setTimeout(retry, 3000);
      return;
    }
    if (h.status !== "ok") { empty("stratHistChart", h.note || "No design backtest for these settings."); tbody.innerHTML = ""; return; }
    how("stratHistHow", h.how_to);
    $("stratHistSource").textContent = h.verified ? `Source: ${h.source} (verified).` : `Source: ${h.source}. ${h.mismatches.join("; ")}`;
    if (!ok()) { empty("stratHistChart", "Plotly could not be loaded."); return; }
    const c = colors(), px = h.price.time.map(iso);
    let traces, L;
    if (sid === "dca") {
      const w = h.worst;
      traces = [line(px, h.price.close, "BTC price", c.muted),
        { ...line(h.invested.time.map(iso), h.invested.value, "Invested", c.info, { yaxis: "y2" }), line: { shape: "hv", color: c.info, width: 1.5 } },
        line(h.value.time.map(iso), h.value.value, "Value of the BTC bought", c.accent, { yaxis: "y2" })];
      if (w) traces.push({ type: "scatter", mode: "markers+text", name: "Worst point", x: [iso(w.day)], y: [w.value], xaxis: "x", yaxis: "y2",
        text: [`worst ${pct(w.roi_pct)}`], textposition: "bottom center", marker: { size: 11, color: c.down, symbol: "x" } });
      L = layout(c, { hovermode: "x unified", xaxis: { type: "date", gridcolor: c.line },
        yaxis: axis(c, { domain: [0.55, 1], tickformat: ",.0f", title: { text: "BTC", font: { size: 10 } } }),
        yaxis2: axis(c, { domain: [0, 0.48], tickformat: ",.0f", title: { text: "USD", font: { size: 10 } } }) });
      tbody.innerHTML = "";
    } else {
      const tr = h.trades;
      const seg = (win) => {
        const xs = [], ys = [];
        tr.filter((t) => t.win === win).forEach((t) => { xs.push(iso(t.entry_time), iso(t.exit_time), null); ys.push(t.fill, t.exit, null); });
        return { type: "scatter", mode: "lines", x: xs, y: ys, name: win ? "Winning trades" : "Losing trades", xaxis: "x", yaxis: "y",
          line: { color: win ? c.up : c.down, width: 2.5 }, hoverinfo: "skip" };
      };
      const hover = tr.map((t) => [`Entry ${when(t.entry_time)} at ${usd(t.fill)}`, `Exit ${when(t.exit_time)} at ${usd(t.exit)} (${t.reason})`,
        `Stop ${usd(t.stop)} · target ${usd(t.target)}`, `Result ${t.r >= 0 ? "+" : ""}${t.r.toFixed(2)}R`,
        `Fees ${t.fees_pct === null || t.fees_pct === undefined ? "N/A (not verified)" : t.fees_pct.toFixed(3) + "% of the account"}`].join("<br>"));
      traces = [line(px, h.price.close, "Price", alpha(c.muted, 0.9), { hoverinfo: "skip" }), seg(true), seg(false),
        { type: "scatter", mode: "markers", name: "Entries", x: tr.map((t) => iso(t.entry_time)), y: tr.map((t) => t.fill), xaxis: "x", yaxis: "y",
          marker: { symbol: "triangle-up", size: 10, color: tr.map((t) => (t.win ? c.up : c.down)) }, hovertext: hover, hoverinfo: "text" },
        { type: "scatter", mode: "markers", name: "Exits", x: tr.map((t) => iso(t.exit_time)), y: tr.map((t) => t.exit), xaxis: "x", yaxis: "y",
          marker: { symbol: "triangle-down", size: 10, color: tr.map((t) => (t.win ? c.up : c.down)) }, hovertext: hover, hoverinfo: "text" },
        line(h.equity.time.map(iso), h.equity.value, "Equity", c.accent, { yaxis: "y2" })];
      if (h.buy_hold) traces.push(line(h.buy_hold.time.map(iso), h.buy_hold.value, "Buy & hold", c.muted, { yaxis: "y2", line: { dash: "dot" } }));
      traces.push({ type: "scatter", mode: "lines", name: "Drawdown", x: h.drawdown.time.map(iso), y: h.drawdown.value.map((v) => -v),
        xaxis: "x", yaxis: "y3", line: { color: c.down, width: 1 }, fill: "tozeroy", fillcolor: alpha(c.down, 0.15) });
      L = layout(c, { xaxis: { type: "date", gridcolor: c.line },
        yaxis: axis(c, { domain: [0.46, 1], tickformat: ",.0f" }),
        yaxis2: axis(c, { domain: [0.2, 0.42], tickformat: ".2f", title: { text: "account", font: { size: 10 } } }),
        yaxis3: axis(c, { domain: [0, 0.16], ticksuffix: "%", title: { text: "DD", font: { size: 10 } } }) });
      const bar = BAR["4h"];
      tbody.innerHTML = tr.map((t, i) => `<tr data-i="${i}" tabindex="0" class="${t.win ? "win" : "loss"}"><td>${esc(when(t.entry_time))}</td>
        <td class="num">${t.r >= 0 ? "+" : ""}${t.r.toFixed(2)}R</td><td>${esc(t.reason)}</td></tr>`).join("")
        || '<tr><td colspan="3" class="empty">No trades.</td></tr>';
      tbody.onclick = tbody.onkeydown = (e) => {
        if (e.type === "keydown" && e.key !== "Enter") return;
        const row = e.target.closest("tr[data-i]");
        if (!row) return;
        const t = tr[Number(row.dataset.i)];
        const a = t.entry_time - 20 * bar, b = t.exit_time + 20 * bar;      // zoom in time and fit the price axis
        const inView = h.price.time.map((tm, i) => (tm >= a && tm <= b ? h.price.close[i] : null)).filter((v) => v !== null)
          .concat([t.fill, t.exit, t.stop, t.target]);
        const lo = Math.min(...inView), hi = Math.max(...inView), pad = (hi - lo) * 0.08 || hi * 0.01;
        Plotly.relayout("stratHistChart", { "xaxis.range": [iso(a), iso(b)], "xaxis.autorange": false,
          "yaxis.range": [lo - pad, hi + pad], "yaxis.autorange": false });
        tbody.querySelectorAll("tr").forEach((r) => r.classList.toggle("sel", r === row));
      };
    }
    plot("stratHistChart", traces, L);
  }

  // ---------------------------------------------------------------- C. forward chart
  function drawForward(sid, f) {
    how("stratFwdHow", f.how_to);
    if (f.status !== "ok") { empty("stratFwdChart", `Empty: ${f.note}`); return; }
    if (!ok()) { empty("stratFwdChart", "Plotly could not be loaded."); return; }
    const c = colors(), x = f.price.time.map(iso);
    const traces = [line(x, f.price.close, "Price", c.muted)];
    if (sid === "dca") {
      traces.push({ ...line(f.invested.time.map(iso), f.invested.value, "Invested", c.info, { yaxis: "y2" }), line: { shape: "hv", color: c.info } },
        line(f.value.time.map(iso), f.value.value, "Value of the BTC bought", c.accent, { yaxis: "y2" }));
    } else {
      const tr = f.trades || [];
      traces.push({ type: "scatter", mode: "markers", name: "Entries", x: tr.map((t) => iso(t.entry_time)), y: tr.map((t) => t.fill),
        marker: { symbol: "triangle-up", size: 9, color: tr.map((t) => (t.win ? c.up : c.down)) }, xaxis: "x", yaxis: "y" },
      line(f.equity.time.map(iso), f.equity.value, "Equity", c.accent, { yaxis: "y2" }));
    }
    plot("stratFwdChart", traces, layout(c, { xaxis: { type: "date", gridcolor: c.line },
      yaxis: axis(c, { domain: [0.5, 1], tickformat: ",.0f" }), yaxis2: axis(c, { domain: [0, 0.44] }) }));
  }

  // ---------------------------------------------------------------- D. comparison
  const SHORT = { dca: "DCA", trend: "Trend", range: "Range", breakout: "Breakout", rotation: "Rotation" };
  async function compare() {
    try {
      const d = await get("/api/strategies/compare-chart");
      how("stratCmpHow", d.how_to);
      if (!d.curves.length) { empty("stratCmpEquity", "No stored design backtest yet."); empty("stratCmpScatter", "No stored design backtest yet."); return; }
      if (!ok()) return;
      const c = colors(), pal = ["#60a5fa", c.accent, "#c084fc", c.up, c.warn];
      plot("stratCmpEquity", d.curves.map((k, i) => line(k.time.map(iso), k.value, k.name, pal[i % pal.length])),
        layout(c, { hovermode: "x unified", xaxis: { type: "date", gridcolor: c.line }, yaxis: axis(c, { type: "log", tickformat: ",.0f" }) }));
      plot("stratCmpScatter", [{ type: "scatter", mode: "markers+text", name: "Strategies",
        x: d.scatter.map((p) => p.max_drawdown_pct), y: d.scatter.map((p) => p.net_return_pct),
        text: d.scatter.map((p) => `${SHORT[p.id] || p.id} (${p.verdict})`), textposition: "top center", cliponaxis: false,
        marker: { size: 12, color: d.scatter.map((_, i) => pal[i % pal.length]) },
        hovertemplate: "%{text}<br>max drawdown %{x:.2f}%<br>net return %{y:.2f}%<extra></extra>" }],
      layout(c, { showlegend: false, xaxis: { title: { text: "max drawdown (%)", font: { size: 10 } }, gridcolor: c.line,
          range: [0, Math.max(...d.scatter.map((p) => p.max_drawdown_pct)) * 1.25 + 1] },
        yaxis: axis(c, { side: "left", title: { text: "net return (%)", font: { size: 10 } } }), margin: { l: 50, r: 16, t: 20, b: 40 } }));
    } catch (err) {
      empty("stratCmpEquity", `Could not load: ${err.message}`);
    }
  }

  // ---------------------------------------------------------------- the Chart tab
  async function render(sid) {
    const base = `/api/strategies/${sid}/chart`;
    const stale = () => document.getElementById("stratDetail").dataset.sid !== sid;
    try { const d = await get(`${base}/live`); if (!stale()) drawLive(sid, d); } catch (err) { empty("stratLiveChart", `Could not load: ${err.message}`); }
    const hist = async () => { try { const h = await get(`${base}/history`); if (!stale()) drawHistory(sid, h, hist); } catch (err) { empty("stratHistChart", `Could not load: ${err.message}`); } };
    await hist();
    try { const f = await get(`${base}/forward`); if (!stale()) drawForward(sid, f); } catch (err) { empty("stratFwdChart", `Could not load: ${err.message}`); }
  }

  window.BTCStrategyCharts = { render, compare };
})();
