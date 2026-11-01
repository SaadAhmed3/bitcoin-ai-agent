# Pre-registration: Strategy Lab, strategy 4 — Breakout trading (4h, long only)

| | |
|---|---|
| Status | **PRE-REGISTERED** (2026-11-02) on the owner's specification, committed **before** the first backtest. Rules, defaults, criteria and sensitivity checks below are fixed; the design backtest runs **once** (`scripts/research_strategy.py breakout`, refused a second time). Nothing is tuned to its result. |
| Config hash: `b4aa0a6c76185000` | `services.strategies.base.config_hash(breakout.DEFAULTS)`. The backtest script refuses to run if the defaults no longer produce this hash. |
| Test data | **Design data only**: Binance BTCUSDT 4h candles (and 1d candles for the H2 comparison) closed before 2025-10-29 00:00 UTC (the contaminated holdout is never used). No "full" period. |
| Live signal | Unchanged: signal-1.8 does not read this strategy. Simulated only, spot long only. |
| Design lesson | From Range (too few trades to judge): **few filters**, so the strategy produces enough trades to be judged. |

## 1. Rules (fixed)

Decided at the **close** of each 4h bar i, closed candles only.

| Part | Rule |
|---|---|
| Level | the **highest HIGH of the previous 20 closed 4h bars** (bars i−20 … i−1; the signal bar is excluded) |
| Entry signal | bar i **CLOSES** above the level (a wick alone never counts) **and** its volume ≥ **1.2 ×** the average volume of the 20 bars before it. BUY SETUP → buy at the **next bar's open**; skipped if that open is already at or below the stop |
| Stop | **breakout level − 1.0 × ATR(14)** (ATR of the signal bar). This is the **initial stop**; it never moves |
| Exits | the stop (intrabar; a gap below it exits at the open); **trailing**: the first 4h close below the **lowest low of the last 10 bars** → exit at the next open; after **60 bars** → exit at the next open. **No fixed target** (breakouts are meant to run). A trade open at the end of the design data is closed at the last close |
| R | measured from the **initial stop**: R = net P&L / (units × (fill − initial stop)) |
| Size and costs | planned loss to the stop = **1%** of the account (ceiling 2%), never more than 100% of the account, **one position at a time**; 0.10% fee + 0.05% slippage per side (the same as Trend and Range); marked at liquidation value |
| Live research signal | BUY SETUP (the rule above on the last closed bar, flat); HOLD (a simulated position from the recent bars is open); EXIT / REDUCE EXPOSURE (its trailing or time exit triggered at the last close); NO TRADE otherwise, or on stale / missing data. The live state replays the backtest's own `step()` over the last 70 bars from a flat start. Strength = share of the 3 checks (heuristic, not a probability). |

**Clarifications needed to make the specification exact** (decided now, before any backtest):

- "Above the level" is strictly greater (close > level). The 20-bar volume average excludes the signal bar.
- ATR(14) is the existing Wilder ATR (`services/technical_analysis.atr`), the same as Trend and Range.
- A setup also needs the stop below the signal close. The entry is skipped if the next open (before slippage) is at or
  below the stop.
- "The lowest low of the last 10 bars" means the 10 bars **before** the closing bar (a close can never be below a low
  that includes its own bar). It is checked from the entry bar's close onwards and may include bars before the entry.
- If one bar both hits the stop and closes below the trailing low, the stop counts (it happened first, intrabar).
- "After 60 bars": when the position has been held for 60 bars (the entry bar is bar 1), exit at the next open.
- While a position is open, new signals are ignored. A signal on the bar where a position was stopped out may enter
  at the next open (still one position at a time).

| Setting | Value |
|---|---|
| `lookback` | `20` |
| `volume_mult` | `1.2` |
| `volume_window` | `20` |
| `stop_atr` | `1.0` |
| `trail_bars` | `10` |
| `time_limit_bars` | `60` |
| `risk_pct` | `1.0` |
| `fee_pct` | `0.1` |
| `slippage_pct` | `0.05` |

## 2. How it differs from Trend (and H2)

Breakout differs from Trend following (round 2) and from H2 (daily EMA 50 / 200):

- **Trend** buys a **pullback** to EMA 21 inside an established uptrend (EMA 21 > 50 > 200, EMA 50 rising, ADX ≥ 20,
  daily close above its EMA 50) and exits at a **fixed 2R target**, a trend break or 30 bars.
- **Breakout** has **no trend, ADX or higher-timeframe filter**: it buys a **close above the 20-bar high on volume**
  (new highs, not pullbacks), with **no fixed target** and a 10-bar trailing low as its exit.
- **H2** is 100% in or out on the daily EMA 50 / 200 cross; Breakout trades 4h structure with 1% risk per trade.

## 3. Diagnostics (reported only, never a filter): how many breakouts failed and how many retested the level

- **Failed breakout**: a trade with a 4h close back below the breakout level within its first **3 bars** (the entry bar
  is bar 1). The trade continues under its normal exits.
- **Retest**: a trade whose low came within **0.25 × ATR(14)** (of the signal bar) of the level within its first
  **10 bars**, without such a close first.

## 4. Design window

From 4h bar 300 of the stored design data (the same warm-up as Range, so Trend's EMA 200 is ready for the comparison;
the breakout rules themselves need 21 bars) to the last 4h bar that closed before 2025-10-29 00:00 UTC.

## 5. Pass criteria (the same as Trend and Range; all must hold; design data only)

| # | Criterion |
|---|---|
| — | **trades ≥ 30**, otherwise the verdict is **INCONCLUSIVE** (neither pass nor fail) |
| C1 | expectancy ≥ **+0.15R** per trade after fees and slippage (R from the initial stop) |
| C2 | profit factor ≥ **1.3** |
| C3 | max drawdown ≤ **15%** of the account |
| C4 | net return beats ≥ **90%** of 1,000 random-entry runs (seed 7): the same number of trades, each with a strategy trade's holding time as its maximum, entered at random non-overlapping bars where the same stop formula gives a stop below the open, with the same stop, sizing and costs |

**Compared (reported only, never a criterion)** over the same window and costs: buy & hold, the **H2** rule (its own
pre-registered definition), and **Trend** (its default rules). Walk-forward: consecutive 182-day windows with the
same fixed rules, reported. Design results never validate the strategy.

## 6. Sensitivity checks (reported only, never used to choose parameters)

lookback 20 / 40 / 55 bars × volume 1.0 / 1.2 / 1.5 × the average (9 runs, the default included). All other rules
unchanged.

## 7. Forward test

The separate strategy forward tracker (`services/strategy_forward.py`, its own fingerprint = `breakout.py` + the
tracker core, append-only) starts this configuration the next UTC midnight after the merged code first runs and
processes each new closed 4h bar once with the backtest's own `features()` and `step()`. After 365 days: F1 net
return > 0 and F2 expectancy ≥ +0.15R; fewer than 30 trades → INCONCLUSIVE. A settings change creates a new
version, NOT VALIDATED (settings changed), inheriting nothing.
