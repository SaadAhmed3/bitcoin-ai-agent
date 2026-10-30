# Quantitative Audit

v0.9.1 / signal-1.7, 2026-10-30. Evidence used: the code, the local Binance history (1h 48,191 / 4h 12,049 /
1d 2,007 candles, 2021-05-01 → 2026-10-29), the stored backtest run #1 and `data/score_study_design.*`.
**No holdout data was used in any new calculation in this audit.**

## 1. Indicator verification (Phase 3)

All indicators are computed on **closed candles only**: `validate_candles` drops any candle with
`close_time >= now`, and the backtest slices windows that end at the decision bar.

| Indicator | Formula check | Look-ahead | Notes / limitations |
|---|---|---|---|
| EMA 9/21/50/200 | `ewm(span=n, adjust=False)`, masked until n bars | None | Seeded with the first close, not an SMA. On the 500-candle window, EMA200 differs from a full-history EMA200 by median 0.014% / max 0.134% (4h) and 0.068% / 0.275% (1d), measured over the last 400 candles. This matters only when price sits right on the EMA. Low priority; an SMA seed would match TradingView. |
| SMA 50/200 | rolling mean | None | Correct. |
| RSI 14 | Wilder RMA, SMA seed; flat → 50, only gains → 100 | None | Matches full-history RSI exactly (max diff 0.0000). |
| MACD 12/26/9 | EMA difference, EMA signal | None | Correct. The engine scales the histogram by ATR, which makes it comparable across price levels. |
| Bollinger 20/2 | SMA ± 2 × population σ | None | Correct (ddof=0 is the convention). |
| ATR 14 | Wilder RMA of true range | None | Correct. |
| ADX/DI 14 | Wilder DM/TR, DX, RMA | None | Correct; matches full history exactly. |
| VWAP | Anchored to each UTC day | None | Meaningful on ≤1h only; on 4h it averages 1–6 candles. |
| Swings / structure | Fractal k=3; needs 3 later bars | None (confirmed swings are ≥3 bars old) | Structure lags by 3 bars by design. |
| S/R | Cluster swings within 0.5 ATR, max width 1 ATR, last 200 bars | None | Heuristic; touch counts are not tested as predictive. |
| Breakout | Close beyond the prior 20-bar range (current bar excluded) | None | Correct. |
| Divergence | Regular RSI divergence on the last two swings | None | Labelled experimental; correct. |
| Volatility regime | ATR% percentile within the last 200 bars | None | The current bar is included in its own percentile (standard). |

**Predictive value per component** (design period, Spearman with the 18-bar forward return, from the score study):

| Component | All bars | Non-overlapping |
|---|---|---|
| trend | −0.052 | −0.030 |
| momentum | +0.020 | +0.070 |
| volume | +0.018 | +0.143 |
| structure | +0.004 | +0.040 |
| overextension | −0.036 | −0.071 |
| **score** | **−0.018** | **+0.034** |

Non-overlapping samples are small (~360), so even ±0.1 is within noise. **No component has demonstrated
predictive value.** The trend component, which has the largest weight, is slightly negative on this horizon.

## 2. Multi-timeframe design (Phase 4)

- Trend = 50% 1d + 50% 4h; momentum = 60% 4h + 40% 1h; overextension = 4h + 1h. 15m and 5m are not used.
- **Independence:** 1h and 4h RSI/MACD on overlapping windows are strongly correlated by construction (the 4h candle
  contains the last four 1h candles). The 1d and 4h trends share their EMA200 information. The components are not
  independent pieces of evidence, so a weighted sum overstates "agreement".
- **Conflicts:** the engine detects a counter-trend setup against 1d (gate) and contradictory evidence (agreement
  < 60%), but it does not explain *why* timeframes disagree (e.g. "1d uptrend, 4h pullback: momentum negative while
  trend positive"). Recommended: an explicit alignment table (trend sign, momentum sign and volatility regime per
  timeframe, plus a one-line classification: aligned / pullback-in-trend / counter-trend bounce / conflict).

## 3. News (Phase 5)

- The engine is careful: headline-only, context-bound rules, negation, BTC relevance, clustering across publishers,
  verification levels (confirmed / unverified / speculative), freshness and "likely priced in".
- **Predictive value is untested and cannot be tested yet**: there is no historical headline archive, so news is
  absent from every backtest (B8). `data/news_review.csv` is a labelling review, not a predictive test.
- Recommendation: from now on, store every analysed event with its scores at collection time. That is an honest
  point-in-time archive. After ~6–12 months it allows a forward test of incremental value (does the news bias add
  to a model without news?). Until then, news should only *block* (fresh-news gate), not *score*.

## 4. Score and thresholds (Phase 6)

From the design-period score study (6,550 bars, 2022-10-30 → 2025-10-25):

| Score bucket | Bars | Mean fwd % | % beat costs |
|---|---|---|---|
| ≤ −0.25 (SELL) | 612 | **1.07** | 55.9 |
| (−0.25, −0.10] | 1,146 | 0.43 | 51.9 |
| (−0.10, 0.10) | 1,647 | 0.31 | 47.2 |
| [0.10, 0.25) | 1,569 | 0.62 | 50.6 |
| [0.25, 0.40) (BUY) | 1,209 | 0.52 | 45.9 |
| ≥ 0.40 | 367 | 1.21 | 53.4 |
| all bars | 6,550 | 0.56 | 49.8 |

- The SELL bucket had the second-highest forward return. In a rising market this is consistent with mean reversion
  after weakness, and it means the SELL label is **not supported** as a timing signal.
- The ≥ 0.40 bucket is higher, but its 95% block-bootstrap CI against the all-bar mean is [−0.25, +1.47] pp:
  not distinguishable from zero.
- **Conclusion:** neither the weights nor the thresholds have demonstrated predictive value. Per the project rules
  they are **not changed**. Changing them on the same design data would be curve fitting. Any change must be
  pre-registered, tested on design data, and then tested **once** on the holdout.

## 5. Reward/risk structure

Backtest run #1 funnel (4 years, 8,760 decision bars):

- 1,776 bars with score ≥ +0.25 (raw BUY)
- **1,755 (98.8%) downgraded for R/R < 1.5**; median R/R 0.46, p75 0.69; 11% had no room at all
- 21 left; 9 blocked by safeguards (extreme volatility 9, counter-trend 3); 9 tradable bars → **6 trades**

The cause is structural: the stop is at least 1 ATR below the entry, while target 1 is the lower edge of the nearest
**2-touch** resistance, which in a 200-bar lookback is usually within ~1 ATR. The R/R gate therefore removes almost
every setup. This is a design property, not a bug, and the honest outcome is NO TRADE. Lowering `min_rr` to create
trades is **not** recommended. The pre-registered alternatives (breakout and pullback entries) were already tested on
design data and failed (breakout PF 0.67; pullback 83 trades, −15.9%).

## 6. Backtest metrics and comparisons

See BACKTEST_VALIDATION_REPORT.md. Key quantitative issues:

- Strategy exposure 1.0% vs EMA 50/200 at 56%: comparing return/max-drawdown across such different exposures
  favours the low-exposure strategy's ratio while hiding that it earns almost nothing (CAGR 1.83% vs 40.9% B&H).
  Add a cash-adjusted comparison (strategy vs B&H scaled to the same exposure).
- The random baseline uses the same trade count and holding times, which is good. With 6 trades its distribution is
  wide (p10 −12.6%, p90 +10.3%), and the strategy's 86th percentile is not significant.
- Missing: Sortino, expectancy (R), average win/loss, max consecutive losses, per-regime results, bootstrap CIs,
  parameter sensitivity.

## 7. Reconciliation: buy & hold +294.32% (this audit) vs +281.87% (README / CHANGELOG "run #3")

| | Run #1 (used in this audit) | Run #3 (cited in README §"Known results" and CHANGELOG 0.6.x) |
|---|---|---|
| Database | `data/btc_agent.sqlite3` (the only database in the repo folder; there are no `data/*.db` files) | An earlier database that is **no longer present**: the current file's run IDs start at #1 |
| Created | 2026-10-29 19:56:34 UTC | Before that (exact time unknown) |
| Settings | full period, 4 years, standard entry, signal-1.7, config hash `76ac44146b5ad053` | full period, 4 years, standard entry (per README) |
| Decision window | 2022-10-30 15:59 → 2026-10-29 11:59 UTC (8,760 4h bars) | Not recorded in the docs |
| Strategy | 6 trades, +7.52%, max DD 3.45% | 6 trades, +7.52%, max DD 3.45% (identical) |
| Buy & hold | **+294.32%** | **+281.87%** |
| EMA 50/200 | +244.87% | +241.99% |
| Random-entry percentile | 86th | 79th |

**Explanation.** The strategy figures are identical, so both runs contain the same 6 trades. Only the baselines
differ, and they depend on the exact start and end bar of the window. Recomputing B&H and EMA 50/200 with the
backtest's own functions on the current candles, for every 4-year window that contains the last trade
(scratch script `bh_reconcile2.py`), the closest windows end on 2026-10-28/29: B&H 282.6–283.3% and EMA 242.0–242.7%.
That is within about 1.5 points of run #3, compared with 12.5 points for run #1. No window matches both numbers
exactly; the old database (and possibly its last candles) is not available, so an exact match cannot be shown.

**Conclusion.** Both figures are genuine outputs of the same configuration over 4-year windows ending at different
times. The two end prices differ by about 3% (3.943 / 3.826 = 1.031 in equity terms), and on a +280% return a 3% change
in the end price moves the return by about 3.8 × 3% ≈ 11–12 points. This is not a calculation error. The random-entry percentile is not stable
either (79th vs 86th) because it depends on the window span; that is one more reason the 6-trade result cannot validate.

**Follow-up.** README and CHANGELOG cite run #3, which no longer exists in the database, while the stored validation
record points at run #1. The docs should quote the run ID together with its window dates, or quote run #1. Batch 1
updates the CHANGELOG only; the README fix is planned for batch 2 with the User Guide.

## 8. Overall assessment

The project's engineering of *honesty* (no look-ahead, NO TRADE by default, labels) is strong. Its *edge* is
unproven: the available evidence says the composite score is not predictive at the 3-day horizon. The highest-value
next step is not adding indicators. It is (a) closing the validation loopholes, and (b) testing a few **simple,
pre-registered** strategies against the baselines on the design period, keeping the holdout untouched until one
earns a single test (Roadmap P2).
