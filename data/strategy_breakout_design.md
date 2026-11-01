# Breakout trading design backtest (design-data evidence only)

Pre-registered in `PREREGISTRATION_BREAKOUT.md` (config hash `b4aa0a6c76185000`), run once. 4h decisions 2021-06-20 to 2025-10-28 (9,550 bars), all closed before the holdout start 2025-10-29. Simulated, long only, 1% planned risk per trade, no fixed target.

## Verdict: FAIL (design data, not validation)

| # | Rule | Value | Passed |
|---|---|---|---|
| C1 | expectancy >= +0.15R per trade after fees and slippage | 0.38 | yes |
| C2 | profit factor >= 1.3 | 1.36 | yes |
| C3 | max drawdown <= 15% of the account | 23.71 | no |
| C4 | beats >= 90% of 1,000 random-entry runs (same trades, holding times, stop rule) | 100.00 | yes |

## Results and comparisons (comparisons reported only)

| | Breakout | Buy & hold | H2 rule | Trend (same window) |
|---|---|---|---|---|
| Net return | +51.08% | +215.85% | +230.17% | -7.82% |
| Max drawdown | 23.71% | 77.04% | 38.24% | 9.57% |
| Trades | 150 | 1 | 3 entries | 94 |

Win rate 26.7%, profit factor 1.36, expectancy 0.38R (average win 4.13R), average trade +0.33%, exposure 29.0%, average hold 18.4 bars, fees 19.62% and slippage 9.81% of the account. Exits: stop 85, trailing exit 54, time limit 10, end of design data 1.

Diagnostics (not filters): 45 of 150 trades closed back below the level within 3 bars (failed breakouts); 85 retested the level within 10 bars without such a close.

Random entries (1000 runs): the strategy beat 100.0% of them (median random run -31.09%).

## Walk-forward windows (182 days, fixed rules, reported only)

| Window | Trades | Return | Sum of R |
|---|---|---|---|
| 2021-06-20 to 2021-12-19 | 18 | +0.09% | +0.49 |
| 2021-12-19 to 2022-06-19 | 13 | -3.10% | -3.06 |
| 2022-06-19 to 2022-12-18 | 15 | -6.85% | -7.05 |
| 2022-12-18 to 2023-06-18 | 15 | +24.09% | +32.41 |
| 2023-06-19 to 2023-12-18 | 20 | +35.40% | +33.04 |
| 2023-12-18 to 2024-06-17 | 16 | +9.89% | +10.58 |
| 2024-06-17 to 2024-12-16 | 21 | -2.40% | -2.61 |
| 2024-12-16 to 2025-06-16 | 18 | -6.49% | -6.08 |
| 2025-06-16 to 2025-10-28 | 14 | -0.76% | -0.65 |

## Sensitivity (reported only, never used to choose parameters)

| Lookback | Volume | Trades | Expectancy | Profit factor | Max DD | Net return |
|---|---|---|---|---|---|---|
| 20 | 1x | 154 | 0.39R | 1.38 | 22.82% | +55.46% |
| 20 (default) | 1.2x | 150 | 0.38R | 1.36 | 23.71% | +51.08% |
| 20 | 1.5x | 130 | 0.54R | 1.47 | 19.20% | +58.66% |
| 40 | 1x | 103 | 0.58R | 1.79 | 11.20% | +71.08% |
| 40 | 1.2x | 100 | 0.61R | 1.83 | 9.84% | +74.23% |
| 40 | 1.5x | 93 | 0.68R | 1.84 | 9.59% | +63.18% |
| 55 | 1x | 92 | 0.32R | 1.44 | 9.07% | +29.65% |
| 55 | 1.2x | 89 | 0.36R | 1.52 | 8.48% | +33.49% |
| 55 | 1.5x | 85 | 0.52R | 1.60 | 8.92% | +37.74% |

## Limitations

- Design data only (before 2025-10-29): evidence about the past, not validation.
- One asset and one price source (Binance BTCUSDT 4h); fixed costs; fills at the next open.
- Long only: a breakdown below support gives NO TRADE, not a short.
- No fixed target: R/R is unknown at entry; R is measured from the initial stop.
