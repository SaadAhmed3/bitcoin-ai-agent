# Task: install and check Phase 5.4

You are working in my local project F:\bitcoin-ai-agent (BTC Signal Lab: Flask + SQLite + pandas, Windows 11, Python 3.13, virtual env at .venv). I have a new build, bitcoin-ai-agent-phase5.4.zip.

Phase 5.4 changes the trade-plan targets:
- T1 = the lower edge of the nearest MAJOR resistance (2+ touches) above the live price, however close.
- T2 = the next major barrier.
- Minor (1-touch) levels before T1 are listed as obstacles.
- When the setup is downgraded, the note names the close barrier.
- "1 touches" now reads "1 touch".

This file is the complete task. Ignore any earlier, partial request.

## Step 1 - Install
- Find bitcoin-ai-agent-phase5.4.zip in $HOME\Downloads. If it is missing, stop and ask me.
- Check the zip contains no .env, .venv or *.sqlite3.
- Back up the current project (excluding .venv and data) to F:\bitcoin-ai-agent-backup-<timestamp>.
- Copy the zip's bitcoin-ai-agent\ contents over F:\bitcoin-ai-agent, excluding .venv, .env and data\*.sqlite3.
- Confirm every file matches the zip by hash.
- Leave the prompt_*.md files in the project folder alone; they are not part of the zip.

## Step 2 - Tests
- Activate .venv and run: python -m pytest
- Expected: 257 passed. Show the summary line.
- If anything fails, show the full failure and its cause. Never weaken or delete tests.

## Step 3 - Live check
- Start the app in the background: $env:APP_PORT="5050"; python app.py
- Confirm /api/health shows version 0.5.4 and /api/signal shows engine signal-1.4.
- Save /api/signal and /api/analysis?tf=4h to your scratchpad.
- Screenshot the #signal section with Playwright at 1440x900, waiting for the selector #signal[data-state=ready]. Save it to data\phase54_signal.png and capture console errors.
- Stop the server and confirm nothing is listening on 5050.

## Step 4 - Confirm the targets fix (one line each: fixed / not fixed, with evidence)
If the setup is BUY, use the plan from /api/signal. Otherwise run build_buy_plan read-only on the live 4h analysis with the live price. Report:

1. **T1 is the nearest major barrier.**
   - Show T1's price and basis.
   - List every resistance above the live price with its low-high range, touch count and minor flag.
   - T1 must be the lower edge of the first non-minor one, even if it is closer than 1 ATR.
2. **T2 is the next major barrier.** Show T2's price and basis. It must be the next major resistance at least 0.5 ATR above T1, otherwise a minor level or an ATR projection.
3. **Obstacles.** Show obstacles_before_t1 and t1_distance_atr. Any minor resistance between the live price and T1 must be listed.
4. **R/R and downgrade note.**
   - Show rr_after_costs and rr_mid_zone for T1 and T2.
   - If T1's rr_after_costs is below 1.5 and the setup would be a buy, the downgrade note should name the barrier and its ATR distance. If the live setup is not a buy, say what the note would contain based on the plan values.
5. **Grammar.** No "1 touches" anywhere in /api/signal or the panel.

## Step 5 - Anything new
Anything a trader would notice in the panel or the numbers that looks wrong or confusing.

## Rules
- Report only: don't modify code, config or signal settings (no PUT calls).
- Never fabricate output; show real errors.

## Final summary (required)
End with a short summary:
- test result;
- live signal, setup, score and BTC price;
- status of the 5 checks;
- any new issues;
- the screenshot path.
