# H1 research - DESIGN data only

H1: hold BTC while the daily close > SMA200, else cash; decide at the close, act at the next open.

Data: Binance BTCUSDT 1d (local database, read-only); 1641 candles 2021-05-02 -> 2025-10-28T23:59 UTC (holdout starts 2025-10-29; never loaded). Common window: decisions from 2022-01-06 (1391 days). Costs 0.10% fee + 0.05% slippage per side.

| | Net return | Max DD | Return / DD | Trades | Time in market | Worst year (>= 180 days) |
|---|---|---|---|---|---|---|
| H1 SMA200 | +209.81% | 31.90% | 6.578 | 15 | 61.0% | 2025 (Jan 01-Oct 28): -11.42% |
| Buy & hold | +161.27% | 66.73% | 2.417 | 1 | 100.0% | 2022 (Jan 06-Dec 31): -61.72% |
| EMA 50/200 (daily) | +336.03% | 28.10% | 11.96 | 2 | 70.0% | 2022 (Jan 06-Dec 31): +0.00% |

Design check against the pre-registered criteria (not a test): **FAIL** - Drawdown at most 60% of buy & hold: pass (31.90% vs limit 40.04% (buy & hold 66.73%)); Return/drawdown at least buy & hold's: pass (6.577 vs 2.417); Beats >= 90% of random entries: FAIL (better than 84.7% of runs)

Random entries (1000 runs, same trade count and holding days): median +72.71%, p10 -8.35%, p90 +270.71%; H1 beats 84.7% of them.

Calendar years (%): 2022: H1 +0.0 / B&H -61.7; 2023: H1 +93.5 / B&H +155.6; 2024: H1 +80.8 / B&H +121.3; 2025: H1 -11.4 / B&H +20.6

## Regimes (trailing 90-day return at the previous close: > +15% bull, < -15% bear, otherwise sideways)

| Regime | Days | H1 | H1 in market | Buy & hold | EMA 50/200 | EMA in market |
|---|---|---|---|---|---|---|
| bull | 512 | +186.28% | 100.0% | +186.28% | +174.17% | 96.7% |
| sideways | 603 | +14.23% | 55.4% | +14.93% | +24.69% | 72.5% |
| bear | 276 | -5.26% | 1.1% | -20.59% | +27.54% | 15.2% |

## Sensitivity (a check, not tuning: SMA 200 with no band stays the rule)

| SMA | Band | Net return | Max DD | Return / DD | Trades | Time in market | Worst year (>= 180 days) |
|---|---|---|---|---|---|---|---|
| 150 | 0.0% | +216.47% | 24.80% | 8.728 | 18 | 58.7% | -7.68% |
| 150 | 1.0% | +180.44% | 28.52% | 6.328 | 13 | 57.4% | -12.49% |
| 150 | 2.0% | +203.05% | 28.48% | 7.131 | 8 | 57.0% | -6.79% |
| 200 | 0.0% | +209.81% | 31.90% | 6.578 | 15 | 61.0% | -11.42% |
| 200 | 1.0% | +214.34% | 33.13% | 6.469 | 9 | 61.1% | -2.19% |
| 200 | 2.0% | +225.84% | 30.74% | 7.347 | 7 | 60.8% | -1.39% |
| 250 | 0.0% | +174.93% | 36.20% | 4.832 | 18 | 67.8% | +0.00% |
| 250 | 1.0% | +184.86% | 35.81% | 5.162 | 11 | 68.2% | +0.00% |
| 250 | 2.0% | +209.58% | 34.25% | 6.12 | 6 | 68.7% | +0.00% |

H1 trades: 2023-01-14 -> 2023-08-18 (216 d), 2023-08-30 -> 2023-08-31 (1 d), 2023-10-17 -> 2024-07-05 (262 d), 2024-07-14 -> 2024-08-04 (21 d), 2024-08-24 -> 2024-08-27 (3 d), 2024-09-25 -> 2024-09-26 (1 d), 2024-09-27 -> 2024-10-01 (4 d), 2024-10-15 -> 2025-03-10 (146 d), 2025-03-13 -> 2025-03-14 (1 d), 2025-03-15 -> 2025-03-17 (2 d), 2025-03-20 -> 2025-03-21 (1 d), 2025-03-24 -> 2025-03-29 (5 d), 2025-04-23 -> 2025-10-18 (178 d), 2025-10-20 -> 2025-10-23 (3 d), 2025-10-24 -> open (5 d)

Script SHA-256: `10ff4f3aa012ff491491f3012a1aa8eb62865073ca61429414213e9e7385cd18`

Research only, design data. Past results do not predict future returns. Not wired into the live signal.
