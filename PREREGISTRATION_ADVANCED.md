# Pre-registration: advanced analysis on design data (regime, liquidity sweeps, volume profile)

| | |
|---|---|
| Status | **PRE-REGISTERED** on the owner's instruction (2026-10-31), written and committed **before** the study runs. Weights, horizons, rules and outputs below are fixed; the study is run **once** and nothing is tuned or picked afterwards. |
| Test data | **DESIGN data only**: Binance BTCUSDT candles from the local database, decisions before the 2025-10-29 holdout start (the same design window as the app's design backtest). No "full" period, no holdout. |
| Live signal | Unchanged: engine **signal-1.8**, its score, weights, thresholds and NO TRADE gates. The three features run in **shadow mode** only. Whatever this study shows, nothing here can enter the score. |
| Label | Every number from this study is **"design-data evidence only"**. Real evidence needs forward data: the app stores the shadow outputs live from 2026-10-31 (table `shadow_context`). |

## 1. Shadow components (exactly as the app computes them, closed candles only)

| Component | At each closed 4h bar i (only data up to bar i) |
|---|---|
| regime | `regime_at(4h, i)` (500-candle window; 1h/1D candles closed by bar i). +confidence/100 in TRENDING BULLISH, -confidence/100 in TRENDING BEARISH, else 0 |
| liquidity_sweep | sweeps detected once over the design 4h series (detection is causal: tested), evaluated as of bar i: the latest CONFIRMED / RECLAIM CONFIRMED sweep confirmed in the last 6 bars, +strength/100 sell-side, -strength/100 buy-side, else 0 |
| volume_profile | `profile_at(4h, i)`: the previous 200 closed 4h candles, 50 bins, 70% value area. +0.5 above VAH, -0.5 below VAL, inside (close - POC) / (VAH - VAL) |

## 2. Part A: engine variants (filter only)

Combined score = (1 - sum of w) x engine score + sum(w x component). Weights, fixed now:

- A: the existing engine (no shadow component)
- B: regime 0.15
- C: liquidity_sweep 0.15
- D: volume_profile 0.15
- E: regime 0.10, liquidity_sweep 0.10, volume_profile 0.10

**Filter only.** A trade that variant A takes is kept only while the combined score is still >= the buy threshold
(0.25). A variant can remove trades, never add one: a new entry would need a plan (stop and target) that only the
engine makes, and no trade is ever forced. Simulation, costs (0.10% fee + 0.05% slippage per side), the time stop and
the 182-day walk-forward windows are the existing backtest code, unchanged.

Reported for each variant: trade count, win rate, net return after costs, maximum drawdown, profit factor, average
trade return, false-signal rate (share of trades with a net return <= 0), performance by the regime label at entry
(variant A's trades), and return / trades per walk-forward window.

Known weakness, stated in advance: variant A makes only about 5 trades on design data, far too few to tell variants
apart. Part B is added for that reason.

## 3. Part B: event study (fixed horizons +6 and +18 4h bars)

- Events: every design 4h bar by its regime label (six groups), and every CONFIRMED sweep at its confirmation bar, by
  direction (buy-side, sell-side).
- Forward return at +6 and +18 bars (1 and 3 days): close of bar i + h / close of bar i - 1, before costs. A bar
  whose +h bar closes at or after the holdout start is dropped (asserted).
- Compared with the unconditional average over all design bars with a forward return.
- For each group: count, mean, the difference vs the unconditional average, and a 95% CI of the group mean from a
  moving-block bootstrap over the time axis of all design bars: each resample redraws blocks of `horizon`
  consecutive bars (group membership travels with each bar) and takes the group mean over the group's bars inside it;
  resamples without one are skipped (2,000 resamples, seed 7, 2.5 / 97.5 percentiles). Neighbouring forward returns
  overlap, so blocks of bars are resampled instead of single bars. No CI for a group with fewer than 2 events.

## 4. How the result may be used

- Not to change the live signal, its weights, thresholds or gates. The strategy stays **NO TRADE** unless validated
  on unseen data.
- No claim of improvement from design data. A difference whose CI excludes the unconditional average is at most a
  candidate for a separate, pre-registered **forward** test.
- Outputs: `data/research_advanced_design.json` and `data/research_advanced_design.md`, written by
  `scripts/research_advanced.py` (run once).
