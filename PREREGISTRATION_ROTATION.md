# Pre-registration: Strategy Lab, strategy 5 — Narrative / sector rotation (weekly relative strength)

| | |
|---|---|
| Status | **PRE-REGISTERED** (2026-11-02) on the owner's specification, committed **before** the first backtest. Rules, defaults, criteria and sensitivity checks below are fixed; the design backtest runs **once** (`scripts/research_strategy.py rotation`, refused a second time). Nothing is tuned to its result. |
| Config hash: `f3de8ea9c08bc780` | `services.strategies.base.config_hash(rotation.DEFAULTS)`. The backtest script refuses to run if the defaults no longer produce this hash. |
| Assets | BTC, ETH, SOL, BNB: Binance USDT pairs (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT), **daily closed candles** from the public klines endpoint (no key). An asset without data at a date is **N/A** there (never zero). |
| Test data | **Design data only**: daily candles closed before 2025-10-29 00:00 UTC (the contaminated holdout is never used). No "full" period. BTC uses the daily candles already stored (from 2021-05-02); ETH, SOL and BNB are downloaded for the same dates. |
| Inputs | **Price, relative strength and volume only.** Volume is shown next to the ranking, not used by the rule. **News is shown live as context only and is never in the backtest** (nor in the rule). |
| Live signal | Unchanged: signal-1.8 does not read this strategy. Simulated only, spot only, never rotates real funds. |

## ⚠ Survivorship bias

**survivorship bias: SOL and BNB were picked knowing they survived** (and grew) through the design years. Coins that
faded or were delisted are not in the list, so the results may look better than reality. This cannot be removed with
four hand-picked assets; it is stated next to every result.

## 1. Rules (fixed)

| Part | Rule |
|---|---|
| When | weekly, at the **Monday 00:00 UTC** open (the open of the Monday daily candle) |
| Rank | each asset's **28-day return** computed on **closes up to Sunday**: close of the Sunday daily candle / close 28 days before − 1. An asset without both closes, or without a Monday open to trade at, is N/A that week |
| Hold | the top asset, **100%**. If the top asset's 28-day return is **≤ 0**, hold **cash** (absolute momentum). Fewer than two assets with a return: cash (nothing to rank) |
| NEUTRAL | the top two are **within 2** percentage points (r1 − r2 ≤ 0.02): **keep the current holding** if it is one of those two; otherwise (cash or another asset) take the top |
| Signals | **BTC STRONGER** (BTC is top) · **ALTCOIN STRENGTH** (an altcoin is top) · **NO TRADE** (cash) · **NEUTRAL** (top two within 2 points) |
| Costs | the same as the other strategies: 0.10% fee + 0.05% slippage **per side**; a switch from one asset to another is two sides (sell, buy); to or from cash is one side. Fills at the Monday open; marked daily at liquidation value |

**Clarifications needed to make the specification exact** (decided now, before any backtest):

- The decision uses only data closed before the Monday open; the Monday candle itself is never used.
- A "switch" is any change of holding, including into or out of cash.
- If the held asset has no Monday open (missing candle), it is sold at its last close.
- Live signal: the rule applied to the last closed daily candle (a preview of the next Monday's decision), with the
  holding the rule would have now (its own decisions replayed over the last 26 Mondays, from cash).
- return / max drawdown uses a drawdown floor of 0.01 percentage points so the ratio stays finite.

| Setting | Value |
|---|---|
| `lookback_days` | `28` |
| `cash_filter` | `on` |
| `neutral_band_pct` | `2.0` |
| `fee_pct` | `0.1` |
| `slippage_pct` | `0.05` |

## 2. Design window

From the first Monday at least 57 days after the first stored BTC daily candle (the longest sensitivity lookback, 56
days, plus the Sunday close; the same window for every sensitivity run) to the last daily candle closed before
2025-10-29 00:00 UTC.

## 3. Pass criteria (against BTC buy & hold over the same window; all must hold; design data only)

| # | Criterion |
|---|---|
| — | **switches ≥ 30**, otherwise the verdict is **INCONCLUSIVE** (neither pass nor fail) |
| C1 | **return / max drawdown** ≥ BTC buy & hold's (same window, same costs) |
| C2 | max drawdown ≤ BTC buy & hold's |
| C3 | net return beats ≥ **90%** of 1,000 **random weekly rotations** among the same assets (seed 7): cash in the weeks the strategy held cash (matched exposure), otherwise a uniformly random asset among those with data that week; same costs |

Reported only: buy & hold of each asset (N/A where it was not listed at the start), weeks in cash / BTC / altcoins,
walk-forward 182-day windows. Design results never validate the strategy.

## 4. Sensitivity checks (reported only, never used to choose parameters)

lookback 14 / 28 / 56 days × cash filter on / **no-cash** variant (always hold the top asset) — 6 runs, the default
included. All other rules unchanged.

## 5. Forward test

The separate strategy forward tracker starts this configuration the next UTC midnight after the merged code first
runs (append-only, own fingerprint = `rotation.py` + the tracker core) and processes each closed day once, waiting
until all four assets' candles of that day are stored. After 365 days: F1 return / max drawdown ≥ BTC buy & hold's
and F2 max drawdown ≤ BTC's over the forward period; fewer than 30 switches → INCONCLUSIVE (likely at a weekly pace).
A settings change creates a new version, NOT VALIDATED (settings changed), inheriting nothing.
