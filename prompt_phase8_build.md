# Task: Part 0 (assistant finish-up), then build Phase 8 (alerts, exports, settings, watchlist, calendar)

Project: F:\bitcoin-ai-agent (BTC Signal Lab: Flask + SQLite + pandas, Windows 11, Python 3.13, .venv). The current version is 0.7.1, engine signal-1.7, with 389 tests passing. Ollama has qwen2.5:3b.

This file is the complete task. Ignore any earlier, partial request.

## Rules (for the whole task)
- Back up the project, including data\*.sqlite3, to F:\bitcoin-ai-agent-backup-<timestamp>. Confirm 389 tests pass first.
- Do not change signal rules, weights, thresholds, `min_rr`, fixed constants or ENGINE_VERSION.
- No holdout run. No paid service, cloud model or API key.
- Never place real orders. Never weaken or delete tests (update a test only when the behaviour it pins is deliberately changed, and say so).
- Never fabricate output; show real errors.
- Respect every data source's rate limits and terms. Reuse the existing cached services and HttpClient instead of adding new polling of the same endpoints.

---

## Part 0 - Assistant finish-up (small)
1. Add the setting `assistant_ai_explanations` (default **false**).
   - When false, explanation questions and `/api/assistant/explain` return the grounded rule draft (instant), with the note "AI rewrite is off (Settings → Assistant)".
   - When true, the current AI rewrite flow is used, labelled "AI rewrite (local <model>) - may contain mistakes; check the Sources".
2. Change the loader text to "Thinking locally… usually 1-3 min on CPU". Keep the 150 s timeout.
3. If the user cancels, or a previous AI request is still running, the next AI question shows "The previous AI request is still finishing on your PC - try again shortly, or use the instant answer". Return the rule draft immediately instead of a bare 429.
4. Live check with AI on: ask "Why is momentum bearish or bullish right now?" and "Explain the signal in simple words". Report the verbatim answers and whether either was withheld. This verifies the MACD false-positive fix.
5. Add tests for the setting, the default-off behaviour and the busy fallback.

---

## Phase 8

### A. Alerts (services/alerts.py)
**Rule types.** All are user-configurable; each has enabled/disabled and a cooldown:
1. **price_above / price_below:** fire once when crossed. Re-arm only after the price moves back ≥ 0.5% (hysteresis).
2. **big_move:** |price change| over 1h ≥ X% (default 3%), or over 24h ≥ Y% (default 8%), from stored snapshots/candles.
3. **significant_news:** a new event with impact "high" and freshness "new". One alert per deduplicated event cluster, never per publisher.
4. **high_volatility:** the 4h or 1h volatility regime becomes "extreme" (fires on the transition only).
5. **signal_change:** the final signal or setup changes versus the last alerted value.
6. **source_failure:** a provider fails ≥ 3 consecutive times, or the price becomes stale. A recovery alert follows when it's OK again.
7. **risk_limit:** the paper account hits its daily loss limit, or exposure exceeds the maximum. Labelled SIMULATED.

**Alert record (SQLite table `alerts`):** id, rule_id, kind, severity (info/warning/critical), title, message, reason, value, threshold, source, links_json, created_at (UTC), acknowledged_at. **Every alert must include a timestamp, the reason, and source links where applicable** (price provider, news article links, signal id).

**Anti-spam:**
- per-rule cooldown (default 30 min; 6 h for news per cluster);
- identical-message dedupe within the cooldown;
- a global cap of 20 alerts per hour, after which a single "alerts paused for spam protection" alert is created;
- state survives restarts (stored in SQLite).

**Monitor:** a background `AlertMonitor` thread evaluates the rules every 60 s, using the existing cached services. It must not start under tests (create_app flag), must stop cleanly, and must catch and log every error. A failing check never kills the thread.

**Delivery:**
- **In-app alert centre:** a badge with the unread count in the top bar, plus a list with Acknowledge and Acknowledge all.
- **Browser notifications:** only after the user clicks "Enable browser notifications" (Notification API; check permission).
- **Windows desktop notifications:** optional, only if a maintained free library works on Python 3.13 here. Check `plyer` first. If none works reliably, skip it and say so. Notification failures never break alerts.

**Routes:**
- GET/POST /api/alerts/rules
- PUT/DELETE /api/alerts/rules/<id>
- GET /api/alerts?unread=1&limit=
- POST /api/alerts/<id>/ack
- POST /api/alerts/ack_all
- POST /api/alerts/test (creates a clearly labelled TEST alert)

All inputs are validated (400 on bad values).

### B. Exports (services/exports.py)
- `GET /api/export/<dataset>?format=csv|json`. Datasets:
  - candles (tf, from, to);
  - signals (signal history);
  - news (hours);
  - backtest (run id: summary, criteria, trades, equity);
  - paper_orders;
  - alerts;
  - chat.
- **CSV:**
  - UTF-8 with BOM (so Excel opens it correctly), ISO UTC times plus a local-time column;
  - **CSV-injection protection:** prefix `'` to any cell starting with = + - @ tab or CR, except plain numbers;
  - filename `btc-signal-lab_<dataset>_<UTC timestamp>.csv`.
- JSON includes metadata: generated_at, app version, engine, source notes, and "hypothetical"/"SIMULATED" labels where relevant.
- Size limits: max 100k rows, with clear 400 errors.
- Add export buttons in the relevant panels (signal history, news, backtest, paper, alerts).

### C. Settings page (/settings, templates/settings.html)
Sections, each validated, persisted in the `settings` table, with "Reset section to defaults":
- **General:** display timezone (validate with zoneinfo), theme, refresh intervals (with sensible minimums so free rate limits are respected).
- **Market data:** provider order (from the supported list), and the CoinGecko demo key status **shown only as "set"/"not set"** (keys stay in .env and are never displayed, returned by any API or written to the DB).
- **Signal:** the existing editable config, with a clear warning: "Changing these makes the current validation stale; the signal stays NO TRADE until a new backtest validates the new settings."
- **Risk & paper:** the existing settings (SIMULATED labels).
- **Alerts:** rule defaults, cooldowns, the global cap, and enable/disable for each delivery channel.
- **Assistant:** AI explanations on/off, model name (validated against `ollama list` when available), timeout.
- **Watchlist:** symbols (see D).
- **Effective value source:** show whether each value comes from the default, the settings DB, or an env override. Env overrides win and are shown read-only.
- Settings changes take effect without a restart where reasonable; otherwise show "restart required".

### D. Watchlist (dashboard item 11)
- Up to 10 Binance spot symbols (default BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT). Validate them against Binance exchangeInfo (cached for 24 h).
- Show last price, 24h change and the source timestamp from ONE batched public ticker request per refresh, cached ≥ 60 s.
- **Display only:** no signals for other coins. Stale labels, and the Coinbase fallback where available.

### E. Economic calendar (dashboard item 7)
- Research whether a **free, keyless, terms-compliant** machine-readable source exists for verified upcoming US events (FOMC meetings, CPI, PPI, jobs report, GDP). Examples to check: official BLS/BEA/Federal Reserve release schedules (ICS/JSON/RSS). Do not scrape pages whose terms forbid it, and do not bypass anything.
- If found: implement a cached fetch (once per day) with source links. Show upcoming events in the next 14 days, with times in UTC and local time.
- If a source needs a free key (e.g. FRED), make it optional via .env and document it.
- If nothing suitable exists: show an honest empty state ("No free verified calendar source configured") and document why.
- Add every new source to `SOURCE_DOCS` and the README sources table: key required? limits, refresh, delay, failure behaviour, terms.

### F. UI
- Top bar: an alert bell with an unread badge, plus a Settings link.
- An alert centre panel.
- Export buttons.
- A watchlist card.
- An economic calendar card.
- Loading, empty and error states everywhere. Dark and light themes. No horizontal scroll at 390 px.
- Remove the Phase 8 item from the roadmap.

### G. Tests (deterministic, no network)
At least:
- each alert rule fires correctly and only on transitions/crossings;
- hysteresis re-arm; cooldowns; dedupe; news fires once per cluster;
- the global cap and the single "paused" alert;
- state persists across restart; the monitor survives an exception;
- the monitor is not started in tests;
- alert records always have a timestamp, reason and links;
- risk alerts are labelled SIMULATED;
- every alert route, with 400s;
- exports for each dataset in CSV and JSON: CSV-injection escaping, the BOM, row limits, metadata labels;
- the key is never exposed (settings API, export, page HTML);
- settings validation and persistence, env-override precedence, reset to defaults;
- the stale-validation warning appears when the signal config changes;
- watchlist symbol validation, the batched request, cache and stale handling;
- calendar: the parser on a fixture, the empty state, and failure handling;
- Part 0 tests.

Run the full suite until it passes.

### H. Live check
1. Start the app: $env:APP_PORT="5050"; python app.py
2. Alerts:
   - create price_above slightly BELOW the live price and price_below slightly ABOVE it (both should fire within about 60 s); then confirm they don't fire again (hysteresis/cooldown);
   - POST /api/alerts/test;
   - report each alert verbatim (title, reason, value, threshold, links, created_at);
   - say whether Windows notifications worked;
   - delete the test rules afterwards.
3. Exports: download signals, news, backtest (run 3) and paper_orders in CSV and JSON. Report the row counts and the first 3 lines of each CSV. Confirm the CSV opens correctly (BOM, columns).
4. Settings: change the display timezone and a refresh interval, restart the app, and confirm they persisted. Confirm the CoinGecko key shows only "set"/"not set". Reset them afterwards.
5. Watchlist: report the prices, the source and the timestamp. The calendar: report its source and events (or the empty-state reason).
6. Playwright at 1440x900 and 390x844: screenshot the alert centre, settings page, watchlist and calendar to data\phase8_*.png. Capture console errors.
7. Acknowledge and clear the test alerts. Stop the server.

### I. README
- Phase 8 section: alert types and anti-spam, exports, settings and env precedence, watchlist, calendar source (or why there's none).
- The updated sources table, test count, and tested vs unverified list.
- APP_VERSION "0.8.0 (Phases 1-8)".

## Report
1. Part 0 results, with verbatim AI answers.
2. Files changed, and tests before → after.
3. The alert live results, verbatim.
4. Export results.
5. Settings persistence.
6. Watchlist and calendar results (and the source decision, with reasons).
7. Desktop-notification library decision.
8. Screenshots and console errors.
9. Anything untested.

## Final summary (required)
- tests;
- whether each of the 7 alert types was verified live or by unit test only;
- exports OK;
- settings OK;
- watchlist and calendar status;
- notification channels working;
- open issues.
