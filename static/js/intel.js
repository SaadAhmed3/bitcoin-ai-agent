/* Global intelligence view: events, macro calendar, other markets. Context, not part of the signal.
 * Read-only: every value comes from /api/intel/* and /api/calendar; nothing is calculated here. Loaded by app.js's view
 * loader (window.BTCIntel.load). Headlines come from outside the app, so everything is escaped and links are http(s) only. */
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const get = async (url) => {
    const r = await fetch(url, { headers: { "X-Requested-With": "BTCSignalLab", Accept: "application/json" } });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  };
  const tz = () => (window.APP && window.APP.displayTz) || "UTC";
  const local = (ms) => new Date(ms).toLocaleString(undefined, { timeZone: tz(), dateStyle: "medium", timeStyle: "short" });
  const utc = (ms) => new Date(ms).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const STATUS = { official_statement: "Official statement", confirmed: "Confirmed", developing: "Developing", unverified: "Unverified",
    speculative: "Speculative", disputed: "Disputed" };
  const FRESH = { new: "New", recent: "Last 24 h", earlier: "Last 3 days", old: "Older" };
  const link = (url, text) => (/^https?:\/\//i.test(url || "")
    ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text));
  let group = "";

  function eventHtml(e) {
    const shown = e.articles.slice(0, 5);
    const more = e.articles_count - shown.length;
    return `<li class="intel-event">
      <span class="intel-title">${esc(e.title)}</span>
      <div class="intel-meta">
        <span class="intel-status ${esc(e.status)}" title="${esc(e.status_reason)}">${esc(STATUS[e.status] || e.status)}</span>
        <span class="intel-cat">${esc(e.category_label)}</span><span>${esc(e.country)}</span>
        <span>${e.sources_count} source${e.sources_count === 1 ? "" : "s"} · ${e.articles_count} article${e.articles_count === 1 ? "" : "s"}</span>
        <span title="${esc(utc(e.first_published_at))}">${esc(FRESH[e.freshness] || e.freshness)}: ${esc(local(e.first_published_at))}${e.date_known ? "" : " (publish time unknown)"}</span>
      </div>
      <ul class="intel-sources">${shown.map((a) => `<li>${link(a.link, a.publisher)}${a.title !== e.title ? `: ${esc(a.title)}` : ""}</li>`).join("")}${more > 0 ? `<li class="muted">and ${more} more</li>` : ""}</ul>
    </li>`;
  }

  async function loadEvents() {
    const card = $("intelEvents");
    try {
      const d = await get(`/api/intel/events?limit=100${group ? `&group=${encodeURIComponent(group)}` : ""}`);
      $("intelEventList").innerHTML = d.events.length ? d.events.map(eventHtml).join("")
        : `<li class="empty">${group ? "No events for this filter in the last 7 days." : "No events yet. Sources are checked in the background; the first results appear within a few minutes of starting the app."}</li>`;
      $("intelEventsMeta").textContent = `${d.events.length} event${d.events.length === 1 ? "" : "s"} in the last 7 days · articles about the same event are grouped into one`;
      card.dataset.state = "ready";
    } catch (err) {
      $("intelEventList").innerHTML = `<li class="empty">Events unavailable: ${esc(err.message)}</li>`;
      card.dataset.state = "error";
    }
  }

  async function loadOverview() {
    try {
      const o = await get("/api/intel/overview");
      $("intelCounts").innerHTML = Object.entries(o.by_category).map(([k, n]) =>
        `<div class="intel-count"><b>${n}</b><span>${esc(o.category_labels[k] || k)}</span></div>`).join("");
      $("intelUnavailable").textContent = "Unavailable, never estimated: " + o.unavailable.map((u) => u.name).join(", ") + " (no verified free source).";
    } catch (err) {
      $("intelCounts").innerHTML = `<p class="empty">Counts unavailable: ${esc(err.message)}</p>`;
    }
  }

  async function loadCalendar() {
    const card = $("intelCalendar");
    try {
      const c = await get("/api/calendar?days=45&past=14");
      $("intelCalList").innerHTML = c.events.length ? c.events.map((e) => `<li>
          <div><b>${esc(e.event)}</b> <span class="tag">${esc(e.kind)}</span> <span class="cal-status ${esc(e.status)}">${e.status === "released" ? "Released" : "Upcoming"}</span></div>
          <div class="mono small">${esc(utc(e.at))} · ${esc(local(e.at))} (${esc(tz())})</div>
          <div class="muted small">${esc(e.time_note || "")} · ${link(e.link, e.source)}</div></li>`).join("")
        : `<li class="empty">${esc(c.empty_reason || "No events.")}</li>`;
      $("intelCalWarn").textContent = (c.warnings || []).join(" ");
      card.dataset.state = "ready";
    } catch (err) {
      $("intelCalList").innerHTML = `<li class="empty">Calendar unavailable: ${esc(err.message)}</li>`;
      card.dataset.state = "error";
    }
  }

  function changeText(s) {
    if (s.change === null || s.change === undefined) return "N/A";
    const sign = s.change >= 0 ? "+" : "";
    return `${sign}${Number(s.change).toFixed(2)}${s.change_pct === null || s.change_pct === undefined ? "" : ` (${sign}${Number(s.change_pct).toFixed(2)}%)`}`;
  }

  function marketRow(s) {
    if (s.status !== "ok") {
      const word = s.status === "unavailable" ? "Unavailable" : "No data yet";
      return `<tr data-series="${esc(s.id)}"><td><b>${esc(s.name)}</b></td><td colspan="4"><span class="na intel-na">${word}</span><div class="muted small">${esc(s.reason || "")}</div></td></tr>`;
    }
    return `<tr data-series="${esc(s.id)}"><td><b>${esc(s.name)}</b></td>
      <td class="mono">${Number(s.value).toLocaleString(undefined, { maximumFractionDigits: 2 })} <span class="muted small">${esc(s.unit)}</span></td>
      <td class="mono">${esc(changeText(s))}${s.previous_date ? `<div class="muted small">vs ${esc(s.previous_date)}</div>` : ""}</td>
      <td class="mono">${esc(s.data_date)}<div class="muted small">${s.age_days} day${s.age_days === 1 ? "" : "s"} old</div></td>
      <td class="small"><span class="mk-source">${link(s.source_url, s.source)}</span><span class="muted">${esc(s.delay_label)}</span>
        ${s.notes ? `<div class="muted">${esc(s.notes)}</div>` : ""}${s.fetch_error ? `<div class="kpi-warn">Last fetch failed; showing the stored value.</div>` : ""}</td></tr>`;
  }

  async function loadMarkets() {
    const card = $("intelMarkets");
    try {
      const m = await get("/api/intel/markets");
      $("intelMkTable").querySelector("tbody").innerHTML = m.series.map(marketRow).join("");
      $("intelMkMeta").textContent = m.note;
      card.dataset.state = "ready";
    } catch (err) {
      $("intelMkTable").querySelector("tbody").innerHTML = `<tr><td colspan="5" class="empty">Markets unavailable: ${esc(err.message)}</td></tr>`;
      card.dataset.state = "error";
    }
  }

  const load = () => Promise.all([loadOverview(), loadEvents(), loadCalendar(), loadMarkets()]);

  $("intelFilters").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-group]");
    if (!b) return;
    group = b.dataset.group;
    document.querySelectorAll("#intelFilters button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    loadEvents();
  });

  window.BTCIntel = { load };
})();
