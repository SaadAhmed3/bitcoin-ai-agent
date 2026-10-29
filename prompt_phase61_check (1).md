# Task: install Phase 6.1 and report the signal funnel

You are working in my local project F:\bitcoin-ai-agent (BTC Signal Lab: Flask + SQLite + pandas, Windows 11, Python 3.13, virtual env at .venv). I have a new build, bitcoin-ai-agent-phase6.1.zip.

This file is the complete task. Ignore any earlier, partial request.

Phase 6.1 does not change the trading rules. It adds:
- signal engine signal-1.6: the same scoring and plans as 1.5, plus recorded diagnostics (the setup before any downgrade, why it was downgraded, R/R to T1, and which safeguards fired);
- a **funnel** in the backtest report that shows where potential trades are lost;
- the fixes from your Phase 6 review:
  1. the walk-forward table explains why the defaults were used;
  2. criteria that pass on too few trades are greyed out as "not meaningful";
  3. the NO TRADE reason quotes the actual backtest result;
  4. the paper account shows avg_fill and cost_basis separately (adds a database column through an automatic migration);
  5. sections no longer hide under the sticky top bar;
  6. "Download history" is ticked only when the stored history is missing or old.

The engine version changed, so the stored validation from run #1 becomes stale. The signal must still say NO TRADE.

## Step 1 - Install
- Find bitcoin-ai-agent-phase6.1.zip in $HOME\Downloads. If it is missing, stop and ask me.
- Check the zip contains no .env, .venv or *.sqlite3.
- Back up the current project (excluding .venv, but INCLUDING data\*.sqlite3 this time, because the schema migrates) to F:\bitcoin-ai-agent-backup-<timestamp>.
- Copy the zip's bitcoin-ai-agent\ contents over F:\bitcoin-ai-agent, excluding .venv, .env and data\*.sqlite3. Confirm every file matches the zip by hash.
- Leave the prompt_*.md files alone.

## Step 2 - Tests
- Activate .venv and run: python -m pytest
- Expected: 299 passed. Show the summary line. If anything fails, show the full failure and its cause. Never weaken or delete tests.

## Step 3 - Start and check before re-running
- Start the app in the background: $env:APP_PORT="5050"; python app.py
- /api/health must show version 0.6.1.
- GET /api/backtest/status: show the validation object. It should show stale = true (run #1 was engine signal-1.5).
- GET /api/signal: report signal, setup, no_trade_reasons and the diagnostics object. Every no_trade reason must be current, with no "(walk-forward backtest is Phase 6)" text. The validation reason must say re-run because the settings/engine changed.
- GET /api/paper: confirm it loads after the migration and has avg_fill and cost_basis fields.

## Step 4 - Re-run the 4-year backtest
- POST /api/backtest/run with {"years": 4, "download": false}.
- Poll GET /api/backtest/status every 30 seconds until state is done, error or cancelled. Report the elapsed time.
- Save GET /api/backtest/latest to your scratchpad as backtest_61.json.
- Results must match run #1 (6 trades, +7.52%), because the rules did not change. If they differ, report the trades that differ. That is a bug to explain, not to fix.

## Step 5 - Report (from backtest_61.json)
1. Verdict, engine, config_hash, and criteria passed/failed (one line each).
2. **The full funnel object, every field, verbatim as JSON.**
3. The funnel as a short table: bars → score ≥ buy threshold → raw buy setups → dropped for low R/R → dropped for no plan → after the R/R check → blocked by each safeguard → tradable buy bars → trades.
4. low_rr_values (median, p75, share ≥ 1.0, share negative) and what_if_min_rr.
5. The walk-forward windows with chosen_reason.

## Step 6 - Paper and UI checks
1. Paper (all SIMULATED): POST /api/paper/order {"side":"BUY","usd":500}, then GET /api/paper. Confirm avg_fill equals the fill price in the message, and cost_basis is about avg_fill × 1.001. Then SELL, then POST /api/paper/reset {"start_balance":10000}.
2. Playwright at 1440x900: open the page, wait for #signal[data-state=ready] and "#btResult .verdict".
   - Click the top-bar or nav link that jumps to #backtest (or call location.hash='#backtest' and scroll). Confirm the section heading is not hidden under the sticky bar.
   - Check the "Download/refresh history first" checkbox is UNticked (history is current).
   - Screenshot #backtest to data\phase61_backtest.png. Capture console errors.
3. Stop the server and confirm nothing is listening on 5050.

## Step 7 - Analysis as a quant (most important; do not change anything)
Using the funnel, answer:
a) Where is the main bottleneck: the score threshold, the R/R rule, or the safeguards? Give the numbers.
b) For setups dropped for low R/R: is it because T1 (nearest major resistance) is very close, i.e. R/R mostly below 1.0 or negative? Or are they near-misses (1.2–1.5)?
c) Propose at most 3 candidate rule changes that address the bottleneck **for reasons a trader would accept on their own**, not because they increase the backtest number. For each one, say how it could be overfitting.
d) Propose a fair test protocol: e.g. decide rules using only data up to a cut-off date, then test once on the last ~12 months held out.

## Rules
- Do not change code, config or signal settings (no PUT /api/signal/config). Running the backtest and the simulated paper orders is expected.
- Never fabricate output; show real errors.

## Final summary (required)
- test result;
- whether results match run #1;
- the funnel in one line (e.g. 8760 → X → Y → ... → 6);
- the main bottleneck;
- your 3 candidate changes;
- the UI checks (scroll, checkbox, paper fields);
- the screenshot path.
