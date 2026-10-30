# Backtest Validation Report

v0.9.1 / signal-1.7, 2026-10-30. This report audits the **existing** backtest engine and the **existing** stored
result (run #1). No new backtest was run and **the holdout period (from 2025-10-29) was not touched.**

## 1. Engine audit (`services/backtesting.py`)

| Check | Result | Evidence |
|---|---|---|
| Look-ahead in the replay | **Pass** | Window `c4h.iloc[i-499 : i+1]` ends at the decision bar; 1h and 1d are cut with `bisect_right(close_times, t)`, so only candles closed by `t` are visible |
| Look-ahead in indicators | **Pass** | Every indicator is causal (EWM, rolling, Wilder); swings need 3 later bars and are therefore old by construction |
| Candle timestamps | **Pass** | Binance `close_time` = open + interval − 1 ms; 4h and 1d closes coincide at day end and are included correctly |
| Entry price | **Pass** | Next bar's open × (1 + fee + slippage); skipped if the open already gapped past the stop or target |
| Exit price | **Pass, mildly optimistic** | Stop touched → filled at the stop price (minus 0.05% slippage and the fee). Real stop fills in fast markets are worse. |
| Gap through stop | **Pass** | Open ≤ stop → fill at the open |
| Same-candle stop/target | **Pass (pessimistic)** | Stop assumed first |
| Time stop / SELL exit | **Pass** | Decided at the close of bar *b*, filled at that close (24/7 market: ≈ next open) |
| Fees and slippage | **Pass** | 0.10% + 0.05% per side, applied multiplicatively on both sides |
| Overlapping / duplicate trades | **Pass** | One position at a time; new signals ignored while in a trade |
| Survivorship bias | n/a | Single asset (BTC) |
| Data leakage across walk-forward windows | **Pass** | Default weights are fixed; alternative weights are chosen on the previous 365 days only |
| Annualisation | **Pass** | 2,190 4h bars per year (24/7) for CAGR and Sharpe |
| Max drawdown | **Pass** | Mark-to-market at each close, including the exit cost |
| Profit factor | Acceptable | Sum of percentage returns (not compounded USD); fine as a ratio |
| Validation scope | **Fail (B3)** | A *full* run (mostly design data) can validate |
| Holdout single use | **Fail (B4)** | Not enforced |
| Sizing realism | **Gap** | 100% of equity per trade; the live risk model uses 1% risk and a 50% cap |
| Small-sample metrics | **Gap (B14)** | Sharpe and PF are shown for 6 trades without a warning |

**Verdict:** the simulation mechanics are correct and conservative where it matters (no look-ahead, stop-first,
next-open fills). The weaknesses are in the **protocol** (what is allowed to validate) and in **reporting**
(missing metrics, no uncertainty estimates).

## 2. Stored result — run #1 (full period, 4 years, standard entry, decisions 2022-10-30 15:59 → 2026-10-29 11:59 UTC)

README and CHANGELOG quote an earlier "run #3" with B&H +281.87%; it is from a database that is no longer present.
The difference is explained by a different window end date: see QUANTITATIVE_AUDIT.md §7.

| | Strategy | Buy & hold | EMA 50/200 | Random entries (200 runs) |
|---|---|---|---|---|
| Net return | +7.52% | +294.32% | +244.87% | median −1.55% (p10 −12.56%, p90 +10.27%) |
| CAGR | 1.83% | 40.92% | — | — |
| Max drawdown | 3.45% | 53.45% | 30.60% | — |
| Return / max DD | 2.18 | 5.51 | 8.00 | — |
| Trades | **6** | 1 | 23 | 6 each |
| Exposure | 1.0% | 100% | 56% | — |
| Win rate | 66.7% (4/6) | | | |
| Profit factor | 3.89 | | | |
| Avg trade | +1.24% | | | |
| Exit reasons | target 2, time stop 3, stop 1 | | | |

Validation criteria: 4 of 7 passed. Failed: enough trades (6 < 30), beats random (86th percentile < 90th), beats
EMA 50/200 on return/DD (2.18 < 8.00). Verdict stored: **NOT VALIDATED**. That verdict is correct.

Walk-forward: 8 windows of 182 days; trades per window 0–2; 3 of the 5 windows with trades were positive. Buy & hold
was positive in 7 of 8 windows.

**Interpretation.** Six trades cannot establish anything. A profit factor of 3.89 from 4 wins and 2 losses has an
enormous confidence interval. The strategy is in the market 1% of the time; its low drawdown comes from being in cash,
not from skill. Per the project rules, **no profitability claim can be made.**

> **Holdout contamination (2026-10-31).** Backtest run #1 (full period, decisions 2022-10-30 → 2026-10-29) computed and **displayed** buy & hold (+294.32%) and the EMA 50/200 crossover (+244.87%) **through the holdout year**, and the audit documents quoted those figures (and run #3's +281.87% / +241.99% from an earlier database). Anyone who has seen them knows roughly how holding and simple trend-following did in the holdout year. **The 2025-10-29 holdout is therefore contaminated for trend-following hypotheses and is not used for H1 or H2.** They are tested on **forward** data only (Research → Forward tests: decisions on daily candles that close after the tracker starts). The app's holdout registry still shows the period as untouched (no holdout run was made); whether it is still clean for the live engine, whose largest component is trend, is an open question for the owner.

## 3. Other existing evidence (design period only)

- Score study: score vs 18-bar forward return Spearman −0.018 (95% CI −0.093 … +0.051). See QUANTITATIVE_AUDIT §4.
- Entry-rule study (Phase 6.2, pre-registered): breakout PF 0.67; pullback 83 trades, −15.9% after costs. Neither
  earned a holdout test. That decision was correct.

## 4. Missing parts of a responsible validation process (Phases 8–9)

| Requirement | Status | Recommendation |
|---|---|---|
| Separate development / validation data | Partly | Design/holdout split exists; make *holdout* the only validating period (B3) |
| Preserve the holdout | Not enforced | Write-once holdout ledger per config hash (B4) |
| Walk-forward | Present | Keep; report per-window trade counts prominently |
| Regime tests (bull / bear / sideways, high / low volatility) | Missing | Label each bar with a regime known at decision time (e.g. 1d close vs SMA200 and the 200-day return; ATR percentile) and report metrics per regime. The labels must use only past data. |
| Parameter sensitivity | Missing | Design period only: ±20% on thresholds, `min_rr`, ATR multiples and the time stop, as a heat-map of net return and trade count. Report it; do **not** pick the best cell. |
| Bootstrap uncertainty | Missing in the backtest (present in the score study) | Trade-level bootstrap CI for mean trade return and PF; block bootstrap on the bar-level equity for Sharpe |
| Cost stress | Missing | Re-run with 2× slippage and an extra 0.25 ATR stop slippage |
| Additional metrics | Missing | Sortino, expectancy (R), average win / average loss, max consecutive losses, average holding time, exposure-adjusted return |
| Exposure-matched baseline | Missing | B&H scaled to the strategy's exposure, so a 1%-exposure strategy is not compared with a 100%-exposure one on raw return alone |

## 5. Rules for any future validation (proposed, to be documented before use)

1. Rules and parameters are frozen and written down (with a config hash) **before** the holdout run.
2. A holdout run is allowed once per config hash; its result is stored even if it is bad.
3. Only a holdout pass followed by the existing 56-day forward observation can lift the validation gate.
4. Minimum 15 holdout trades (existing pre-registered value) — and the report shows the bootstrap CI next to every
   ratio.
5. Historical performance never implies future returns. Every result page keeps this statement.
