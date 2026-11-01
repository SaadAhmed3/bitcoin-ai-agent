# DCA design backtest (design-data evidence only)

Pre-registered in `PREREGISTRATION_DCA.md` (config hash `f626cffd73fb1e10`), run once. Daily candles 2021-05-02 to 2025-10-28 (1641 days), all closed before the holdout start 2025-10-29. Simulated only. $100 every Monday at the 00:00 UTC open.

## Whole design period

| | DCA | Lump sum (same budget, day 1) |
|---|---|---|
| Budget | $23,500 | $23,500 |
| Return on the budget | +187.93% | +94.75% |
| Max drawdown | 26.59% | 76.63% |
| BTC bought | 0.600232 (235 buys) | 0.405983 |
| Average price paid (incl. costs) | $39,151.51 | |
| Fees / slippage | $23.50 / $11.73 | $23.50 / $11.73 |
| Unrealized / realized P&L | $44,163.65 / $0.00 (never sells) | |

Missed buys (no stored candle): 0.

## Pre-registered criteria: PASS (design data, not validation)

| # | Rule | DCA | Lump sum | Passed |
|---|---|---|---|---|
| C1 | median max drawdown over rolling 365-day windows: DCA < lump sum | 19.23% | 28.10% | yes |
| C2 | 5th-percentile 365-day return (worst case): DCA > lump sum | -43.02% | -65.30% | yes |

183 rolling 365-day windows (7-day steps, overlapping): mean return DCA +23.90% vs lump sum +50.25%; DCA beat lump sum in 29.0% of windows.

## Calendar years

| Year | Days | DCA return | DCA max DD | Lump return | Lump max DD |
|---|---|---|---|---|---|
| 2021 | 244 | +1.56% | 29.06% | -20.28% | 49.39% |
| 2022 | 365 | -34.10% | 39.02% | -64.31% | 66.93% |
| 2023 | 365 | +53.42% | 13.41% | +154.85% | 20.00% |
| 2024 | 366 | +49.19% | 14.92% | +120.64% | 26.15% |
| 2025 | 301 | +10.51% | 13.93% | +20.29% | 28.10% |

## Limitations

- Design data only (before 2025-10-29): evidence about the past, not validation.
- One asset, one price source (Binance BTCUSDT daily); fills at the daily open with fixed costs.
- Rolling windows overlap heavily (7-day steps), so they are not independent samples.
- Win rate and profit factor do not apply: DCA never sells.
- Walk-forward: not applicable: DCA has no fitted parameters.
