# Test Results

## Baseline — before any change (2026-10-30, commit `1d23a89`)

Environment: Windows 11, project virtual environment `.venv`, Python 3.14.6, pytest 9.2.0, Playwright 1.64.1.
(The system `python` has no pytest installed; always use `.venv\Scripts\python.exe`.)

| Suite | Command | Passed | Failed | Skipped | Deselected | Time |
|---|---|---|---|---|---|---|
| Unit / integration | `.venv\Scripts\python.exe -m pytest -q` | **493** | 0 | 0 | 81 (e2e) | 129 s |
| Browser (Playwright) | `.venv\Scripts\python.exe -m pytest -m e2e -q` | **81** | 0 | 0 | 493 | 237 s |
| Dependency audit | `.venv\Scripts\python.exe -m pip_audit -r requirements.txt` | No known vulnerabilities | | | | |

**Coverage:** not measured during the audit. Measured afterwards on the same commit (`be5370d`, in a separate
worktree) once `pytest-cov` was installed: **93.5%** of statements (see the table below).

### Test files (unit)

`test_app_and_db`, `test_assistant`, `test_assistant_71`, `test_backtesting`, `test_entry_modes`,
`test_http_client`, `test_market_data`, `test_news`, `test_news_regressions`, `test_phase8`,
`test_risk_and_paper`, `test_score_study`, `test_security`, `test_signal_engine`, `test_technical_analysis`,
`test_ui_docs`. Browser: `tests/e2e/test_dashboard_e2e.py`, `tests/e2e/test_views_e2e.py`.

### Audit reproduction scripts (not part of the suite)

Run from the repository root against a **temporary** database; they do not touch `data/btc_agent.sqlite3`
(the EMA check opens it read-only).

| Script (kept in the session scratchpad, not committed) | Result |
|---|---|
| `race_check.py` — two concurrent `check_levels` at a price below the stop | **Bug B1 reproduced:** 2 SELL orders, 79.96 BTC sold from a 39.98 BTC position |
| `config_check.py` — `SignalService.update_config` bounds | **Bug B2 reproduced:** negative fee/slippage, `min_rr = -1`, `min_agreement = 5`, `buy_threshold = 9` accepted |
| `ema_window_check.py` — 500-bar window vs full history | EMA200 max diff 0.134% (4h) / 0.275% (1d); RSI and ADX identical |

These become regression tests in the first implementation phase (see the roadmap).

### Known limitations of the current suites

- No concurrency tests for the paper account (B1).
- No range tests for engine settings (B2).
- Backtest tests use synthetic candles; there is no golden-file test pinning the metrics of a small real-data replay.
- Network providers are always faked (correct for unit tests); `scripts/check_sources.py` is the manual live check.
- Unit tests take ~2 minutes, mostly in backtest replays.

## After batch 1 (0.9.2, branch `batch1-safety-fixes`, 2026-10-30)

Every fix was test-first: the new regression test was run on the old code and failed, then the fix made it pass, then
the full unit and e2e suites ran. No test was removed or skipped.

| Commit | Fix | Red on old code | Unit | E2E |
|---|---|---|---|---|
| `c08aa51` | (a) atomic paper orders; concurrent closes sell once | 2 SELLs recorded | 495 ✔ | 80 ✔, 1 ✘ (pre-existing race, next row) |
| `8272165` | (extra) atomic source-health UPSERT | `UNIQUE constraint failed` | 496 ✔ | 81 ✔; flaky test 3/3 ✔ |
| `f276799` | (b) server-side range validation + UI | 14 unit + 4 e2e cases failed | 513 ✔ | 85 ✔ |
| `a034958` | (c) daily loss = move since 00:00 UTC | 5.55% reported vs 0.53% real | 517 ✔ | 85 ✔ |
| `93cc83a` | (d) calculation error → NO TRADE | HTTP 500 | 519 ✔ | 85 ✔ |
| `29e02ea` | (e) stale manual SELL refused | SELL filled | 520 ✔ | 85 ✔ |
| `e6b2d19` | (f) background stop/target check | 0 SELLs from the monitor | 522 ✔ | 85 ✔ |
| `a610b86` | (g) holdout-only validation + registry | full run validated; failed holdout re-run into a pass | 535 ✔ | 85 ✔ |

**Final:** unit **535 passed, 0 failed, 0 skipped**; e2e **85 passed, 0 failed, 0 skipped**. (Final re-run after the
version bump: 535 passed, 85 e2e passed, 0 skipped.)

**Pre-existing flaky test found and fixed:** `test_no_placeholder_text_or_console_errors_in_any_view_with_an_empty_database`
failed 2 of 3 runs on the untouched `be5370d` (source-health insert race). The 81/81 audit baseline passed by chance.

**Tests changed deliberately for new rules (not weakened):** `test_risk_ceiling_and_validation` (risk above the
ceiling is rejected instead of capped); `test_validation_record_unlocks_signal_only_for_matching_settings`,
`test_forward_observation_gate_lasts_56_days` and `test_backtest_service_records_validation_and_unlocks_signal` now use
a first-holdout record or run (a full run can no longer validate); `test_existing_database_upgrades_to_chat_table`
expects schema 7; `test_app_version_is_the_batch_1_release` expects 0.9.2.

### Coverage (unit suite; `--cov=app --cov=config --cov=services --cov=database`)

| File | Before (be5370d) | After (0.9.2) |
|---|---|---|
| app.py | 90% | 93% |
| database/db.py | 95% | 97% |
| services/backtest_service.py | 91% | 92% |
| services/backtesting.py | 96% | 96% |
| services/paper_trading.py | 93% | 92% |
| services/risk_manager.py | 99% | 99% |
| services/signal_engine.py | 98% | 99% |
| services/signal_service.py | 94% | 93% |
| services/market_data.py | 82% | 82% |
| services/history.py | 21% | 21% |
| **Total** | **93.5%** (316 of 4,868 statements missed) | **93.9%** (307 of 5,053 missed) |

Uncovered new lines are mostly defensive: the compare-and-set conflict branch in `paper_trading._swap` (practically
unreachable under `BEGIN IMMEDIATE`, kept as a database-level guard), "account missing" in `buy`, and the daily-loss
branch for an open position with no current price. `history.py` (the Binance history downloader) is exercised only
by the manual download script; it is network code with no unit tests (unchanged).

## After batch 2 (0.9.5, signal-1.8, branch `batch2-data-checks`, 2026-10-31)

| Commit | Change | Red on the previous code | Unit | E2E |
|---|---|---|---|---|
| `6f46d77` | GET /api/paper read-only (B16) | a GET sold the position | 554 ✔ | 87 ✔ |
| `1d0b210` | 1h stale/gap gate; engine signal-1.8 | stale 1h gave BUY SETUP | 556 ✔ | 87 ✔ |
| `d5c28a8` | cross-source price check | 0.77% apart gave only 'not_validated' | 565 ✔ | 87 ✔ |
| `f084955` | news out of the score, block tiers, weight migration notice | 10 of 16 news tests, e2e notice | 581 ✔ | 88 ✔ |
| `5af977c` | README rewrite, USER_GUIDE.md | /docs/USER_GUIDE.md 404 | 582 ✔ | 88 ✔ |
| `c6355b3` | stale validation reasons | "re-run it" wording | 583 ✔ | 88 ✔ |

**Final:** unit **583 passed, 0 failed, 0 skipped**; e2e **88 passed, 0 failed, 0 skipped**. Coverage **94.0%**
(315 of 5,246 statements missed; 93.9% after batch 1).

**Design-period equivalence (0.9.4 vs signal-1.8):** decisions 2022-10-30 15:59 → 2025-10-28 19:59 UTC, only candles
closing before the 2025-10-29 holdout start loaded: 6,566 bars compared, 0 differences in setup, raw setup, tradable,
unrounded score, stop, target, downgrade or gate keys; the same 5 trades (+9.08%). Scripts: `equiv_replay.py`
(session scratchpad, not committed).
