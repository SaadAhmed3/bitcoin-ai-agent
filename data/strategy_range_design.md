# Range trading design backtest (design-data evidence only)

Pre-registered in `PREREGISTRATION_RANGE.md` (config hash `3abb2ee3b4292819`), run once. 4h decisions 2021-06-20 to 2025-10-28 (9,550 bars), all closed before the holdout start 2025-10-29. Simulated, long only, 1% planned risk per trade. Regime SIDEWAYS on 15.3% of the bars; a valid range (all filters) on 1.6%.

**Only 4 trades (fewer than 30): too few to judge; the verdict is INCONCLUSIVE.**

## Verdict: INCONCLUSIVE (design data, not validation)

| # | Rule | Value | Passed |
|---|---|---|---|
| C1 | expectancy >= +0.15R per trade after fees and slippage | 0.47 | yes |
| C2 | profit factor >= 1.3 | 16.35 | yes |
| C3 | max drawdown <= 15% of the account | 0.55 | yes |
| C4 | beats >= 90% of 1,000 random-entry runs (same trades, holding times, stop/target rules) | 97.60 | yes |

## Results

| | Range | Buy & hold |
|---|---|---|
| Net return | +1.88% | +215.85% |
| Max drawdown | 0.55% | 77.04% |
| Trades | 4 | 1 |

Win rate 75.0%, profit factor 16.35, expectancy 0.47R, average planned reward/risk 0.99, average trade +0.47%, exposure 0.2%, average hold 5.8 bars, fees 0.33% and slippage 0.16% of the account. Exits: stop 0, target 1, regime left 3, time limit 0, end of design data 0.

Random entries (1000 runs): the strategy beat 97.6% of them (median random run -0.39%).

## Walk-forward windows (182 days, fixed rules, reported only)

| Window | Trades | Return | Sum of R |
|---|---|---|---|
| 2021-06-20 to 2021-12-19 | 1 | +0.68% | +0.68 |
| 2021-12-19 to 2022-06-19 | 0 | +0.00% | +0.00 |
| 2022-06-19 to 2022-12-18 | 0 | +0.00% | +0.00 |
| 2022-12-18 to 2023-06-18 | 2 | +0.56% | +0.56 |
| 2023-06-19 to 2023-12-18 | 0 | +0.00% | +0.00 |
| 2023-12-18 to 2024-06-17 | 0 | +0.00% | +0.00 |
| 2024-06-17 to 2024-12-16 | 0 | +0.00% | +0.00 |
| 2024-12-16 to 2025-06-16 | 1 | +0.62% | +0.62 |
| 2025-06-16 to 2025-10-28 | 0 | +0.00% | +0.00 |

## Sensitivity (reported only, never used to choose parameters)

| Lookback | ADX max | Target | Trades | Expectancy | Profit factor | Max DD | Net return |
|---|---|---|---|---|---|---|---|
| 30 | 15 | mid | 0 | n/aR | n/a | 0.00% | +0.00% |
| 30 | 20 | mid | 7 | -0.00R | 0.99 | 1.24% | -0.02% |
| 30 | 25 | mid | 7 | -0.00R | 0.99 | 1.24% | -0.02% |
| 50 | 15 | mid | 1 | 0.68R | n/a | 0.43% | +0.68% |
| 50 (default) | 20 | mid | 4 | 0.47R | 16.35 | 0.55% | +1.88% |
| 50 | 25 | mid | 4 | 0.47R | 16.35 | 0.55% | +1.88% |
| 80 | 15 | mid | 2 | -0.49R | 0.09 | 2.56% | -0.97% |
| 80 | 20 | mid | 4 | -0.31R | 0.14 | 2.56% | -1.23% |
| 80 | 25 | mid | 4 | -0.31R | 0.14 | 2.56% | -1.23% |
| 50 | 20 | top | 4 | 0.83R | 28.07 | 0.55% | +3.36% |

## Limitations

- Design data only (before 2025-10-29): evidence about the past, not validation.
- One asset and one price source (Binance BTCUSDT 4h); fixed costs; fills at the next open.
- The regime label comes from services/regime.py (pinned by SHA-256); its own thresholds are part of these rules.
- Long only: only the bottom of a range is bought; no short at the top.
