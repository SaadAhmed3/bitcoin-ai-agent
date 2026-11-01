# Strategy Lab — methodology

Five independent **spot** research strategies, side by side. Research only and simulated only: nothing is bought or
sold, no exchange account, no leverage, futures, margin or shorting. The Lab is **separate from the live signal
(signal-1.8)**: it never reads or changes signal-1.8's settings, and signal-1.8 never reads the Lab.

| Round | Strategy | Signals | Status |
|---|---|---|---|
| 1 | DCA (dollar-cost averaging) | ACCUMULATE / WAIT / PAUSE | implemented, pre-registered, design backtest run once |
| 2 | Trend following (defined differently from H2's EMA 50/200) | BUY SETUP / HOLD / NO TRADE / EXIT / REDUCE EXPOSURE | planned |
| 3 | Range trading | same | planned |
| 4 | Breakout trading (never from a wick alone) | same | planned |
| 5 | Narrative / sector rotation (BTC, ETH, SOL, BNB; price, relative strength, volume) | BTC STRONGER / ALTCOIN STRENGTH / NEUTRAL / NO TRADE | planned |

## Shared result schema (`strategy-result-1`, `services/strategies/base.py`)

`strategy_id`, `strategy_version`, `evaluated_at`, `asset`, `timeframe`, `signal`, `strength` (0–100 heuristic or
none — **never a probability of profit**), `supporting`, `contradictory`, `entry_zone`, `stop`, `targets`,
`reward_risk`, `fees_pct`, `slippage_pct`, `data` (source, retrieved_at, last_candle_close, is_stale,
missing_candles, warnings), `regime` (4h context), `explanation`, `rejection`, `config_version`, `config_hash`,
`labels` (LIVE DATA / CALCULATED / MODEL ESTIMATE / HYPOTHETICAL / SIMULATED / NOT VALIDATED).

## Rules against data snooping

1. **Pre-registration first.** Each strategy's defaults and pass criteria are written in
   `PREREGISTRATION_<STRATEGY>.md` (with the config hash) and committed **before** its first backtest.
2. **Design data only, once.** `python scripts\research_strategy.py <id>` backtests on candles closed before
   2025-10-29 (the contaminated holdout is never used, no "full" period). It refuses to run without a committed
   pre-registration, when the defaults no longer match its hash, or a second time (the stored result is append-only and
   unique per strategy and configuration; output files are created, never overwritten). Nothing is tuned to results;
   sensitivity checks are reported, never used to pick parameters.
3. **Settings changes.** Saving settings creates a new configuration version. A configuration that differs from the
   pre-registered one is **NOT VALIDATED (settings changed)** and inherits neither the backtest nor the forward test.
   Resetting to the defaults (same config hash) is the pre-registered configuration again.
4. **Forward testing** is the only path to "forward tested": a separate tracker (`services/strategy_forward.py`, own
   append-only tables, own fingerprint over `base.py` + the strategy module + the tracker, own start date = the next
   UTC midnight after it is first registered, i.e. the day after the merged code runs) processes each new closed
   daily candle once and gives a fixed verdict after 365 days. It is independent of the H1/H2 tracker.

Validation statuses: NOT VALIDATED · NOT VALIDATED (settings changed) · NOT VALIDATED (code changed) · BACKTESTED
(design data) · FORWARD TESTING · FORWARD TESTED: PASS / FAIL. Design results never validate a strategy.

## DCA (round 1)

- **Signal:** ACCUMULATE when the next daily open (00:00 UTC) is a scheduled buy day and the data is fresh and
  complete; WAIT otherwise (the next buy date is named); PAUSE when daily data is stale, has missing candles or is
  absent. No price timing: DCA is a schedule, not a prediction. Strength, entry zone, invalidation and reward/risk do
  not apply.
- **Simulation:** buy at the open of each scheduled day: fill = open × (1 + slippage), fee = amount × fee,
  BTC = (amount − fee) / fill. A scheduled day without a stored candle is a missed buy (reported, never invented).
  Portfolio on the whole budget: equity = cash not yet invested + BTC × close × (1 − fee − slippage). DCA never
  sells (realized P&L 0). Lump sum = the same budget at the first day's open, same costs and marking.
- **Pre-registered criteria** (`PREREGISTRATION_DCA.md`, config hash `f626cffd73fb1e10`): over rolling 365-day
  windows (7-day steps) C1 median max drawdown DCA < lump sum; C2 5th-percentile return DCA > lump sum. Forward:
  F1 max drawdown over 365 forward days DCA < lump sum.
- **Design result** (`data/strategy_dca_design.md`, run once on 2021-05-02 … 2025-10-28): PASS on C1 (19.23% vs
  28.10%) and C2 (−43.02% vs −65.30%); the mean 365-day return was lower (+23.90% vs +50.25%; DCA beat lump sum in
  29% of windows). Design-data evidence only: status BACKTESTED (design data), not validated.
- **Limitations:** one asset and one price source (Binance BTCUSDT daily); fixed costs; overlapping windows are not
  independent samples; win rate and profit factor do not apply; walk-forward does not apply (no fitted parameters).

## API

`GET /api/strategies` (cards + comparison), `GET /api/strategies/<id>` (detail, backtest, forward, history, config
versions), `GET` / `PUT /api/strategies/<id>/config`, `POST /api/strategies/<id>/config/reset`.
