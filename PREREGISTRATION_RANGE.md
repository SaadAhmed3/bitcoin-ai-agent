# Pre-registration: Strategy Lab, strategy 3 — Range trading (4h, long only)

| | |
|---|---|
| Status | **PRE-REGISTERED** (2026-11-01) on the owner's specification, committed **before** the first backtest. Rules, defaults, criteria and sensitivity checks below are fixed; the design backtest runs **once** (`scripts/research_strategy.py range`, refused a second time). Nothing is tuned to its result. |
| Config hash: `3abb2ee3b4292819` | `services.strategies.base.config_hash(range.DEFAULTS)`. The backtest script refuses to run if the defaults no longer produce this hash. |
| Test data | **Design data only**: Binance BTCUSDT 4h candles closed before 2025-10-29 00:00 UTC (the contaminated holdout is never used). No "full" period. |
| Live signal | Unchanged: signal-1.8 does not read this strategy. Simulated only, spot long only. |
| Dependency | The regime label comes from `services/regime.py`, pinned by SHA-256 `c70265f88c17281ab588508874fdf47dda9304f4700eb3debf22c54bb8559bcd` in `range.py`; if that file changes, results and the forward test are flagged "dependency changed". |

## 1. Rules (fixed)

Decided at the **close** of each 4h bar i, closed candles only.

| Part | Rule |
|---|---|
| Regime | the existing 4h market regime (`regime_at`, each bar from its own past) is **SIDEWAYS** / RANGING, and **ADX(14) < 20** |
| Range | highest high / lowest low of the last **50 closed 4h bars** before bar i (bars i−50 … i−1) |
| Valid range | each boundary has **>= 2 touches** and the width (high − low) is **≥ 3 × ATR(14)** |
| Entry | (a) bar i traded below the range low (its low below it) and closed back above it, **or** (b) bar i−1 closed in the **bottom 20%** of the range and bar i closed bullish (close > open). BUY SETUP → buy at the next 4h open |
| Stop | **range low − 0.5 × ATR(14)** (ATR of the signal bar) |
| Target | the range **midpoint**; the range high is reported as a sensitivity only |
| Exits | stop (intrabar; a gap below it exits at the open); target; if one bar touches both, the **stop** counts; the regime no longer SIDEWAYS at a 4h close → exit at the next open; after **30 × 4h bars** → exit at the next open; a trade open at the end of the design data is closed at the last close |
| Size and costs | planned loss to the stop = 1% of the account (ceiling 2%), never more than 100% of the account, one position at a time; 0.10% fee + 0.05% slippage per side; marked at liquidation value |
| Live research signal | BUY SETUP (rules met); HOLD (valid range, no trigger); EXIT / REDUCE EXPOSURE (the regime was SIDEWAYS at the previous close and is not now); NO TRADE otherwise, or on stale / missing data or a changed `regime.py`. Strength = share of the 5 range checks (heuristic, not a probability). |

**Clarifications needed to make the specification exact** (decided now, before any backtest):

- A **touch** of the range high (low) is a bar in the 50-bar window whose high (low) is within **0.25 × ATR(14)** of
  it; touching bars next to each other count as **one** touch.
- The range uses the bars **before** the signal bar, so a dip below the range low in bar i can be measured.
- A setup also needs the close **below the midpoint** (otherwise the target is not above the entry) and the stop
  below the close; the trade is skipped if the next open is at or beyond the stop or the target.
- The regime module's SIDEWAYS label already requires ADX < 20 (its own rule); the ADX < 20 condition is kept as
  specified, so ADX 25 in the sensitivity grid cannot add trades.

| Setting | Value |
|---|---|
| `lookback` | `50` |
| `adx_max` | `20.0` |
| `touch_atr` | `0.25` |
| `min_touches` | `2` |
| `min_width_atr` | `3.0` |
| `bottom_zone` | `0.2` |
| `stop_atr` | `0.5` |
| `target` | `mid` |
| `time_limit_bars` | `30` |
| `risk_pct` | `1.0` |
| `fee_pct` | `0.1` |
| `slippage_pct` | `0.05` |

## 2. Design window

From 4h bar 300 of the stored design data (warm-up: the regime needs 220 bars, the range up to 80) to the last 4h bar
that closed before 2025-10-29 00:00 UTC.

## 3. Pass criteria (the Trend criteria; all must hold; design data only)

| # | Criterion |
|---|---|
| — | **trades ≥ 30**, otherwise the verdict is **INCONCLUSIVE** (neither pass nor fail) |
| C1 | expectancy ≥ **+0.15R** per trade after fees and slippage (R = net P&L / planned risk to the stop) |
| C2 | profit factor ≥ **1.3** |
| C3 | max drawdown ≤ **15%** of the account |
| C4 | net return beats ≥ **90%** of 1,000 random-entry runs (seed 7): the same number of trades, each with a strategy trade's holding time as its maximum, entered at random non-overlapping bars where the same formulas give stop < open < target, with the same stop, target, sizing and costs |

Buy & hold (same window, same costs) is reported for comparison, not as a criterion. Walk-forward: consecutive
182-day windows with the same fixed rules, reported. Design results never validate the strategy.

## 4. Sensitivity checks (reported only, never used to choose parameters)

lookback 30 / 50 / 80 bars × ADX 15 / 20 / 25 (9 runs, the default included), plus the default rules with the
**range high** as the target. All other rules unchanged.

## 5. Forward test

The separate strategy forward tracker starts this configuration the next UTC midnight after the merged code first
runs and processes each new closed 4h bar once with the backtest's own `features()` and `step()`. After 365 days:
F1 net return > 0 and F2 expectancy ≥ +0.15R; fewer than 30 trades → INCONCLUSIVE. A settings change creates a new
version, NOT VALIDATED (settings changed), inheriting nothing.
