# BTC Signal Lab

A local Bitcoin **research** dashboard for Windows. It collects free market data and news, explains what the
indicators say, produces a cautious research signal, backtests that signal honestly, and lets you practise with a
**simulated** paper account.

It never places real orders, has no exchange account connection and needs no paid service. **It is not financial
advice.** The signal has **not** been validated: by design it says **NO TRADE** until a strict out-of-sample test
passes (see [Validation](#validation-why-the-signal-says-no-trade)).

New to the app? Read the **[User Guide](USER_GUIDE.md)**: installation step by step, every screen, and what the
signal and NO TRADE mean.

| | |
|---|---|
| App version | 0.9.5 (batch 2: data checks) |
| Signal engine | **signal-1.8** |
| Validation | Not validated. Reserved holdout (from 2025-10-29): **untouched** |
| Platform | Windows 10/11, Python 3.12+ (tested on 3.14.6), runs on `http://127.0.0.1:5000` |

## Quick start (PowerShell)

```powershell
cd $HOME\Projects\bitcoin-ai-agent        # the project folder
python -m venv .venv                      # once
.\.venv\Scripts\Activate.ps1              # every new PowerShell window
pip install -r requirements.txt           # once (and after updates)
python app.py                             # start; leave the window open, Ctrl+C stops it
Start-Process "http://127.0.0.1:5000"     # open the dashboard
```

Optional local AI assistant (free): `winget install Ollama.Ollama`, then `ollama pull qwen2.5:3b`. The app works
without it. Full instructions, including the "running scripts is disabled" fix: [User Guide → Installation](USER_GUIDE.md#1-installation).

## What it does

| View | What you see |
|---|---|
| Overview | Live price and 24h change (with source and age), the current signal and its main reason, chart, alerts, next US macro event, watchlist |
| Market & chart | Candles 5m-1d with EMA, Bollinger, VWAP, support/resistance; a multi-timeframe table |
| News & calendar | Headlines grouped into events, with sentiment, impact, verification and freshness; FOMC/BLS dates |
| Signal details | The signal, every NO TRADE reason, evidence for and against, components, the research trade plan, risk |
| Research (backtest) | Backtests on real Binance history, baselines, validation criteria, holdout status |
| Risk & paper | Position-size calculator; the SIMULATED paper account |
| Alerts | Rules, cooldowns, history |
| Ask the data | Questions answered from the app's own data (optional local AI rewrite) |
| System & sources | Source health, versions, these documents |

## How the signal works (signal-1.8)

The engine reads **closed** candles only (1h, 4h, 1d) and scores five components from −1 to +1:

| Component | What it measures | Weight (setting) | Effective weight |
|---|---|---|---|
| Trend | 1d + 4h structure, EMA stack, price vs EMA200, SMA50/200 | 0.30 | 35.3% |
| Momentum | 4h + 1h RSI, MACD histogram (ATR-scaled), ADX/DI | 0.25 | 29.4% |
| Volume | 4h volume vs its 20-bar average | 0.10 | 11.8% |
| Structure | Breakouts, false breakouts, nearby levels, RSI divergence | 0.10 | 11.8% |
| Overextension | RSI extremes and closes outside the Bollinger Bands (a brake) | 0.10 | 11.8% |
| News | **Not in the score** (signal-1.8). News can only block (see below) | 0 | 0 |

The score is the weighted sum, rescaled over the components that have data. That is the formula every backtest has
used. **The score is not a probability.**

| Score | Setup |
|---|---|
| ≥ +0.50 and ≥ 75% of weighted evidence agrees | STRONG BUY SETUP |
| ≥ +0.25 | BUY SETUP |
| ≤ −0.25 | SELL / REDUCE EXPOSURE |
| otherwise | HOLD |

A BUY setup also needs reward/risk ≥ 1.5 to its first target **after** fees and slippage (0.10% + 0.05% per side);
otherwise it becomes HOLD and the plan is shown for research only.

**NO TRADE** overrides any setup when one of these is true:

| Condition | Rule |
|---|---|
| No price / stale price | no live price, or older than 120 s |
| Price sources disagree | the second exchange differs by more than 0.5% (setting 0.1-5%) **after converting USDT to USD** with Coinbase's USDT-USD rate. Quotes (and the rate) observed more than 15 s apart are not compared. Without a usable rate, or without a second source, it is a warning only. USDT more than 1% away from 1.00 gives a separate "stablecoin deviation" warning and alert (never a block by itself) |
| Too little data | fewer than 100 closed 4h candles, or trend/momentum cannot be computed |
| Stale or gappy candles | 1h, 4h or 1d: latest candle older than two intervals, or more than 5 missing candles |
| Extreme volatility | 4h or 1h ATR% in the top 5% of its recent range |
| Contradictory evidence | \|score\| ≥ 0.25 but less than 60% of weighted evidence agrees |
| Against the daily trend | a BUY setup with the 1d trend below −0.30 (a SELL setup above +0.30) |
| News against the setup | high impact and **confirmed** within 24 h (not priced in); or **unverified** or **speculative** within 2 h. Bearish news blocks BUY setups, bullish news blocks SELL setups; HOLD is never blocked. Older speculative news is a warning |
| Calculation error | any exception while computing the signal (the reason names the step) |
| Not validated | no passing holdout run for these exact rules (see below) |

What changed in signal-1.8 is listed exactly in [CHANGELOG.md](CHANGELOG.md).

## Validation (why the signal says NO TRADE)

The backtest replays the live engine bar by bar on real Binance history, with no look-ahead: decisions at a 4h
close, fills at the next open, stop assumed first when a candle touches both, costs on both sides.

| Period | Use | Can it lift NO TRADE? |
|---|---|---|
| design (to the holdout start) | developing and comparing rules | never |
| full | informational overview (includes design data) | never |
| holdout (from 2025-10-29) | one pre-registered test | only the **first** holdout run in the period |

- **Single-use holdout.** The first holdout run in a holdout period, whatever its rules, spends the period. Later
  runs are stored and labelled "not valid for validation: holdout already spent on <date>, run #N".
- **New holdout start.** After the holdout is spent, a different rule set can only be tested on a new holdout that
  starts after the spending run (forward data only), set in the Research view.
- **Rule-set fingerprint.** Each run records the settings plus a SHA-256 of `signal_engine.py`,
  `technical_analysis.py` and `backtesting.py`. A validation counts only while that fingerprint matches the code
  that is running.
- **Stored verdict.** PASS/FAIL and the criteria values are stored at run time in write-once database rows; the gate
  never recalculates them.
- **Forward observation.** Even after a passing holdout run, NO TRADE stays on for 56 more days.

Criteria (all must pass): at least 30 trades (15 on the holdout), profit factor ≥ 1.2, positive net return after
costs, max drawdown ≤ 25%, better than 90% of random-entry runs, better return/drawdown than EMA 50/200, and ≥ 60% of
walk-forward windows positive.

**Results so far** (design data only, see [QUANTITATIVE_AUDIT.md](QUANTITATIVE_AUDIT.md)): the score shows no
measurable predictive value at the 3-day horizon, and the reward/risk rule leaves very few trades (5 in three years
of design data). That is why the app says NO TRADE.

## Paper trading and risk

- The paper account is **simulated**: every order is stored with `simulated = 1` (a database rule) and labelled
  SIMULATED. Fees and slippage are charged; orders are refused at a stale price.
- Simulated stops and targets are checked **only** by the background monitor (every 60 s while the app runs).
  Opening the Paper view never sells anything.
- The daily loss limit measures today's change since 00:00 UTC.
- The risk calculator sizes a position so a stop-out costs your chosen risk (default 1%, at most `max_risk_pct`,
  default 2%) **including** costs, capped at 50% exposure, and halves risk after two losses in a row. Stops are not
  guaranteed fills.

## Data sources

All free; no account needed. The only optional key is a free CoinGecko Demo key.

| Source | Provides | Notes |
|---|---|---|
| Binance spot (`api.binance.com`, mirror `data-api.binance.vision`) | BTC/USDT price, candles 5m-1d, backtest history | Blocked in some regions (HTTP 451) |
| Coinbase Exchange | BTC/USD price and candles; the **second price** and the **USDT-USD rate** for the cross-check | USD and USDT series are never mixed in a chart; for the cross-check the USDT price is converted to USD first |
| CoinGecko | Market cap, volume, BTC dominance | "Data from CoinGecko" attribution shown |
| RSS (Cointelegraph, CoinDesk, The Block, Decrypt, Bitcoin Magazine, Google News) | Headlines | Headline, a short snippet and the link only |
| federalreserve.gov, BLS (optional) | FOMC dates; CPI/PPI/jobs dates | BLS needs `CALENDAR_CONTACT_EMAIL` in `.env` |
| Ollama on `127.0.0.1` (optional) | Local AI rewrites of rule-based answers | Data never leaves your PC |

Never mixed or hidden: every price shows its source and age; cached or stale data is labelled; missing candles are
counted, not filled in.

## API (local only)

Writes (POST/PUT/DELETE) from outside the dashboard need the header `X-Requested-With: BTCSignalLab`.

| Endpoint | Purpose |
|---|---|
| `GET /api/health`, `/api/price`, `/api/overview`, `/api/sources` | Status, price, market overview, source health |
| `GET /api/candles?tf=1h`, `/api/analysis?tf=4h`, `/api/mtf` | Candles, indicators, multi-timeframe table |
| `GET /api/news`, `POST /api/news/refresh` | News events; fetch now |
| `GET /api/signal`, `/api/signal/history`, `/api/signal/<id>` | Current signal; stored history |
| `GET`/`PUT /api/signal/config` | Engine settings (range-checked; `{"reset": true}`) |
| `POST /api/backtest/run` `{"years": 4, "period": "design", "entry_mode": "standard"}` | Start a backtest |
| `GET /api/backtest/status`, `/latest`, `/runs`, `/<id>`, `/holdout` | Progress, results, holdout status and registry |
| `POST /api/backtest/holdout_start` `{"start": "YYYY-MM-DD"}` | New holdout period (only after the current one is spent) |
| `POST /api/risk/size` | Position size and maximum loss at the stop |
| `GET /api/paper` (read-only), `POST /api/paper/order`, `/reset`, `/settings` | SIMULATED account |
| `GET`/`POST /api/alerts/rules`, `GET /api/alerts` | Alert rules and alerts |
| `GET /api/export/<dataset>?format=csv\|json` | candles, signals, news, backtest, paper_orders, alerts, chat |
| `GET /api/settings`, `PUT /api/settings/<section>` | Settings (env values win and are shown read-only) |

## Project layout

```
app.py                      Flask app and routes (binds to 127.0.0.1)
config.py                   settings from environment / .env
database/db.py              SQLite schema, additive migrations, backups before upgrades
services/market_data.py     providers, candle validation, cross-source price check
services/technical_analysis.py  indicators, swings, support/resistance
services/signal_engine.py   the signal (pure function), NO TRADE gates
services/backtesting.py     replay, simulation, metrics, validation criteria
services/backtest_service.py   background runs, holdout registry, validation record
services/paper_trading.py   SIMULATED account (atomic orders)
services/news_*.py          RSS collection and rule-based news analysis
services/assistant.py       grounded question answering (+ optional Ollama)
services/alerts.py          alert rules, cooldowns, background monitor
static/, templates/         dashboard (no build step)
tests/                      unit/API tests; tests/e2e Playwright UI tests
```

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest                                    # unit + API tests, no network (about 3-4 min)
python -m playwright install chromium               # once
python -m pytest -m e2e                             # browser tests on a local fake-data app (about 5 min)
python -m pytest --cov=app --cov=services --cov=database   # with coverage
python -m pip_audit -r requirements.txt             # known-vulnerability check
```

Latest results are in [TEST_RESULTS.md](TEST_RESULTS.md).

## Security

Local-first: the app binds to `127.0.0.1` (a network bind needs `APP_ALLOW_REMOTE=1`), rejects foreign Host headers
and cross-site writes, sends a strict Content-Security-Policy and never shows the debugger on a network address.
See [SECURITY.md](SECURITY.md) and [SECURITY_AUDIT.md](SECURITY_AUDIT.md).

## Documents

| Document | Contents |
|---|---|
| [USER_GUIDE.md](USER_GUIDE.md) | Beginner guide: install, screens, signal, NO TRADE, backtests, paper trading, troubleshooting |
| [CHANGELOG.md](CHANGELOG.md) | Every version, including exactly what signal-1.8 changed |
| [PROJECT_AUDIT.md](PROJECT_AUDIT.md), [BUGS_AND_RISKS.md](BUGS_AND_RISKS.md) | Audit and its status |
| [QUANTITATIVE_AUDIT.md](QUANTITATIVE_AUDIT.md), [BACKTEST_VALIDATION_REPORT.md](BACKTEST_VALIDATION_REPORT.md), [SIGNAL_ENGINE_REPORT.md](SIGNAL_ENGINE_REPORT.md) | Indicators, evidence, backtest method |
| [SECURITY.md](SECURITY.md), [SECURITY_AUDIT.md](SECURITY_AUDIT.md) | Threat model and controls |
| [TEST_RESULTS.md](TEST_RESULTS.md), [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) | Test results; what comes next |

## Disclaimer

Research and education only. Past performance, simulated or backtested, does not predict future results. Crypto
prices can fall sharply; stops can fill far below their level. Nothing here is a recommendation to buy or sell.
