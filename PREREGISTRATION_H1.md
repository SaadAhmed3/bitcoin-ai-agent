# Pre-registration: hypothesis H1 (daily SMA 200 trend filter)

| | |
|---|---|
| Status | **FAILED ON DESIGN DATA (C3 84.7%), holdout NOT run.** Owner's decision, 2026-10-31: criteria kept as written (C3 stays at 90%); no criterion is changed after seeing results. H1 is closed as a hypothesis. |
| Written | 2026-10-31, on branch `research-1`, before any holdout data was read for H1 |
| Holdout | **Not run, and will not be run for H1.** The 2025-10-29 holdout is contaminated for trend-following hypotheses (see §9). |
| Live signal | H1 is **not** wired into the live signal (engine signal-1.8 is unchanged). |

## 1. Hypothesis

A long-or-cash trend filter on Bitcoin reduces drawdown compared with simply holding Bitcoin, with a better return
per unit of drawdown, and this is not explained by luck in *when* it is invested.

## 2. Exact rules (fixed)

| Item | Rule |
|---|---|
| Instrument | BTC spot, long or cash. No leverage, no shorting. |
| Data | Binance BTCUSDT **daily** candles (UTC days), from the app's local database |
| Signal | At each **closed** daily candle: **in the market if close > SMA(200) of daily closes**, otherwise out. SMA = simple mean of the last 200 closes including that day's. No band. |
| Execution | Act at the **next day's open**: buy at open × (1 + 0.15%), sell at open × (1 − 0.15%). |
| Costs | 0.10% fee + 0.05% slippage **per side** (the engine's costs). |
| Sizing | 100% of the account in or out (a research rule; the risk calculator is separate). |
| Marking | Each daily close at liquidation value: units × close × (1 − 0.15%). |
| Start of the test | Flat (cash) at the 2025-10-28 close. The first possible trade is at the **2025-10-29 open**. Closes before 2025-10-29 are used only to compute the SMA. |
| End of the test | The last complete daily candle in the database when the test is run. |

## 3. Pass criteria (fixed before the holdout)

The holdout test produces **PASS**, **FAIL** or **INCONCLUSIVE**. PASS requires every criterion:

| # | Criterion | Rule |
|---|---|---|
| C1 | Drawdown | H1 max drawdown ≤ **60%** of buy & hold's max drawdown over the same days |
| C2 | Return per drawdown | H1 net return / max drawdown ≥ buy & hold's |
| C3 | Not luck | H1 net return beats ≥ **90%** of 1,000 random-entry runs (same number of trades and the same holding days, shuffled, non-overlapping; seed 7) |

**INCONCLUSIVE** (no verdict, the holdout is still spent): fewer than **365** holdout days, or H1 never changes
position during the holdout, or no random-entry comparison is possible.

Reported but **not** a criterion: the EMA 50/200 daily crossover (a competing trend filter, not the claim being
tested), calendar years, regimes, number of trades, time in the market.

These are the same functions the design study used (`evaluate()` and `run_rule()` in `scripts/research_h1.py`).

## 4. Code fingerprint

| File | SHA-256 (line endings normalised to LF) |
|---|---|
| `scripts/research_h1.py` (rules, simulation, metrics, criteria, holdout test) | `10ff4f3aa012ff491491f3012a1aa8eb62865073ca61429414213e9e7385cd18` |

The script records its own SHA-256 in every result. A holdout result whose fingerprint differs from this one is
**not** a test of this pre-registration. Libraries: the pinned `requirements.txt` (numpy 2.5.4, pandas 3.1.0).

How to check the fingerprint (PowerShell, project folder, `.venv` active):

```powershell
python -c "import hashlib; print(hashlib.sha256(open('scripts/research_h1.py','rb').read().replace(b'\r\n', b'\n')).hexdigest())"
```

## 5. What the design data showed (before pre-registration)

Design data only: Binance daily candles 2021-05-02 → 2025-10-28 23:59 UTC. Decisions from 2022-01-06, the first day on
which every sensitivity variant's SMA exists (1,391 days). Full tables: `data/research_h1_design.md`.

| | Net return | Max DD | Return / DD | Trades | Time in market | Worst year (≥ 180 days) |
|---|---|---|---|---|---|---|
| **H1 SMA 200** | +209.81% | 31.90% | 6.58 | 15 | 61.0% | 2025 (to Oct 28): −11.42% |
| Buy & hold | +161.27% | 66.73% | 2.42 | 1 | 100% | 2022 (from Jan 6): −61.72% |
| EMA 50/200 (daily) | +336.03% | 28.10% | 11.96 | 2 | 70.0% | 2022 (from Jan 6): 0.00% |
| Random entries (1,000) | median +72.71% (p10 −8.35%, p90 +270.71%) | | | | | H1 beats 84.7% |

Regimes (from the trailing 90-day return known at the previous close; > +15% bull, < −15% bear):

| Regime | Days | H1 | Buy & hold |
|---|---|---|---|
| Bull | 512 | +186.28% (in the market 100%) | +186.28% |
| Sideways | 603 | +14.23% (in 55%) | +14.93% |
| Bear | 276 | −5.26% (in 1%) | −20.59% |

Sensitivity (a check, **not** tuning): SMA 150/200/250 × band 0/1/2% gives net returns +175% to +226%, max drawdowns
25% to 36% and return/drawdown 4.8 to 8.7. Every variant beats buy & hold on drawdown and return/drawdown; SMA 200 with
no band sits in the middle. The results are stable and the rule does **not** clearly break, so **SMA 200 with no band
stays the rule**. Bands cut whipsaw trades (15 → 7 for SMA 200), most of them in March 2025.

**Design check against these criteria: FAIL.** C1 pass (31.90% ≤ 40.04%), C2 pass (6.58 ≥ 2.42), **C3 fail (84.7% <
90%)**. On the design data, H1 does not beat random entries with the same holding lengths at the pre-registered level.

## 6. Decisions the owner must make before signing

1. **Run it at all?** H1 already fails C3 on the design data. A holdout test is a fair test of the rule, but the
   prior chance of PASS is low. Changing C3 *now* (before the holdout) is still legitimate if the owner thinks the
   random-entry baseline is too strict for a trend filter. It must be written here before signing, not after the run.
2. **The single-use holdout.** The holdout period is single-use for every rule set. Running H1 on it uses it up for
   the live engine too (signal-1.8 could then only be validated on a new holdout start). Options:
   - **(A) recommended:** record the H1 run in the app's holdout registry, so the period is spent for every rule set,
     as the single-use rule says;
   - **(B):** keep H1 outside the registry. That means two hypotheses on one holdout (a multiple-testing risk), and
     it contradicts the single-use rule.
   The script does not write to the registry yet; option A needs a small change, which would change the fingerprint
   above before signing.
3. **EMA 50/200 did better on the design data.** Switching H1 to EMA 50/200 now would choose a rule *because* it did
   best on the design data, which is exactly what a pre-registration prevents. If wanted, it should be a separate
   hypothesis (H2) with its own pre-registration and its own fresh holdout.

## 7. How the single test will be run (only after signing)

```powershell
python scripts\research_h1.py --holdout --confirm-spend-holdout
```

The script refuses without `--confirm-spend-holdout`. It writes `data\research_h1_holdout.json` with the verdict,
every criterion's value and its own fingerprint. The result is kept whatever it is, and the test is never repeated.

## 8. Sign-off

| | |
|---|---|
| Owner decision | 2026-10-31 (UTC), recorded on the owner's instruction |
| §6.1 | Keep C3 at 90%; do not change criteria after seeing results. **H1 FAILED ON DESIGN DATA (C3 84.7%).** |
| §6.2 | Not applicable: the holdout is not run for H1 (contaminated, §9). |
| §6.3 | EMA 50/200 becomes a separate hypothesis, H2 (`PREREGISTRATION_H2.md`), tested on forward data only. |
| Criteria changed? | **No.** |

H1 is still run by the forward paper tracker (Research → Forward tests), **for information only**: those results do not change this verdict.
