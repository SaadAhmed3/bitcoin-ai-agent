# Test Results

## Baseline — before any change (2026-10-30, commit `1d23a89`)

Environment: Windows 11, project virtual environment `.venv`, Python 3.14.6, pytest 9.2.0, Playwright 1.64.1.
(The system `python` has no pytest installed; always use `.venv\Scripts\python.exe`.)

| Suite | Command | Passed | Failed | Skipped | Deselected | Time |
|---|---|---|---|---|---|---|
| Unit / integration | `.venv\Scripts\python.exe -m pytest -q` | **493** | 0 | 0 | 81 (e2e) | 129 s |
| Browser (Playwright) | `.venv\Scripts\python.exe -m pytest -m e2e -q` | **81** | 0 | 0 | 493 | 237 s |
| Dependency audit | `.venv\Scripts\python.exe -m pip_audit -r requirements.txt` | No known vulnerabilities | | | | |

**Coverage:** not measured. `pytest-cov` is not installed and adding it was not part of the audit. Adding it
(`pip install pytest-cov`, then `pytest --cov=services --cov=app --cov-report=term-missing`) is proposed in the
roadmap as a development-only dependency.

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

## After changes

_Not yet run: no code has been changed. This section will list every phase's results._
