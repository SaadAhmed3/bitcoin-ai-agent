# Pre-registration: Strategy Lab, variant — Breakout-0.5R (forward test only)

| | |
|---|---|
| Status | **PRE-REGISTERED** (2026-11-02) on the owner's decision, **after the Breakout design result** (FAIL: max drawdown 23.71% > 15%). Label everywhere: **chosen after design results: forward test only, no design verdict**. |
| Config hash: `337072c89af639f9` | `services.strategies.base.config_hash(breakout_05r.DEFAULTS)`. |
| Design backtest | **None, by design.** The idea (half the risk) came from seeing the design result, so any design backtest of it would be data snooping. `scripts/research_strategy.py` refuses it. It has no design verdict and never inherits Breakout's. |
| Breakout | Unchanged: its pre-registration, its single design run and its **FAIL** verdict stand; its own forward test continues at 1% risk. |
| Live signal | Unchanged: signal-1.8 does not read this strategy. Simulated only, spot long only. |

## 1. Rules

**identical** to Breakout (`PREREGISTRATION_BREAKOUT.md`): the same code (`services/strategies/breakout.py`:
`features()`, `step()`, `evaluate_frames()`, `forward_step()`), not a copy. The only difference is the planned loss
to the initial stop: **0.5%** of the account per trade instead of 1%.

| Setting | Value |
|---|---|
| `lookback` | `20` |
| `volume_mult` | `1.2` |
| `volume_window` | `20` |
| `stop_atr` | `1.0` |
| `trail_bars` | `10` |
| `time_limit_bars` | `60` |
| `risk_pct` | `0.5` |
| `fee_pct` | `0.1` |
| `slippage_pct` | `0.05` |

## 2. Fingerprint

Its own forward fingerprint: `services/strategies/breakout_05r.py` + the tracker core. That module pins
`services/strategies/breakout.py` by SHA-256 `81a0aaf854af5c4b423875abf96bba18d0bb8488486bb1adc2e248e3d851bb8c`, so
the rules it runs are part of its fingerprint. If `breakout.py` changes, the variant shows NO TRADE ("dependency
changed") and its forward test pauses instead of silently running other rules.

## 3. Forward test (the only evidence it can get)

The separate strategy forward tracker starts it the next UTC midnight after the merged code first runs, append-only,
and processes each new closed 4h bar once. After 365 days: F1 net return > 0 and F2 expectancy ≥ +0.15R; fewer than
30 trades → INCONCLUSIVE (Breakout's forward criteria). The forward drawdown is reported. A settings change creates a
new version, NOT VALIDATED (settings changed), inheriting nothing.
