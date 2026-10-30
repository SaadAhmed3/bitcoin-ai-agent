# Bugs and Risks

Audit of v0.9.1 / signal-1.7, 2026-10-30. Severity follows the roadmap: **P0** = financial-calculation correctness
or a safety bypass; **P1** = data quality, validation validity, risk management; **P2** = signal/research quality;
**P3** = UX; **P4** = optional. "Reproduced" means a script in this audit showed the failure. Scripts were run on a
temporary database, not on the user's data.

## P0

### B1 — Concurrent stop/target checks record duplicate SELL orders (reproduced)
- **Where:** `services/paper_trading.py` `check_levels()` → `sell()`; called from GET `/api/paper` (`app.py`).
- **What:** `sell()` reads the account in one connection and writes in another, with no lock and no re-check. Flask
  runs threaded. Two simultaneous refreshes (two tabs, or the 30 s poll plus a manual refresh) both see the position
  and both sell it.
- **Reproduction:** buy 4,000 USDT at 100 with stop 95, then run two concurrent `check_levels` at price 90.
  Result: **2 SELL orders, 79.96 BTC "sold" from a 39.98 BTC position**. Cash ends correct only because the last
  write wins. Realised P&L is recorded twice, the loss streak counts 2 losses (so the risk calculator halves risk),
  and today's realised loss doubles (so the daily loss limit can trip falsely). Exports show the phantom order.
- **Fix:** one `threading.Lock` around buy/sell/check_levels, plus `BEGIN IMMEDIATE` and a re-read of the account in
  the same transaction as the write. Regression test: two threads with a barrier, as in the reproduction.

### B2 — Engine settings accept negative costs and impossible values (reproduced)
- **Where:** `services/signal_service.py` `update_config()`: one range check `-1 <= v <= 10` for every number.
- **Accepted in this audit:** `fee_pct = -1`, `slippage_pct = -1` (round-trip cost −4%), `min_rr = -1`,
  `min_agreement = 5`, `buy_threshold = 9` (unreachable on a −1…+1 score).
- **Why P0:** these values feed both the live R/R check and the backtest. Combined with B3, a backtest with
  negative costs could set `validated = true` and lift NO TRADE. The fields are editable on the Settings page.
- **Fix:** per-field ranges, e.g. fee 0–1%, slippage 0–1%, min_rr 0.5–5, agreement 0–1, thresholds inside
  [−1, +1] with sell < 0 < buy ≤ strong ≤ 1, counter_trend_limit 0–1. Add 400 tests for each bound.

## P1

### B3 — A *full*-period backtest can validate the strategy
- **Where:** `services/backtesting.py` (`validated = all_passed and cfg.period != "design"`), `backtest_service.py`.
- **What:** with the default 4 years, a *full* run covers 2022-10-30 → today: about 3 years of design data the rules
  were developed on (versions 1.0–1.7, the 6.2 entry-rule study, the score study) plus the holdout year. A pass would
  be mostly in-sample. README documents this as intended; methodologically it is a leak.
- **Fix:** only a *holdout* run may write `validated = true`. *Full* becomes informational (like *design*, with its
  own badge). This tightens the gate and never loosens it.

### B4 — The holdout can be re-run without limit
- **Where:** `POST /api/backtest/run` with `period=holdout`; the Research view's Period select.
- **What:** the "test once" protocol (README §6.2) is not enforced. Re-running with changed settings is exactly
  how a holdout gets overfitted.
- **Fix:** record each holdout run (config hash, time) in a write-once table. A second holdout run for a config hash
  that has already been tested is shown as "holdout already used" and cannot validate. Optionally require an explicit
  confirmation before the first holdout run.

### B5 — Manual paper SELL fills at a stale price
- **Where:** `paper_trading.py` `sell()`. `buy()` rejects `is_stale`; `sell()` does not. When every price source
  fails, `get_price(force=True)` returns the cached price marked stale, and the SELL fills at it.
- **Fix:** reject stale prices in `sell()` the same way (`check_levels` already skips stale prices).

### B6 — Paper stops and targets are only checked while the Paper view is open
- **Where:** `static/js/app.js` (`every(30000, ["paper"], loadPaper)`); the background `AlertMonitor` reads paper
  state but never calls `check_levels`.
- **What:** with the dashboard on another view or closed, a stop is not "hit" until someone opens Paper. It then
  fills at the current price, possibly far beyond the stop. The docstring warns that fills can be late, but the
  delay is unbounded.
- **Fix:** call `check_levels` from the 60 s monitor loop (it already runs in `python app.py`), under the B1 lock.

### B7 — No data-quality gate for the 1h timeframe
- **Where:** `signal_engine.generate_signal`: the staleness and gap gates loop over `("4h", "1d")` only.
- **What:** 1h drives 40% of momentum and half of overextension. A stale 1h series silently feeds the signal.
- **Fix:** include 1h in the stale/gap gates (thresholds scaled to the interval).

### B8 — Live engine differs from the tested engine
- News has 15% weight live; it is re-normalised away in every backtest because headlines cannot be replayed. A
  validation therefore applies to a configuration that never runs live.
- **Fix (choose one, both conservative):** (a) the validation applies only while the news weight is 0, or
  (b) news may only *block* (the existing fresh-news gate) and never add to the score until it has its own
  out-of-sample evidence. (b) is recommended.

### B9 — No cross-source price sanity check
- A single bad print from the first provider is accepted. Recommended: compare with the second provider (or
  CoinGecko) when available; above about 1% deviation, mark the price "unverified" and raise NO TRADE.

### B10 — Price and candles may come from different providers after a fallback
- `get_price` and `get_candles` fall back independently, so a Binance USDT price can be compared with Coinbase USD
  levels. Show both sources and warn when the quotes differ.

## P2

| ID | Issue | Where | Fix |
|---|---|---|---|
| B11 | Paper "daily loss" counts the full unrealised loss since entry as today's loss | `paper_trading._today_pnl` / `state` | Mark the position at the day-start price (or the last-day snapshot) and use only the change since 00:00 UTC |
| B12 | Paper buys ignore the 1% risk rule and loss-streak halving (calculator only) | `paper_trading.buy` | Show the calculated size next to the order form; warn (do not block) when the order exceeds it |
| B13 | Unrealised P&L and equity ignore exit fee and slippage | `paper_trading.state` | Add "liquidation value" next to equity, or label equity "before exit costs" |
| B14 | Sharpe is reported for 6 trades; profit factor `inf` with 0 losses | `backtesting.metrics` | Suppress ratio metrics below 30 trades with "not meaningful (n=6)" |
| B15 | A zero-return trade counts as a loss | `backtesting.metrics` (`r <= 0`) | Count as a scratch, or document it |
| B16 | GET `/api/paper` has a side effect (it can execute a simulated exit) | `app.py` | Moving the check to the monitor (B6) removes it from GET |
| B17 | Session VWAP on 4h/1d is not meaningful | `technical_analysis.add_indicators` | Show VWAP on ≤1h only |
| B18 | `/api/mtf` note "No signal engine yet (Phase 5)" is out of date | `app.py` | Update the text |

## P3 / hygiene

- `.gitignore` lists every entry three times; `data/*.png` (~4.5 MB) and `prompt_phase*.md` are tracked despite
  being ignored. Untrack them with `git rm --cached` (the files stay on disk).
- `_rr_after_costs` duplicates `build_buy_plan.rr`.
- `user_agent` still says `0.3`.

## Risks that are not bugs

- **No demonstrated edge.** See QUANTITATIVE_AUDIT.md. The app already shows NO TRADE for this reason.
- **Free API terms and availability.** Binance returns HTTP 451 in some jurisdictions; CoinGecko's public limits are
  tight. The fallback chain handles this; there is no third candle source.
- **Stops are not guaranteed.** Both the backtest and the paper account fill touched stops at the stop price (plus
  0.05% slippage); real fast-market fills are worse. This is documented in the UI, but the backtest has no
  stress setting (e.g. extra stop slippage).
