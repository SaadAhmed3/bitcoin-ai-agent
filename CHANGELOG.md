# Changelog

Versions follow the build phases (0.N = Phase N). The project is a research tool; no version is "1.0" or production software.

## 0.9.7 (holdout contaminated; forward data only) — 2026-10-31

Owner's decision: the 2025-10-29 holdout is contaminated for the live engine too (full run #1 replayed the live engine
over that year; its sixth trade is inside it). **No signal rule changed** (only the NO TRADE reason text). **No
holdout run.**

- **Recorded, append-only** (`holdout_contaminations`, schema 11): at app start every stored full run that reached
  into a holdout period marks it contaminated (run #1 → the 2025-10-29 holdout); a new full run that reaches into the
  current holdout period does the same.
- **Cannot validate.** A contaminated period gets no counting holdout run: runs are stored as "not valid for
  validation: holdout contaminated (seen via full run #N on <date>)" (badge "HOLDOUT CONTAMINATED"), and a stored
  validation from that period does not lift NO TRADE.
- **Forward data only.** A new holdout start must be **after today** (and after any spending run). The Research view
  shows "Holdout status: contaminated (seen via full run #1 on 2026-10-29), cannot validate" with the new-start box;
  the NO TRADE reason says "validation now needs forward data (Research -> Forward tests, or a new holdout start after
  today)".
- Docs: README, USER_GUIDE, BACKTEST_VALIDATION_REPORT updated. Manual DB backup made before the schema change.
- Tests: new `tests/test_holdout_contamination.py` (8) and an e2e test, shown failing first. Updated deliberately: the
  schema-version test (11) and the 0.9.3 holdout e2e test (a new start must now also be after today).

## 0.9.6 (research-1: H1/H2 and forward tests) — 2026-10-31

**No live-signal change (engine signal-1.8). No holdout run.** Research only, simulated only.

- **H1** (daily close vs SMA 200, long or cash): researched on design data only (`scripts/research_h1.py`,
  `data/research_h1_design.md`). +209.81%, max drawdown 31.90%, return/drawdown 6.58, 15 trades vs buy & hold
  +161.27% / 66.73% / 2.42; beats 84.7% of random entries. Sensitivity (SMA 150/200/250, band 0-2%) stable. **Failed
  on design data (C3 84.7% < 90%)**; criteria not changed after seeing results; holdout not run
  (`PREREGISTRATION_H1.md`).
- **Holdout contamination:** run #1 displayed buy & hold and EMA 50/200 through the 2025-10-29 holdout year, so that
  holdout is not used for trend-following hypotheses (noted in the research documents).
- **H2** (daily EMA 50/200 crossover): pre-registered (`PREREGISTRATION_H2.md`) for a **forward** test only; criteria
  C1-C3 fixed now; verdict on exactly the first 365 processed days.
- **Forward paper tracker** (`services/forward_tests.py`, fingerprint `5e4b8a0e…`): H1, H2 and buy & hold on new
  closed daily candles from the next UTC midnight after the background monitor first runs it; decisions, simulated
  fills and marks appended to the database (append-only, schema 10). New Research panel "Forward tests" (day N of
  365) and `GET /api/forward` (read-only).
- Tests: 592 → 607 unit, 88 → 89 e2e.

## 0.9.5 (batch 2: data checks, signal-1.8) — 2026-10-31

Changes **live signal behaviour**. **Engine signal-1.7 → signal-1.8.** **The holdout was not run**; the full 4-year
window was not re-run. Each fix was written test-first (shown failing on the previous code) and is its own commit.

### Exactly what signal-1.8 changed

**Score**
| | signal-1.7 | signal-1.8 |
|---|---|---|
| News in the weighted score | yes, 15%, whenever there was enough directional news | **no** (weight 0) |
| Other weights (settings) | 0.30 / 0.25 / 0.10 / 0.10 / 0.10 | unchanged |
| Effective weights live | 30 / 25 / 10 / 10 / 10 / news 15% with news; 35.29 / 29.41 / 11.76 ×3 without | **always 35.29 / 29.41 / 11.76 / 11.76 / 11.76%** |
| Thresholds, agreement, min_rr, costs | +0.25 / +0.50 / −0.25, 60% / 75%, 1.5, 0.10% + 0.05% | unchanged |

The signal-1.8 live formula is the one every backtest used: the backtest never had news (it passes no news summary,
so news was excluded and the rest rescaled). Proof on the design period (decisions 2022-10-30 → 2025-10-28, no
candle at or after the 2025-10-29 holdout start loaded): **6,566 of 6,566 bars identical** between 0.9.4 and
signal-1.8 (setup, raw setup, tradable, unrounded score, stop, target, downgrade, gate keys), and the same **5 trades,
+9.08%**. The golden synthetic replay test passes unchanged.

**NO TRADE gates** (nothing was loosened)
| Gate | signal-1.7 | signal-1.8 |
|---|---|---|
| Stale candles (> 2 intervals) | 4h, 1d | **1h, 4h, 1d** (stricter) |
| Missing candles (> 5) | 4h, 1d | **1h, 4h, 1d** (stricter) |
| Exchanges disagree | — | **new**: second exchange differs by more than `max_source_divergence_pct` (default 0.5%, setting 0.1-5) **after converting USDT to USD with Coinbase's USDT-USD rate** → NO TRADE showing raw prices, the rate, the converted price and the raw gap. Quotes and the rate observed > 15 s apart are not compared; no usable rate → raw gap as a warning only; a missing/stale/unreachable second source → warning only |
| News against the setup, high impact, **confirmed** | blocks < 2 h | blocks < 2 h **and 2-24 h if not priced in** (stricter) |
| … **unverified** (incl. "reportedly"/rumour wording) | blocks < 2 h | blocks < 2 h ("unverified, short block") — unchanged |
| … **speculative** (opinion, forecast, question) | blocks < 2 h | blocks < 2 h ("speculative, short block") — unchanged; 2-24 h: **new warning** |
| Direction | bearish blocks BUY/STRONG BUY, bullish blocks SELL; HOLD never | unchanged |
| All other gates (price, data, volatility, contradiction, daily trend, calculation error, validation) | — | unchanged |

Reason texts now name what is affected (e.g. "1h candles are stale …; momentum and overextension would use old
data") and news reasons show headline, publisher, age and link.

### Other changes
- **GET /api/paper is read-only (B16).** Simulated stop/target exits happen only in the 60-second background monitor,
  through the shared atomic close path. The response no longer has `auto_exit`.
- **Settings:** a news weight above 0 is rejected ("news can only block"). A stored news weight above 0 from an older
  version is set to 0 once, keeping the other weights in their proportions, with a one-time notice on the Settings page.
  New range-checked setting `max_source_divergence_pct`.
- **Stablecoin deviation:** USDT-USD more than 1% away from 1.00 → a separate warning and a "Stablecoin deviation"
  alert (own cooldown key); never a block by itself.
- **Alerts:** "Price not cross-checked" (from the existing source-failure rule) uses its own cooldown key and the
  duplicate check; no alert when no second source is configured. A failing second source also counts toward the
  existing "source failing 3 times in a row" alert.
- **Validation reason wording:** a stale record no longer says "re-run it to validate": a full run is described as
  "no holdout run yet … cannot validate", and a stale holdout record asks for a new holdout period.
- **Docs:** README rewritten (~260 lines, current behaviour only); new USER_GUIDE.md for beginners (PowerShell
  commands, every screen, signal, NO TRADE, backtests, paper trading, risk example, troubleshooting, data limits),
  linked from the System view.

### Live signal before/after (same inputs, 2026-10-30 21:11 UTC, BTC 76,733.56 USDT)
| | 0.9.4 / signal-1.7 | 0.9.5 / signal-1.8 |
|---|---|---|
| Signal | NO TRADE | NO TRADE |
| Setup | SELL / REDUCE EXPOSURE | SELL / REDUCE EXPOSURE |
| Score | −0.306 | −0.290 |
| News | bearish −0.394, counted at 15% | bearish −0.394, not counted |
| NO TRADE reasons | 4h volatility extreme; not validated | 4h volatility extreme; not validated |
| Cross-check (live, normal caching) | — | raw Binance vs Coinbase gap 0.22-0.27% (mostly USDT vs USD); after converting with USDT-USD 0.9986 (measured 22:39 UTC): **0.08-0.10%**, well within the 0.5% limit |

### Tests
554 → 592 unit (coverage 94.0%) (+ `tests/test_price_cross_check.py`, `tests/test_news_blocking.py`), 87 → 88 e2e. Updated
deliberately (old rules, listed in each commit): the export test's engine pin; `test_news_moves_the_score` →
`…does_not_move…`; the config API test (default weights sum 0.85, reset gives news 0); the config-hash test changes
trend instead of news; the stale-reason assertion; the docs-route test (4 documents).

## 0.9.4 (batch 1b follow-up: stored holdout verdict) — 2026-10-31

**Signal engine unchanged: signal-1.7. The holdout was not run** (synthetic data and fake runners only). Regression
tests were written first and shown failing on 0.9.3.

- **`services/backtesting.py` is part of the rule code.** The code fingerprint in the rule-set hash now covers
  `signal_engine.py`, `technical_analysis.py` and `backtesting.py` (fills, stop handling, time stop, pass criteria).
  Relaxing a criterion in code, e.g. `min_trades` 30 → 10, changes the fingerprint, so a stored validation no longer
  counts for the edited code.
- **Holdout verdict stored at run time, never recalculated.** Each holdout run stores PASS / FAIL and the criteria
  values used (thresholds + each criterion's name, result and detail) in the registry and in the validation record.
  Registry rows are **write-once**: database triggers abort any update or delete, except linking the stored backtest
  run once. The NO TRADE gate now reads the verdict of the run that spent the period. On 0.9.3 found: rewriting the
  validation setting as a PASS after a FAIL lifted the gate.
- **Research subtitle:** "Only the first holdout run in the current holdout period can lift the NO TRADE gate."
- **Database schema 9:** `holdout_runs.verdict`, `holdout_runs.criteria` and the write-once triggers (additive). Rows
  from 0.9.2/0.9.3 keep the run-time outcome stored in their result (`all_criteria_passed`); nothing is recalculated.
  Backups: automatic before the upgrade, and a manual one made.

**Tests:** 547 → 553 unit (new `tests/test_holdout_verdict.py`), 87 e2e (subtitle assertion added). Updated
deliberately: the 0.9.3 fingerprint test now expects three rule files; the schema-version test expects 9; the version
test expects 0.9.4; two tests that hand-write a validated record now register the spending run with a stored PASS verdict.

## 0.9.3 (batch 1b: single-use holdout) — 2026-10-30

Owner's decision: the holdout is **single-use and global**. **Signal engine unchanged: signal-1.7**; no weights,
thresholds, news weight or live-signal rule changed. **The holdout was not run** (tests use synthetic data and fake
runners). Regression tests were written first and shown failing on 0.9.2.

- **Single-use, global (replaces 0.9.2's "first run per rule set").** The first holdout run in a holdout period, whatever
  its rule set, counts and spends the period. Later runs are stored with the label "not valid for validation: holdout
  already spent on <date>, run #N" and the badge "HOLDOUT SPENT - not valid for validation". One `INSERT` decides this
  atomically; a unique index allows one counting run per period. On 0.9.2 found: a second rule set counted again on
  the same holdout.
- **New holdout start, forward data only.** `POST /api/backtest/holdout_start {"start": "YYYY-MM-DD"}` (and a box in
  the Research view, shown only when the holdout is spent). Refused while the current holdout is untouched, and for
  any date before the day after the spending run. Backtests then use the new start: holdout runs begin there and
  design runs extend up to it. The previous periods are kept in the setting `holdout_period.previous`.
- **Code fingerprint in the rule-set hash.** SHA-256 of `services/signal_engine.py` and `services/technical_analysis.py`
  (line endings normalised). On 0.9.2 found: changing the stop from 2 ATR to 2.1 ATR without an engine version bump
  left the hash unchanged. A validation now counts only while its fingerprint matches the running code and it is the
  record of the run that spent its period; 0.9.2 records (no fingerprint) never lift the gate.
- **Research view:** "Holdout status: untouched (reserved period from <date>)" or "spent on <date> by run #N"; the
  period list shows the current start; "Holdout runs made: N · K counted for validation".
- **Database schema 8:** `holdout_runs.holdout_start` (additive; existing rows get 2025-10-29). The app backs the file
  up before upgrading; a manual backup was also made. A 0.9.2 database holding two counting runs in one period still
  opens: the earliest one is the spender, and the unique index is skipped instead of rewriting data.

**Tests:** 537 → 547 unit (new `tests/test_holdout_single_use.py`), 86 → 87 e2e. Updated deliberately for the new rule:
`test_new_settings_are_a_new_hypothesis_with_their_own_first_run` → `test_new_settings_do_not_get_a_second_attempt_on_a_spent_holdout`;
two tests that hand-write a validated record now include the spending registry run and the code fingerprint; the
schema-version test expects 8; the version test expects 0.9.3.

## 0.9.2 (batch 1: safety fixes) — 2026-10-30

Bug fixes and stricter safeguards from the 2026-10-30 audit ([PROJECT_AUDIT.md](PROJECT_AUDIT.md),
[BUGS_AND_RISKS.md](BUGS_AND_RISKS.md)). **Signal engine unchanged: signal-1.7.** No change to weights, thresholds,
the news weight, `min_rr` defaults or any live-signal rule. **The holdout was not run.** Each fix was written test-first
(the regression test was shown failing on the old code) and is its own commit.

**Paper trading (SIMULATED account)**
- **Atomic orders (B1).** Every buy and sell is one SQLite transaction (`BEGIN IMMEDIATE`). The account row is re-read
  inside it and written with a compare-and-set (`UPDATE … WHERE cash=<read> AND btc=<read>`); the order is recorded only
  if exactly one row changed. Before, two simultaneous refreshes recorded **two SELL orders for one position**
  (reproduced: 79.96 BTC "sold" from a 39.98 BTC holding), doubling realised P/L and the loss streak.
- **Daily loss = today's move (B11).** Today's P/L is now equity now minus equity at 00:00 UTC (today's orders reversed
  exactly; a carried-over position is marked at the 00:00 price from the stored 1h candle or a price snapshot). Before,
  the whole loss since entry counted as today's: a position bought yesterday at 100, worth 90 at midnight and 89 now,
  showed a 5.55% daily loss and blocked buys; the real move today is 0.53%. Without a 00:00 price the old,
  conservative rule applies and the Paper view says which basis was used.
- **No SELL at a stale price (B5).** A manual SELL is refused when the price is stale, like a BUY already was.
- **Stops and targets checked in the background (B6).** The 60-second background monitor now checks the simulated
  stop/target whatever view is open (before: only while the Paper view was open), through the same atomic close path.

**Signal and settings**
- **Range-checked settings (B2).** The server rejects fees or slippage below 0 or above 1%, `min_rr` ≤ 0, thresholds
  outside the score's −1…+1 range or not ordered `−1 ≤ sell < 0 < buy < strong ≤ 1`, and agreement / counter-trend values
  outside 0…1. Messages name the field, the allowed range and the value. Out-of-range values stored by older versions
  are ignored (defaults apply). Risk per trade must lie in (0, `max_risk_pct`]: it used to be capped silently and is
  now rejected. The Settings page shows each allowed range and the server's message.
- **Calculation errors → NO TRADE (P1-5).** An exception in data fetching, indicator analysis or the engine now gives
  NO TRADE with the reason "Calculation error in <stage>: …" (stored in signal history) instead of an HTTP 500.

**Validation protocol**
- **Only the holdout can validate (B3).** Full-period runs include the design data the rules were developed on; they
  are now labelled "FULL PERIOD - includes design data, cannot validate" and never write a validation.
- **Holdout run registry (B4).** Every holdout run is recorded (time, engine version, settings, hypothesis hash,
  result; `GET /api/backtest/holdout`). Only the **first** run of a hypothesis can change the validation; repeats are
  stored and labelled "HOLDOUT ALREADY USED - not valid for validation". A failed first run cannot be retried into a
  pass. The hypothesis hash covers the trading rules but not run length, seed, window sizes or pass criteria.
  Validation records written before 0.9.2 never lift NO TRADE.
- **Database schema 7** (new table `holdout_runs`, additive; existing runs kept). The app now makes a consistent
  backup (`<db>.backup-<UTC time>-schema-<old>`) before any schema upgrade. A manual backup of the local database was
  also made before this change.

**Follow-up after manual checks (UI wording and visibility)**
- The NO TRADE reason no longer calls a full-period run "the validation backtest": "Not validated: no holdout run
  yet. Run #1 is a full-period run (it includes design data) and cannot validate." The Overview line reads "No proven
  edge yet: no holdout run has been made". The assistant's fact says the same; its check that an AI answer names the
  right validation run accepts both wordings (the new wording had briefly disabled it; a test caught that).
  Holdout records keep their wording. Text only: no gate or rule changed, engine still signal-1.7.
- The "FULL PERIOD - includes design data, cannot validate" badge now also shows for runs stored before 0.9.2 (the
  API derives it when a stored payload has none; a missing period kind means a pre-6.2 full run).
- Research view: "Holdout runs made: N" (and how many counted for validation), from `/api/backtest/status`.
- Tests: a new browser test (old full run: badge, Signal reason, Overview line, holdout count) and 2 unit tests; three
  wording tests updated deliberately. Unit 537, e2e 86, all pass.

**Extra fix (not on the batch list)**
- Source health is recorded with one atomic UPSERT. Two requests recording the first health row of a source at the same
  time raised `UNIQUE constraint failed` (HTTP 500 on a first dashboard load); it made one e2e test fail 2 runs out of 3,
  on the unchanged 0.9.1 code too.

**Tests:** 493 → 535 unit, 81 → 85 e2e; all pass. Tests changed deliberately for the new rules (not weakened):
`test_risk_ceiling_and_validation` (risk above the ceiling is now rejected instead of capped); three tests that wrote a
validated record without the registry fields, or validated through a full run, now use a first-holdout record; the
schema-version test expects 7. Coverage (pytest-cov, new dev dependency): 93.5% → 93.9% of statements (details in TEST_RESULTS.md).

**Audit documents added:** PROJECT_AUDIT, BUGS_AND_RISKS, QUANTITATIVE_AUDIT (incl. the reconciliation of buy & hold
+294.32% in run #1 with the +281.87% quoted from an earlier run #3), BACKTEST_VALIDATION_REPORT, SIGNAL_ENGINE_REPORT,
SECURITY_AUDIT, TEST_RESULTS, IMPLEMENTATION_ROADMAP.

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
