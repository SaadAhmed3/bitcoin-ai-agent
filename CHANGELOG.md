# Changelog

Versions follow the build phases (0.N = Phase N). The project is a research tool; no version is "1.0" or production software.

## 0.9.1 (Phases 1-9, UI polish) — 2026-10-30

Presentation only. No change to signal rules, weights, thresholds, `min_rr`, ENGINE_VERSION (still signal-1.7), API response formats, the database schema or any calculation. No holdout run.

**Views instead of one long page**
- The sidebar switches views (`#/overview`, `#/market`, `#/news`, `#/signal`, `#/research`, `#/paper`, `#/alerts`, `#/assistant`, `#/system`); one view is shown at a time. Every section id and `data-state` marker is kept; old anchors (`#backtest`, `#news`, …) open the view that contains the section and scroll to it. Back/Forward work, the active item has `aria-current="page"`, and the window title names the view.
- Each view loads its data when first opened; timers refresh only the visible view; the price, the alert badge (now also in the sidebar) and source health update everywhere. Below 900 px the sidebar is a ☰ menu (`aria-expanded`, closes on a link, Escape or a tap outside).

**Signal**
- Overview signal summary card: the big colour-coded signal, one plain-language reason from the first NO TRADE reason (e.g. "Volatility is extreme: too risky to judge (+1 more reason)"), setup, score "on −1…+1, not a probability", price and source, age, any pending order, and "See full reasoning →".
- Signal view: the summary and NO TRADE reasons first; then folded sections (closed by default, state remembered in the browser): evidence for/against, components table, plan/levels/invalidation, risk & volatility, related news, diagnostics, data warnings (now all of them, not the first 4).

**Research view**
- Verdict banner, then four tiles (net return vs buy & hold, max drawdown, trades, criteria passed x/7), then the equity chart; folded: criteria & baselines, funnel, walk-forward windows, trades, limitations, run settings. A compact run toolbar with a short help text on the period and entry-rule choices. With no run: one call to action, "No backtest yet. Download history, then run a 4-year full/standard backtest (about 8 min)."

**Chart, freshness, tables, consistency**
- Chart overlays from data the app already has: support/resistance (bands for 2+ touches, dashed minor levels, touches on hover), active price-alert lines ("Alert above/below X"), signal-history markers by setup with time/signal/score on hover. Each has a toggle, remembered in the browser; off by default on phones. Levels outside the visible price range are counted in a note instead of squashing the chart.
- Top-bar freshness: "Updated N s ago · next refresh in N s"; amber when the price is stale or a source fails, red when no price source answers; links to System & sources.
- Missing values go through one helper (`show()` in app.js) with context-specific text; no bare "—", "— · —%", NaN, null or undefined.
- Below 600 px the wide tables (multi-timeframe, components, sources, signal history, watchlist, paper orders, alert rules, backtest trades, walk-forward, baselines) become stacked cards (CSS + `data-label`), still semantic tables.
- Card headers: title, one-line subtitle and a kind label: LIVE / CALCULATED / MODEL ESTIMATE / HYPOTHETICAL or SIMULATED. Light-theme accent, green, red, amber and blue darkened so text meets 4.5:1. Visible `:focus-visible` ring.
- New System view: source health, app and engine version, links to Settings and to README / SECURITY / CHANGELOG, served read-only as plain text from a fixed allow-list at `/docs/<name>`.
- Inline `style=` attributes removed from the dashboard's own markup (styles are set through the DOM or CSS classes). The CSP is unchanged.

**Tests:** 491 → 493 unit (`/docs` route, version); 29 → 81 e2e (52 new in `tests/e2e/test_views_e2e.py`). Existing e2e tests were updated only to open the view that now holds their panel; one also reads criteria text with `text_content` because the criteria now sit in a closed fold, and the keyboard test reaches its focus-ring target with Tab instead of a programmatic focus.

**Live check (2026-10-30, port 5050, real database with 4.5 years of downloaded history, no stored backtest run):** screenshots `data\ui_<view>_desktop.png` and `data\ui_<view>_mobile.png` for all 9 views (1440×900, 390×844): no console errors, no horizontal overflow; read-only smoke subset 62 passed, 1 skipped (no backtest export without a run); server log without errors.

## 0.9.0 (Phases 1-9) — Phase 9: UI tests, security review, final docs — 2026-10-29

Signal engine unchanged: **signal-1.7**. No rules, weights, thresholds, `min_rr` or fixed constants changed. No holdout run.

**Playwright end-to-end tests** (`tests/e2e/`, marker `e2e`, 29 tests, ~2 min)
- The app runs in a background thread on a free `127.0.0.1` port with a temporary database and fake providers (no network, no Ollama). Seeded: candles, news (incl. a hostile `<script>` headline), a stored design-period backtest, signal history, paper settings, a watchlist and the FOMC calendar. The browser is blocked from every non-local host.
- Covers: ready markers, price, chart and no console errors; signal panel; multi-timeframe and analysis; news links and the empty feed; backtest verdict, greyed criteria, funnel, selects and design badge; risk calculator; paper BUY → SELL → Reset; assistant rule answer, Sources and the offline badge; alert centre; settings save/reload/reset and key status; every export button; theme persistence; nav jumps; no horizontal scroll at 390×844; accessible names, chart labels and keyboard Tab.
- `python -m pytest` excludes them (`pytest.ini`: `-m "not e2e"`); run them with `python -m pytest -m e2e`. Tests marked `smoke` are read-only and can run against a live app via `E2E_BASE_URL`.

**Security review** (see [SECURITY.md](SECURITY.md), 49 tests in `tests/test_security.py`)
- Loopback bind by default; `APP_ALLOW_REMOTE=1` needed for a network bind (with a warning); debugger never on a network bind.
- Host allow-list (DNS rebinding) and same-origin / `X-Requested-With: BTCSignalLab` requirement for every POST/PUT/DELETE (CSRF). The dashboard JS sends the header.
- CSP, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`; the page config moved from an inline script to a JSON data block so scripts can stay `'self'`.
- 64 KB body limit; malformed/non-object JSON → 400; per-endpoint rate limits with `Retry-After`.
- Fixed: non-finite numbers (`nan`, `inf`, `1e400`) caused a 500 on paper orders and were stored/returned as invalid JSON; now 400 everywhere.
- Fixed: `POST /api/risk/size` with a non-object `settings` caused a 500; now 400.
- Fixed: RSS items with `javascript:`/`data:` links are dropped at ingestion, and every data-driven link on the dashboard goes through `safeUrl()`.
- Requirements pinned to exact versions; `pip-audit`: no known vulnerabilities.

**UI fixes**
- Anchor jumps landed 168 px below the top (the html `scroll-padding-top` and a section `scroll-margin-top` added up); now one 84 px offset, just below the sticky bar.
- On a first start (no backtest yet) every dashboard load logged a console error (404 from `/api/backtest/latest`). The dashboard now asks `/api/backtest/runs` first; the API itself is unchanged. Found by the live smoke run; covered by the new fresh-install e2e test.
- Accessible names for the chat box, the timeframe group, every settings input and the charts (`role="figure"` + label); empty favicon to avoid a console 404.

**Docs**
- README final pass: complete Windows steps (Python check, venv, ExecutionPolicy, Ollama, tests), architecture, data-source/cost table, security summary, honest results, tested vs unverified, troubleshooting, limitations. `SECURITY.md` and this changelog added.

**Tests**
- One existing assertion updated on purpose: `test_settings_validation_persistence_and_reset` checked for `theme: "light"` in an inline script; it now checks `"theme": "light"` in the JSON config block (the behaviour moved for the CSP).
- 442 → 491 unit/API tests, + 29 e2e tests.

**Live check (2026-10-29, real app on port 5050, live data, fresh database)**
- Binance BTC/USDT ~80,8xx, fresh; signal NO TRADE; all `data-state` markers ready; Ollama 0.40 + qwen2.5:3b reachable.
- Read-only e2e smoke subset (`E2E_BASE_URL`): 21 passed, 1 skipped (no backtest export button on a fresh database), after the console-404 fix above (the first smoke run failed on it).
- Screenshots `datainal_desktop.png`, `final_mobile.png`, `final_settings_desktop.png`, `final_settings_mobile.png` (1440×900 and 390×844): no console errors, no horizontal overflow. Server log: no errors.

## 0.8.0 — Phase 8: alerts, exports, settings, watchlist, calendar

- Alert rules (price above/below with hysteresis, big move, significant news per story cluster, high volatility, signal change, source failure, SIMULATED risk limit), cooldowns, dedupe, a global hourly cap, SQLite state, and a background AlertMonitor (60 s) started only by `python app.py`. Alert centre with unread badge, acknowledge and a rule editor; optional browser and Windows notifications.
- CSV/JSON exports (BOM, UTC + local time, CSV-injection protection, 100k-row cap) for candles, signals, news, backtests, paper orders, alerts and chat.
- Settings page with env > settings DB > default precedence, validation and per-section reset; the CoinGecko key shown only as set / not set.
- Watchlist (one batched Binance request, Coinbase fallback) and an economic calendar (FOMC from federalreserve.gov; BLS optional with a contact e-mail).
- Assistant: AI rewrites off by default; a busy AI request returns the rule draft instead of HTTP 429.

## 0.7.x — Phase 7 / 7.1: local AI assistant and score study

- Optional local assistant via Ollama (default qwen2.5:3b): numbered fact sheet, citations and a server-built Sources list, advice/prediction filter, rule-based answers without Ollama.
- 7.1: hybrid routing (factual intents always answered by rule templates), AI only rewrites a grounded draft, number/run/publisher/claim checks with "AI withheld" fallback, validation-run vs latest-run wording, qwen3:4b compared (all rewrites timed out).
- Score study on design data only: no measurable predictive value (Spearman −0.018, 95% CI [−0.093, +0.051]).

## 0.6.x — Phase 6 / 6.1 / 6.2: backtest, validation gate, paper trading, risk

- Bar-by-bar walk-forward backtest of the live engine with no look-ahead, costs, baselines (buy-and-hold, EMA 50/200, random entries) and seven validation criteria; a validation applies only to the exact engine version and settings.
- SIMULATED paper account and a spot-only risk calculator (costs included, exposure cap, risk halved after losses, never increased).
- 6.1: signal funnel, greyed "not meaningful" criteria, walk-forward reasons.
- 6.2 (engine signal-1.7): pre-registered breakout and pullback entry rules, fixed design/holdout split at 2025-10-29, 56-day forward observation after a validation.
- Result: run #3 (4 years, standard) NOT VALIDATED: 6 trades, +7.52%, max DD 3.45% vs buy & hold +281.87% and EMA 50/200 +241.99%; the R/R ≥ 1.5 rule drops 98.8% of raw buys. Design-period entry rules failed (breakout profit factor 0.67; pullback 83 trades, −15.9%). The holdout is untouched.

## 0.5.x — Phase 5: transparent signal

- Weighted component score (trend, momentum, volume, structure, overextension, news) with documented weights, NO TRADE safeguards, trade plan with reward/risk after costs, signal history and an audit log. Two live reviews with fixes in 5.1 and 5.2.

## 0.4.x — Phase 4: news intelligence

- Six RSS feeds, de-duplication into events, rule-based categories, sentiment, impact, verification and freshness ("priced in"), source links. Rules v2.2 frozen after three live reviews and 117 regression cases.

## 0.3.0 — Phase 3: technical analysis

- Indicators (EMA, RSI, MACD, ATR, ADX, Bollinger, VWAP, volume), structure, support/resistance, volatility regimes, divergence and a multi-timeframe snapshot, all on closed candles with a no-look-ahead test.

## 0.2.0 — Phase 2: market data

- Binance → mirror → Coinbase price and candle fallback, CoinGecko overview, caching, freshness labels, closed-candle rule, candlestick chart with Plotly served locally.

## 0.1.0 — Phase 1: foundation

- Flask app, config from env/.env, SQLite schema, dashboard shell, test suite with no network.
