# Pre-registration: Strategy Lab, strategy 1 — DCA (dollar-cost averaging)

| | |
|---|---|
| Status | **PRE-REGISTERED** (2026-11-01), committed **before** the first backtest. Defaults, criteria and outputs below are fixed; the design backtest runs **once** (`scripts/research_strategy.py dca`, refused a second time) and nothing is tuned to its result. |
| Config hash: `f626cffd73fb1e10` | `services.strategies.base.config_hash(dca.DEFAULTS)`. The backtest script refuses to run if the defaults no longer produce this hash. |
| Test data | **Design data only**: Binance BTCUSDT daily candles that closed before 2025-10-29 00:00 UTC (the contaminated holdout is never used). No "full" period. |
| Live signal | Unchanged: signal-1.8 does not read this strategy and this strategy does not read or change signal-1.8 settings. Simulated only: nothing is ever bought. |

## 1. Hypothesis

Buying a fixed USD amount of BTC on a fixed weekly schedule for a year lowers drawdown and the worst-case outcome
compared with investing the same total at once on the first day (lump sum), at the cost of a lower average return in
rising markets. DCA is an accumulation method, not a prediction: it does not time the market.

## 2. Fixed defaults

| Setting | Value | Meaning |
|---|---|---|
| `amount_usd` | `100.0` | simulated USD per scheduled buy |
| `interval` | `weekly` | daily / weekly / monthly |
| `weekday` | `0` | weekly buys on Monday (UTC) |
| `month_day` | `1` | monthly buys on day 1 (only used for monthly) |
| `fee_pct` | `0.1` | fee per buy, % |
| `slippage_pct` | `0.05` | slippage per buy, % |
| `horizon_days` | `365` | DCA-vs-lump-sum comparison window |

## 3. Simulation rules (fixed)

- A buy happens at the **open** (00:00 UTC) of each scheduled day: fill = open × (1 + slippage); fee = amount × fee;
  BTC = (amount − fee) / fill. A scheduled day without a stored candle is a **missed** buy (reported, not invented).
- Portfolio measured on the whole budget (amount × scheduled buys in the window): equity = cash not yet invested +
  BTC × daily close × (1 − fee − slippage). DCA never sells: realized P&L is 0.
- Lump sum: the same budget bought at the first day's open with the same costs, marked the same way.
- Closed daily candles only; each day uses only its own and earlier candles.

## 4. Design criteria (fixed now)

Rolling windows of `horizon_days` (365) days, a new window every 7 days, over the whole design period:

| # | Criterion |
|---|---|
| C1 | Median max drawdown of the windows: DCA **<** lump sum |
| C2 | 5th-percentile window return (the bad case): DCA **>** lump sum |

PASS on design data needs both. Reported as well (no criterion): full-period DCA vs lump sum, mean window returns,
the share of windows where DCA beat lump sum, calendar-year results, fees and slippage paid. Sensitivity checks
(other schedules) may be reported later but are never used to change these defaults. Walk-forward does not apply:
DCA has no fitted parameters. **Design results never validate the strategy.**

## 5. Forward test (the only path to "forward tested")

The separate strategy forward tracker (`services/strategy_forward.py`, own fingerprint, append-only) starts this
configuration the next UTC midnight after the merged code first runs, processes each new closed daily candle once,
and after 365 days gives a fixed verdict:

| # | Forward criterion |
|---|---|
| F1 | Max drawdown over the 365 forward days: DCA **<** lump sum bought on the start day |

Changing any setting creates a new configuration version, marked **NOT VALIDATED (settings changed)**; it inherits
neither the design backtest nor the forward test.

## Addendum (2026-11-01) — reported-only drawdown views; the original text above is unchanged

Added after the design backtest, on the owner's review. **It changes nothing above:** not the defaults, not C1/C2, not
F1, not the locked study or its verdict (PASS on design data, stored with the SHA-256 of the text above).

- **Limitation of the pre-registered drawdown.** C1 measures max drawdown on the whole budget from day 1: equity =
  cash not yet invested + BTC at liquidation value. No deposits are added, so new money does not inflate the value,
  but the uninvested cash dampens the drawdown: early in a window most of the account is cash. Part of DCA's lower
  drawdown therefore comes from holding cash, not from buying better.
- **Two deposit-neutral views, reported only, no criterion** (`services/strategies/dca_metrics.py`), shown in
  Strategy Lab for the locked study's own period and configuration and for the forward test:
  1. a **time-weighted** return index of the invested BTC (each day split at the buy at the open; fee and slippage
     included), with its max drawdown;
  2. **(value − invested) / invested** over time, with its worst point.
- They are computed from the simulation's own records (no new prices). They are never used to judge, tune or
  re-label the strategy; the verdict stays as recorded.
