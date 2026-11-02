/* Global intelligence view: events, macro calendar, other markets. Context, not part of the signal.
 * Read-only: every value, including the study's statistics, comes from /api/intel/* and /api/calendar; nothing is calculated here. Loaded by app.js's view
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
  const TIME_FLAG = { publication_missing: "No publication time: not measured", published_after_first_seen: "Publication time later than first seen: left out of the statistics",
    publication_partial: "Some articles have no publication time" };
  const FRESH = { new: "New", recent: "Last 24 h", earlier: "Last 3 days", old: "Older" };
  const link = (url, text) => (/^https?:\/\//i.test(url || "")
    ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text));
  const sign = (v, digits = 2) => (v === null || v === undefined ? "N/A" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(digits)}%`);
  const num = (v, digits = 2) => (v === null || v === undefined ? "N/A" : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits }));
  // Phone tables stack into cards and read their labels from data-label, set from the header row.
  const labelTable = (table) => {
    const heads = [...table.tHead.rows[0].cells].map((th) => th.textContent.trim());
    [...table.tBodies[0].rows].forEach((tr) => [...tr.cells].forEach((td, i) => { if (td.colSpan === 1 && heads[i]) td.dataset.label = heads[i]; }));
  };
  const REACT_STATUS = { measured: "Measured", pending: "Waiting", missing: "Candle missing", unavailable: "Not available" };
  let group = "";
  let study = null;
  let horizon = null;

  function eventHtml(e) {
    const shown = e.articles.slice(0, 5);
    const more = e.articles_count - shown.length;
    return `<li class="intel-event">
      <span class="intel-title">${esc(e.title)}</span>
      <div class="intel-meta">
        <span class="intel-status ${esc(e.status)}" title="${esc(e.status_reason)}">${esc(STATUS[e.status] || e.status)}</span>
        <span class="intel-cat">${esc(e.category_label)}</span><span>${esc(e.country)}</span>
        <span>${e.sources_count} source${e.sources_count === 1 ? "" : "s"} · ${e.articles_count} article${e.articles_count === 1 ? "" : "s"}</span>
        <span title="${esc(utc(e.first_published_at))}">${esc(FRESH[e.freshness] || e.freshness)}</span>
      </div>
      <div class="intel-time small muted">
        ${e.event_time_at ? `<span title="${esc(utc(e.event_time_at))}">Event time: ${esc(local(e.event_time_at))}</span>` : "<span>Event time: unknown</span>"}
        ${e.first_seen_at ? `<span title="${esc(utc(e.first_seen_at))}">First seen: ${esc(local(e.first_seen_at))}</span>` : ""}
        ${e.time_flag ? `<span class="kpi-warn" data-flag="${esc(e.time_flag)}">${esc(TIME_FLAG[e.time_flag] || e.time_flag)}</span>` : ""}
      </div>
      <ul class="intel-sources">${shown.map((a) => `<li>${link(a.link, a.publisher)}${a.title !== e.title ? `: ${esc(a.title)}` : ""}</li>`).join("")}${more > 0 ? `<li class="muted">and ${more} more</li>` : ""}</ul>
      <details class="intel-impact" data-event="${Number(e.id)}"><summary>Impact assessment</summary>
        <div class="intel-impact-body" data-state="idle"></div></details>
      <details class="intel-reaction" data-event="${Number(e.id)}"><summary>What Bitcoin did around this event</summary>
        <div class="intel-reaction-body" data-state="idle"></div></details>
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

  function studyRow(attr, name, s, extra) {
    if (!s.n) return `<tr ${attr}><td><b>${esc(name)}</b>${extra || ""}</td><td colspan="6" class="muted small">${esc(s.sample_note)}</td></tr>`;
    const ratio = s.abs_vs_normal === null ? "N/A" : `${num(s.abs_vs_normal)} x`;
    return `<tr ${attr}><td><b>${esc(name)}</b>${extra || ""}</td>
      <td class="mono">${s.n}<div class="muted small">${s.episodes} episode${s.episodes === 1 ? "" : "s"}</div></td>
      <td class="mono">${esc(sign(s.median_pct))}</td>
      <td class="mono">${esc(sign(s.min_pct))} to ${esc(sign(s.max_pct))}</td>
      <td class="mono">${s.ci95 ? `${esc(sign(s.ci95[0]))} to ${esc(sign(s.ci95[1]))}` : "Too few episodes"}</td>
      <td class="mono">${esc(ratio)}<div class="muted small">${esc(num(s.median_abs_pct))}% vs ${esc(num(s.normal_abs_pct))}% normal</div></td>
      <td class="small"><span class="study-flag ${esc(s.sample_flag)}">${esc(s.sample_note)}</span></td></tr>`;
  }

  function renderStudy() {
    if (!study) return;
    $("intelStudyHorizons").innerHTML = study.horizons.map((h) => `<button type="button" class="chip-btn" data-horizon="${esc(h)}" aria-pressed="${h === horizon}">+${esc(h)}</button>`).join("");
    $("intelStudyHorizonNote").textContent = `Move from the last closed candle before each event to the first close at least +${horizon} after it.`;
    const cat = (bucket) => study.categories.map((c) => studyRow(`data-category="${esc(c.category)}"`, c.label, c[bucket].horizons[horizon])).join("");
    $("intelStudyTable").querySelector("tbody").innerHTML = cat("clean");
    $("intelStudyMacroWindowTable").querySelector("tbody").innerHTML = cat("macro_window");
    $("intelStudyReleases").querySelector("tbody").innerHTML = study.macro_releases.map((m) => studyRow(`data-kind="${esc(m.kind)}"`, m.label, m.horizons[horizon],
      m.shares_window ? `<div class="muted small">${m.shares_window} of ${m.events} within 2 h of another release</div>` : "")).join("");
    ["intelStudyTable", "intelStudyMacroWindowTable", "intelStudyReleases"].forEach((id) => labelTable($(id)));
  }

  async function loadStudy() {
    const card = $("intelStudy");
    try {
      study = await get("/api/intel/reactions");
      horizon = horizon || study.default_horizon;
      $("intelStudyNotes").innerHTML = study.notes.map((n) => `<li>${esc(n)}</li>`).join("");
      $("intelStudyWarn").textContent = study.calendar.warning || "";
      const x = study.excluded;
      $("intelStudyExcluded").textContent = `${study.totals.events} events: ${study.totals.clean} not near a macro release, ${study.totals.macro_window} near one. Left out: ${x.publication_missing} with no publication time, ${x.published_after_first_seen} published later than first seen, ${x.macro_unknown} outside the known release dates.`
        + (study.candles_through ? ` Candles through ${utc(study.candles_through)}.` : " No candles stored yet.");
      $("intelStudyConsensus").textContent = study.consensus_note;
      renderStudy();
      card.dataset.state = "ready";
    } catch (err) {
      $("intelStudyNotes").innerHTML = `<li class="empty">The study is unavailable: ${esc(err.message)}</li>`;
      card.dataset.state = "error";
    }
  }

  function reactionHtml(d) {
    const b = d.baseline;
    const a = d.anchor_article;
    const near = d.macro_window.releases;
    const rows = d.horizons.map((h) => `<tr data-horizon="${esc(h.horizon)}"><td><b>+${esc(h.horizon)}</b></td>
      <td><span class="react-status ${esc(h.status)}">${esc(REACT_STATUS[h.status] || h.status)}</span></td>
      <td class="mono">${esc(sign(h.return_pct))}</td>
      <td class="mono">${h.window_hours === null ? "N/A" : `${h.window_hours} h`}</td>
      <td class="mono">${h.normal_abs_pct === null ? "N/A" : `${esc(num(h.normal_abs_pct))}%`}</td></tr>`).join("");
    const afterCell = (x) => (x ? `${esc(num(x.value))}<div class="muted small">${esc(x.date)}${x.change === null ? "" : `, ${x.change >= 0 ? "+" : ""}${esc(num(x.change))}`}</div>` : "N/A");
    const markets = d.other_markets.map((m) => `<tr data-series="${esc(m.id)}"><td><b>${esc(m.name)}</b><div class="muted small">${esc(m.source)}</div></td>
      <td class="mono">${m.before ? `${esc(num(m.before.value))}<div class="muted small">${esc(m.before.date)}</div>` : "N/A"}</td>
      <td class="mono">${afterCell(m.after["1d"])}</td><td class="mono">${afterCell(m.after["7d"])}</td><td class="mono">${afterCell(m.after["30d"])}</td></tr>`).join("");
    return `${d.time_flag_text ? `<p class="kpi-warn">${esc(d.time_flag_text)}</p>` : ""}
      ${a ? `<p class="small">Measured from the earliest report: ${link(a.link, a.publisher)}, ${esc(utc(a.published_at))}.</p>` : ""}
      ${near.length ? `<p class="kpi-warn">Within 2 hours of a macro release: ${near.map((r) => esc(r.kind)).join(", ")}. The move may belong to the release.</p>` : ""}
      ${b ? `<p class="small">At the last closed candle (${esc(utc(b.base_open_time + 3600000))}): price ${esc(num(b.base_close))}, volume ${b.volume_ratio === null ? "N/A" : `${esc(num(b.volume_ratio))} x the 24 h average`}, ATR ${b.atr_pct === null ? "N/A" : `${esc(num(b.atr_pct))}%`}, regime ${esc(b.regime || "not enough history")}.</p>`
    : `<p class="small muted">Before, at the last closed candle: ${esc(d.baseline_note)}</p>`}
      <div class="table-wrap"><table class="table stack"><thead><tr><th>After</th><th>Status</th><th>BTC move</th><th>Window</th><th>Normal size</th></tr></thead><tbody>${rows}</tbody></table></div>
      <h4>Other markets</h4><p class="muted small">${esc(d.other_markets_note)}</p>
      <div class="table-wrap"><table class="table stack"><thead><tr><th>Market</th><th>Before</th><th>+1 day</th><th>+7 days</th><th>+30 days</th></tr></thead><tbody>${markets}</tbody></table></div>
      <p class="muted small">${esc(d.label)}. ${d.notes.map(esc).join(" ")}</p>`;
  }

  async function loadReaction(details) {
    const body = details.querySelector(".intel-reaction-body");
    if (body.dataset.state !== "idle") return;
    body.dataset.state = "loading";
    body.textContent = "Loading…";
    try {
      body.innerHTML = reactionHtml(await get(`/api/intel/events/${Number(details.dataset.event)}/reaction`));
      body.querySelectorAll("table").forEach(labelTable);
      body.dataset.state = "ready";
    } catch (err) {
      body.textContent = `Unavailable: ${err.message}`;
      body.dataset.state = "idle";
    }
  }

  const IMPACT_NAMES = { "0-24h": "0-24h", "1-7d": "1-7d", "1-3m": "1-3 months" };
  const IMPACT_ORDER = Object.keys(IMPACT_NAMES);      // the API's JSON keys arrive sorted, which would put 1-3 months before 1-7d
  const READING = { could_weigh: "One reading", could_support: "Another reading", no_clear_link: "Or" };

  function impactHtml(d) {
    const a = d.assessment;
    if (!a) return `<p class="small muted">${esc(d.note)}</p>`;
    const rows = IMPACT_ORDER.map((h) => [h, a.horizons[h]]).map(([h, v]) => `<tr data-horizon="${esc(h)}"><td><b>${esc(IMPACT_NAMES[h] || h)}</b></td>
      <td><span class="impact-label ${esc(v.label.toLowerCase())}">${esc(v.label)}</span></td>
      <td class="mono">${v.score > 0 ? "+" : ""}${Number(v.score)}</td><td class="small">${esc(v.why)}</td></tr>`).join("");
    const factors = a.factors.map((f) => {
      if (f.key !== "historical_reaction") {
        return `<tr><td><b>${esc(f.label)}</b></td><td class="mono">${Number(f.value).toFixed(2)}</td><td class="small">${esc(f.note)}</td></tr>`;
      }
      const hist = IMPACT_ORDER.map((h) => [h, f.by_horizon[h]]).map(([h, s]) => (s
        ? `${esc(IMPACT_NAMES[h] || h)}: ${s.n} measured, ${s.episodes} episode${s.episodes === 1 ? "" : "s"}, median ${esc(sign(s.median_pct))} (${esc(s.sample_flag.replace("_", " "))})`
        : `${esc(IMPACT_NAMES[h] || h)}: no data`)).join("<br>");
      return `<tr><td><b>${esc(f.label)}</b></td><td class="mono">N/A</td><td class="small">${esc(f.note)}<div class="muted">${hist}</div></td></tr>`;
    }).join("");
    const readings = a.interpretations.map((i) => `<li><b>${esc(READING[i.stance] || "Or")}:</b> ${esc(i.text)}</li>`).join("");
    return `<p class="muted small">${esc(a.score_note)} Assessed ${esc(utc(d.assessed_at))}. ${esc(d.note)}${d.macro_window ? " Near a macro release, or with unknown release dates, so it is left out of the main tracker figures." : ""}${d.eligible ? "" : " Not counted by the tracker: made more than 24 hours after publication, or its time is flagged."}</p>
      <div class="table-wrap"><table class="table stack"><thead><tr><th>Horizon</th><th>Label</th><th>Evidence score</th><th>Why</th></tr></thead><tbody>${rows}</tbody></table></div>
      <h4>Competing readings</h4><ul class="small intel-study-notes">${readings}</ul>
      <h4>Factors</h4><p class="muted small">Only the historical reaction and reports of BTC moving can give a direction. The others change the size of the score. The event type never decides it.</p>
      <div class="table-wrap"><table class="table stack"><thead><tr><th>Factor</th><th>Value (0 to 1)</th><th>Detail</th></tr></thead><tbody>${factors}</tbody></table></div>
      <p class="muted small">${esc(d.label)}.</p>`;
  }

  async function loadImpact(details) {
    const body = details.querySelector(".intel-impact-body");
    if (body.dataset.state !== "idle") return;
    body.dataset.state = "loading";
    body.textContent = "Loading…";
    try {
      body.innerHTML = impactHtml(await get(`/api/intel/events/${Number(details.dataset.event)}/impact`));
      body.querySelectorAll("table").forEach(labelTable);
      body.dataset.state = "ready";
    } catch (err) {
      body.textContent = `Unavailable: ${err.message}`;
      body.dataset.state = "idle";
    }
  }

  function summaryHtml(item) {
    const head = `<div class="intel-title">${esc(item.title)}</div>
      <div class="intel-meta"><span class="intel-status ${esc(item.status)}">${esc(STATUS[item.status] || item.status)}</span><span class="intel-cat">${esc(item.category_label)}</span>
      <span>${item.sources_count} source${item.sources_count === 1 ? "" : "s"} · ${item.articles_count} article${item.articles_count === 1 ? "" : "s"}</span></div>`;
    if (!item.summary) return `<article class="intel-summary" data-has-summary="false">${head}<p class="muted small">${esc(item.state_text)}</p></article>`;
    const cite = (id, n) => {
      const a = item.articles[id] || {};
      return /^https?:\/\//i.test(a.link || "")
        ? `<a class="intel-cite" href="${esc(a.link)}" target="_blank" rel="noopener noreferrer" title="${esc(`${a.publisher || ""}: ${a.title || ""}`)}">${esc(a.publisher || `Source ${n}`)}</a>` : "";
    };
    const fields = item.fields.filter((f) => (item.summary[f.key] || []).length).map((f) => `<div class="intel-summary-field"><b>${esc(f.label)}</b><ul>${
      item.summary[f.key].map((p) => `<li>${esc(p.text)} <span class="small muted">[${p.citations.map(cite).filter(Boolean).join(", ")}]</span></li>`).join("")}</ul></div>`).join("");
    return `<article class="intel-summary" data-has-summary="true">${head}<div class="intel-summary-body">${fields}</div>
      <p class="muted small">${esc(item.model)}, ${esc(utc(item.created_at))}.${item.older_articles ? " Written before the newest articles arrived." : ""}${item.dropped_count ? ` ${item.dropped_count} point${item.dropped_count === 1 ? " was" : "s were"} removed by the checks.` : ""}</p></article>`;
  }

  async function loadSummaries() {
    const card = $("intelSummaries");
    try {
      const d = await get("/api/intel/summaries");
      $("intelSummaryList").innerHTML = d.items.length ? d.items.map(summaryHtml).join("") : `<p class="empty">No recent events to summarise yet.</p>`;
      $("intelSummariesMeta").textContent = d.status_text;
      $("intelSummariesNote").textContent = d.note;
      card.dataset.state = "ready";
    } catch (err) {
      $("intelSummaryList").innerHTML = `<p class="empty">AI summary unavailable: ${esc(err.message)}</p>`;
      card.dataset.state = "error";
    }
  }

  const rate = (v) => (v === null || v === undefined ? "N/A" : `${(v * 100).toFixed(1)}%`);

  function trackerRow(h, v) {
    const c = v.counted;
    const hidden = `Hidden until ${tracker.min_samples} scored, ${tracker.min_episodes} episodes`;
    const b = c.baselines || {};
    const m = v.macro_window;
    return `<tr data-horizon="${esc(h)}"><td><b>${esc(IMPACT_NAMES[h] || h)}</b></td><td class="small">${esc(v.scored_at)}</td>
      <td class="mono"><span class="tracker-rate">${c.shown ? `${rate(c.hit_rate)} (${c.hits} of ${c.n})` : esc(hidden)}</span>${c.ci95 ? `<div class="muted small">${rate(c.ci95[0])} to ${rate(c.ci95[1])}</div>` : ""}</td>
      <td class="mono">${c.n}<div class="muted small">${c.episodes} episode${c.episodes === 1 ? "" : "s"}</div></td>
      <td class="mono">${rate(b.always_neutral)}</td><td class="mono">${rate(b.random_uniform)}</td><td class="mono">${rate(b.label_mix)}</td>
      <td class="small">${esc(c.note)}</td>
      <td class="small">${m.n} scored${m.shown ? `, ${rate(m.hit_rate)}` : ""}</td>
      <td class="small">${v.not_scored} Mixed or Uncertain, ${v.pending} waiting, ${v.unscorable} no candle</td></tr>`;
  }
  let tracker = null;

  async function loadTracker() {
    const card = $("intelTracker");
    try {
      tracker = await get("/api/intel/tracker");
      $("intelTrackerNotes").innerHTML = tracker.notes.map((n) => `<li>${esc(n)}</li>`).join("");
      const t = tracker.totals;
      $("intelTrackerTotals").textContent = `Rules version ${tracker.rules_version}. ${t.assessments} assessments stored, ${t.counted_first_assessments} count (first of a story, within 24 hours), ${t.not_eligible} stored but never counted. Baselines use the same rows: always Neutral, random labels, and random labels with the same mix.`;
      $("intelTrackerTable").querySelector("tbody").innerHTML = IMPACT_ORDER.map((h) => trackerRow(h, tracker.horizons[h])).join("");
      labelTable($("intelTrackerTable"));
      card.dataset.state = "ready";
    } catch (err) {
      $("intelTrackerNotes").innerHTML = `<li class="empty">The tracker is unavailable: ${esc(err.message)}</li>`;
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
      <td class="mono">${esc(s.data_date)}<div class="muted small">${s.age_days} day${s.age_days === 1 ? "" : "s"} old</div>
        <div class="muted small mk-cadence">${esc(s.release_cadence)}</div>${s.stale ? `<div class="kpi-warn small mk-stale">${esc(s.stale_note)}</div>` : ""}</td>
      <td class="small"><span class="mk-source">${link(s.source_url, s.source)}</span><span class="muted">${esc(s.delay_label)}</span>
        ${s.notes ? `<div class="muted">${esc(s.notes)}</div>` : ""}${s.fetch_error ? `<div class="kpi-warn">Last fetch failed; showing the stored value.</div>` : ""}</td></tr>`;
  }

  async function loadMarkets() {
    const card = $("intelMarkets");
    try {
      const m = await get("/api/intel/markets");
      $("intelMkTable").querySelector("tbody").innerHTML = m.series.map(marketRow).join("");
      labelTable($("intelMkTable"));
      $("intelMkMeta").textContent = m.note;
      card.dataset.state = "ready";
    } catch (err) {
      $("intelMkTable").querySelector("tbody").innerHTML = `<tr><td colspan="5" class="empty">Markets unavailable: ${esc(err.message)}</td></tr>`;
      card.dataset.state = "error";
    }
  }

  const load = () => Promise.all([loadOverview(), loadSummaries(), loadEvents(), loadStudy(), loadTracker(), loadCalendar(), loadMarkets()]);

  $("intelStudyHorizons").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-horizon]");
    if (!b) return;
    horizon = b.dataset.horizon;
    renderStudy();
  });
  // "toggle" does not bubble, so listen in the capture phase.
  $("intelEventList").addEventListener("toggle", (e) => {
    if (!e.target.matches || !e.target.open) return;
    if (e.target.matches("details.intel-reaction")) loadReaction(e.target);
    if (e.target.matches("details.intel-impact")) loadImpact(e.target);
  }, true);

  $("intelFilters").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-group]");
    if (!b) return;
    group = b.dataset.group;
    document.querySelectorAll("#intelFilters button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    loadEvents();
  });

  window.BTCIntel = { load };
})();
