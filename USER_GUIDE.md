# BTC Signal Lab — User Guide

This guide is for someone who has never used the app. It explains how to install it on Windows, what every screen
shows, and how to read the signal. Commands are for **Windows PowerShell**. Copy them line by line.

> **Important.** BTC Signal Lab is a research and learning tool. It never trades with real money and cannot connect
> to an exchange account. The "paper" account is a simulation. Nothing in the app is financial advice.

**Contents**
1. [Installation](#1-installation)
2. [Starting and stopping](#2-starting-and-stopping)
3. [A tour of the dashboard](#3-a-tour-of-the-dashboard)
4. [Understanding the signal](#4-understanding-the-signal)
5. [Understanding NO TRADE](#5-understanding-no-trade)
6. [Backtesting](#6-backtesting)
7. [Paper trading (simulated)](#7-paper-trading-simulated)
8. [The risk calculator](#8-the-risk-calculator)
9. [Alerts and the assistant](#9-alerts-and-the-assistant)
10. [Troubleshooting](#10-troubleshooting)
11. [Data-source limitations](#11-data-source-limitations)
12. [Your data and backups](#12-your-data-and-backups)
13. [Glossary](#13-glossary)

---

## 1. Installation

You need Windows 10 or 11, an internet connection and about 1.5 GB of free disk space. Everything is free.

### 1.1 Install Python (once)

Open **PowerShell** (Start menu → type "PowerShell" → Enter) and check:

```powershell
python --version
```

You need **Python 3.12 or newer**. If you see an older version or an error, install Python:

```powershell
winget install Python.Python.3.13
```

or download it from <https://www.python.org/downloads/> and tick **"Add python.exe to PATH"** in the installer.
Close PowerShell and open a new window afterwards, then run `python --version` again.

### 1.2 Put the project in a folder

If you received a zip file, unzip it to `C:\Users\<you>\Projects\bitcoin-ai-agent`. Then go there:

```powershell
cd $HOME\Projects\bitcoin-ai-agent
```

(If you use Git: `git clone <repository address> $HOME\Projects\bitcoin-ai-agent`.)

### 1.3 Create a private Python environment (once)

A "virtual environment" keeps the app's packages separate from the rest of your computer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Your prompt now starts with `(.venv)`. If you see **"running scripts is disabled on this system"**, allow local
scripts for your user once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### 1.4 Install the packages (once, and after each update)

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

This takes a few minutes the first time.

### 1.5 Optional: your own settings file

```powershell
Copy-Item .env.example .env
notepad .env
```

You can change the port, your display time zone (`DISPLAY_TIMEZONE`, e.g. `Europe/London`) and a few other things.
You do not need to change anything to get started. Never share your `.env` file.

### 1.6 Optional: the local AI assistant

The app answers questions without AI. If you want answers rewritten in plainer language by a small AI model that
runs **on your own computer**:

```powershell
winget install Ollama.Ollama
```

Close and reopen PowerShell, then:

```powershell
ollama pull qwen2.5:3b        # about 2 GB, once
ollama list                   # qwen2.5:3b should be listed
```

Then turn on **Settings → Assistant → AI explanations**.

---

## 2. Starting and stopping

Every time you want to use the app:

```powershell
cd $HOME\Projects\bitcoin-ai-agent
.\.venv\Scripts\Activate.ps1
python app.py
```

Leave this window open: the app runs while it is open. Then open your browser at **http://127.0.0.1:5000**, or:

```powershell
Start-Process "http://127.0.0.1:5000"
```

To stop the app, click the PowerShell window and press **Ctrl + C**.

The app is only reachable from your own computer (127.0.0.1). It checks simulated stops, alerts and data every
minute **while it is running**; nothing happens while it is closed.

---

## 3. A tour of the dashboard

The menu on the left switches between views. Labels on each card tell you what kind of number it is:
**LIVE** (from an exchange now), **CALCULATED** (computed by the app), **MODEL ESTIMATE** (the signal),
**HYPOTHETICAL** (backtests) and **SIMULATED** (paper account).

| View | What it is for |
|---|---|
| **Overview** | The price, 24-hour change, the current signal with its main reason, the chart, recent alerts, the next important US economic event and your watchlist. The top bar shows how old the data is ("Updated 5 s ago"); it turns amber when data is stale and red when no price source answers. |
| **Market & chart** | Candles from 5 minutes to 1 day, with optional lines: moving averages (EMA), Bollinger Bands, VWAP, support and resistance, your alert prices, past signals and a volume profile. Below: indicator readings, the **Advanced market analysis** card (see [Context panels](#context-panels-regime-sweeps-volume-profile)) and a table that compares the timeframes. |
| **News & calendar** | Headlines grouped into events. Each shows whether it reads bullish or bearish, how important it is, whether it is **confirmed**, **unverified** (single source or "reportedly") or **speculative** (opinion, forecast, question), and whether it is fresh or probably already priced in. |
| **Signal details** | The full reasoning behind the signal: every NO TRADE reason, evidence for and against, each component's score, a research trade plan and its risk. |
| **Research (backtest)** | Test the signal on past data. See [Backtesting](#6-backtesting). |
| **Risk & paper** | The risk calculator and the simulated paper account. |
| **Alerts** | Your alert rules and the alert history. |
| **Ask the data** | Ask questions such as "Why is it NO TRADE?". Answers come from the app's own data and list their sources. |
| **System & sources** | Which data sources are working, versions, and these documents. |
| **Strategy Lab** | Five research strategies side by side (DCA active; four planned), their validation status, an unranked comparison and DCA settings. See [Strategy Lab](#strategy-lab). |
| **Settings** (in the menu) | Refresh intervals, alerts, signal settings, risk settings, advanced analysis (shadow mode), watchlist. Values set in `.env` are shown read-only. |

---

## 4. Understanding the signal

The signal is a **research summary**, not an instruction. It has five possible values:

| Signal | Meaning |
|---|---|
| **STRONG BUY SETUP** | Most of the evidence points up and it agrees with itself. |
| **BUY SETUP** | The evidence leans up, and there is enough room to the first target to be worth the risk. |
| **HOLD** | No clear direction, or not enough reward for the risk. |
| **SELL / REDUCE EXPOSURE** | The evidence leans down. It does **not** mean "sell everything". |
| **NO TRADE** | A safety rule says the app should not offer a setup now. See the next section. |

**A BUY SETUP does not mean a trade is authorised.** It means the indicators lean up. The app only shows it as
actionable when every safety rule is satisfied, and so far the strategy has not been validated, so the app shows
NO TRADE.

### The score

The score runs from **−1** (all evidence bearish) to **+1** (all evidence bullish). It combines five parts:

| Part | Share of the score | In plain words |
|---|---|---|
| Trend | 35% | Is the price in a rising or falling pattern on the 4-hour and daily charts? |
| Momentum | 29% | Is the move gaining or losing strength (RSI, MACD, ADX)? |
| Volume | 12% | Is more or less trading than usual behind the latest candle? |
| Structure | 12% | Breakouts, failed breakouts, nearby support/resistance. |
| Overextension | 12% | A brake: is the price stretched too far, too fast? |

**The score is not a probability.** A score of +0.40 does not mean a 40% or 70% chance of anything. So far, tests
have found no reliable link between the score and later price moves (see the [results](#64-what-the-results-mean)).

**News is not part of the score.** News headlines were never part of any backtest, so since engine signal-1.8 they
cannot push the score up or down. Important news can only **block** a signal (next section).

### Other numbers on the Signal page

- **Agreement**: how much of the weighted evidence points the same way as the score.
- **Reward/risk**: the distance to the first target divided by the distance to the stop, after fees and
  slippage. A BUY setup needs at least 1.5.
- **Invalidation**: what would prove the setup wrong (for example a 4-hour close below the stop).
- **Horizon**: the signal looks 1-3 days ahead.

---

### Context panels (regime, sweeps, volume profile)

The **Advanced market analysis** card on Market & chart, and the grey line under the signal that starts with
"Context, not part of the score", describe the market. **They do not change the signal**: not its score, not its
weights, not NO TRADE. They use closed candles only, so a value never changes after the candle that produced it.

- **Market regime** (4-hour candles): is Bitcoin trending up, trending down, ranging, unusually volatile, unusually
  quiet, or in between? The confidence (0-100) counts how many checks agree; it is **not** a probability. "Evidence"
  and "Conflicting evidence" list the checks. The app uses its existing EMA 21 where some guides use EMA 20.
- **Liquidity sweeps**: price pokes above a previous swing high (buy-side) or below a swing low (sell-side) and closes
  back inside. It starts as **potential**; the next candles make it **confirmed**, **failed** (price stayed beyond the
  level), **reclaim confirmed** or **no confirmation**. The strength (0-100) is a heuristic. A sweep is never a buy or
  sell signal.
- **Volume profile**: where most volume traded in the last 200 closed 4-hour candles (choose 1h or 1d in the panel).
  **POC** is the busiest price, **VAH/VAL** bound the 70% value area, high/low-volume nodes are busy/thin areas. Tick
  "Volume profile" above the chart to draw it. Candles do not record the price of every trade, so the volume of each
  candle is spread evenly over its range: an approximation.

**On the chart** (Market & chart, tick *Market regime*, *Liquidity sweeps* or *Volume profile* above it): faint
background colours show the 4-hour regime (green trending up, red trending down, grey ranging, amber unusually
volatile; a `|` at the top marks a change), arrows show stored sweeps at the candle that made the high or low (filled =
confirmed, open amber = potential, open grey = failed), and the volume profile sits on the right edge. The profile
uses the last 200 candles of the chart, or choose "visible range" to follow your zoom. All of it is context only.

**Shadow mode** (Settings → Advanced analysis) stores these three readings every 4 hours next to the live signal, so
that one day they can be tested on data nobody has seen yet. Turning it off stops the storing; the panels stay.

### Strategy Lab

**Strategy Lab** (in the menu) shows five research strategies side by side. Each card shows its current research
signal, the data it used, its costs and its **validation status**, which always says how it did on design data
(for example "FAILED on design data · not validated"). DCA (buying a fixed amount on a schedule), **trend following**
(buying 4-hour trend pullbacks) and **range trading** (buying near the bottom of a sideways range) are active so far;
the others say "planned". Use the picker in the details card to see each one's backtest, forward test, settings and history.

- **DCA** says ACCUMULATE on a scheduled buy day, WAIT on other days and PAUSE when the price data is not trustworthy.
  It does not try to time the market, and nothing is ever bought.
- **Trend following** says BUY SETUP (with entry, stop and target) only when the 4-hour trend is confirmed and price
  resumes after a pullback, HOLD while the trend holds without a new entry, EXIT / REDUCE EXPOSURE when the trend
  breaks and NO TRADE otherwise. Its design backtest failed its pre-registered criteria, so every setup it shows
  says "Failed on design data: research only". Nothing is ever traded.
- **Range trading** only looks for setups when the 4-hour market is sideways: BUY SETUP near the range bottom (stop
  below the range, target the middle), EXIT / REDUCE EXPOSURE when the market stops being sideways. On design data it
  found only 4 trades in over four years, too few to judge (INCONCLUSIVE).
- The **comparison table** does not rank strategies. N/A means there is no valid number yet.
- **Settings** can be changed in the DCA details. Saving creates a new version; if it differs from the
  pre-registered settings, the strategy shows **NOT VALIDATED (settings changed)** and its earlier results no longer
  apply. "Reset" goes back to the pre-registered settings.
- **Chart tab** (in the details card): the strategy's own chart. When there is a setup it draws the entry, the stop
  (red) and the target (green); when there is none, a box lists each rule with ✓ (met) or ✗ (not met), so you can see
  why. Below: every trade of the design backtest (click one in the list to zoom to it), the account against buy &
  hold, the drawdown, and the forward test once it has data. Each chart has a short "How to read this chart" note.
- "BACKTESTED (design data)" is evidence about the past only. A strategy can only become "forward tested" after its
  own 365-day forward test.

## 5. Understanding NO TRADE

NO TRADE means: *the app will not offer a setup right now.* It is a safety decision, and it is shown with every
reason that applies. You will see it most of the time, which is intended.

| Reason you might see | What it means | What you can do |
|---|---|---|
| "Not validated: the holdout from 2025-10-29 is contaminated …; validation now needs forward data" | The holdout's results were already seen (a full backtest showed them), so it cannot test the strategy. | Nothing to fix: this is the main reason today. Validation needs new data: see [Backtesting](#6-backtesting) and Research → Forward tests. |
| "BTC price is stale" / "No current BTC price" | The exchange has not answered recently. | Check your internet; see System & sources. |
| "Price sources disagree by 0.8% after USDT-to-USD conversion …" | Binance (in USDT, converted to USD with the USDT-USD rate) and Coinbase (in USD) show different prices; one may be wrong or delayed. The raw prices, the rate and the gap are shown. | Wait; it usually clears within minutes. The limit (default 0.5%) is in Settings → Signal. |
| "1h candles are stale …" / "… missing candles" | Some chart data is old or has holes. | Usually clears on the next update. |
| "Volatility is extreme" | Prices are moving much more than usual. | Wait for calmer conditions. |
| "Evidence is contradictory" | The indicators disagree with each other. | Wait. |
| "Buy setup fights the daily downtrend" | The short-term picture points up but the daily trend points down. | Wait. |
| "Fresh / Recent high-impact bearish news (confirmed, …)" | Important bad news against a buy setup. The headline, publisher and link are shown. **Confirmed** news blocks for up to 24 hours (unless the price already moved); **unverified** or **speculative** news blocks for 2 hours. | Read the article; wait until it is digested. |
| "Calculation error in …" | Something went wrong while computing. The app refuses to guess. | Restart the app; if it repeats, check the PowerShell window for the error. |

"Price not cross-checked: …" in the data warnings means the second exchange did not answer, and "Price cross-check not
converted to USD" means the USDT-USD rate was missing or too old. Both are warnings only; they do not block.
"Stablecoin deviation: USDT-USD is 0.9850 …" means USDT is more than 1% away from 1 US dollar. That is worth knowing
(USDT prices are then less reliable as dollar prices), but it does not block by itself.

---

## 6. Backtesting

A backtest replays the signal on past prices, candle by candle, as if it had been running live, and simulates the
trades it would have made (including fees and slippage). Results are **hypothetical**.

### 6.1 Running one

1. Open **Research (backtest)**.
2. Choose a **Period** (below) and an **Entry rule** (start with *standard*).
3. Keep **Download/refresh history first** ticked the first time (it fetches up to a few years of free Binance data).
4. Click **Run backtest**. A 4-year run takes about 5-10 minutes.

### 6.2 The three periods

| Period | What it is | Can it validate the strategy? |
|---|---|---|
| **design** | History up to the holdout start. Use it to explore. | Never |
| **full** | Everything. An overview that includes the design data. | Never |
| **holdout** | A reserved period whose results nobody has seen. | Only the **first** holdout run, and only if the period is not contaminated |

The holdout is **single-use**: the first holdout run, whatever its rules, uses it up ("spent"). Later holdout runs
are stored but marked "not valid for validation".

A holdout is also used up the moment its results are **seen** some other way. A **full** run shows performance
through the holdout period, so the app marks that period **contaminated** and no run in it can ever count. This has
happened to the original holdout (from 2025-10-29): full run #1 showed the signal and the simple baselines over that
year. The Research view therefore shows **Holdout status: contaminated (seen via full run #1 on <date>), cannot
validate**.

So any rule set can now only be validated on **forward** data, data that did not exist when the rules were written:

- the **Forward tests** panel in Research (H1, H2 and buy & hold, judged after 365 days), or
- a **new holdout start after today**, set in the box that appears under the holdout status. Then wait for enough
  new data, and do not run a **full** backtest in the meantime: one that reaches into the new period contaminates it
  too.

The app also records a fingerprint of the rule code and the PASS/FAIL verdict at the time of the run. Editing the
code or relaxing the pass criteria later cannot turn a failed test into a pass.

### 6.3 What a validation needs

All of: at least 30 trades (15 on the holdout), profit factor ≥ 1.2, a positive return after costs, at most 25%
drawdown, beating 90% of random-entry runs, beating a simple EMA 50/200 strategy on return per unit of drawdown, and
being positive in at least 60% of the test windows. After a pass, NO TRADE still stays on for 56 days of observation.

### 6.4 What the results mean

- **Buy & hold** (just owning BTC) is shown for comparison. Over strong bull markets it is hard to beat.
- **Few trades = no conclusion.** On the design data the signal made only 5 trades in three years: far too few to
  say anything, whatever the return.
- **Past performance does not predict the future.** A good backtest can still fail live.

---

## 7. Paper trading (simulated)

The paper account practises with **pretend money** (10,000 USDT by default). Every order is labelled SIMULATED and
nothing is sent to any exchange.

- **Buy**: enter an amount in USDT, optionally a stop (below the price) and a target (above the price).
- **Sell**: sells your whole simulated position at the current price.
- Fees (0.10%) and slippage (0.05%) are charged on each side, like a real exchange.
- Orders are refused when the price is stale.
- **Stops and targets** are checked by the app every 60 seconds **while it is running**, whatever page you are on.
  The fill uses the price at that check, so it can be worse than your stop, just as in real markets.
- **Daily loss limit** (default 3%): once today's loss (since 00:00 UTC) reaches it, new simulated buys are blocked
  until tomorrow.
- **Reset** sets the account back to the starting balance and deletes its history.
- **Export** downloads your simulated orders as CSV or JSON.

---

## 8. The risk calculator

The calculator answers: *"How much should I buy so that, if my stop is hit, I lose about X% of my money?"*
It always includes fees and slippage on both sides.

**Example** (defaults: 1% risk, 0.10% fee + 0.05% slippage per side, 50% maximum exposure):

| Input | Value |
|---|---|
| Portfolio | 10,000 USDT |
| Entry | 60,000 |
| Stop | 58,000 |

- Money at risk: 1% × 10,000 = **100 USDT**
- Loss per BTC if stopped out: (60,000 − 58,000) + 0.15% × 60,000 + 0.15% × 58,000 = 2,000 + 90 + 87 = **2,177**
- Position: 100 ÷ 2,177 = **0.0459 BTC** (≈ 2,756 USDT, 27.6% of the portfolio, under the 50% cap)

Rules: risk per trade must be greater than 0 and at most the maximum (default 2%); after two losing trades in a row
the risk is halved; the size is never increased to win losses back. **A stop is not guaranteed**: in a fast market
the fill can be well below the stop.

---

## 9. Alerts and the assistant

**Alerts** (bell icon) tell you about big price moves, important news, extreme volatility, a changed signal, data
sources failing, the price not being cross-checked, and simulated risk limits. Each rule has a cooldown (default 30
minutes) so the same alert does not repeat. Optional Windows notifications: `pip install plyer` and turn them on in
Settings → Alerts. Alerts never mean a trade was made.

**Ask the data** answers from the app's own numbers and lists its sources. It never predicts prices or gives trade
advice. If the AI is on and Ollama is running, answers are reworded and then checked for invented numbers.

---

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| "running scripts is disabled on this system" | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then activate again. |
| `python` is not recognised | Reinstall Python with "Add python.exe to PATH", then open a new PowerShell window. |
| `No module named flask` (or similar) | You forgot to activate: `.\.venv\Scripts\Activate.ps1`, then `pip install -r requirements.txt`. |
| The page does not open | Is the PowerShell window with `python app.py` still open? Use `http://127.0.0.1:5000` (not https). |
| "Address already in use" | Another program uses port 5000. Set `APP_PORT=5050` in `.env` and open `http://127.0.0.1:5050`. |
| Price is "stale" or the top bar is red | Check your internet. Binance is blocked in some countries (HTTP 451); the app then uses Coinbase. |
| "Price sources disagree" for a long time | One exchange may be having problems. Check System & sources. You can raise the limit (Settings → Signal), but the default 0.5% is deliberately strict. |
| Assistant says the AI is offline | Start Ollama (tray icon) or run `ollama list`; the rule-based answers still work. |
| A backtest says "Not enough history" | Tick "Download/refresh history first" and run again. |
| The Settings page says "Not saved: …" | The value is outside its allowed range; the message says the range. |
| Something else | Look at the PowerShell window running the app: errors are printed there. |

Check that the data sources answer from your network:

```powershell
python scripts\check_sources.py
Invoke-RestMethod http://127.0.0.1:5000/api/health
```

---

## 11. Data-source limitations

- **Free public data** can be delayed, rate-limited or briefly unavailable. The app shows the source and age of
  every price and marks old data as stale instead of pretending it is live.
- **Binance quotes in USDT, Coinbase in USD.** USDT does not trade at exactly 1 USD, so the raw prices usually differ
  by a few tenths of a percent (0.23-0.27% when measured on 30 October 2026). The app never mixes them in one chart.
  For the cross-check it converts the USDT price to USD with Coinbase's USDT-USD rate first, so this difference does
  not count against the 0.5% limit. If the rate is unavailable, the raw gap is shown as a warning only.
- **CoinGecko** (market cap, dominance) is aggregated and can lag by a few minutes.
- **News** comes from public RSS feeds. The app reads headlines only and classifies them with rules, which can be
  wrong. Always read the article before acting on it.
- **Economic calendar**: FOMC dates come from the Federal Reserve; US CPI/PPI/jobs dates need your e-mail in `.env`
  (`CALENDAR_CONTACT_EMAIL`) because the BLS blocks anonymous scripts.
- **History for backtests** comes from Binance; a few hourly candles are missing in the record (exchange outages),
  and the app counts them rather than filling them in.

---

## 12. Your data and backups

Everything is stored in one file on your computer: `data\btc_agent.sqlite3`. Before the app upgrades this file's
structure it makes a copy next to it (`btc_agent.sqlite3.backup-<date>-schema-<n>`). To make your own copy, stop the
app and run:

```powershell
Copy-Item data\btc_agent.sqlite3 "data\btc_agent.sqlite3.backup-$(Get-Date -Format yyyyMMdd)"
```

---

## 13. Glossary

| Term | Meaning |
|---|---|
| Candle | Open, high, low and close price over one period (e.g. 4 hours). The app uses **closed** candles only. |
| EMA / SMA | Moving averages of the price; they smooth out noise to show the trend. |
| RSI | Momentum from 0 to 100; above 70 is "overbought", below 30 "oversold". |
| MACD | Difference between two moving averages; shows whether momentum is rising or falling. |
| ADX | Trend strength (not direction); above 25 means a strong trend. |
| ATR | Average size of a candle's move; used for stops and to measure volatility. |
| Support / resistance | Price levels where the price turned several times before. |
| Drawdown | The fall from a peak to a later low, as a percentage. |
| Profit factor | Total gains divided by total losses. Above 1 means more gained than lost. |
| Slippage | The difference between the expected price and the price you actually get. |
| Holdout | Data kept aside and used only once to test the final rules. |
| Paper trading | Practising with simulated money. |
