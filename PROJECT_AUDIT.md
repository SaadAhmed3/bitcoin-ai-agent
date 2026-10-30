# BTC Signal Lab — Project Audit (v0.9.1, engine signal-1.7)

Audit date: 2026-10-30. Scope: the whole repository at commit `1d23a89`. **No source file was changed during this audit.**
Companion documents: [BUGS_AND_RISKS.md](BUGS_AND_RISKS.md), [QUANTITATIVE_AUDIT.md](QUANTITATIVE_AUDIT.md),
[BACKTEST_VALIDATION_REPORT.md](BACKTEST_VALIDATION_REPORT.md), [SIGNAL_ENGINE_REPORT.md](SIGNAL_ENGINE_REPORT.md),
[SECURITY_AUDIT.md](SECURITY_AUDIT.md), [TEST_RESULTS.md](TEST_RESULTS.md), [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md).

## Summary

BTC Signal Lab is in better shape than most projects of this kind. It is local-first, never places orders, says plainly
that it is not validated, and its NO TRADE gate is real and conservative. The backtester replays the live engine
candle by candle without look-ahead. 493 unit tests pass.

The problems are concentrated in a few places:

1. **Validation protocol.** A *full*-period backtest, which overlaps the design data the rules were developed on, is
   allowed to lift the NO TRADE gate. The holdout's "test once" rule is a convention only; nothing enforces it.
2. **Unbounded engine settings.** Negative fees, negative slippage and a negative `min_rr` are accepted. Together with
   point 1, a user could "validate" a strategy using negative trading costs.
3. **Paper-trading integrity.** Stop/target exits are not atomic: two concurrent refreshes record two SELL orders for
   one position (reproduced). Stops are only checked while the Paper view is open. A manual SELL fills at a stale price.
4. **No demonstrated edge.** The score has no measurable predictive value on the design period (Spearman −0.018,
   95% CI [−0.093, +0.051]). The live configuration made 6 trades in 4 years. This is a research finding, not a bug.
   The app already reports it honestly.

## Architecture

```
app.py (Flask, 49 routes, create_app factory)  ── security.py (host allow-list, CSRF, CSP, rate limits)
 ├─ services/market_data.py   Binance → Coinbase fallback, CoinGecko overview, candle validation, SQLite cache
 ├─ services/technical_analysis.py   EMA/SMA/RSI/MACD/BB/ATR/ADX/VWAP, swings, S/R, breakouts, divergence
 ├─ services/news_collector.py + news_analyzer.py + news_service.py   6 RSS feeds, rule-based sentiment, clustering
 ├─ services/signal_engine.py (pure) + signal_service.py (inputs, history, settings)
 ├─ services/backtesting.py (pure replay/simulate/metrics) + backtest_service.py (thread, persistence, validation)
 ├─ services/risk_manager.py, paper_trading.py   sizing calculator, SIMULATED account
 ├─ services/assistant.py + ollama_service.py   grounded fact sheet, rule templates, optional qwen2.5:3b rewrite
 ├─ services/alerts.py, exports.py, settings_service.py, watchlist.py, calendar_service.py
 ├─ database/db.py   SQLite (WAL), 16 tables, CHECK(simulated = 1) on paper orders
 └─ templates/ + static/js/app.js (1,423 lines, hash router, 9 views) + static/css/app.css
```

Size: ~16,600 lines (≈8,100 application Python incl. scripts, ≈5,900 test Python, ≈2,550 frontend).

## 1. Existing features

| Area | Features |
|---|---|
| Market data | BTC/USDT price and 24h change (Binance, mirror, Coinbase fallback); OHLCV 5m/15m/1h/4h/1d; market cap, 24h volume and BTC dominance (CoinGecko); source health table; history downloader (5.5 years stored locally) |
| Technical analysis | EMA 9/21/50/200, SMA 50/200, RSI 14 (Wilder), MACD 12/26/9, Bollinger 20/2, ATR 14, ADX/DI 14, session VWAP, volume ratio, fractal swings, market structure score, S/R clusters, breakout/false breakout, volatility regime, RSI divergence |
| Multi-timeframe | `/api/mtf` table for all 5 timeframes; the signal uses 1d + 4h (trend), 4h + 1h (momentum) |
| News | 6 RSS feeds, URL canonicalisation, cross-publisher clustering, headline-only rule sentiment (rules-2.2), BTC relevance, verification level, freshness (new / recent / priced-in / too old / superseded), conflict detection, news bias |
| Signal | 5 labels, 6 weighted components, agreement, trade plan (entry zone, stop, 2 targets, R/R after costs), pending breakout/pullback orders, 9 NO TRADE gates, evidence for/against, invalidation, signal history with audit trail |
| Research | Walk-forward backtest, buy & hold / EMA 50/200 / random baselines, 7 validation criteria, funnel diagnostics, hit rate, design/holdout split, 56-day forward observation, score study script |
| Risk & paper | Position-size calculator (risk %, cap, loss-streak halving, costs); SIMULATED account with fees, slippage, stop/target, exposure cap, daily loss limit |
| Assistant | Rule-based answers from a fact sheet; optional Ollama rewrite checked for invented numbers, advice and outlet names |
| Alerts | 8 rule kinds, cooldowns, dedupe, hourly cap, optional Windows desktop notifications |
| Other | CSV/JSON exports, settings page with env precedence, watchlist, FOMC/BLS calendar, light/dark themes, mobile layout |

## 2. Features that work correctly (verified in this audit)

- Candle validation drops malformed, non-finite, inconsistent (high < max(open, close) …) and **still-open** candles;
  de-duplicates by open time; gap counting is correct. The stored 4h and 1d history has **no gaps**; 1h has 7 missing
  candles in 5.5 years (exchange outages).
- Providers are never mixed in one candle series; cached and stale prices are labelled as such.
- Indicators match their textbook definitions (Wilder RSI/ATR/ADX seeded with an SMA; population std for Bollinger).
  RSI and ADX on the live 500-candle window match the full-history values exactly. EMA200 differs by at most 0.13% (4h)
  and 0.28% (1d) because of the EMA seed (see QUANTITATIVE_AUDIT §1).
- Backtest replay: decisions at the close of bar *i*, fills at the open of bar *i+1*; only candles closed by the
  decision time are visible; 1h and 1d are aligned with `bisect_right` on close times. Stop-first on same-candle
  ambiguity; gap-through-stop fills at the worse open; fees and slippage on both sides; one position at a time.
- NO TRADE gates fire as documented and are listed to the user with reasons.
- Security layer (see SECURITY_AUDIT.md): loopback bind, Host allow-list, CSRF header/Origin check, CSP, body limit,
  rate limits, non-finite number rejection.
- Paper orders are forced to `simulated = 1` by a database CHECK constraint; no exchange keys exist anywhere.

## 3. Incomplete features

| Requested | State |
|---|---|
| Live engine = tested engine | News carries 15% weight live but is absent from every backtest, so the live configuration has never been tested |
| Signal confidence | `confidence` is a fixed text string; `agreement` exists but is not presented as confidence |
| Timeframe agreement / "why timeframes disagree" | Per-timeframe trends are shown, but there is no explicit alignment or conflict summary |
| 15m / 5m in the analysis | Fetched and charted; not used by the signal (acceptable: Phase 4 calls 5m optional) |
| Backtest metrics | Missing: Sortino, expectancy in R, average win / average loss, max consecutive losses, per-regime results, bootstrap confidence intervals, parameter sensitivity |
| Strategy research (Phase 10) | Only the composite engine and two pre-registered entry variants are tested; no standalone simple strategies |
| ML (Phase 11) | scikit-learn is a dependency but unused in the app |
| Alerts | Missing: S/R reached, RSI extremes, MACD cross, daily trend change, signal invalidation, paper stop/target hit |
| Paper | Risk-per-trade sizing and loss-streak halving are not applied to paper buys (calculator only); no per-trade P&L view (account-level average cost only) |
| Docs | No USER_GUIDE; README is 654 lines and developer-oriented |

## 4. Bugs and potential failures

Full list with severity and reproduction: [BUGS_AND_RISKS.md](BUGS_AND_RISKS.md). The most important:

- B1 (P0) Concurrent stop/target checks record duplicate SELL orders (reproduced).
- B2 (P0) Signal-engine settings accept negative fees, slippage and `min_rr`, and nonsensical thresholds.
- B3 (P1) A *full*-period backtest can validate the strategy and lift NO TRADE.
- B4 (P1) The holdout can be re-run any number of times from the UI and API.
- B5 (P1) Manual paper SELL fills at a stale cached price.
- B6 (P1) Paper stops are only evaluated while the Paper view is open.
- B7 (P1) A stale or gappy 1h series does not trigger NO TRADE (only 4h and 1d are checked).
- B8 (P1) The live engine (news weight 15%) is not the configuration that any backtest tested.
- B11 (P2) The paper daily loss counts the full unrealised loss since entry as today's loss.

## 5. Data-quality problems

- No cross-source sanity check: one bad Binance print would be accepted (no comparison with Coinbase or CoinGecko).
- After a fallback, the price can come from one provider while the candles come from another (USDT vs USD basis).
  Levels and the live price are then compared across quotes. The basis is usually small (<0.1%), but it is not shown.
- The signal checks staleness and gaps for 4h and 1d only; a stale or gappy **1h** series (used for momentum and
  overextension) does not trigger NO TRADE.
- The 1d series is "stale" only after 2 days (2 × interval); for a daily trend filter, 1 missed daily close should warn.
- Session VWAP on 4h (6 candles per UTC day) is shown but is not meaningful on that timeframe.

## 6. Quantitative weaknesses (details in QUANTITATIVE_AUDIT.md)

- The composite score shows no predictive value on design data; the SELL bucket had *above-average* forward returns.
- 98.8% of raw BUY bars are downgraded by the reward/risk check (median R/R 0.46), so the engine almost never trades:
  6 trades in 4 years. That cannot be validated statistically, whatever the result.
- Backtest sizing (100% of equity per trade) differs from the live risk model (1% risk, 50% cap), so backtest
  drawdowns do not describe what a user following the app would experience.
- Validation criteria compare return/drawdown ratios between strategies with 1% and 56% exposure. That is a weak
  comparison, and with only 5 windows the walk-forward consistency criterion is weak too.

## 7. Security risks

None critical. See SECURITY_AUDIT.md. Main items: GET `/api/paper` has a side effect (it can execute a simulated
exit), and the engine settings lack range validation (B2). `pip-audit -r requirements.txt` (run 2026-10-30): no known vulnerabilities.

## 8. Performance

- `/api/mtf`, `/api/signal`, alerts and the assistant each call `analyze()` on the same candles independently. A
  dashboard refresh recomputes the same indicators several times. There is no analysis cache keyed on (timeframe,
  last candle).
- The backtest replays ~8,760 bars × 5 weight sets with full `analyze()` per bar: 5–10 minutes for 4 years. That is
  acceptable for a research tool; it could be made several times faster by caching the per-bar analyses that do not
  depend on the weights (already shared) and vectorising the indicators once per window.
- Unit tests take 130 s; most of it is backtest replays in tests.

## 9. Duplicate or unnecessary code

- `_rr_after_costs` duplicates the inner `rr()` of `build_buy_plan` (it says so in its docstring); one function would do.
- Three `_num`-style helpers (technical_analysis, app.py, others) with different semantics.
- `.gitignore` repeats its entries three times. `data/*.png` (≈4.5 MB of screenshots) and `prompt_phase*.md` are
  listed in `.gitignore` but were committed before that, so they are still tracked.
- `/api/mtf` returns the note "No signal engine yet (Phase 5)", which is out of date.

## 10. Recommended improvements

See [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) (P0–P4, files, risks, tests, acceptance criteria).
In short: fix the paper and settings bugs (P0); tighten the validation protocol, enforce single use of the holdout,
gate on 1h data quality and add cross-source price checks (P1); add the missing metrics, regime breakdown, bootstrap
and sensitivity analysis, and a separate simple-strategy research track on design data only (P2); UX for confidence,
timeframe alignment and Overview summaries (P3); ML only if it beats the baselines after costs (P4).
