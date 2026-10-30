# Implementation Roadmap

Based on the 2026-10-30 audit (PROJECT_AUDIT.md, BUGS_AND_RISKS.md). Order of work: P0 → P1 → P2 → P3 → P4, in
small phases, with the full regression suite (493 unit + 81 e2e) after each phase. **Nothing here lowers a
safeguard, changes weights or thresholds, or touches the holdout.** Items that change the live signal are marked
⚑ and need a new engine version and the owner's approval first.

---

## P0 — Critical bugs and financial-calculation correctness

### P0-1 Atomic paper orders (B1)
- **Wrong:** concurrent stop/target checks record duplicate SELL orders; P&L history, loss streak and daily loss
  become wrong.
- **Change:** a module-level `threading.Lock` in `PaperTrading` around `buy`, `sell`, `check_levels` and `reset`;
  inside, read and write the account in **one** connection with `BEGIN IMMEDIATE`; re-check `btc > 0` before selling.
- **Files:** `services/paper_trading.py`; tests in `tests/test_risk_and_paper.py`.
- **Risk:** low; a deadlock is impossible with a single lock and no nested acquisition.
- **Test:** two threads with a barrier (as `race_check.py`) → exactly 1 SELL; two concurrent buys cannot spend the
  same cash twice.
- **Accept:** the concurrency tests pass; the existing paper tests are unchanged and pass.

### P0-2 Range validation for engine settings (B2)
- **Wrong:** negative fee, slippage and `min_rr`, agreement > 1 and unreachable thresholds are accepted.
- **Change:** a per-field range table in `SignalService.update_config` (fee 0–1, slippage 0–1, min_rr 0.5–5,
  min_agreement and strong_min_agreement 0–1, counter_trend_limit 0–1, thresholds within ±1 with
  sell < 0 < buy ≤ strong ≤ 1); also apply it when reading stored settings (corrupt values fall back to defaults);
  show the ranges on the Settings page.
- **Files:** `services/signal_service.py`, `static/js/settings.js`; tests in `tests/test_signal_engine.py` /
  `tests/test_security.py`.
- **Risk:** a user with an out-of-range stored value gets the default back. Report it in the response.
- **Accept:** every out-of-range value → 400 with the allowed range; valid values unchanged.

---

## P1 — Data quality, backtest validity, risk management

### P1-1 Only the holdout can validate (B3)
- **Change:** `validated` is true only for `period == "holdout"`; *full* runs get a badge "FULL PERIOD — includes
  design data, cannot validate" (same mechanism as design). Default the Research view's period to *design*.
- **Files:** `services/backtesting.py`, `services/backtest_service.py`, `templates/index.html`, `static/js/app.js`,
  README §6.2; tests in `tests/test_entry_modes.py` / `tests/test_backtesting.py`.
- **Risk:** stricter than before; existing stored validations: none is validated (checked: run #1 is not).
- **Accept:** a passing *full* run does not write `validated: true`; the badge is shown.

### P1-2 Single-use holdout (B4)
- **Change:** a `holdout_runs` table (config_hash, run_id, created_at). The first holdout run per config hash may
  validate; later runs are labelled "holdout already used for these settings" and cannot validate. The UI asks for
  confirmation before the first holdout run ("This uses the reserved holdout once for these settings").
- **Files:** `database/db.py` (schema + migration), `services/backtest_service.py`, `static/js/app.js`.
- **Accept:** second holdout run for the same hash → `validated` false with a clear reason; tested.

### P1-3 Paper stops checked in the background; no stale SELL (B5, B6, B16)
- **Change:** `AlertMonitor` (60 s) calls `paper.check_levels(price)`; GET `/api/paper` no longer executes exits
  (it reports the last auto-exit); `sell()` rejects stale prices like `buy()`.
- **Files:** `app.py`, `services/paper_trading.py`, `services/alerts.py` (optional "paper stop/target hit" alert,
  worded "SIMULATED exit").
- **Accept:** the stop triggers without the Paper view open; a stale SELL → 400.

### P1-4 Data-quality gates (B7, B9, B10)
- ⚑ Adds NO TRADE conditions (only ever more conservative).
- **Change:** stale/gap gates for 1h; a cross-source price check (second provider, cached ≤60 s) with a deviation
  over 1% → `price_unverified` gate; the signal shows the price provider and the candle provider and warns when
  they differ.
- **Files:** `services/signal_engine.py`, `services/market_data.py`, `services/signal_service.py`; engine version
  → `signal-1.8` (existing validations become stale, which is correct).
- **Accept:** the new gates have unit tests; golden-signal fixture updated deliberately with a note.

### P1-5 Calculation errors become NO TRADE
- **Change:** `SignalService.current()` catches exceptions from analysis and engine code and returns a NO TRADE
  signal with the reason "calculation error" (logged with a trace), instead of an HTTP 500.
- **Accept:** a forced exception in `analyze` → NO TRADE with the reason; tested.

### P1-6 Paper daily loss and liquidation value (B11, B13)
- **Change:** daily loss = realised today + (mark now − mark at 00:00 UTC) on the position; show "equity after
  exit costs" next to equity.
- **Accept:** a position bought yesterday at a loss does not count yesterday's loss toward today's limit; tested.

---

## P2 — Signal quality and research

### P2-1 Backtest reporting
- Add Sortino, expectancy (R), average win/loss, max consecutive losses, exposure-matched B&H, trade-bootstrap
  95% CIs, and "not meaningful below 30 trades" for ratio metrics (B14). **Files:** `services/backtesting.py`,
  `static/js/app.js`. These are reporting-only changes: same trades, same verdict logic.

### P2-2 Regime breakdown and sensitivity (design period only)
- Regime labels from past data only (1d close vs SMA200 plus 200-day return sign → bull/bear/sideways; 4h ATR%
  percentile → high/low volatility). Metrics per regime.
- A sensitivity script (`scripts/sensitivity_study.py`) varies `buy_threshold`, `min_rr`, the time stop and the ATR
  stop multiple by ±20% on **design** data and writes a table. It is reported, never used to pick a "best" setting.

### P2-3 Simple-strategy research track (Phase 10)
- A separate, pre-registered study script (not the live engine): EMA trend following, a Donchian breakout, an RSI
  pullback in an uptrend, mean reversion, a volatility filter on B&H. Each has a hypothesis, fixed entry/exit/stop,
  costs, trade count and comparison to the baselines, on **design** data only. Results go to
  `data/strategy_study_design.md`. Only a strategy that clearly beats the baselines with ≥30 design trades may be
  proposed for **one** holdout test.

### P2-4 Signal explanation: confidence and timeframe alignment
- Confidence = data coverage × agreement × timeframe alignment, shown as its inputs, never as a probability;
  timeframe alignment table with a one-line reason for disagreement. Presentation only, so no engine version change.

### P2-5 News: point-in-time archive; news as a blocker only ⚑
- Store analysed events with their scores at collection time (an archive for future forward tests). Proposal for
  approval: news weight 0 in the score, keeping the fresh-news gate, until it has out-of-sample evidence (B8).

---

## P3 — UI/UX and usability
- Overview: add the daily trend, nearest support/resistance, the news bias and the provider of each data block.
- Signal view: the confidence and alignment blocks (P2-4); a visible "BUY SETUP ≠ trade authorised" line.
- Research view: default to *design*; holdout confirmation; per-regime table; CIs next to the ratios.
- Paper: "SIMULATED ACCOUNT" banner on every paper card; export of closed trades; suggested size from the risk
  calculator next to the order form (B12).
- Alerts: S/R reached, RSI extremes, MACD cross, daily trend change, signal invalidation (all deduplicated through
  the existing cooldowns, all worded as observations, never as executed trades).
- System: database backup button; optional rotating log file.
- Hygiene: untrack `data/*.png` and `prompt_*.md` (`git rm --cached`), de-duplicate `.gitignore`, update the
  `/api/mtf` note and the user agent version.

## P4 — Advanced / optional
- ML experiment script (logistic regression, gradient boosting) with walk-forward time splits on design data,
  calibration plots, and comparison with the baselines after costs. Adopted only if it beats them out-of-sample;
  otherwise the result is documented and nothing ships.
- Volatility forecasting (e.g. an EWMA or HAR model) for position sizing research.
- Assistant: add backtest-metric intents (P2-1 fields) with the same grounding checks.
- Coverage reporting (`pytest-cov`, dev-only).

---

## Proposed first implementation batch (awaiting approval)

**P0-1, P0-2, P1-1, P1-2, P1-3, P1-5, P1-6.** All are bug fixes or stricter safeguards; none changes the signal
score. P1-4 and P2-5 change live signal behaviour (⚑) and are proposed separately.
Each step: regression test first (red), fix (green), full suite, CHANGELOG entry.
