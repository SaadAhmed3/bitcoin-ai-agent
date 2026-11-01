# Pre-registration: Strategy Lab, strategy 2 — Trend following (4h pullback continuation)

| | |
|---|---|
| Status | **PRE-REGISTERED** (2026-11-01) on the owner's approval, committed **before** the first backtest. Rules, defaults, criteria and sensitivity checks below are fixed; the design backtest runs **once** (`scripts/research_strategy.py trend`, refused a second time). Nothing is tuned to its result, and no setups were counted on design data to choose these rules. |
| Config hash: `27ce4fee913591af` | `services.strategies.base.config_hash(trend.DEFAULTS)`. The backtest script refuses to run if the defaults no longer produce this hash. |
| Test data | **Design data only**: Binance BTCUSDT 4h and daily candles closed before 2025-10-29 00:00 UTC (the contaminated holdout is never used). No "full" period. |
| Live signal | Unchanged: signal-1.8 does not read this strategy and this strategy does not read or change signal-1.8 settings. Simulated only, spot long only. |

## 1. How it differs from H2

H2 (`PREREGISTRATION_H2.md`) is a **daily EMA 50/200 crossover**: always 100% in the market while the daily EMA 50 is
above the daily EMA 200, otherwise cash; no stops, no targets, no entry timing. This strategy **differs** in every
part: it decides on **4h** candles; it requires the full 4h EMA 21/50/200 alignment, a rising EMA 50, ADX strength
and a daily confirmation; it enters only on a **pullback-and-resume trigger**; every trade has a **structure/ATR
stop**, a **2R target**, a **trend-break exit** and a **time limit**; and it sizes each trade by **1% planned risk**,
so it is usually far from 100% invested. The owner removed the RSI 45–70 and volume ≥ 0.8× filters before any
backtest (fewer conditions, less hidden fitting).

## 2. Rules (fixed)

Decided at the **close** of each 4h bar from closed candles only; the daily filter uses only daily candles that had
closed by that 4h close.

| Part | Rule |
|---|---|
| Trend filter | 4h EMA 21 > EMA 50 > EMA 200 (pandas `ewm(span, adjust=False)`), EMA 50 higher than 10 bars earlier, ADX(14) ≥ 20, and the last closed daily close > the daily EMA 50 |
| Entry trigger | the previous bar's low touched EMA 21 (low[i−1] ≤ EMA21[i−1]) and this bar closes above the previous bar's high |
| Entry | BUY SETUP when filter and trigger hold → buy at the **next 4h open**: fill = open × (1 + slippage), fee on the notional |
| Stop | the **lowest low of the last 5 closed 4h bars** (including the signal bar) **− 0.25 × ATR(14)** of the signal bar; the trade is skipped if the next open is at or below it |
| Target | fill + 2 × (fill − stop) (2R; planned reward/risk 2.0, above the 1.5 research minimum) |
| Exits | stop (intrabar; a gap below it exits at the open); target; if one bar touches both, the **stop** counts; a 4h close below EMA 50 → exit at the next open; after **30 × 4h bars** (5 days) → exit at the next open; a trade still open at the end of the design data is closed at the last close |
| Size | planned loss to the stop = 1% of the account (absolute ceiling 2%), never more than 100% of the account (no leverage), one position at a time |
| Costs | 0.10% fee + 0.05% slippage per side; equity marked at liquidation value |
| Live research signal | BUY SETUP (filter + trigger); HOLD (filter holds, no trigger); EXIT / REDUCE EXPOSURE (the filter held on the previous bar and this close is below EMA 50); NO TRADE otherwise, or on stale / missing data. Strength = share of the 4 filter checks (heuristic, not a probability). |

| Setting | Value |
|---|---|
| `ema_fast` | `21` |
| `ema_mid` | `50` |
| `ema_slow` | `200` |
| `slope_bars` | `10` |
| `adx_min` | `20.0` |
| `htf_ema` | `50` |
| `swing_bars` | `5` |
| `stop_atr_buffer` | `0.25` |
| `target_r` | `2.0` |
| `time_limit_bars` | `30` |
| `risk_pct` | `1.0` |
| `fee_pct` | `0.1` |
| `slippage_pct` | `0.05` |

## 3. Design window

From the first 4h bar with a full warm-up (EMA 200 + 100 bars and a valid daily EMA 50) to the last 4h bar that closed
before 2025-10-29 00:00 UTC.

## 4. Pass criteria (all must hold; design data only)

| # | Criterion |
|---|---|
| — | **trades ≥ 30**, otherwise the verdict is **INCONCLUSIVE** (neither pass nor fail) |
| C1 | expectancy ≥ **+0.15R** per trade after fees and slippage (R = net P&L / planned risk to the stop) |
| C2 | profit factor ≥ **1.3** |
| C3 | max drawdown ≤ **15%** of the account |
| C4 | net return beats ≥ **90%** of 1,000 random-entry runs (seed 7): the same number of trades, each with the same holding time as a strategy trade as its maximum, entered at random non-overlapping bars, with the same stop formula, target, sizing and costs |

Buy & hold (same 4h window, same costs) and the H2 rule (same period, its own definition) are **reported for
comparison, not as pass criteria**. Walk-forward: consecutive 182-day windows with the same fixed rules, reported
(trades, return, sum of R per window). Design results never validate the strategy.

## 5. Sensitivity checks (reported only, never used to choose parameters)

ADX 15 / 20 / 25 × target 1.5R / 2R / 3R (9 runs, the default included), each with all other rules unchanged.

## 6. Forward test (the only path to "forward tested")

The separate strategy forward tracker (`services/strategy_forward.py`) starts this configuration the next UTC
midnight after the merged code first runs and processes each new closed 4h bar once with the same per-bar step as
the backtest. After 365 days: F1 net return > 0 and F2 expectancy ≥ +0.15R; fewer than 30 trades → INCONCLUSIVE.
Changing any setting creates a new configuration version, marked NOT VALIDATED (settings changed), which inherits
neither the backtest nor the forward test.
