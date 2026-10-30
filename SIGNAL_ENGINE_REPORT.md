# Signal Engine Report (signal-1.7)

Audit date 2026-10-30. Source: `services/signal_engine.py`, `services/signal_service.py`. **No rule, weight or
threshold was changed.**

## 1. What the engine does

```
analyses(1h, 4h, 1d) + live price + news summary
  → 6 components in [−1, +1]          trend 30 · momentum 25 · news 15 · volume 10 · structure 10 · overextension 10
  → weights re-normalised over the components that have data
  → score in [−1, +1]
  → setup:  ≥ +0.50 and agreement ≥ 75% → STRONG BUY SETUP
            ≥ +0.25                     → BUY SETUP
            ≤ −0.25                     → SELL / REDUCE EXPOSURE
            otherwise                   → HOLD
  → BUY plan; R/R to target 1 after costs < 1.5 → downgraded to HOLD
  → gates: any gate → final signal NO TRADE
```

The terminology, weights and thresholds match the project specification exactly.

## 2. Requirement check (Phase 6)

| # | Requirement | Status |
|---|---|---|
| 1 | Explain why a signal was generated | ✔ component facts, setup notes, downgrade reason |
| 2 | Bullish evidence | ✔ `evidence_bullish` (top 10) |
| 3 | Bearish evidence | ✔ `evidence_bearish` |
| 4 | Conflicting evidence | ✔ `evidence_against` relative to the score's sign; `contradictory` gate |
| 5 | Confidence separate from the score | ✘ `confidence` is a fixed sentence. `agreement` and `directional_weight` exist but are not presented as a confidence measure |
| 6 | Timeframe agreement | Partial: per-component facts name timeframes; there is no alignment summary |
| 7 | Invalidation conditions | ✔ for BUY and SELL; HOLD shows "what would change it" |
| 8 | Nearby support and resistance | ✔ re-sorted against the live price, minor/major marked |
| 9 | Research horizon | ✔ "1-3 days (4h primary, 1d trend filter, 1h confirmation)" |
| 10 | Timestamp | ✔ `generated_at`, plus the candle close basis per timeframe |
| 11 | Engine version | ✔ `engine: signal-1.7`; validations are tied to a config hash |
| 12 | Why NO TRADE | ✔ every gate has a readable reason; the Overview shows the first one |

Also correct: a BUY plan is labelled "research only - not actionable" under NO TRADE; SELL is phrased as "reduce
exposure", not "sell everything"; the score is repeatedly labelled "not a probability".

## 3. NO TRADE safety system (Phase 7)

| Condition | Implemented | Notes |
|---|---|---|
| Missing price | ✔ `no_price` | |
| Stale price | ✔ `stale_price` (>120 s) | |
| Insufficient candles | ✔ `insufficient_data` (<100 closed 4h candles, or no trend/momentum) | |
| Candle gaps | ✔ 4h, 1d (>5 missing) | ✘ not 1h (B7) |
| Stale candles | ✔ 4h, 1d | ✘ not 1h (B7) |
| Extreme volatility | ✔ 4h or 1h ATR% ≥ 95th percentile | |
| Contradictory evidence | ✔ \|score\| ≥ 0.25 and agreement < 60% | |
| Unfavourable R/R | ✔ as a **downgrade** to HOLD, not a gate | Correct: HOLD is shown and the plan is visible |
| Strong adverse news | ✔ fresh (<2 h) high-impact opposing event | |
| Higher-timeframe conflict | ✔ 1d trend beyond ±0.30 against the setup | |
| Unvalidated strategy | ✔ `not_validated`, plus 56-day forward observation | Loophole: a *full* run can validate (B3) |
| Unexpected calculation errors | Partial | Errors in `analyze` become a 500 or "insufficient data"; `signals.current()` has no try/except that turns an exception into NO TRADE with a reason |
| Cross-source price disagreement | ✘ | Proposed (B9) |

NO TRADE is independent of the setup and is never removed to produce signals. Keep it that way.

## 4. Do the weights and thresholds have predictive value?

Tested on the design period by the existing score study (QUANTITATIVE_AUDIT §4): **no**. Score Spearman −0.018;
the SELL bucket had higher forward returns than average; the top bucket's excess is inside its bootstrap CI.
Following the project rules:

- weights and thresholds stay as they are;
- the score stays labelled "not a probability" and is **not** converted into a probability (that would need
  calibration on data where it has shown a relationship, which it has not);
- research into alternatives happens on design data, pre-registered, with one holdout test (Roadmap P2).

## 5. Observations on specific components

- **Trend (30%)**: 1d and 4h EMA stack, EMA200, SMA50/200 and structure. Four of the five sub-signals are
  moving-average relationships, so they are highly collinear. Its Spearman is slightly negative at the 3-day horizon.
  A trend filter may still be useful as a *regime gate* rather than a score term (testable in P2).
- **Momentum (25%)**: RSI, ATR-scaled MACD histogram, ADX/DI on 4h and 1h. Reasonable construction.
- **Volume (10%)**: the sign comes from the colour of the **last** candle only; 1.0–1.5× volume on an up candle
  adds +0.4. That is a very noisy input. Its non-overlapping Spearman (+0.14) is the highest of all components but
  rests on ~360 samples.
- **Structure (10%)**: breakout table plus level proximity plus divergence. Additive, then clipped.
- **Overextension (10%)**: a contrarian brake (RSI extremes, closes outside the bands). Slightly negative
  correlation, as intended for a brake.
- **News (15%)**: never backtested (B8).

## 6. Recommended changes (none implemented yet)

1. Present **confidence** as data-quality × agreement, with the inputs shown (share of weight with data,
   agreement %, timeframe alignment), and never as a probability.
2. Add a **timeframe alignment** block: trend/momentum sign and volatility regime per timeframe, with a
   classification (aligned / pullback in trend / counter-trend bounce / conflicting) and one sentence on why they
   disagree.
3. Wrap signal generation so that any exception becomes NO TRADE with the reason "calculation error: …"
   instead of an HTTP 500.
4. Add 1h to the stale/gap gates; add the cross-source price gate.
5. Restrict news to blocking until it has out-of-sample evidence (B8). This changes the live score, so it needs
   a new engine version (signal-1.8) and the user's approval.
