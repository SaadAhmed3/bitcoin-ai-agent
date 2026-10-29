# BTC Signal Lab — AI Bitcoin Analysis & Signal Agent

A local, free-first research and decision-support tool for Bitcoin. It collects public market data, calculates technical indicators across timeframes, and (in later phases) adds news intelligence, transparent signals, backtesting and a local AI assistant.

> **This is not a trading bot.** It never places orders, never connects to an exchange account, and makes no profit claims. Indicator readings are evidence, not predictions.

## Build status

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation, config, SQLite, dashboard | ✅ Done, tested |
| 2 | Market data (Binance → Coinbase fallback, CoinGecko overview), candlestick chart | ✅ Done, tested, verified live on Windows |
| 3 | Technical indicators, structure, S/R, regimes, multi-timeframe | ✅ Done, tested |
| 4 | News ingestion, dedup, categories, sentiment, impact, verification, freshness, source links | ✅ Done; feeds verified live; rules v2.2 (frozen) after three live reviews |
| 5 | Transparent signal scoring + NO TRADE safeguards, trade plan, history | ✅ Done; two live reviews, fixes in 5.1/5.2 |
| 6 | Walk-forward backtest, validation gate, paper trading, risk calculator | ✅ Done; 4-year run not validated (6 trades). 6.2: pre-registered entry rules + design/holdout split |
| 7 | Local AI assistant (Ollama, optional) + design-data score study | ✅ Done, tested; verified live with qwen2.5:3b |
| 8 | Alerts, exports, settings page, watchlist, economic calendar | ✅ Done, tested; verified live on Windows (see Phase 8 below) |
| 9 | Playwright UI tests, security review, final docs | Planned |

## Windows setup (PowerShell)

```powershell
# 1. Check Python (3.11+) and Git
python --version
git --version

# 2. Go to the project folder (after unzipping)
cd $HOME\Projects\bitcoin-ai-agent

# 3-4. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
#   If activation is blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 5. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# Optional: local settings
Copy-Item .env.example .env

# Verify live data sources from YOUR network (real calls)
python scripts\check_sources.py

# 8. Start the app
python app.py

# 9. Open the dashboard
Start-Process "http://127.0.0.1:5000"

# 10. Run the test suite (no network needed)
python -m pytest
```

Ollama (Phase 7) is not required yet. Install steps will be added with that phase.

## Project layout

```
bitcoin-ai-agent/
  app.py                      Flask app + JSON API (single startup command)
  config.py                   Settings from env / .env
  database/db.py              SQLite schema + persistence
  services/http_client.py     Timeouts, retries, backoff, per-host rate limit
  services/market_data.py     Providers, fallbacks, caching, staleness, closed-candle rule
  services/technical_analysis.py  Indicators, structure, levels, regimes, divergence
  services/news_collector.py  RSS fetch + parse (feedparser), per-feed health, polite polling
  services/news_analyzer.py   Rule-based categories, sentiment, impact, verification, freshness, dedup
  services/news_service.py    Combines news with BTC prices for the 'priced in' check
  services/signal_engine.py   Component scoring, weights, NO TRADE gates, trade plan (pure functions)
  services/signal_service.py  Gathers inputs, stores signal history, signal settings
  services/history.py         Downloads historical candles from Binance (paged, validated)
  services/backtesting.py     Bar-by-bar replay, trade simulation, baselines, walk-forward, validation criteria
  services/backtest_service.py  Background runs, progress, records the validation result
  services/ollama_service.py  Local Ollama client (status, non-streaming chat); every failure -> fallback
  services/assistant.py       Fact sheet, system prompt, citations/sources, rule-based fallback answers
  scripts/score_study.py      Research: does the score predict forward returns? (design data only)
  services/risk_manager.py    Position sizing, max loss at stop, limits, no size-up after losses
  services/paper_trading.py   SIMULATED account: orders, stops/targets, daily loss limit
  scripts/download_history.py Download 1h/4h/1d history from the command line
  services/settings_service.py  Settings page values: validation, env > settings DB > default
  services/alerts.py          Alert rules, anti-spam, SQLite state, AlertMonitor thread
  services/exports.py         CSV/JSON exports (BOM, UTC + local time, CSV-injection protection)
  services/watchlist.py       Watchlist prices (one batched Binance request, Coinbase fallback)
  services/calendar_service.py  FOMC calendar (Fed) + optional BLS releases
  templates/index.html        Dashboard
  templates/settings.html     Settings page (/settings)
  static/css/app.css, static/js/app.js, static/js/settings.js
  scripts/check_sources.py    Live connectivity check
  tests/                      442 deterministic tests (no network)
  scripts/review_news.py      Re-analyse stored/live news and print a review report
```

## API

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

## Data sources

| Source | Provides | API key | Limits | Delay | On failure |
|---|---|---|---|---|---|
| Binance (api.binance.com, mirror data-api.binance.vision) | BTC/USDT price, OHLCV | None | Weight-based, far above this app's use | Near real-time | Mirror → Coinbase → cached (marked stale) |
| Coinbase Exchange | BTC/USD price, OHLCV (4h built from closed 1h) | None | ~10 req/s public; 300 candles/request | Near real-time | Cached (marked stale) |
| CoinGecko | Market cap, dominance | Optional free Demo key | Low public limits; 5-min cache | 1–5 min | Cached or "unavailable" |
| Binance watchlist (`/api/v3/ticker/24hr?symbols=[…]`, `exchangeInfo`) | Last price + 24h change for up to 10 spot symbols | None | ONE batched request per refresh, cached ≥ 60 s; symbol checks cached 24 h | Near real-time | Coinbase `<BASE>-USD` stats where a product exists → last cached list marked stale |
| Federal Reserve Board FOMC calendar (federalreserve.gov) | FOMC meeting / decision dates | None | One page fetch per day (1 h back-off after a failure) | Published months ahead | Cached copy, else an honest empty state. Public domain (US-government work); credited to the Board. Dates only: 2:00 p.m. ET is shown as the *usual* statement time |
| BLS release calendar (iCalendar), optional | CPI, PPI, Employment Situation times | None, but set `CALENDAR_CONTACT_EMAIL` | One fetch per day | Official schedule | Skipped with the reason shown. BLS returns HTTP 403 to anonymous scripts and asks them to identify their owner, so it is off until you add your own e-mail |
| RSS: Cointelegraph, CoinDesk, The Block, Decrypt, Bitcoin Magazine, Google News | Headlines, links, publish time, short summary | None | Polled every 10 min | Minutes | Stored items still shown; feed marked error |

Only the headline, a ≤300-character snippet and the link are stored; full articles stay on the publisher's site. Google News is an aggregator: the real publisher is taken from each item's `<source>` tag.

Binance is blocked in some regions (HTTP 451); the app falls back automatically. USDT and USD series are never mixed.

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
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:5000/api/signal/config -ContentType "application/json" -Body '{"weights": {"news": 0.10}, "min_rr": 2}'
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:5000/api/signal/config -ContentType "application/json" -Body '{"reset": true}'
```
Weights are validated (no negatives or unknown names) and normalised to 100%. A settings page comes in Phase 8.

## Backtest and validation (Phase 6)

Run it from the **Backtest & validation** panel, or `Invoke-RestMethod -Method Post http://127.0.0.1:5000/api/backtest/run`.

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

**Forward observation.** After a validation from a holdout or full run, the NO TRADE gate stays on for **56 days**, with the reason "Validated on <date>; forward observation period until <date>." Only then does the validation gate clear.

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

**API**: `GET /api/assistant/status`, `POST /api/assistant/chat {"message": "..."}` (400 if empty or too long, 429 if busy), `GET /api/assistant/history?limit=50`, `POST /api/assistant/clear`, `GET /api/assistant/explain?ai=0|1`, `POST /api/assistant/route {"message": "..."}` (which path a question takes).

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

## Tested vs unverified

**Tested (442 passing, no network):**
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
- App: SQLite persistence, all API routes, bad input.
- Dashboard: rendered with fixtures on desktop and mobile with no console errors.

**Verified live on Windows:** Binance, Coinbase, CoinGecko, all six RSS feeds, the 4-year backtest (not validated), the design-period entry-rule runs, the score study, and the assistant with Ollama 0.40 + qwen2.5:3b (and its fallback with Ollama unreachable). Phase 8 (2026-10-29): price_above / price_below alerts fired about 56 s after creation and did not refire in later cycles or after a restart; the TEST alert; the monitor running with no logged errors; CSV/JSON exports of signals, news, backtest run 3 and paper orders (BOM, consistent columns); settings persisting across a restart, then reset; the CoinGecko key shown only as "not set"; the watchlist (4 Binance prices, one request); the FOMC calendar fetched from federalreserve.gov (next decision 2026-12-09, outside the 14-day window); screenshots at 1440×900 and 390×844 with no console errors and no horizontal scroll; AI explanations on, with one rewrite accepted and one withheld by the advice/prediction filter.

**Not verified:** the holdout (deliberately unused); AI answer quality on other models; answer times on a machine with a GPU. Phase 8: big_move, significant_news, high_volatility, signal_change, source_failure and risk_limit alerts are unit-tested only (no such market event happened during the live check); whether the Windows toast actually appeared (plyer raised no error, but the desktop was not observed); browser notifications (headless Chromium denies permission, so they were not shown); the BLS calendar (needs `CALENDAR_CONTACT_EMAIL`; not enabled); Coinbase watchlist fallback live (Binance worked).

## Troubleshooting

| Problem | Fix |
|---|---|
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ModuleNotFoundError` | Activate `.venv` first, then `pip install -r requirements.txt` |
| Port 5000 in use | Set `APP_PORT=5050` in `.env` |
| Binance `HTTP 451` | Regional block — app falls back to Coinbase automatically |
| All sources fail | Check internet / firewall / VPN; run `scripts\check_sources.py` |
| Chart area blank | Ensure `plotly` installed; open `/vendor/plotly.min.js` to confirm |
| "stale" badge | Source lagging or unreachable — data shown is not current |
| News panel empty | Run `scripts\check_sources.py`; a feed may block your network or change format. Click **Fetch now** to retry |
| A headline looks mis-scored | Open "Why …" on the item to see the matched rules; send me the headline to adjust the rules |
| `btc_agent.sqlite3-wal` / `-shm` files appear | Normal SQLite sidecar files (write-ahead log). Safe; never delete them while the app runs |
