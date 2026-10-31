# Pre-registration: hypothesis H2 (daily EMA 50/200 crossover)

| | |
|---|---|
| Status | **PRE-REGISTERED** on the owner's instruction (2026-10-31). Rules and pass criteria are fixed now, before any forward data exists. Nothing in this document may change after the forward test starts. |
| Test data | **Forward only**: daily candles that close after the tracker's start. Not the 2025-10-29 holdout (contaminated for trend-following, see §6) and not design data (in-sample for H2, see §5). |
| Live signal | H2 is **not** wired into the live signal (engine signal-1.8 is unchanged). No real orders: simulated only. |

## 1. Hypothesis

A long-or-cash daily EMA 50/200 trend filter on Bitcoin reduces drawdown compared with simply holding Bitcoin, with a
better return per unit of drawdown, and this is not explained by luck in *when* it is invested.

## 2. Exact rules (fixed)

| Item | Rule |
|---|---|
| Instrument | BTC spot, long or cash. No leverage, no shorting. |
| Data | Binance BTCUSDT **daily** candles (UTC days) from the app's local database; only **closed** candles. |
| Indicators | EMA 50 and EMA 200 of daily closes: pandas `ewm(span=n, adjust=False, min_periods=n).mean()` over the **full stored daily history** (it begins 2021-05-02), recomputed each day on the closes up to and including that day. |
| Signal | At each closed daily candle: **in the market if EMA 50 > EMA 200**, otherwise out. |
| Execution | Act at the **next day's open**: buy at open × (1 + 0.15%), sell at open × (1 − 0.15%). |
| Costs | 0.10% fee + 0.05% slippage **per side**. |
| Sizing | 100% of the simulated account in or out. |
| Marking | Each daily close at liquidation value: units × close × (1 − 0.15%). |
| Start | The tracker's start day: the next UTC midnight after the app's background monitor first runs the tracker code (`forward_tests.start_day`, stored once, never moved). Flat at the start; the first decision is at the close of the start day and the first possible fill at the next day's open. An initial entry from cash counts as a position change. |
| Missing data | A missing daily candle stops processing until it is stored; days are never skipped or filled in. |

## 3. Pass criteria (fixed now)

**The verdict is computed once, on exactly the first 365 processed days** (start day to start day + 364). Days after
that are shown in the panel but never change the verdict. PASS requires every criterion:

| # | Criterion | Rule |
|---|---|---|
| C1 | Drawdown | H2 max drawdown ≤ **60%** of buy & hold's max drawdown over the same 365 days |
| C2 | Return per drawdown | H2 net return / max drawdown ≥ buy & hold's |
| C3 | Not luck | H2 net return beats ≥ **90%** of 1,000 random-entry runs (same number of trades and the same holding days, shuffled, non-overlapping, within the same 365 days; seed 7) |

**INCONCLUSIVE** (no verdict): H2 never changes position in the 365 days, buy & hold or H2 has no drawdown to
compare, or no random-entry comparison is possible (e.g. a single trade covering the whole period).

Buy & hold uses the same execution: flat at the start, bought at the open after the first decision, marked at
liquidation value.

## 4. Code fingerprint

| File | SHA-256 (line endings normalised to LF) |
|---|---|
| `services/forward_tests.py` (rules, simulated fills, marks, criteria, verdict window) | `5e4b8a0ebba56ae03631b37acc114ae63c1cb8d6a1ab69cc32f1f5f6e5b0a450` |

The tracker stores this fingerprint with each rule set when it starts, and the Research → Forward tests panel shows
"unchanged since the start" or a warning. If the code changes after the start, the H2 result is **not** a clean test
of this pre-registration.

## 5. What was known before writing this (H2 is not an untouched idea)

H2 was chosen **after** its design-data result was seen in the H1 research (`data/research_h1_design.md`, decisions
2022-01-06 → 2025-10-28): EMA 50/200 daily +336.03%, max drawdown 28.10%, return/drawdown 11.96, **2 entries**, 70% in
the market. The design data is therefore in-sample for H2 and cannot test it.

**Expect a long wait.** On the design data the crossover entered the market only twice in about 3.8 years. In 365
forward days it may never change position, which these rules make **INCONCLUSIVE**. With one or two trades, the
random-entry comparison (C3) is also weak evidence either way. Those are properties of the rule, accepted here.

## 6. Why not the 2025-10-29 holdout

Backtest run #1 (full period) displayed buy & hold (+294.32%) and EMA 50/200 (+244.87%) through the holdout year, and
the audit documents quoted those figures. The holdout is therefore contaminated for trend-following hypotheses and is
not used for H1 or H2 (see `QUANTITATIVE_AUDIT.md` §7b).

## 7. H1 alongside

The tracker also runs H1 (SMA 200). H1 **failed on design data** (C3 84.7%, `PREREGISTRATION_H1.md`); its forward
numbers are information only and change nothing.

## 8. Reading the result

Open **Research → Forward tests**. It shows days elapsed / 365, each rule next to buy & hold, and after day 365 H2's
verdict with every criterion's value. The full record is in the `forward_tests` and `forward_events` tables, which
the database keeps append-only. Simulated results; past or forward performance does not guarantee future results.
