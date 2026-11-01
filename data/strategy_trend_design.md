# Trend following design backtest (design-data evidence only)

Pre-registered in `PREREGISTRATION_TREND.md` (config hash `27ce4fee913591af`), run once. 4h decisions 2021-06-20 to 2025-10-28 (9,547 bars), all closed before the holdout start 2025-10-29. Simulated, long only, 1% planned risk per trade.

## Verdict: FAIL (design data, not validation)

| # | Rule | Value | Passed |
|---|---|---|---|
| C1 | expectancy >= +0.15R per trade after fees and slippage | -0.08 | no |
| C2 | profit factor >= 1.3 | 0.82 | no |
| C3 | max drawdown <= 15% of the account | 9.57 | yes |
| C4 | beats >= 90% of 1,000 random-entry runs (same trades, holding times, stop/target rules) | 83.00 | no |

## Results

| | Trend | Buy & hold | H2 rule (daily EMA 50/200) |
|---|---|---|---|
| Net return | -7.82% | +217.05% | +230.17% |
| Max drawdown | 9.57% | 77.04% | 38.24% |
| Trades | 94 | 1 | 3 entries |

Win rate 27.7%, profit factor 0.82, expectancy -0.08R, average trade -0.08%, average win +1.42%, average loss -0.65%, exposure 12.1%, average hold 12.3 bars, fees 7.14% and slippage 3.57% of the account. Exits: stop 19, target 16, trend break 48, time limit 11, end of design data 0.

Random entries (1000 runs): the strategy beat 83.0% of them (median random run -14.96%).

## Walk-forward windows (182 days, fixed rules, reported only)

| Window | Trades | Return | Sum of R |
|---|---|---|---|
| 2021-06-20 to 2021-12-19 | 11 | -1.69% | -1.64 |
| 2021-12-20 to 2022-06-20 | 5 | -1.81% | -1.79 |
| 2022-06-20 to 2022-12-19 | 5 | -2.17% | -2.19 |
| 2022-12-19 to 2023-06-19 | 10 | -1.18% | -1.13 |
| 2023-06-19 to 2023-12-18 | 12 | +1.94% | +1.99 |
| 2023-12-18 to 2024-06-17 | 16 | -3.21% | -3.21 |
| 2024-06-17 to 2024-12-16 | 13 | +2.49% | +2.54 |
| 2024-12-17 to 2025-06-17 | 11 | -0.97% | -0.91 |
| 2025-06-17 to 2025-10-28 | 11 | -1.37% | -1.32 |

## Sensitivity (reported only, never used to choose parameters)

| ADX min | Target | Trades | Expectancy | Profit factor | Max DD | Net return |
|---|---|---|---|---|---|---|
| 15 | 1.5R | 130 | -0.11R | 0.76 | 16.95% | -13.84% |
| 15 | 2R | 128 | -0.09R | 0.81 | 17.07% | -11.41% |
| 15 | 3R | 120 | -0.18R | 0.64 | 21.95% | -19.87% |
| 20 | 1.5R | 94 | -0.08R | 0.81 | 9.76% | -7.63% |
| 20 (default) | 2R | 94 | -0.08R | 0.82 | 9.57% | -7.82% |
| 20 | 3R | 89 | -0.10R | 0.78 | 10.50% | -9.18% |
| 25 | 1.5R | 61 | -0.16R | 0.67 | 10.96% | -9.37% |
| 25 | 2R | 57 | -0.11R | 0.77 | 9.35% | -6.40% |
| 25 | 3R | 56 | -0.18R | 0.65 | 11.31% | -10.12% |

## Limitations

- Design data only (before 2025-10-29): evidence about the past, not validation.
- One asset and one price source (Binance BTCUSDT 4h and 1d); fixed costs; fills at the next open.
- Indicators are computed over the stored history; the live view uses the app's 500-candle window, so values can differ slightly.
- Long only (no shorting): a bearish trend gives NO TRADE, not a short.
