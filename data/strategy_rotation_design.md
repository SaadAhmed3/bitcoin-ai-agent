# Rotation design backtest (design-data evidence only)

Pre-registered in `PREREGISTRATION_ROTATION.md` (config hash `f3de8ea9c08bc780`), run once. Weekly decisions from 2021-06-28 (a Monday) to 2025-10-28 (1,584 days), all daily candles closed before the holdout start 2025-10-29. Simulated, spot, 100% in one asset or cash.

**Survivorship bias: SOL and BNB were picked knowing they survived (and grew); coins that faded are not in the list, so these results may look better than reality.**

## Verdict: PASS (design data, not validation)

| # | Rule | Value | BTC | Passed |
|---|---|---|---|---|
| C1 | return / max drawdown >= BTC buy & hold's (same window) | 53.49 | 2.93 | yes |
| C2 | max drawdown <= BTC buy & hold's | 59.70 | 76.63 | yes |
| C3 | beats >= 90% of 1,000 random weekly rotations among the same assets | 97.70 | n/a | yes |

## Results and comparisons (buy & hold of each asset reported only; N/A = not listed at the start)

| | Rotation | BTC buy & hold | ETH | SOL | BNB |
|---|---|---|---|---|---|
| Net return | +3193.17% | +224.36% | +100.14% | +507.12% | +278.20% |
| Max drawdown | 59.70% | 76.63% | 79.30% | 96.27% | 69.85% |

Switches 91 over 227 weeks; weeks in cash 30.0%, in BTC 11.0%, in an altcoin 59.0%; fees and slippage 242.42% of the starting account; return / max drawdown 53.49.

Random weekly rotations (1000 runs, cash in the same weeks, a random asset otherwise): the strategy beat 97.7% of them (median run +564.89%).

## Walk-forward windows (182 days, fixed rules, reported only)

| Window | Switches | Return |
|---|---|---|
| 2021-06-28 to 2021-12-26 | 12 | +311.44% |
| 2021-12-27 to 2022-06-26 | 7 | -12.46% |
| 2022-06-27 to 2022-12-25 | 12 | -1.47% |
| 2022-12-26 to 2023-06-25 | 11 | +41.55% |
| 2023-06-26 to 2023-12-24 | 5 | +452.23% |
| 2023-12-25 to 2024-06-23 | 15 | -27.41% |
| 2024-06-24 to 2024-12-22 | 10 | +4.23% |
| 2024-12-23 to 2025-06-22 | 14 | -17.27% |
| 2025-06-23 to 2025-10-28 | 5 | +89.63% |

## Sensitivity (reported only, never used to choose parameters)

| Lookback | Cash filter | Switches | Net return | Max DD | Return / DD |
|---|---|---|---|---|---|
| 14 days | on | 114 | +1091.65% | 66.70% | 16.37 |
| 14 days | off | 108 | +935.41% | 84.42% | 11.08 |
| 28 days (default) | on | 91 | +3193.17% | 59.70% | 53.49 |
| 28 days | off | 78 | +1005.06% | 83.36% | 12.06 |
| 56 days | on | 63 | +557.06% | 79.21% | 7.03 |
| 56 days | off | 54 | +456.65% | 84.11% | 5.43 |

## Limitations

- Design data only (before 2025-10-29): evidence about the past, not validation.
- Survivorship bias: SOL and BNB were picked knowing they survived (and grew); coins that faded are not in the list, so these results may look better than reality.
- Four assets, one price source (Binance USDT pairs, daily); fixed costs; trades at the Monday open.
- Spot only and long only: a weak market means cash, never a short.
