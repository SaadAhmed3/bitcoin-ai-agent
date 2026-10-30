# BTC Signal Lab — AI Bitcoin Analysis & Signal Agent

Version **0.9.1 (Phases 1-9, UI polish)** · signal engine **signal-1.7**

A local, free, single-user research tool for Bitcoin. It collects public market data and news, calculates technical indicators across timeframes, scores a transparent rule-based signal with NO TRADE safeguards, backtests that signal walk-forward, and offers a SIMULATED paper account, a risk calculator, alerts, exports and an optional local AI assistant (Ollama).

> **This is not a trading bot and not financial advice.** It never places orders, never connects to an exchange account and holds no exchange keys. Its own backtests found **no validated edge**, so the live signal stays **NO TRADE** (see [Honest results](#honest-results)).

## Build status

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation, config, SQLite, dashboard | Done, tested |
| 2 | Market data (Binance → mirror → Coinbase fallback, CoinGecko overview), candlestick chart | Done, tested, verified live on Windows |
| 3 | Technical indicators, structure, S/R, regimes, multi-timeframe | Done, tested |
| 4 | News ingestion, dedup, categories, sentiment, impact, verification, freshness, source links | Done; feeds verified live; rules v2.2 (frozen) after three live reviews |
| 5 | Transparent signal scoring + NO TRADE safeguards, trade plan, history | Done; two live reviews, fixes in 5.1/5.2 |
| 6 | Walk-forward backtest, validation gate, paper trading, risk calculator | Done; not validated. 6.1 funnel; 6.2 pre-registered entry rules + design/holdout split |
| 7 | Local AI assistant (Ollama, optional) + design-data score study | Done, tested; verified live with qwen2.5:3b |
| 8 | Alerts, exports, settings page, watchlist, economic calendar | Done, tested; verified live on Windows |
| 9 | Playwright UI tests, security review ([SECURITY.md](SECURITY.md)), final docs | Done (see [CHANGELOG.md](CHANGELOG.md)) |
| 9.1 | UI polish: one view at a time, signal summary, folded details, chart overlays, freshness, mobile tables | Done (presentation only, no logic changes) |

## The dashboard

The sidebar switches between **views**; only one is shown at a time and each loads its data the first time you open it. Every view has its own address, so bookmarks and the browser's Back/Forward buttons work. Old links such as `#backtest` or `#news` still work: they open the view that holds that panel and scroll to it.

| View | Address | What it shows | Screenshots |
|---|---|---|---|
| Overview (default) | `#/overview` | Price KPIs, the **signal summary card** (big signal, one plain-language reason, setup, score "not a probability", price and source, age, pending order, "See full reasoning →"), the price chart, the 3 latest alerts, the watchlist and the next economic event | `data\ui_overview_desktop.png`, `_mobile.png` |
| Market & chart | `#/market` | Price chart, indicator readings, structure & levels, multi-timeframe table | `data\ui_market_*.png` |
| News & calendar | `#/news` | News sentiment & impact, the news feed, the economic calendar | `data\ui_news_*.png` |
| Signal details | `#/signal` | The signal and its NO TRADE reasons on top; below, folded sections: evidence for/against, components table, plan/levels/invalidation, risk & volatility, related news, diagnostics, data warnings. Signal history & audit log | `data\ui_signal_*.png` |
| Research (backtest) | `#/research` | Verdict banner, four tiles (net return vs buy & hold, max drawdown, trades, criteria passed), the equity chart; folded: criteria & baselines, funnel, walk-forward windows, trades, limitations, run settings. Compact run toolbar with a short help text; a clear call to action when no run exists yet | `data\ui_research_*.png` |
| Risk & paper | `#/paper` | Position-size calculator and the SIMULATED paper account | `data\ui_paper_*.png` |
| Alerts | `#/alerts` | Alert centre and alert rules | `data\ui_alerts_*.png` |
| Ask the data | `#/assistant` | The local assistant | `data\ui_assistant_*.png` |
| System & sources | `#/system` | Data-source health, app/engine version, links to Settings, README, SECURITY and CHANGELOG (served read-only at `/docs/<name>`) | `data\ui_system_*.png` |

- **Folded sections** (`<details>`) start closed; the app remembers which ones you opened (in your browser only).
- **Chart overlays** (toggles above the chart, remembered in your browser): support/resistance from the analysis (levels with 2+ touches as shaded bands, minor levels as dashed lines; hover shows the touches), your active price alerts as dotted lines ("Alert above/below X"), and stored signal records as markers (▲ BUY setup, ▼ SELL, ○ HOLD; hover shows time, signal and score). On phones these three start switched off.
- **Freshness** in the top bar: "Updated 12 s ago · next refresh in 3 s". It turns amber when the price is stale or any source is failing, red when no price source answers, and links to System & sources.
- **Labels** on every card say what kind of number it is: LIVE observation, CALCULATED indicator, MODEL estimate, or HYPOTHETICAL / SIMULATED result.
- Timers refresh only the visible view; the price, the alert badge and source health update everywhere.
- Below 600 px wide, the wide tables turn into stacked cards (CSS only; still real tables). Below 900 px the sidebar becomes a menu (☰).
- Missing values are written out ("Not enough data", "No trades yet", "Waiting for first refresh…") instead of a bare dash.

## Windows setup (PowerShell)

Needs Windows 10/11, **Python 3.12 or newer** (the pinned numpy 2.5 / scipy 1.18 need 3.12+; tested on 3.14.6) and about 1.5 GB of disk for the packages and the test browser. Everything is free; no account or key is needed.

```powershell
# 1. Check Python and Git (Git is only needed if you clone instead of unzipping)
python --version            # must print Python 3.12 or newer
git --version
#    No Python? Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH")
#    or:  winget install Python.Python.3.13

# 2. Create a folder and put the project there (unzip it, or: git clone <repo-url> bitcoin-ai-agent)
New-Item -ItemType Directory -Force $HOME\Projects | Out-Null
cd $HOME\Projects\bitcoin-ai-agent

# 3. Create a virtual environment
python -m venv .venv

# 4. Activate it (the prompt then starts with "(.venv)")
.\.venv\Scripts\Activate.ps1
#    "running scripts is disabled on this system"?  Allow local scripts for your user once, then activate again:
#    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# 5. Install the pinned dependencies (a few minutes the first time)
python -m pip install --upgrade pip
pip install -r requirements.txt

# 6. Optional: local settings (port, time zone, Ollama, calendar e-mail). Never commit .env.
Copy-Item .env.example .env

# 7. Optional: the local AI assistant (free). The app works without it.
winget install Ollama.Ollama        # or download the installer from https://ollama.com
#    Close and reopen PowerShell so "ollama" is on PATH, then:
ollama --version
ollama pull qwen2.5:3b              # about 2 GB, one time
ollama list                         # qwen2.5:3b should be listed
#    Ollama runs in the background (tray icon). Check it answers:  Invoke-RestMethod http://127.0.0.1:11434/api/tags

# 8. Optional: check the live data sources from YOUR network (real calls)
python scripts\check_sources.py

# 9. Start the app - this is the single start command. Leave this window open; Ctrl+C stops it.
python app.py

# 10. Open the dashboard (in a second PowerShell window, or just type the address in your browser)
Start-Process "http://127.0.0.1:5000"
#    Health check:  Invoke-RestMethod http://127.0.0.1:5000/api/health
```

### Running the tests

```powershell
# Unit + API tests: deterministic, no network, no browser (about 3 minutes)
python -m pytest

# Playwright UI tests (Phase 9). One-time browser download (~150 MB, free):
python -m playwright install chromium
python -m pytest -m e2e             # about 2 minutes; starts its own app on a free port with fake data
```

- `python -m pytest` runs every test **except** the e2e ones (`pytest.ini` sets `-m "not e2e"`), so it never needs a browser or the network.
- `python -m pytest -m e2e` starts the app in a background thread on a free `127.0.0.1` port, with a temporary database and fake providers (no network, no Ollama; the browser itself is blocked from every host except the local app). It seeds candles, news (including a hostile `<script>` headline), a stored design-period backtest, signal history and paper settings.
- Read-only smoke checks against your **running** app (no paper orders, no settings changes):
  ```powershell
  $env:E2E_BASE_URL = "http://127.0.0.1:5000"; python -m pytest -m "e2e and smoke"; Remove-Item Env:E2E_BASE_URL
  ```
- Dependency audit (free): `pip install pip-audit` then `python -m pip_audit -r requirements.txt`.

## Architecture

```
 Browser (127.0.0.1 only)                                    Flask app (app.py, one process)
 ───────────────────────                                     ─────────────────────────────────────────────────
 templates/index.html  ── fetch() + X-Requested-With ──►     services/security.py   Host allow-list, same-origin
 templates/settings.html                                                            writes, CSP + headers, JSON guard,
 static/js/app.js, settings.js  (all data text escaped)                             rate limits, 64 KB body limit
 /vendor/plotly.min.js  (served from the plotly package)     JSON API routes  (app.py)
                                                                   │
            ┌──────────────────────────┬────────────────────────┼─────────────────────────┬──────────────────────┐
            ▼                          ▼                        ▼                         ▼                      ▼
   market_data.py               news_collector.py         signal_service.py         backtest_service.py    assistant.py
   Binance → mirror →           6 RSS feeds →             signal_engine.py          backtesting.py         ollama_service.py
   Coinbase; CoinGecko          news_analyzer.py          (pure scoring,            (replay, walk-forward, (local Ollama, optional;
   (http_client.py: timeouts,   news_service.py           NO TRADE gates,           validation criteria)   rule answers without it)
   retries, per-host limit)     (dedup, sentiment,        trade plan)               history.py (download)
            │                   freshness)                     │                         │
            ▼                          │                       │                         │
   technical_analysis.py               │          risk_manager.py · paper_trading.py (SIMULATED only)
   (indicators, levels,                │          alerts.py (AlertMonitor thread, 60 s) · exports.py (CSV/JSON)
   regimes, closed candles)            │          settings_service.py · watchlist.py · calendar_service.py
            └──────────────┬───────────┘
                           ▼
              database/db.py  →  data\btc_agent.sqlite3  (WAL; candles, prices, news, signals, backtests,
                                                          paper account/orders, chat, alerts, settings, health)
```

Data flow: providers are fetched on demand and cached in SQLite (price 10 s, candles a fraction of the candle interval, overview 5 min, news every 10 min). Analysis and the signal use **closed candles only**. The signal is stored on every change and hourly. A backtest replays the same signal code bar by bar and writes a validation record, which is the only thing that can lift the "not validated" NO TRADE gate (and only for the exact engine version and settings it ran with). The alert monitor reads the same cached services. Nothing leaves the PC except the documented public data requests (and Ollama, which stays on localhost by default).

## Project layout

```
bitcoin-ai-agent/
  app.py                      Flask app + JSON API (single startup command: python app.py)
  config.py                   Settings from env / .env (binds 127.0.0.1 unless APP_ALLOW_REMOTE=1)
  database/db.py              SQLite schema, migrations (constant column list) + parameterized queries
  services/security.py        Phase 9 request guards and security headers
  services/http_client.py     Timeouts, retries, backoff, per-host rate limit
  services/market_data.py     Providers, fallbacks, caching, staleness, closed-candle rule
  services/technical_analysis.py  Indicators, structure, levels, regimes, divergence
  services/news_collector.py  RSS fetch + parse (feedparser), per-feed health, polite polling, http(s) links only
  services/news_analyzer.py   Rule-based categories, sentiment, impact, verification, freshness, dedup
  services/news_service.py    Combines news with BTC prices for the 'priced in' check
  services/signal_engine.py   Component scoring, weights, NO TRADE gates, trade plan (pure functions)
  services/signal_service.py  Gathers inputs, stores signal history, signal settings
  services/history.py         Downloads historical candles from Binance (paged, validated)
  services/backtesting.py     Bar-by-bar replay, trade simulation, baselines, walk-forward, validation criteria
  services/backtest_service.py  Background runs (single-flight), progress, records the validation result
  services/risk_manager.py    Position sizing, max loss at stop, limits, no size-up after losses
  services/paper_trading.py   SIMULATED account: orders, stops/targets, daily loss limit
  services/ollama_service.py  Local Ollama client (status, non-streaming chat); every failure -> fallback
  services/assistant.py       Fact sheet, system prompt, citations/sources, rule-based answers, AI checks
  services/settings_service.py  Settings page values: validation, env > settings DB > default
  services/alerts.py          Alert rules, anti-spam, SQLite state, AlertMonitor thread
  services/exports.py         CSV/JSON exports (BOM, UTC + local time, CSV-injection protection, 100k-row cap)
  services/watchlist.py       Watchlist prices (one batched Binance request, Coinbase fallback)
  services/calendar_service.py  FOMC calendar (Fed) + optional BLS releases
  templates/index.html, templates/settings.html
  static/css/app.css, static/js/app.js, static/js/settings.js
  scripts/check_sources.py    Live connectivity check
  scripts/download_history.py Download 1h/4h/1d history from the command line
  scripts/review_news.py      Re-analyse stored/live news and print a review report
  scripts/score_study.py      Research: does the score predict forward returns? (design data only)
  tests/                      493 unit/API tests (no network)
  tests/e2e/                  81 Playwright UI tests (python -m pytest -m e2e)
  SECURITY.md                 Threat model, review findings, how to report issues
  CHANGELOG.md                Phases 1-9
```

## API

All routes are on `127.0.0.1` only. POST/PUT/DELETE from outside the dashboard need the header `X-Requested-With: BTCSignalLab` (else 403). Bad input returns 400 with `{"error": "..."}`; rate-limited endpoints return 429 with `Retry-After`.

| Endpoint | Returns |
|---|---|
| `GET /api/health` | App + DB status |
| `GET /api/price` | Latest price, source, observed time, freshness |
| `GET /api/overview` | Market cap, dominance (CoinGecko) |
| `GET /api/candles?tf=1h&limit=300` | Closed candles + overlays |
| `GET /api/analysis?tf=1h` | Full indicator analysis for one timeframe |
| `GET /api/mtf` | Snapshot across 5m, 15m, 1h, 4h, 1d |
| `GET /api/news?hours=72&sentiment=&category=` | News events with sentiment, impact, verification, freshness, sources |
| `POST /api/news/refresh` | Force-fetch all feeds now |
| `GET /api/signal` | Current signal with full reasoning (stored on change) |
| `GET /api/signal/history?limit=50` | Signal audit log |
| `GET /api/signal/<id>` | Full stored signal |
| `GET` / `PUT /api/signal/config` | View or change weights and thresholds (`{"reset": true}` restores defaults) |
| `POST /api/backtest/run` `{"years": 4, "download": true, "entry_mode": "standard", "period": "full"}` | Start a backtest in the background. `entry_mode`: standard / breakout / pullback; `period`: full / design / holdout (bad values return 400) |
| `GET /api/backtest/status` | Progress, stored history, current validation status |
| `GET /api/backtest/latest`, `/api/backtest/runs`, `/api/backtest/<id>` | Results |
| `POST /api/risk/size` | Position size, max loss at stop (`portfolio_value`, `entry`, `stop`) |
| `GET /api/paper`, `POST /api/paper/order`, `/api/paper/reset`, `/api/paper/settings` | Simulated account |
| `GET /api/sources` | Source health + documentation |
| `GET` / `POST /api/alerts/rules`, `PUT` / `DELETE /api/alerts/rules/<id>` | Alert rules (400 on bad kind/params/cooldown) |
| `GET /api/alerts?unread=1&limit=50` | Alerts (newest first), unread count, whether the monitor runs |
| `POST /api/alerts/<id>/ack`, `POST /api/alerts/ack_all`, `POST /api/alerts/test` | Acknowledge; create a clearly labelled TEST alert |
| `GET /api/export/<dataset>?format=csv\|json` | candles (`tf`, `from`, `to`), signals, news (`hours`), backtest (`run`, CSV `part`=trades/equity/criteria/summary), paper_orders, alerts, chat |
| `GET /api/settings`, `PUT /api/settings/<section>`, `POST /api/settings/<section>/reset` | Settings with each value's source (env / settings / default) |
| `GET /api/watchlist` | Watchlist prices, 24h change, source time, stale flags |
| `GET /api/calendar` | Verified US macro events in the next 14 days (or the empty-state reason) |
| `GET /settings` | Settings page |

## Data sources and costs

Every source is free. The only optional key is a free CoinGecko Demo key; the app works without it. There are no paid services.

| Source | Provides | Key | Limits | Refresh in the app | Delay | On failure | Terms |
|---|---|---|---|---|---|---|---|
| Binance spot (api.binance.com, mirror data-api.binance.vision) | BTC/USDT price, 24h change, OHLCV 5m-1d; history for backtests | None | Weight-based, 6,000/min per IP; the app uses a tiny fraction | Price cached 10 s; candles re-fetched at most every 1/5 of the interval (min 30 s) | Near real-time | Mirror → Coinbase → cached data labelled stale | Public market data; blocked in some regions (HTTP 451) |
| Coinbase Exchange (api.exchange.coinbase.com) | BTC/USD price, OHLCV (4h built from closed 1h) | None | ~10 req/s public; 300 candles/request | Same as above | Near real-time | Cached data labelled stale | Public market data; USD and USDT series are never mixed |
| CoinGecko (api.coingecko.com) | Market cap, 24h volume, BTC dominance | Optional free Demo key (`COINGECKO_DEMO_API_KEY`) | Public ~5-15 calls/min; Demo ~30/min with a monthly cap | 5 min cache | Aggregated, 1-5 min | Cached values or "unavailable" | Attribution required ("Data from CoinGecko" is shown) |
| Binance watchlist (`/api/v3/ticker/24hr?symbols=[…]`, `exchangeInfo`) | Last price + 24h change, up to 10 spot symbols | None | ONE batched request per refresh; symbol checks cached 24 h | 60 s cache | Near real-time | Coinbase `<BASE>-USD` stats where a product exists → last cached list labelled stale | As Binance above; display only |
| Federal Reserve Board FOMC calendar (federalreserve.gov) | FOMC meeting / decision dates | None | One page fetch per day (1 h back-off after a failure) | Daily | Published months ahead | Cached copy, else an honest empty state | US-government work, public domain; Board credited. 2:00 p.m. ET shown as the *usual* statement time |
| BLS release calendar (www.bls.gov iCalendar), optional | CPI, PPI, Employment Situation times | None, but set `CALENDAR_CONTACT_EMAIL` | One fetch per day | Daily | Official schedule | Skipped with the reason shown | BLS returns HTTP 403 to anonymous scripts and asks them to identify their owner, so it stays off until you add your own e-mail |
| RSS: Cointelegraph, CoinDesk, The Block, Decrypt, Bitcoin Magazine, Google News | Headlines, links, publish time, short summary | None | Polite polling | Every 10 min (`NEWS_REFRESH_SECONDS`); **Fetch now** max 3/min | Minutes after publication | Stored items still shown; feed marked as error | Only headline, a ≤300-character snippet and the link are stored; full articles stay on the publisher's site |
| Ollama (127.0.0.1:11434), optional | Local AI rewrites of rule answers | None | One request at a time; 150 s timeout | On demand (off by default: Settings → Assistant) | 1-3 min per rewrite on a CPU | Rule-based answer with the same Sources | Runs on your PC; keep `OLLAMA_URL` on localhost (the app warns at startup if it is not) |

Google News is an aggregator: the real publisher is taken from each item's `<source>` tag. Feed items whose link is not `http(s)://` are dropped.

## Correctness rules built in

- Only **closed** candles are analysed; the in-progress candle is dropped.
- Malformed candles (high < low, non-positive prices, NaN) are rejected; gaps are counted and reported.
- Prices carry their source, observation time, and a **fresh / stale / cached fallback** label. Nothing cached is shown as current.
- RSI, ATR and ADX use Wilder smoothing seeded with an SMA (matches Wilder's definition and TradingView). Verified against a hand-computed reference value.
- A test confirms indicators have no look-ahead: appending future bars never changes past values.
- Bullish/bearish counts are labelled as counts, never probabilities.
- `pandas-ta` was deliberately not used (its releases have lagged NumPy 2 / pandas 3); all formulas are implemented and tested directly.

## How news intelligence works (Phase 4)

All rule-based; no AI model needed. Every conclusion carries its reasons and a link to the source.

- **Headline only.** Sentiment and impact come from the headline. Summaries often mention unrelated context ("…despite a hawkish Fed") and are used only to find a category when the headline has none.
- **BTC relevance.** Macro news and direct BTC/crypto-market news count fully. Other crypto news is ×0.6, altcoin-only news (XRP, ETH…) ×0.3, foreign macro (e.g. rand, UK CPI) ×0.4. Off-topic aggregator items and one-to-two-word headlines are dropped.
- **De-duplication.** The same story from several outlets is grouped into one *event*. Matching uses three things:
  - title similarity that weights rare words (names like *Kalshi*) above common ones;
  - fixed patterns for scheduled releases (Fed decision, CPI, PPI, jobs, GDP, PCE), so "holds steady", "leaves unchanged" and "stands pat" all become one Fed event. Fed *previews* ("expected to hold", "what to watch") form a separate, speculative event. Foreign central-bank news never matches;
  - the same named company in the same category within 24h, with two distinctive shared words or one name plus the same action (e.g. *Bithumb* + *breach/leak*).

  Generic words ("data", "system", "breach") alone never merge two stories. Daily newsletters are never merged. The 36h window rolls with the latest article, so coverage spread over several days stays in one event.

  The earliest report is treated as the original, and an event counts once however many outlets carried it.
- **Categories.** Fed and rates, inflation and macro, dollar and yields, ETF flows, regulation, government policy, geopolitics, equities, institutional, security incidents, network and mining, crisis, price commentary.
- **Sentiment.** Bullish, bearish, neutral or uncertain, from weighted phrases (e.g. *rate cut* +2, *ETF outflows* −2, *exchange hacked* −2). Rules only fire in context: an approval must come from a regulator, a hack must hit an exchange, wallet or protocol, and a price move must be about BTC or crypto. Negation either reverses a rule (*rules out a rate cut* → bearish) or cancels it (*denies breach* → no signal). Mixed evidence, or outlets disagreeing on one story, gives **uncertain**. Rewrites with no direction don't water down an event's score.
- **Verification.**
  - *Confirmed*: official or confirming language, or two or more independent publishers.
  - *Unverified*: "reportedly", rumours, or a single uncorroborated source.
  - *Speculative*: forecasts, questions, opinion.
- **Impact.** Low, medium or high. Based on the category's typical market relevance, plus large-magnitude wording, plus breadth of coverage, minus speculation.
- **Freshness.**
  - *New*: under 2h old.
  - *Recent*: under 24h old.
  - *Likely priced in*: over 24h old, or BTC already moved ≥1.5% in the implied direction since publication.
  - *Too old*: over 72h; excluded from the bias.
- **News bias.** A weighted mean of *directional* event scores (impact × verification × freshness). Neutral events are listed but don't dilute it. It is a sentiment estimate, **not a probability** and not a signal.

**Reviewing the rules:** `python scripts\review_news.py` re-analyses the articles stored in your database (opened read-only). It prints the top events and every merged group (so wrong merges are easy to spot), and writes `data\news_review.csv` for Excel. Add `--live` to fetch the feeds instead. Every headline found to be mis-scored becomes a test in `tests\test_news_regressions.py`.

**Rules frozen at v2.2** after three live reviews of about 220 real articles each. Further keyword tuning has diminishing returns. New errors are fixed only when they move the bias, and each fix ships with a regression test.

Known limits:
- Keyword rules miss sarcasm, nuance and new phrasing, and headlines can mislead.
- Related-but-separate stories from one company, a day apart, can still merge (e.g. Ripple's OCC approval and Moody's rating of RLUSD).
- An event's freshness is measured from its earliest article, so a story with long-running coverage reads as "likely priced in" sooner.

The local AI model in Phase 7 will be an optional second opinion, never a replacement for the source link.

## How the signal works (Phase 5)

The signal answers one question for a **1–3 day horizon**: is there a long setup worth considering on spot, should exposure be reduced, or should you do nothing? The 4h chart is primary, the 1d chart is the trend filter, and the 1h chart confirms.

**Six components**, each scored from −1 to +1 with written reasons:

| Component | Default weight | What it measures | Why this weight |
|---|---|---|---|
| Trend | 30% | 1d + 4h structure, EMA stack, price vs EMA200, SMA50/200 | Higher-timeframe trend is the most persistent technical feature |
| Momentum | 25% | 4h + 1h RSI, MACD, ADX/DI | Confirms the trend is being expressed now |
| Volume | 10% | 4h volume vs 20-bar average, volume-confirmed breakouts | Participation behind the move |
| Structure | 10% | Breakouts and failed breakouts, room to nearest level, divergence | Immediate odds and room to move |
| Overextension | 10% | RSI extremes, closes outside Bollinger Bands | A brake against chasing |
| News | 15% | News bias × confidence | Can move price, but noisy and often priced in |

These weights are **reasoned defaults, not optimised values**. Phase 6 walk-forward testing will evaluate them and the alternatives. Components without data are left out, and the remaining weights are re-normalised.

**Setup.** The score is the weighted sum. ≥ +0.50 with 75% agreement is a STRONG BUY SETUP, ≥ +0.25 a BUY SETUP, ≤ −0.25 SELL / REDUCE EXPOSURE, and anything in between is HOLD. A buy setup also needs reward/risk ≥ 1.5 to target 1 *after* fees and slippage, or it drops to HOLD.

**Trade plan (buy setups only).**
- Entry zone: from the current price down to the nearest support, or 0.75 ATR.
- Stop: 0.5 ATR under support. If there's no usable support, 2 ATR; never closer than 1 ATR.
- Targets: T1 is the **lower edge of the nearest major resistance** (2+ touches) above the live price, however close. It is the first real barrier, so a close barrier gives poor reward/risk and a HOLD, with a note naming it. T2 is the next major barrier. ATR projections are used only when no resistance exists, and minor levels in between are listed as obstacles.
- Reward/risk is shown twice: buying at the live price (the top of the zone, used for the HOLD check) and a mid-zone fill.
- Costs assumed by default: 0.10% fee and 0.05% slippage per side.
- A stop is a reference level, not a guarantee.

**NO TRADE safeguards.** Any one of these turns the final signal into NO TRADE, and the dashboard lists which ones fired:
- stale or missing price
- fewer than 100 closed 4h candles
- stale candles or more than 5 gaps
- extreme 4h or 1h volatility
- contradictory evidence (less than 60% weighted agreement)
- a setup against the daily trend
- fresh (under 2h) high-impact news against the setup
- **no validated edge yet**

> **Why you'll see NO TRADE for now.** Your spec says to output NO TRADE when the strategy has no validated edge. Until Phase 6's walk-forward backtest records a validation, the final signal stays NO TRADE. The dashboard still shows the underlying **setup**, its full reasoning and a *research-only* plan. For research you can turn this off (`require_validated_edge: false`), but then the signals are unvalidated.

**How levels and structure are found (5.1).**
- *Support and resistance:* clusters of swing points from the last 200 bars. A swing joins a level only within 0.5 ATR of the level's centre, and a level is at most 1 ATR wide, so levels can't chain into a wide band. A level that contains the current price is the "current zone", never support or resistance.
- *Structure:* scored over the last four swing highs and four swing lows. Each step up counts +1 and each step down −1, averaged. So one higher high inside a run of lower highs is a range, not an uptrend.
- *MACD:* scaled by the histogram's size relative to ATR. It reaches full weight at half an ATR, so a flat histogram counts close to 0.
- *Levels for the signal:* re-sorted against the **live** price, because a level can be crossed between candle closes. Single-touch levels are marked *minor*. The entry floor, stop, bear trigger and downside figure use levels with 2+ touches; targets may use any resistance. `/api/analysis` keeps the closed-candle basis.
- *ADX:* scaled by how far apart +DI and −DI are.

**Signal history.** A record is stored when the signal or setup changes, the engine version changes, or the score moves by 0.10 or more, and otherwise once an hour. The panel says either "stored as record #N" or "unchanged since record #N".

**Reading the panel.**
- Evidence is grouped as **bullish** and **bearish** by each fact's own direction, so facts inside a component that nets to zero are still shown.
- *Agreement* is the share of directional evidence that sides with the score. *Directional weight* shows how much of the total weight carries any direction at all.
- Indicators use the last **closed** candle of each timeframe, so their prices can differ from the live header price.

**The score is not a probability.** It counts weighted evidence. No confidence percentage is shown until Phase 6 can calibrate one on out-of-sample data.

**Changing settings** (PowerShell, app running):
```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/signal/config
Invoke-RestMethod -Method Put -Headers @{"X-Requested-With"="BTCSignalLab"} -Uri http://127.0.0.1:5000/api/signal/config -ContentType "application/json" -Body '{"weights": {"news": 0.10}, "min_rr": 2}'
Invoke-RestMethod -Method Put -Headers @{"X-Requested-With"="BTCSignalLab"} -Uri http://127.0.0.1:5000/api/signal/config -ContentType "application/json" -Body '{"reset": true}'
```
Weights are validated (no negatives or unknown names) and normalised to 100%. They can also be edited on the Settings page (Phase 8).

## Backtest and validation (Phase 6)

Run it from the **Backtest & validation** panel, or `Invoke-RestMethod -Method Post -Headers @{"X-Requested-With"="BTCSignalLab"} http://127.0.0.1:5000/api/backtest/run` (state-changing requests from scripts need that header, see Security).

1. **History.** Downloads up to 4 years of 1h, 4h and 1d BTC/USDT candles from Binance's public API, plus 1.5 years of warm-up. That's about 60–80 requests, with no key needed.
2. **Replay with no look-ahead.** At every closed 4h candle, the backtest rebuilds exactly what the live app would have seen: the last 500 closed candles on each timeframe, with daily candles only once they've closed. It then runs the same analysis and signal code. Tests confirm that adding future candles never changes past decisions.
3. **Trades: spot, long only, one at a time.**
   - A BUY setup that passes every safeguard enters at the **next** 4h open.
   - The stop and target 1 come from the plan.
   - The trade exits at the stop, at target 1, on a SELL setup, or after 18 bars (3 days, the signal horizon).
   - If one candle touches both the stop and the target, the stop is assumed to hit first.
   - If a candle opens beyond the stop, the fill is at that worse open.
   - Fees and slippage are charged on entry and exit.
4. **Baselines.** Buy-and-hold; an EMA 50/200 crossover; and 200 random-entry runs with the same number of trades and the same holding times.
5. **Walk-forward.** Results are split into consecutive 6-month test windows. The default weights are never fitted, so all their results are out-of-sample. Four alternative weight sets are compared, and each window uses the one that did best on the **previous year only**.
6. **Validation.** The NO TRADE gate lifts only if **all** of these hold out-of-sample:
   - at least 30 trades;
   - profit factor ≥ 1.2;
   - positive net return after costs;
   - maximum drawdown ≤ 25%;
   - better than 90% of the random-entry runs;
   - better return/drawdown than the EMA crossover;
   - at least 60% of the windows that traded were profitable.

   A validation applies **only to the engine version and settings it was run with**. If you change weights or costs, the signal returns to NO TRADE until you re-run the backtest.

The report also gives precision, recall and balanced accuracy for BUY setups (whether the price 3 days later beat round-trip costs). There's no Brier score or calibration, because the rule engine doesn't output probabilities.

**A "not validated" result is a normal, honest outcome.** It means the rules haven't shown an edge after costs, so the app keeps saying NO TRADE.

Known limits:
- News can't be replayed, because there's no historical headline archive.
- Fills are modelled on the next 4h open, and real fills can be worse.
- The backtest puts 100% of the simulated account into each trade; position sizing is handled separately.
- Past results don't guarantee future ones.

### Signal funnel (Phase 6.1)

Each backtest report now includes a **funnel**, which shows where potential trades are lost. It covers:
- how many 4h bars had a score at or above the BUY threshold, and the score percentiles;
- how many raw BUY setups there were;
- how many of those were downgraded to HOLD because reward/risk was under the minimum (with the median and spread of the R/R they had) or because no plan was possible;
- how many each safeguard blocked (counter-trend, contradictory, extreme volatility, data problems);
- how many trades were actually taken.

A "what-if" line counts the raw BUY bars that would pass at lower minimum R/R values. It is **diagnostic only**. It is not a backtest of those settings, and changing a setting because of it would be fitting the rules to the past. Any strategy change should be judged on data kept out of the decision.

Also new in 6.1:
- The walk-forward table says *why* a window used the default weights.
- Criteria that "pass" on too few trades are shown greyed out as **not meaningful**.
- The NO TRADE reason quotes the actual backtest result.
- Paper trading shows the average **fill** and the fee-inclusive **cost basis** as separate fields.
- "Download/refresh history" is ticked only when the stored history is missing or more than about a day old.

A 4-year run replays about 8,700 bars and takes roughly 5–10 minutes. It runs in the background with a progress bar and can be cancelled.

### Entry rules and the design/holdout protocol (Phase 6.2, engine signal-1.7)

The 6.1 funnel showed that nearly every raw BUY setup is dropped by the reward/risk rule, because buys appear while price is pressing into resistance. Phase 6.2 adds two entry rules that traders actually use. **Every parameter below was fixed in advance and must not be tuned on backtest results.** `min_rr`, the weights and the thresholds are unchanged.

`entry_mode` (in the signal config and per backtest run):
- **standard** — the original behaviour, unchanged (a test checks it reproduces signal-1.6 exactly).
- **breakout** — when a BUY setup is downgraded for low reward/risk, place a *pending* order instead. Let R be the nearest major (2+ touches) resistance above the live price.
  - Trigger = R's upper edge. The order fills only after a **4h close above the trigger**, at the **next** 4h open.
  - Stop = R's lower edge − 0.5 ATR(4h), but never closer than 1 ATR below the trigger.
  - Target = the lower edge of the next major resistance at least 0.5 ATR above the trigger; else the first resistance of any strength at least 1 ATR above it; else trigger + 2 ATR.
- **pullback** — let S be the nearest major support below the live price.
  - Limit = S's upper edge, and only if it is at most **3 ATR** (`PULLBACK_MAX_DIST_ATR`) below the live price.
  - It fills only when the price trades **strictly below** the limit (a touch does not fill), at min(open, limit).
  - Stop = S's lower edge − 0.5 ATR, but never closer than 1 ATR below the limit. Target = the standard plan's target 1.
- Both: reward/risk is measured from the trigger or limit, after costs, with the same formula as the standard plan. The order is created only if that R/R ≥ `min_rr`. It is valid for **6 bars** (`PENDING_VALID_BARS`, one day). The signal stays HOLD (and NO TRADE while not validated); the panel shows it as "Pending order (research only, not actionable)".

In the backtest a pending order counts only if no safeguard other than the validation gate fired and the raw setup did not fight the daily trend. While flat, a newer order replaces an older one, a SELL setup cancels it, and it expires after 6 bars. A fill at or beyond the stop or the target is skipped. In a pullback's fill bar only the stop is checked (pessimistic), not the target. The time stop counts from the fill. The entry mode is part of the settings fingerprint, so a validation applies only to the same entry rule.

**Periods** (`period` on `POST /api/backtest/run`): `full` (default), `design` or `holdout`. The cut-off is fixed: **HOLDOUT_START = 2025-10-29 00:00 UTC**.
- **design** = the start of the period up to the cut-off. No design trade touches a holdout candle. Design runs are labelled "DESIGN PERIOD - in-sample for rule choices, cannot validate" and **never write a validation**.
- **holdout** = decisions from the cut-off to the end. Earlier candles are used only as indicator warm-up. The criteria are the same, except the minimum is **15 trades** (stated before any holdout run).
- Walk-forward, baselines, random entries and the hit rate are all computed on the chosen period only.
- Protocol: choose a rule using design runs only, then test it **once** on the holdout.
- **full** (0.9.2) = the whole span. It includes the design data, so it is informational only: labelled "FULL PERIOD - includes design data, cannot validate" and it **never writes a validation**.
- **Holdout registry** (0.9.2): every holdout run is recorded (time, engine version, settings, rule-set hash, holdout start, result; `GET /api/backtest/holdout`). The rule-set hash covers the settings (engine version, weights, thresholds, costs, entry rule), a **code fingerprint** (SHA-256 of `services/signal_engine.py` and `services/technical_analysis.py`, line endings normalised; 0.9.3), the candle window, time stop, exit on SELL and the holdout start. Run length, seed, window sizes and criteria are left out.
- **Single-use holdout** (0.9.3): the first holdout run in a holdout period, **whatever its rule set**, counts for validation and spends the period. Later runs are stored and labelled "not valid for validation: holdout already spent on <date>, run #N". The Research view shows "Holdout status: untouched / spent on <date> by run #N". To validate a different rule set, set a **new holdout start** (`POST /api/backtest/holdout_start {"start": "YYYY-MM-DD"}`, or the box in the Research view). It is accepted only once the current holdout is spent, and only from the day after the spending run: forward data only. Design runs then extend up to the new start. A validation counts only while the rule code fingerprint matches, so editing the code (even without an engine version bump) needs a new holdout. Records from before 0.9.3 never lift the gate.

**Forward observation.** After a validation from the run that spent a holdout period, the NO TRADE gate stays on for **56 days**, with the reason "Validated on <date>; forward observation period until <date>." Only then does the validation gate clear.

Funnel changes in 6.2:
- `share_negative` is renamed `share_no_room` (R/R ≤ 0).
- `bars_score_at_or_above_buy` now counts the **unrounded** score. Before, it counted the score rounded to 3 decimals, so a score such as 0.2496 was counted as ≥ 0.25 although its setup was HOLD. `rounding_check` shows the old count.
- New: `pending_order_bars`, `pending_orders` (created, replaced, expired, cancelled by sell, skipped gap, filled, dropped for a standard entry, open at window end) and `trades_by_entry` (standard / breakout / pullback).

## Risk calculator and paper trading (Phase 6)

- **Position size.** Sized so that a stop-out loses your chosen % of the portfolio, *including* fees and slippage on both sides. Exposure is capped (default 50%), risk is capped at 2% per trade, and after 2 losses in a row the risk is **halved**. Size is never increased to win losses back. The calculator always warns that stops are not guaranteed.
- **Paper trading.** A separate SIMULATED account (default 10,000 USDT), spot only.
  - Buys fill at the latest price plus slippage, with the fee charged.
  - Optional stop and target are checked whenever the account refreshes, so fills can be late and worse, just like real stops.
  - Buys are blocked when the price is stale, when the exposure limit would be exceeded, or after the daily loss limit (default 3%) is hit.
  - Every order is labelled SIMULATED, and the database refuses any order not marked simulated.
- **The app never places a real order and has no exchange account connection.**

## Local AI assistant (Phase 7, optional)

The **Ask the data** panel answers questions using **only this app's collected data**: price, signal, indicators, news, backtest, validation, the SIMULATED paper account and data-source health. It is free and fully local: no paid API, no cloud model, no API key.

**What it can do**
- Explain the current signal and why it is NO TRADE ("Explain current signal" button, or ask).
- Report the price and where it came from, summarize the scored news, the latest backtest, the validation state and the paper account.
- Every answer shows its mode (**AI local** or **Rule-based**), a timestamp and a **Sources** list (fact text, source, time, link) built by the server from the facts the answer cited.

**What it can't and won't do**
- No price predictions, no buy/sell advice, no profit promises, no leverage or futures. AI answers that break this are **withheld** and replaced by the deterministic answer, with a warning.
- No knowledge outside the app: if a fact isn't in the app it says "I don't have that data in the app".
- News headlines are treated as untrusted data (quoted, control characters removed), never as instructions.
- An answer that cites no fact gets the warning "No app data cited - treat with caution". URLs that are not the app's own news links are removed.

**How it works.** For every question the server builds a numbered fact sheet ([F1], [F2], … at most ~6,000 characters; news is trimmed first) and sends it with a fixed system prompt (in `services/assistant.py`) and the last 6 turns to the local model (`temperature 0.2`, `num_ctx 4096`, not streamed). Only one AI request runs at a time; since Phase 8 a second AI question gets the grounded rule draft at once with the note "The previous AI request is still finishing on your PC - try again shortly, or use the instant answer" (no more HTTP 429). Chat history is stored in the `chat_messages` table (an existing database upgrades automatically).

**Without Ollama** everything still works: answers come from deterministic rule-based templates filled from the same facts, with the same Sources list. The rest of the app never depends on Ollama.

**Install and verify Ollama (Windows PowerShell)**
```powershell
winget install Ollama.Ollama      # or download from https://ollama.com
ollama --version
ollama pull qwen2.5:3b            # about 2 GB, one time
ollama list                       # qwen2.5:3b should be listed
```
Then restart the app; the badge shows "Local AI: qwen2.5:3b connected". Only explanation questions use the model (see 7.1 below); on this CPU-only PC a rewrite took 128–145 s with qwen2.5:3b. The timeout is 150 s, after which the rule-based answer is shown.

**Settings (`.env`)**: `OLLAMA_URL` (default `http://127.0.0.1:11434`), `OLLAMA_MODEL` (default `qwen2.5:3b`), `OLLAMA_TIMEOUT_SECONDS` (default 150), `ASSISTANT_MAX_MESSAGE_CHARS` (default 1000).

**Privacy.** Keep `OLLAMA_URL` on localhost (`127.0.0.1`). The fact sheet and your questions then never leave this computer.

**API**: `GET /api/assistant/status`, `POST /api/assistant/chat {"message": "..."}` (400 if empty or too long; while an AI request is running it returns the rule draft at once; 429 only from the rate limit, 30/min), `GET /api/assistant/history?limit=50`, `POST /api/assistant/clear`, `GET /api/assistant/explain?ai=0|1`, `POST /api/assistant/route {"message": "..."}` (which path a question takes).

### Phase 7.1: hybrid routing and checked AI rewrites

The Phase 7 live check showed a 3B model getting facts wrong (wrong backtest run, "no price data" when the price was there, wrong publisher). Rule answers were correct, cited and took ~2 s. So:

- **Hybrid routing.** Every question is classified first. Price, paper account, backtest, validation, data sources, "why NO TRADE", news, buy/sell advice, price predictions and attempts to override the rules are **always answered by rule templates** (instant, grounded, cited) and never reach the model. Only open explanation questions ("explain the signal", "what does ADX mean here", "why is momentum bearish", "what would need to change") and `explain?ai=1` use the model, and the model only **rewrites a rule-built, grounded draft** for a beginner. The Sources list always comes from the draft.
- **Checks on every AI rewrite.** Every number (including "run #N") must match a number in the draft or facts, allowing rounding to the shown precision (80,180.01 ≈ 80,180 ≈ 80.2k). A run named next to "validation", "NO TRADE" or "gate" must be the validation run. Publishers it names must be fact publishers, and a cited headline must be attributed to its real publisher. Interpretive labels (oversold, overbought, divergence, breakout, golden/death cross, …) must appear in the data. The advice/prediction filter runs as a second layer, including phrasings like "is likely to continue". If any check fails, the AI text is withheld, the rule draft is shown ("AI withheld - rule answer") and what failed is logged in `chat_messages.error`.
- **Wording.** The NO TRADE reason now says "the validation backtest (run #N, <period> period, <entry> entry, engine X)", and the fact sheet has separate facts for the validation run and the latest run (e.g. "Latest run #6: DESIGN period, pullback entry - research only, cannot validate").
- **UX.** Rule answers are instant (no loader). AI rewrites show "Thinking locally… usually 1-3 min on CPU" (Phase 8 wording) with a live seconds counter and a Cancel button. Mode labels: "Rule-based (instant)", "AI rewrite (local <model>) - may contain mistakes; check the Sources", "AI withheld - rule answer".
- **Phase 8: AI rewrites are OFF by default** (Settings → Assistant → AI explanations). While off, explanation questions and `explain?ai=1` return the grounded rule draft instantly with the note "AI rewrite is off (Settings → Assistant)".
- **Model choice (measured on this CPU-only PC, 12 questions each).**
  - qwen2.5:3b: 8 factual questions answered by rules in 1.2–1.9 s; 4 explanation rewrites in 128–145 s, all passing the number check. But 3 of the 4 contained wrong claims (calling RSI 45 "oversold", a "likely to continue" prediction, a wrong support comparison). The term check and the prediction filter were then strengthened to catch the first two kinds.
  - qwen3:4b (sent with `think: false`, `<think>` blocks stripped): all 4 rewrites **timed out at 150 s** and fell back to the rule draft.
  - Default stays **qwen2.5:3b**: qwen3:4b was not better (no rewrite finished in time). Treat AI rewrites as a convenience; the rule answers are the reliable part.

### Score study (research only, design data)

`python scripts\score_study.py` replays the engine (default weights, standard mode) over the **design period only** (2022-10-30 → 2025-10-25 decisions, 6,550 4h bars; the last forward-return bar closes before the 2025-10-29 holdout start, which is asserted) and asks whether the score predicts the return 18 bars after the next open. Results are in `data\score_study_design.json` and `data\score_study_design.md`.

- All-bar baseline: mean +0.563% per 18 bars before costs, 54.2% positive.
- Score buckets are **not monotonic**: the most bearish bucket (≤ −0.25) averaged +1.07%, the middle bucket +0.31%, the top bucket (≥ 0.40) +1.21%.
- Spearman correlation of the score with the forward return: **−0.018** (non-overlapping sample +0.034); 95% block-bootstrap CI **[−0.093, +0.051]**, i.e. indistinguishable from zero. No component is reliably different from zero either.
- Top bucket minus the all-bar mean: 95% CI **[−0.25, +1.47]** percentage points, which includes zero.

Conclusion: on design data the score shows **no measurable predictive value** for the 3-day forward return. No rule was changed because of this study.

## Alerts, exports, settings, watchlist and calendar (Phase 8)

### Alerts
A background **AlertMonitor** thread checks the rules every 60 s, using the app's existing cached services (no extra polling). It starts only with `python app.py`; tests and imports never start it. A failing rule is logged and skipped, and an error never stops the thread.

| Rule | Fires when | Severity |
|---|---|---|
| `price_above` / `price_below` | The price crosses your level. Fires once, then re-arms only after the price moves back ≥ 0.5% (hysteresis, configurable) | info |
| `big_move` | \|change\| over 1 h ≥ 3% (from stored price snapshots, else the 1h candles) or over 24 h ≥ 8%. Fires on the transition into the breach only | warning |
| `significant_news` | A news event with impact **high** and freshness **new**. One alert per de-duplicated story cluster (never per publisher), with up to 3 article links | warning |
| `high_volatility` | The 4h or 1h volatility regime **becomes** "extreme" (transition only) | warning |
| `signal_change` | The final signal or setup differs from the last alerted one (first look only stores a baseline). Links to `/api/signal/<id>` | info |
| `source_failure` | A source fails ≥ 3 times in a row (`source_health.consecutive_failures`), or the BTC price goes stale. A recovery alert follows | critical / info |
| `risk_limit` | The **SIMULATED** paper account hits its daily loss limit or exposure exceeds the maximum. Labelled SIMULATED in title, message, reason and source | warning |

Every alert stores: id, rule_id, kind, severity, title, message, **reason**, value, threshold, source, **links**, created_at (UTC) and acknowledged_at. The non-price rules are created once by default (you can disable or delete them); price rules are yours to add.

**Anti-spam:** a per-rule cooldown (30 min default, or the rule's own `cooldown_s`; 6 h per news cluster); identical messages are dropped within the cooldown; a global cap of 20 alerts per hour, after which ONE "Alerts paused for spam protection" alert is created; all of this state is in SQLite (`alert_state`), so it survives restarts.

**Delivery:** the in-app alert centre (bell with unread badge in the top bar, list, Acknowledge / Acknowledge all, rule editor). Browser notifications start only after you click **Enable browser notifications** and the browser grants permission. Windows desktop notifications are optional: `pip install plyer==2.1.0`, then Settings → Alerts → channel desktop. A failed notification never breaks alerting.

### Exports
`GET /api/export/<dataset>?format=csv|json` for candles, signals, news, backtest, paper_orders, alerts and chat (buttons on the signal history, news, backtest, paper and alert panels).
- **CSV**: UTF-8 with BOM (Excel opens it correctly); every time as ISO UTC **plus** a local-time column in the display time zone; CSV-injection protection (a cell starting with `=` `+` `-` `@`, tab or CR gets a leading `'` unless it is a plain number); filename `btc-signal-lab_<dataset>_<UTC timestamp>.csv`.
- **JSON**: `metadata` (generated_at, app version, engine, display time zone, row count, source notes, and the HYPOTHETICAL / SIMULATED labels) plus `data`. A backtest JSON contains summary, criteria, trades and equity.
- At most 100,000 rows; larger requests get a 400 asking you to narrow the range.

### Settings page (`/settings`)
General (time zone checked with zoneinfo, theme, refresh intervals with minimums: price ≥ 10 s, panels ≥ 30 s, news/signal ≥ 300 s), Market data (provider order; the CoinGecko key shown only as **set / not set**: it stays in `.env` and is never returned, displayed or stored), Signal (the existing editable config, with the warning "Changing these makes the current validation stale; the signal stays NO TRADE until a new backtest validates the new settings."), Risk & paper (SIMULATED), Alerts, Assistant (AI explanations on/off, model checked against `ollama list` when Ollama runs, timeout) and Watchlist. Each section has **Reset section to defaults**.

**Precedence:** environment variable (process or `.env`) **>** settings database **>** default. Every value shows its source; env-set values are read-only on the page. Changes apply live, except the provider order, which is marked "restart required".

### Watchlist
Up to 10 Binance spot symbols (default BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT), checked against Binance `exchangeInfo` when saved (cached 24 h). Prices come from **one** batched `ticker/24hr?symbols=[…]` request, cached ≥ 60 s. **Display only:** no signals for other coins. If Binance fails, Coinbase `<BASE>-USD` is used where it exists (USD quotes), else the last cached list is shown and labelled stale.

### Economic calendar: source decision
- **FOMC (used):** no ICS/JSON feed exists, so the Federal Reserve Board's meeting-calendar page is fetched once a day and its dated meeting rows parsed (US-government work, public domain, credited). Notation votes are skipped. The page lists dates only, so 2:00 p.m. ET is shown and labelled as the *usual* statement time.
- **BLS CPI / PPI / jobs report (optional):** BLS publishes an official iCalendar file, but it returns HTTP 403 to anonymous scripts and its policy asks automated clients to identify their owner. Nothing is bypassed: set `CALENDAR_CONTACT_EMAIL` (your own e-mail) in `.env` to enable it. Until then the card says why it is missing.
- **GDP (BEA):** BEA publishes no machine-readable schedule feed, so it is not included.
- **FRED** (needs a free key) was not needed.
The card shows events in the next 14 days in UTC and your display time zone, or an honest empty state, plus the next FOMC date when it is further out.

## Security (Phase 9)

The app is a local, single-user tool with **no login**, so it is locked to this computer by default. Full threat model and findings: [SECURITY.md](SECURITY.md).

- Binds to `127.0.0.1`. `APP_HOST=0.0.0.0` is ignored unless `APP_ALLOW_REMOTE=1` is also set, and then a startup warning is printed; the Flask debugger is never enabled on a non-loopback address. `APP_DEBUG` defaults to off.
- Requests whose `Host` is not `localhost` / `127.0.0.1` / `[::1]` (on the app's port) are rejected with 400, which stops DNS-rebinding attacks. Add a LAN name to `APP_ALLOWED_HOSTS` only together with `APP_ALLOW_REMOTE=1`.
- POST/PUT/DELETE need a same-origin `Origin` (or `Referer`), or the header `X-Requested-With: BTCSignalLab`, which only the app's own JavaScript sends. A cross-site page gets 403. Local scripts must send that header, e.g.
  `Invoke-RestMethod -Method Post http://127.0.0.1:5000/api/alerts/test -Headers @{"X-Requested-With"="BTCSignalLab"}`
- Headers: `Content-Security-Policy` (scripts only from the app, incl. the local Plotly route), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` / `frame-ancestors 'none'`, `Referrer-Policy: no-referrer`.
- Bodies over 64 KB, malformed JSON and non-object JSON return 400; numbers must be finite; errors never include a stack trace (it is logged in the console). Per-endpoint rate limits (429 + `Retry-After`) on news refresh, exports, assistant, backtest start, test alerts, paper orders and settings saves.
- Keys stay in `.env` (gitignored); the settings page shows only "set" / "not set".

## Honest results

These are the research outcomes so far. They are why the app is a research tool and not a trading system.

1. **The backtest found no validated edge.** The validation run, **run #3** (full 4 years, standard entry), is **NOT VALIDATED**: 6 trades, +7.52% after costs, max drawdown 3.45%. Buy & hold made +281.87% (drawdown 53.45%) and the EMA 50/200 crossover +241.99%. It failed three criteria: enough trades (6 of 30), beats random entries (79th percentile, needs 90th) and beats EMA on return/drawdown.
   - Why so few trades (funnel): the reward/risk ≥ 1.5 rule drops 98.8% of raw BUY setups (their median R/R is 0.46), because buys fire while price is pressing into resistance.
2. **The design-period entry-rule study failed.** The pre-registered breakout and pullback rules (Phase 6.2, every parameter fixed in advance) were run on the design period only (2022-10-30 → 2025-10-28): breakout entries had a profit factor of 0.67; pullback entries made 83 trades and −15.9% after costs. Neither earned a holdout test.
3. **The score has no measurable predictive value.** The design-data score study found Spearman −0.018 (95% CI [−0.093, +0.051]), and the score buckets are not monotonic.
4. **So the app correctly stays NO TRADE.** The validation gate is closed and only a validated backtest for the exact engine version and settings can lift it.
5. **The holdout year is unused.** Data from **2025-10-29** onward has never been run. It is reserved for one future, pre-registered idea, tested once; it must not be used to tune the current rules.

These figures come from the live runs on the original installation (`F:\bitcoin-ai-agent`, Python 3.13, full history database). The Phase 9 copy (`E:\bitcoin-ai-agent`, Python 3.14) had no database, so they were not re-checked in Phase 9 (see Open items).

## Open items

- **Reconcile the two installations.** Phase 9 was built and tested on `E:\bitcoin-ai-agent` (Python 3.14.6, fresh database). Phases 1-8 ran on `F:\bitcoin-ai-agent` (Python 3.13, full history DB with runs #1-#6). To merge: copy the Phase 9 code over the F: copy (keep its `data\` folder and `.env`), recreate `.venv` there, `pip install -r requirements.txt`, then run `python -m pytest` and `python -m pytest -m e2e`, and check that the backtest panel shows run #3 with the figures above.
- Most alert types, and Windows/browser notifications, have not been seen live.
- The BLS calendar needs `CALENDAR_CONTACT_EMAIL`.

## Tested vs unverified

**Automated tests: 493 unit/API tests (`python -m pytest`, no network) + 81 Playwright UI tests (`python -m pytest -m e2e`).**

Tested:
- Market data: HTTP retries, timeouts, 429 and geo-blocks, provider parsing, fallbacks, cached-stale behaviour, total outage, incomplete/malformed/missing candles, 1h→4h aggregation, Windows pre-1970 timestamps.
- Indicators: all of them, plus no look-ahead.
- News: RSS parsing, Google News publisher extraction, URL de-duplication, syndicated-story clustering, sentiment with negation, contradictory reports, rumour and speculation flags, freshness and priced-in, empty, garbage and failing feeds, polite retry interval.
- News regressions: 117 cases from three live reviews of real headlines, covering wrong direction, category, verification, relevance (wrongly dropped and wrongly kept), wrong and missed merges, Fed decision vs preview, and superseded previews.
- Signals: every NO TRADE condition on its own, setup thresholds, weighted-sum arithmetic, weights re-normalised when news is missing, reward/risk maths with and without costs, downgrade on low reward/risk, config validation, history recording (on change and hourly), and the validation record unlocking the signal.
- Backtest: no look-ahead (truncating the future leaves past decisions unchanged; daily candles only once closed), next-open entries, stop-first when both levels are touched, gaps through the stop, skipped entries on gaps, time stop, sell exit, costs on both sides, one position at a time, drawdown and profit-factor maths, buy-and-hold, criteria logic, the settings fingerprint, an end-to-end run, and a validation unlocking the signal only for matching settings.
- Entry rules and periods (6.2): standard mode reproduces signal-1.6 decisions and trades exactly (golden fixture), pending orders only on low-R/R downgrades with the stated trigger/limit, stop and target, the 3 ATR pullback limit, rejection when the order's own R/R is too low, breakout fills only after a close above the trigger (next open), pullback fills only strictly below the limit at min(open, limit), skipped gap fills, expiry after 6 bars, cancel on SELL, replacement, no target check in the pullback fill bar, no look-ahead for pending orders, the design/holdout split, the holdout 15-trade minimum, design runs never writing a validation, the 56-day forward-observation gate, entry mode in the settings fingerprint, 400 on a bad entry mode or period, and the funnel score count matching the raw buy count.
- Risk and paper: sizing to the exact risk including costs, exposure cap, size reduced (never increased) after losses, risk ceiling, input validation, warnings, buy/sell maths with fees, stale-price and cash and exposure blocks, no short selling, stop/target exits, the daily loss limit, and the database rejecting non-simulated orders.
- Assistant (Phase 7, fake Ollama, no network): status offline / online with and without the model / 30 s cache; non-streaming low-temperature chat; timeout, connection and HTTP errors falling back to rule answers; fact sheet sources, timestamps, NO TRADE reasons, SIMULATED label, news links, the 6,000-character budget (news dropped first) and a prompt-injection headline quoted as data; foreign URLs stripped; Sources built from cited ids; the no-citation note; all fallback intents (advice and predictions refused); the advice/prediction filter on AI output; routes (400, busy fallback, history, clear, explain ai=0/1); and the database upgrade from the Phase 6 schema.
- Assistant 7.1: every factual intent is answered without calling the model; explanation intents send the draft; the number/run/publisher/claim checks pass correct rewrites and catch invented numbers, wrong run numbers, wrong publishers and the exact wrong sentences from the live run; withheld answers fall back with a warning and a logged error; Sources come from the draft; qwen3 gets `think: false` and `<think>` blocks are stripped; validation-run vs latest-run facts; the 150 s timeout default; the new NO TRADE wording, including old validation records.
- Score study: forward returns never reach the holdout; bucket counts add up.
- Phase 8 alerts: every rule fires only on a crossing or transition; hysteresis re-arm; per-rule cooldown; identical-message dedupe; one alert per news cluster; the global cap with a single "paused" alert; state surviving a restart (new process, same SQLite file); a failing rule not stopping the others; notifier failures ignored; the monitor surviving exceptions and stopping cleanly; the monitor never started by `create_app`; every alert having a timestamp, reason and links; risk alerts labelled SIMULATED; every alert route and its 400/404 cases.
- Phase 8 exports: every dataset in CSV and JSON, the BOM, UTC + local columns, CSV-injection escaping (numbers kept), the row limit, HYPOTHETICAL/SIMULATED metadata, bad dataset/format/timeframe/range/run (400/404).
- Phase 8 settings: validation, persistence across a new service instance, env precedence and read-only, invalid env ignored, reset, live model/timeout change, model check against Ollama, the stale-validation warning after a signal-config change, and the CoinGecko key never appearing in the settings API, pages, exports, sources or the settings table.
- Phase 8 watchlist and calendar: one batched request, the 60 s cache, cached-stale fallback, Coinbase fallback, unavailable state, symbol validation (24 h cache, network failure never treated as "unknown"); the FOMC parser on a trimmed real Fed page (EST/EDT, cross-month meetings, notation votes skipped), the 14-day window, daily cache, failure back-off, cached copy on failure, layout-change detection, the BLS ICS parser and contact header.
- Part 0: AI explanations off by default (rule draft + note, model never called), on (AI label), the busy fallback after a cancel (rule draft, no 429), the loader text and the 150 s timeout.
- App: SQLite persistence, all API routes, bad input (a fuzz test sends malformed bodies and query strings to every route: never a 500, never NaN/Infinity in JSON).
- Phase 9 security (`tests/test_security.py`): loopback bind and the explicit remote flag, foreign `Host` → 400, cross-origin POST/PUT/DELETE → 403 (Origin, Referer, `null`, other local port), same-origin and app-header requests accepted, the app's JS sending the header, security headers on pages/API/static/Plotly, no inline executable script, template escaping, malformed/large/non-object JSON → 400, non-finite numbers rejected, rate limits with `Retry-After`, no stack trace in a 500 (logged locally), the CoinGecko key absent from every page, API response, export and the database, a key-pattern scan of the source tree, documented outbound hosts only, timeouts on every outbound call, no real-order/exchange-key/withdrawal/futures code, the paper `CHECK (simulated = 1)` constraint, `javascript:`/`data:` feed links dropped, pinned requirements.
- Phase 9 UI (`tests/e2e/`, Chromium, fake data): every `data-state="ready"` marker, price and Plotly chart with no console errors (so the CSP allows the local Plotly route); NO TRADE reasons, evidence for and against, "not a probability"; multi-timeframe and analysis values; news links `target=_blank rel=noopener`, the empty-feed state, and hostile `<script>`/`<img onerror>` text in news, alerts and chat shown literally and never executed; backtest verdict, greyed "not meaningful" criteria, funnel, entry-rule and period selects, design badge; risk calculator valid and invalid input; paper BUY → SELL → Reset with SIMULATED in every message; assistant rule answer with Sources and the offline badge; alert centre test alert, Acknowledge, Acknowledge all and the unread badge; settings save → reload → persisted → reset and the key status "set"/"not set"; every export button (CSV and JSON, name and header); theme toggle persisting; nav jumps landing below the sticky bar; no horizontal scroll at 390×844 on the dashboard and settings page; an accessible name on every visible control, labelled charts, `lang="en"`, and Tab reaching the main controls. No browser request leaves 127.0.0.1 (external hosts are stubbed and checked).

- 0.9.1 UI (`tests/e2e/test_views_e2e.py`): every view from the sidebar and from its hash, the title and `aria-current`, old anchors opening their view below the sticky bar, Back/Forward, the mobile menu (button, link, Escape, focus return), the overview reason line and "See full reasoning", folded sections closed by default, expanding and remembered, the research tiles and folds, the empty-state call to action, no bare "—"/NaN/null/undefined in any view (seeded and empty database, with every fold open), no console errors in any view, no horizontal scroll at 390 px in every view, stacked tables on a phone, chart overlays drawn/toggled/remembered and off by default on a phone, the freshness text ticking and turning amber on a failing source, a title and kind label on every card, and text contrast of at least 4.5:1 for the main text, labels and badges in both themes. `tests/test_ui_docs.py`: the read-only `/docs` route.

**Verified live on Windows (Phases 2-8):** Binance, Coinbase, CoinGecko, all six RSS feeds, the 4-year backtest (not validated), the design-period entry-rule runs, the score study, and the assistant with Ollama 0.40 + qwen2.5:3b (and its fallback with Ollama unreachable). Phase 8 (2026-10-29): price_above / price_below alerts fired about 56 s after creation and did not refire in later cycles or after a restart; the TEST alert; the monitor running with no logged errors; CSV/JSON exports of signals, news, backtest run 3 and paper orders (BOM, consistent columns); settings persisting across a restart, then reset; the CoinGecko key shown only as "not set"; the watchlist (4 Binance prices, one request); the FOMC calendar fetched from federalreserve.gov (next decision 2026-12-09, outside the 14-day window); screenshots at 1440×900 and 390×844 with no console errors and no horizontal scroll; AI explanations on, with one rewrite accepted and one withheld by the advice/prediction filter.

**Phase 9 live check (2026-10-29):** see [CHANGELOG.md](CHANGELOG.md) for the final smoke run against the real app on port 5050 and the final screenshots (`data\final_*.png`).

**Not verified:**
- The holdout (deliberately unused).
- Any trading edge: none was found, and none is claimed.
- AI answer quality on models other than qwen2.5:3b / qwen3:4b; answer times with a GPU.
- Alerts big_move, significant_news, high_volatility, signal_change, source_failure and risk_limit live (unit-tested only; no such market event during the live checks).
- Windows toast notifications actually appearing (plyer raised no error, but the desktop was not observed); browser notifications (headless Chromium denies permission).
- The BLS calendar (needs `CALENDAR_CONTACT_EMAIL`); the Coinbase watchlist fallback live (Binance worked).
- Accessibility beyond the automated checks above: axe-core was not available offline, and no screen reader was used.
- Browsers other than Chromium (Edge is Chromium-based; Firefox/Safari untested).
- Python 3.12 and 3.13 with the pinned versions (only 3.14.6 was installed for Phase 9).
- Exposing the app on a network (`APP_ALLOW_REMOTE=1`): supported but not recommended and not tested beyond unit tests.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then activate again. Or skip activation and call `.\.venv\Scripts\python.exe app.py` directly |
| `python` opens the Microsoft Store / is not found | Install Python from python.org with "Add python.exe to PATH", or use `py -3.13` instead of `python` when creating the venv |
| `ModuleNotFoundError` | Activate `.venv` first (the prompt shows `(.venv)`), then `pip install -r requirements.txt` |
| `pip install` fails on numpy/scipy | Python is older than 3.12. Install a newer Python and recreate `.venv` |
| `pip install` sits for minutes with no progress | A stalled download. Press Ctrl+C and retry with `pip install --timeout 30 --retries 3 -r requirements.txt` (seen once during the Phase 9 setup; the retry worked) |
| Port 5000 already in use (`OSError: [WinError 10048]`) | Set `APP_PORT=5050` in `.env` (or `$env:APP_PORT=5050; python app.py`) and open `http://127.0.0.1:5050`. Find the other program with `Get-NetTCPConnection -LocalPort 5000` |
| "Host not allowed" (400) | Open the app as `http://127.0.0.1:<port>` or `http://localhost:<port>`, not via another name or IP |
| "Cross-origin request blocked" (403) from a script | Send the header `X-Requested-With: BTCSignalLab` (see Security) |
| Binance `HTTP 451` or timeouts | Regional block or firewall. The app tries the mirror `data-api.binance.vision`, then Coinbase, automatically; the data panel shows which source served. To skip Binance: `MARKET_PROVIDERS=coinbase` in `.env`. Backtest history needs Binance or its mirror |
| All sources fail | Check internet / firewall / VPN / proxy; run `python scripts\check_sources.py` |
| "stale" badge / old prices | A source is lagging or unreachable; the data shown is the last good copy and is labelled so. Paper orders are blocked while the price is stale. Click **Refresh**, check the Data-source health panel |
| Chart area blank | Make sure `plotly` is installed; open `/vendor/plotly.min.js` to confirm it loads |
| News panel empty ("No news collected yet") | A feed may be blocked on your network or changed format. Click **Fetch now** (max 3 per minute), run `scripts\check_sources.py`, check the Data-source health panel |
| Ollama badge says offline | Start Ollama (tray icon, or `ollama serve`), check `Invoke-RestMethod http://127.0.0.1:11434/api/tags`, and `ollama list` shows `qwen2.5:3b`. The assistant still answers with rule-based text |
| AI answers slow or time out | Normal on a CPU: 1-3 min per rewrite. Factual questions never use the model. Raise `OLLAMA_TIMEOUT_SECONDS`, or turn AI explanations off (Settings → Assistant) |
| `database is locked` | Another process holds the SQLite file: usually a second `python app.py` or a DB browser. Close it; the app waits up to 10 s per query. Don't run two copies on the same `data\btc_agent.sqlite3` |
| `btc_agent.sqlite3-wal` / `-shm` files | Normal SQLite write-ahead-log files. Never delete them while the app runs |
| Dates wrong / `OSError` on old timestamps / `ZoneInfoNotFoundError` | Windows has no built-in IANA time-zone database: the `tzdata` package (installed with pandas) provides it, so reinstall the requirements. Pre-1970 timestamps are handled without the Windows `OSError`. Set the display zone in Settings → General (e.g. `Asia/Karachi`); stored times are always UTC |
| A headline looks mis-scored | Open "Why …" on the item to see the matched rules |
| e2e tests fail with "Executable doesn't exist" | Run `python -m playwright install chromium` once |
| Settings won't save a value | It is set in `.env` (shown as "env override (read-only)"). Remove it from `.env` and restart |

## Limitations and disclaimer

- **Research and education only. Not financial advice.** Nothing here is a recommendation to buy or sell anything.
- No validated edge was found (see Honest results). Backtests are hypothetical, use simplified fills, cannot replay news, and past results do not predict future ones.
- Spot only, long only. No leverage, futures, margin or short selling. The paper account is SIMULATED; the app never places a real order and has no exchange connection or exchange keys.
- Data comes from free public sources that can be delayed, wrong, rate-limited or blocked. Prices from different exchanges differ slightly; USD and USDT are not mixed.
- News scoring is rule-based and English-only; it can misread headlines. The optional AI assistant can make mistakes; its Sources list is the reliable part.
- A local single-user app with no login: keep it on `127.0.0.1`.
- This is version 0.9.1. It is not "1.0" and not production software.
