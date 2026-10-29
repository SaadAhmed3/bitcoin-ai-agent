# Task: install Phase 6 and run the first real backtest

You are working in my local project F:\bitcoin-ai-agent (BTC Signal Lab: Flask + SQLite + pandas, Windows 11, Python 3.13, virtual env at .venv). I have a new build, bitcoin-ai-agent-phase6.zip.

Phase 6 adds:
- a historical candle downloader (Binance public API, no key);
- a walk-forward backtest that replays the live signal engine bar by bar with no look-ahead;
- baselines (buy-and-hold, EMA 50/200, random entries);
- fixed validation criteria that decide whether the NO TRADE gate lifts;
- a risk/position-size calculator;
- a SIMULATED paper-trading account.

This file is the complete task. Ignore any earlier, partial request.

## Step 1 - Install
- Find bitcoin-ai-agent-phase6.zip in $HOME\Downloads. If it is missing, stop and ask me.
- Check the zip contains no .env, .venv or *.sqlite3.
- Back up the current project (excluding .venv and data) to F:\bitcoin-ai-agent-backup-<timestamp>.
- Copy the zip's bitcoin-ai-agent\ contents over F:\bitcoin-ai-agent, excluding .venv, .env and data\*.sqlite3. Confirm every file matches the zip by hash.
- Leave the prompt_*.md files in the project folder alone.

## Step 2 - Tests
- Activate .venv and run: python -m pytest
- Expected: 294 passed (this takes about 30-60 seconds). Show the summary line.
- If anything fails, show the full failure and its cause. Never weaken or delete tests.

## Step 3 - Download history
- Run: python scripts\download_history.py --years 5.5
- Show the full output.
- Expected: three [OK] lines (1d, 4h, 1h) with candle counts and a missing-candle count. Report any FAIL exactly as printed.

## Step 4 - Run the 4-year backtest
- Start the app in the background: $env:APP_PORT="5050"; python app.py
- Confirm /api/health shows version 0.6.0.
- Start the run with POST /api/backtest/run and the body {"years": 4, "download": false} (history is already downloaded).
- Poll GET /api/backtest/status every 30 seconds until state is "done", "error" or "cancelled". It should take roughly 5-15 minutes. Report the elapsed time.
- If the state is "error", show the error and stop.
- Save GET /api/backtest/latest to your scratchpad as backtest_latest.json.

## Step 5 - Report the results (from backtest_latest.json)
1. The verdict, the period (start, end, years, bars), the engine and the config_hash.
2. Every criterion: name, passed or failed, and its detail.
3. A table: signal engine (default weights), weights chosen on the prior year, buy & hold, EMA 50/200. Columns: net return %, max drawdown %, return/drawdown, trades, exposure %. Below the table, the random-entry median and the 10th-90th percentile range.
4. Strategy details: profit factor, win rate, average trade %, average R, average bars held, costs paid, exit reasons.
5. Hit rate: base rate, number of signals, precision, recall, balanced accuracy.
6. The walk-forward windows table (dates; default return and trades; chosen weights and return; buy & hold), plus windows_with_trades / windows_total.
7. The last 10 trades: entry and exit dates, prices, reason, net %, R.

## Step 6 - Live checks with the app still running
1. GET /api/backtest/status: show the validation object. Its validated value must match the backtest verdict.
2. GET /api/signal: report signal, setup and no_trade_reasons. If the backtest was NOT validated, the "no validated edge" reason must still be there. If it WAS validated, that reason must be gone.
3. POST /api/risk/size with {"portfolio_value": 10000, "entry": <live price rounded>, "stop": <live price minus 2%>}. Report quantity, notional, max_loss_at_stop, max_loss_pct, notes and warnings. The max loss must be about 1% of 10,000 (100 USDT).
4. Paper trading, all SIMULATED:
   - GET /api/paper (show cash, equity and settings);
   - POST /api/paper/order {"side": "BUY", "usd": 500};
   - GET /api/paper;
   - POST /api/paper/order {"side": "SELL"};
   - POST /api/paper/reset {"start_balance": 10000}.

   Report each message. Every order must be labelled SIMULATED.
5. Screenshot the #backtest and #risk sections with Playwright at 1440x900, waiting for #signal[data-state=ready] and then for "#btResult .verdict". Save them to data\phase6_backtest.png and data\phase6_risk.png, and capture console errors.
6. Stop the server and confirm nothing is listening on 5050.

## Step 7 - Review as a quant / trader (most important)
a) Do the results look plausible? Check: buy & hold return vs BTC's actual move over the period; the number of trades; the exposure; exit reasons dominated by stops vs targets vs time stops.
b) Look for look-ahead or simulation errors in the trades list. For example: entries at prices outside that 4h candle's range; exits at a target the candle never reached; trades overlapping in time.
c) Is "not validated" (or "validated") a fair conclusion from the numbers? Which criteria decide it?
d) Anything in the backtest, risk or paper panels that a trader would find wrong or confusing.

## Rules
- Do not change code, config or signal settings (no PUT /api/signal/config). Running the backtest and the simulated paper orders above is expected.
- Never fabricate output; show real errors.

## Final summary (required)
End with a short summary:
- test result;
- history downloaded (candle counts);
- backtest time taken;
- verdict and how many criteria passed;
- strategy vs buy & hold return and drawdown;
- the live signal;
- issues found in Step 7;
- the screenshot paths.
