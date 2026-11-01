# Pre-registration: Rotation survivorship stress test (reported only)

| | |
|---|---|
| Status | **PRE-REGISTERED** (2026-11-02) on the owner's request, committed **before** the run. Runs **once** (`scripts/research_rotation_stress.py`, refused a second time). |
| Purpose | Rotation's design result (PASS) used four assets picked knowing they survived. This test runs the **same rule** over a wider universe that includes assets which later collapsed or were delisted, to show how much of the result is survivorship. |
| Status of Rotation | **Reported only.** The Rotation strategy, its configuration (`f3de8ea9c08bc780`), its stored design result, its **PASS verdict** and its forward test stay **unchanged**. Nothing is written to `strategy_backtests`. |
| Data | **Design data only** (daily candles closed before 2025-10-29 00:00 UTC); the same window as the original (Mondays from 2021-06-28). Binance spot USDT pairs, daily, from the public klines endpoint (no key). |

## 1. Universe (fixed now, point in time)

The top 20 by market cap on **2021-06-27** (CoinMarketCap historical snapshot, the day before the first design
decision), **without** stablecoins (USDT, USDC, BUSD) and wrapped BTC (WBTC), which track other assets. Plus LUNA
and FTT, named by the owner as assets that later collapsed (they were outside that top 20).

`BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT` (the strategy's own four), `ADAUSDT`, `DOGEUSDT`, `XRPUSDT`, `DOTUSDT`,
`UNIUSDT`, `BCHUSDT`, `LTCUSDT`, `LINKUSDT`, `MATICUSDT`, `THETAUSDT`, `XLMUSDT`, `ICPUSDT`, `LUNAUSDT`, `FTTUSDT`
(18).

**Availability, checked before the run** (only whether daily history exists, not any result): all 18 are
available on Binance's public klines from at least 2021-05-11 (ICP listed then). Three histories end or break
inside the window: **LUNA** ends **2022-05-13** (last close $0.00005) and resumes 2022-05-31 at $1 as **LUNA 2.0**;
**FTT** ends **2022-11-15** and resumes 2023-09-22 after a 10-month halt; **MATIC** ends **2024-09-10** (migrated to
POL). Any asset found missing at run time is reported as not available.

## 2. Rules

- Exactly Rotation's rule (`rotation.decide`): weekly at the Monday 00:00 UTC open, rank by the 28-day return on
  closes up to Sunday, hold the top asset 100% or cash if its return is ≤ 0 (or fewer than two can be ranked),
  NEUTRAL within 2 points; 0.10% fee + 0.05% slippage per side. Ranked over every available asset that week.
- **An asset's history ends at its first gap of more than 7 days.** Rows after that gap under the same ticker are
  **a different asset** (LUNA 2.0) or a relisting after a halt (FTT) and are not used. Ticker migrations
  (MATIC → POL) are not followed.
- **If the held asset's data ends, it is sold at its last close** at the next Monday (a delisting liquidation; this
  may be better than what was actually possible). An asset without data is N/A for ranking, never zero.
- The study code mirrors Rotation for any list of assets and must reproduce `rotation.simulate` exactly with the
  original four (tested), so it cannot drift from the strategy.

## 3. Reported (nothing is a verdict)

Next to the original: net return, max drawdown, return / max drawdown, switches, weeks held per asset, and the
original's criteria applied to the wider universe (C1–C3 against BTC buy & hold, random weekly rotations among the
same wider universe, 1,000 runs, seed 7, cash in the strategy's cash weeks). The original's PASS stands whatever this
shows.
