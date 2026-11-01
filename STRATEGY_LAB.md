# Strategy Lab — methodology

Five independent **spot** research strategies, side by side. Research only and simulated only: nothing is bought or
sold, no exchange account, no leverage, futures, margin or shorting. The Lab is **separate from the live signal
(signal-1.8)**: it never reads or changes signal-1.8's settings, and signal-1.8 never reads the Lab.

| Round | Strategy | Signals | Status |
|---|---|---|---|
| 1 | DCA (dollar-cost averaging) | ACCUMULATE / WAIT / PAUSE | implemented, pre-registered, design backtest run once: PASS (design data) |
| 2 | Trend following (4h pullback continuation; defined differently from H2's EMA 50/200) | BUY SETUP / HOLD / NO TRADE / EXIT / REDUCE EXPOSURE | implemented, pre-registered, design backtest run once: FAIL (design data) |
| 3 | Range trading (4h, only in the SIDEWAYS regime) | same | implemented, pre-registered, design backtest run once: INCONCLUSIVE (4 trades) |
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
   append-only tables, own start date = the next UTC midnight after it is first registered, i.e. the day after the
   merged code runs) processes each new closed candle of the strategy's timeframe once, with the strategy's own
   per-bar step (the same as its backtest), and gives a fixed verdict after 365 days. It is independent of the H1/H2
   tracker. Since round 2 the tracker is a generic core and a strategy's fingerprint covers **its own module + the
   core** only, so later rounds do not disturb earlier strategies. DCA was registered under the round-1 scheme and had
   processed no day; its fingerprint was re-based once (append-only `strategy_forward_rebases`; the database refuses a
   re-base after the first processed day).

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

## DCA forward-test fingerprint re-base (2026-11-01)

What happened, exactly, and why it is not a reset of evidence:

| | |
|---|---|
| Forward test | `strategy_forward_tests` id 1, `dca`, config hash `f626cffd73fb1e10`, start day 2026-11-02 00:00 UTC (registered 2026-11-01 11:51 UTC, after the round-1 merge) |
| Old fingerprint (round-1 scheme) | `f576a36a9cb5fdeacb3a99a6e63c0915b0817a4b5295000e8f85d6bc9cd5d4f6` = SHA-256 over `base.py` + `dca.py` + the whole `strategy_forward.py` |
| New fingerprint (round-2 scheme) | `6f3658518fdbc3db35e07bea8e736d1234dbe975b8b7994378491787aa2da378` = SHA-256 over `dca.py` + the generic `strategy_forward.py` core |
| Recorded | 2026-11-01 12:47:46 UTC in `strategy_forward_rebases` (append-only), with the reason "round 2: generic forward tracker; fingerprint = strategy module + tracker core (re-based before the first day)" |
| Days processed at that moment | **0** (the first DCA day, 2026-11-02, could not close before 2026-11-03 00:00 UTC) |

- **Why:** the round-1 fingerprint covered the shared `base.py` and the whole tracker, so adding any later strategy
  would have marked DCA "code changed" for good. Round 2 made the tracker a generic core and moved DCA's daily step
  into `dca.py` with identical logic and event format (tested against the round-1 behaviour).
- **Guarantees:** the forward test row itself is unchanged (start day, configuration, original fingerprint); the
  re-base is a separate append-only row; a database trigger refuses any re-base once the test has a processed day;
  `apply_rebases()` only acts on the exact old fingerprint listed in `KNOWN_REBASES` (`services/strategy_lab.py`) and
  only once. Strategy Lab shows the re-base under the DCA forward test.
- **From now on** DCA's forward fingerprint changes only if `dca.py` or the tracker core changes; such a change marks
  DCA "code changed" (NOT VALIDATED (code changed)) and cannot be re-based, because days have been processed by then.

## DCA addendum (2026-11-01): deposit-neutral drawdown, reported only

C1's drawdown is on the whole budget (cash not yet invested + BTC). The time-weighted index of the invested BTC and
(value − invested) / invested are shown next to it, reported only (`services/strategies/dca_metrics.py`). On the
design period: time-weighted max drawdown 76.72% and worst return on invested −54.71% (2022-06-18), against 26.59% on
the budget basis. The locked study and its verdict are unchanged.

## Trend following (round 2)

- **Rules** (`PREREGISTRATION_TREND.md`, config hash `27ce4fee913591af`): 4h EMA 21 > 50 > 200 with EMA 50 rising,
  ADX(14) ≥ 20 and the last daily close above the daily EMA 50; entry after the previous bar's low touched EMA 21 and
  this bar closed above the previous high, at the next open; stop = lowest low of the last 5 closed 4h bars −
  0.25 × ATR(14); target 2R; exit on a 4h close below EMA 50 or after 30 × 4h bars; 1% planned risk, never more than
  100% of the account; 0.10% fee + 0.05% slippage per side. Differs from H2 in timeframe, filters, entry timing,
  stops, targets, exits and sizing.
- **Criteria:** trades ≥ 30 (else INCONCLUSIVE); expectancy ≥ +0.15R; profit factor ≥ 1.3; max drawdown ≤ 15%;
  beats ≥ 90% of 1,000 random-entry runs with the same holding times and stop/target rules. Buy & hold, the H2 rule,
  walk-forward windows and an ADX 15/20/25 × target 1.5/2/3R grid are reported only.
- **Design result** (`data/strategy_trend_design.md`, run once, 4h decisions 2021-06-20 … 2025-10-28): **FAIL**.
  94 trades, expectancy −0.08R, profit factor 0.82, max drawdown 9.57%, net −7.82%; beat 83% of random runs. Buy &
  hold +217%, H2 rule +230% (max drawdowns 77% and 38%). Two of nine 182-day windows were positive; no sensitivity
  combination had a positive expectancy. Status: BACKTESTED (design data), not validated; its forward test runs
  anyway as pre-registered.

## Range trading (round 3)

- **Rules** (`PREREGISTRATION_RANGE.md`, config hash `3abb2ee3b4292819`): only when the existing 4h regime is
  SIDEWAYS and ADX(14) < 20; range = highest high / lowest low of the 50 closed 4h bars before the signal bar, valid
  with ≥ 2 touches per boundary (within 0.25 × ATR; neighbouring touching bars count once) and width ≥ 3 × ATR(14);
  entry on a reclaim of the range low or a bullish close after a close in the bottom 20%, only below the midpoint;
  stop = range low − 0.5 × ATR(14); target = midpoint; exit when the regime leaves SIDEWAYS or after 30 × 4h bars;
  1% risk, long only. `services/regime.py` is pinned by SHA-256 (a change is flagged as a dependency change).
- **Criteria:** as Trend. Sensitivity: lookback 30/50/80 × ADX 15/20/25 and the range-high target (reported only).
- **Design result** (`data/strategy_range_design.md`, run once, 4h decisions 2021-06-20 … 2025-10-28):
  **INCONCLUSIVE — only 4 trades** (fewer than 30). The regime was SIDEWAYS on 15.3% of the bars, but every filter
  held on only 1.6%. The four trades: +1.88%, expectancy +0.47R, profit factor 16.35, max drawdown 0.55%, beat 97.6%
  of random runs; average planned reward/risk 0.99 (the midpoint is close to the entry). Four trades say nothing.
  As noted in advance, ADX 25 gave the same trades as ADX 20. Status: BACKTESTED (design data), not validated; the
  forward test runs as pre-registered (at this frequency it will likely end INCONCLUSIVE too).

## Charts (visualisation only, `services/strategy_charts.py`)

- **Live chart** (Chart tab of each strategy): the strategy's own timeframe and closed candles with only the
  indicators its rules use (trend: EMA 21/50/200, the daily EMA 50 level, ADX; range: the range box, midpoint, touches,
  sideways shading, ADX; DCA: scheduled buy days, average buy price). Computed by the strategy's own functions
  (`trend.indicators`, `range.features` / `regime_labels`, `dca.scheduled_days` / `simulate`); the plan (entry band,
  stop, targets, R:R) comes from the strategy's own result. Without a setup a box lists each rule as ✓ / ✗ from the
  same feature row; if every rule holds but the strategy gives another signal (e.g. an exit takes priority), the box
  says so. A strategy that failed on design data shows "Failed on design data: research only".
- **History chart:** the STORED design result: every trade (▲ entry, ▼ exit, green win / red loss, hover with times,
  prices, stop, target, R, exit reason, fees), equity vs buy & hold and drawdown on a shared time axis, and a trade
  list (a click zooms to the trade). DCA: invested capital vs value, the BTC price and the worst point for the invested
  money. Series that are not stored at full resolution (equity, drawdown, buy & hold, fees) are **recomputed in memory**
  with the locked configuration and shown only if **every stored metric, the trade list and the buy & hold return are
  reproduced exactly**; otherwise the mismatch is shown and only stored data is drawn. No result is written. All three
  stored results verified exactly on 2026-11-01. Verified series are kept in a derived cache
  (`data/cache/strategy_charts/`, keyed by the strategy's forward fingerprint + the stored result's SHA-256 + the cache
  version), reused on app start and recomputed only when the key changes (range needs about 4 minutes).
- **Forward chart:** the strategy forward tracker's stored events; empty until its first processed bar.
- **Comparison:** the stored design equity samples normalised to 100 and net return vs max drawdown; hypothetical,
  not ranked.

## API

`GET /api/strategies/<id>/chart/live|history|forward`, `GET /api/strategies/compare-chart` (charts),
`GET /api/strategies` (cards + comparison), `GET /api/strategies/<id>` (detail, backtest, forward, history, config
versions), `GET` / `PUT /api/strategies/<id>/config`, `POST /api/strategies/<id>/config/reset`.
