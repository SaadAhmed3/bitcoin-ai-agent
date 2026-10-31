"""Research H1 (design data only): a daily SMA trend filter, spot, long or cash.

H1, as pre-registered in PREREGISTRATION_H1.md:
  * hold BTC while the daily close is above its 200-day simple moving average, otherwise hold cash;
  * decide on CLOSED daily candles only, act at the NEXT day's open;
  * 0.10% fee + 0.05% slippage per side (the engine's costs).

Only daily candles that close BEFORE the holdout start (2025-10-29 00:00 UTC) are loaded, and this is asserted.
The holdout is never read. Not wired into the live signal.

Usage (project folder, .venv active):
    python scripts\\research_h1.py
Writes data\\research_h1_design.json and data\\research_h1_design.md.
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_START = 1_761_696_000_000            # 2025-10-29 00:00 UTC (= services.backtesting.HOLDOUT_START)
COST = (0.10 + 0.05) / 100                   # per side: fee + slippage
SMA_N, BAND = 200, 0.0                       # the pre-registered rule
SENSITIVITY_N = (150, 200, 250)              # sensitivity check only - never used to choose the rule
SENSITIVITY_BAND = (0.0, 0.01, 0.02)
REGIME_LOOKBACK, REGIME_THRESHOLD = 90, 0.15  # trailing 90-day return: > +15% bull, < -15% bear, else sideways
RANDOM_RUNS, RANDOM_SEED = 1000, 7


# --------------------------------------------------------------------------- data
def load_design_daily(db_path: str | Path) -> pd.DataFrame:
    """Binance BTCUSDT daily candles that CLOSE before the holdout start (read-only connection)."""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("""SELECT open_time, open, high, low, close, volume, close_time FROM candles
                               WHERE provider='binance' AND symbol='BTCUSDT' AND timeframe='1d' AND close_time < ?
                               ORDER BY open_time""", (HOLDOUT_START,)).fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])
    return check_design(df)


def check_design(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("no daily candles before the holdout start")
    if int(df["close_time"].max()) >= HOLDOUT_START:
        raise ValueError("holdout candle in the design data")       # never: the holdout stays untouched
    steps = np.diff(df["open_time"].to_numpy())
    if (steps != 86_400_000).any():
        raise ValueError(f"{int((steps != 86_400_000).sum())} gap(s) in the daily series")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- rules (decided at a close)
def sma_filter(close: np.ndarray, n: int, band: float) -> np.ndarray:
    """In the market after a close above SMA*(1+band); out after a close below SMA*(1-band); unchanged between.
    band = 0 is H1: in exactly when close > SMA. NaN while the SMA is not yet defined (counts as out)."""
    sma = pd.Series(close).rolling(n, min_periods=n).mean().to_numpy()
    out, state = np.zeros(len(close), dtype=bool), False
    for i, (c, s) in enumerate(zip(close, sma)):
        if np.isnan(s):
            state = False
        elif not state and c > s * (1 + band):
            state = True
        elif state and c < s * (1 - band):
            state = False
        out[i] = state
    return out


def ema_cross(close: np.ndarray, fast: int = 50, slow: int = 200) -> np.ndarray:
    s = pd.Series(close)
    f = s.ewm(span=fast, adjust=False, min_periods=fast).mean()
    w = s.ewm(span=slow, adjust=False, min_periods=slow).mean()
    return ((f > w) & f.notna() & w.notna()).to_numpy()


# --------------------------------------------------------------------------- simulation
def simulate(df: pd.DataFrame, signal: np.ndarray, start: int) -> dict:
    """signal[i] is decided at the close of day i and acted on at the open of day i+1. Starts in cash at day `start`
    (the first decision). Equity is marked at each close at liquidation value (exit cost deducted)."""
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    cash, units, entry_i = 1.0, 0.0, None
    eq, trades, in_mkt = [1.0], [], [False]
    for i in range(start + 1, len(df)):
        want = bool(signal[i - 1])
        if want and units == 0:
            units, cash, entry_i = cash / (o[i] * (1 + COST)), 0.0, i
        elif not want and units > 0:
            cash, units = units * o[i] * (1 - COST), 0.0
            trades.append({"entry": entry_i, "exit": i, "days": i - entry_i})
            entry_i = None
        eq.append(units * c[i] * (1 - COST) if units > 0 else cash)
        in_mkt.append(units > 0)
    if units > 0:                                                    # still open at the end of the design data
        trades.append({"entry": entry_i, "exit": None, "days": len(df) - entry_i})
    return {"equity": np.array(eq), "trades": trades, "in_market": np.array(in_mkt)}


def buy_and_hold(df: pd.DataFrame, start: int) -> dict:
    sig = np.ones(len(df), dtype=bool)
    return simulate(df, sig, start)


def random_entries(df: pd.DataFrame, start: int, holds: list[int], runs: int, seed: int) -> np.ndarray:
    """Net return of `runs` random placements of the same number of trades with the same holding days (shuffled,
    non-overlapping, entry at an open, exit at a later open; a trade reaching the end is marked at the last close)."""
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    first, last = start + 1, len(df) - 1
    span, total = last - first + 1, sum(holds)
    if not holds or total >= span:
        return np.array([])
    rng, out = random.Random(seed), []
    for _ in range(runs):
        hs = holds[:]
        rng.shuffle(hs)
        cuts = sorted(rng.randint(0, span - total) for _ in hs)
        eq, pos, prev = 1.0, first, 0
        for cut, h in zip(cuts, hs):
            pos += cut - prev
            prev = cut
            e, x = pos, pos + h
            sell = o[x] * (1 - COST) if x <= last else c[last] * (1 - COST)
            eq *= sell / (o[e] * (1 + COST))
            pos = x
        out.append(eq - 1)
    return np.array(out) * 100


# --------------------------------------------------------------------------- metrics
def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def metrics(df: pd.DataFrame, res: dict, start: int) -> dict:
    eq = res["equity"]
    days = len(eq) - 1
    peak = np.maximum.accumulate(eq)
    mdd = float(np.max(1 - eq / peak)) * 100
    net = float(eq[-1] - 1) * 100
    years = days / 365.0
    by_year = {}
    dates = [_utc(int(t)) for t in df["close_time"].iloc[start:start + len(eq)]]
    year_end, year_span = {}, {}
    for d, v in zip(dates, eq):
        year_end[d.year] = v
        first, _ = year_span.get(d.year, (d, d))
        year_span[d.year] = (first, d)
    prev = 1.0
    for y in sorted(year_end):
        by_year[str(y)] = round((year_end[y] / prev - 1) * 100, 2)
        prev = year_end[y]
    # Worst calendar year among years with at least 180 days in the window; partial years are labelled with their
    # dates (the window starts in early January 2022 and ends in late October 2025).
    eligible = {y: r for y, r in by_year.items() if (year_span[int(y)][1] - year_span[int(y)][0]).days + 1 >= 180}
    worst_y, worst_r = min(eligible.items(), key=lambda kv: kv[1])
    a, b = year_span[int(worst_y)]
    span = "" if (a.month, a.day, b.month, b.day) == (1, 1, 12, 31) else f" ({a:%b %d}-{b:%b %d})"
    worst = (f"{worst_y}{span}", worst_r)
    return {"net_return_pct": round(net, 2), "cagr_pct": round((eq[-1] ** (1 / years) - 1) * 100, 2),
            "max_drawdown_pct": round(mdd, 2), "return_over_max_dd": round(net / mdd, 3) if mdd > 0 else None,
            "trades": len(res["trades"]), "time_in_market_pct": round(float(res["in_market"][1:].mean()) * 100, 1),
            "worst_year": {"year": worst[0], "return_pct": worst[1]}, "calendar_years_pct": by_year,
            "days": days}


def regimes(df: pd.DataFrame) -> np.ndarray:
    """Regime of day i from the trailing 90-day return known at the close of day i-1 (past data only)."""
    c = df["close"].to_numpy(float)
    trail = np.full(len(c), np.nan)
    trail[REGIME_LOOKBACK:] = c[REGIME_LOOKBACK:] / c[:-REGIME_LOOKBACK] - 1
    lab = np.array(["n/a"] * len(c), dtype=object)
    for i in range(1, len(c)):
        r = trail[i - 1]
        if not np.isnan(r):
            lab[i] = "bull" if r > REGIME_THRESHOLD else "bear" if r < -REGIME_THRESHOLD else "sideways"
    return lab


def regime_table(df: pd.DataFrame, start: int, results: dict[str, dict]) -> dict:
    lab = regimes(df)[start:start + len(next(iter(results.values()))["equity"])]
    out = {}
    for name in ("bull", "sideways", "bear"):
        days = [k for k in range(1, len(lab)) if lab[k] == name]
        row = {"days": len(days)}
        for key, res in results.items():
            eq = res["equity"]
            r = np.prod([eq[k] / eq[k - 1] for k in days]) - 1 if days else 0.0
            row[f"{key}_return_pct"] = round(float(r) * 100, 2)
            if key not in ("buy_and_hold",):
                row[f"{key}_in_market_pct"] = round(float(np.mean(res["in_market"][days])) * 100, 1) if days else None
        out[name] = row
    return out


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# --------------------------------------------------------------------------- pre-registered criteria (PREREGISTRATION_H1.md)
CRITERIA = {
    "min_holdout_days": 365,          # shorter -> INCONCLUSIVE (no verdict)
    "max_dd_vs_buy_hold": 0.60,       # H1 max drawdown <= 60% of buy & hold's
    "min_random_percentile": 90.0,    # H1 net return beats >= 90% of random-entry runs
}


def evaluate(h1_m: dict, bh_m: dict, random_pct: float | None, position_changes: int, days: int) -> dict:
    """PASS only if every criterion passes; INCONCLUSIVE if there is too little data or H1 never changed position."""
    if days < CRITERIA["min_holdout_days"] or position_changes == 0 or random_pct is None:
        why = (f"only {days} days (need {CRITERIA['min_holdout_days']})" if days < CRITERIA["min_holdout_days"]
               else "H1 never changed position" if position_changes == 0 else "no random-entry comparison possible")
        return {"verdict": "INCONCLUSIVE", "reason": why, "criteria": []}
    h1_ratio = h1_m["net_return_pct"] / h1_m["max_drawdown_pct"] if h1_m["max_drawdown_pct"] > 0 else None
    bh_ratio = bh_m["net_return_pct"] / bh_m["max_drawdown_pct"] if bh_m["max_drawdown_pct"] > 0 else None
    limit = CRITERIA["max_dd_vs_buy_hold"] * bh_m["max_drawdown_pct"]
    checks = [
        ("Drawdown at most 60% of buy & hold", h1_m["max_drawdown_pct"] <= limit,
         f"{h1_m['max_drawdown_pct']:.2f}% vs limit {limit:.2f}% (buy & hold {bh_m['max_drawdown_pct']:.2f}%)"),
        ("Return/drawdown at least buy & hold's", h1_ratio is not None and bh_ratio is not None and h1_ratio >= bh_ratio,
         f"{h1_ratio if h1_ratio is None else round(h1_ratio, 3)} vs {bh_ratio if bh_ratio is None else round(bh_ratio, 3)}"),
        ("Beats >= 90% of random entries", random_pct >= CRITERIA["min_random_percentile"],
         f"better than {random_pct:.1f}% of runs"),
    ]
    rows = [{"name": n, "passed": bool(ok), "detail": d} for n, ok, d in checks]
    return {"verdict": "PASS" if all(r["passed"] for r in rows) else "FAIL", "criteria": rows}


def _changes(res: dict) -> int:
    return int(np.sum(res["in_market"][1:] != res["in_market"][:-1]))


def run_rule(df: pd.DataFrame, start: int) -> dict:
    """H1, buy & hold, EMA 50/200 and random entries over df from decision index `start` - the same code for the
    design study and the single holdout test."""
    close = df["close"].to_numpy(float)
    h1 = simulate(df, sma_filter(close, SMA_N, BAND), start)
    bh = buy_and_hold(df, start)
    ema = simulate(df, ema_cross(close), start)
    rnd = random_entries(df, start, [t["days"] for t in h1["trades"]], RANDOM_RUNS, RANDOM_SEED)
    m = {"h1_sma200": metrics(df, h1, start), "buy_and_hold": metrics(df, bh, start), "ema_50_200": metrics(df, ema, start)}
    m["buy_and_hold"]["trades"] = 1
    pct = round(float(np.mean(rnd < m["h1_sma200"]["net_return_pct"])) * 100, 1) if len(rnd) else None
    random_summary = ({"runs": int(len(rnd)), "median_pct": round(float(np.median(rnd)), 2),
                       "p10_pct": round(float(np.percentile(rnd, 10)), 2), "p90_pct": round(float(np.percentile(rnd, 90)), 2),
                       "h1_percentile": pct} if len(rnd) else {"runs": 0, "h1_percentile": None})
    return {"results": {"h1": h1, "buy_and_hold": bh, "ema": ema}, "metrics": m, "random_entries": random_summary,
            "evaluation": evaluate(m["h1_sma200"], m["buy_and_hold"], pct, _changes(h1), m["h1_sma200"]["days"])}


def load_all_daily(db_path: str | Path) -> pd.DataFrame:
    """ALL daily candles, holdout included. Used ONLY by holdout_test() - never by the design study."""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("""SELECT open_time, open, high, low, close, volume, close_time FROM candles
                               WHERE provider='binance' AND symbol='BTCUSDT' AND timeframe='1d' ORDER BY open_time""").fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])


def holdout_test(df: pd.DataFrame) -> dict:
    """The single pre-registered holdout test. Decisions from the last design close (2025-10-28) - flat at that
    point - so the first possible trade is at the 2025-10-29 open; earlier closes are SMA warm-up only."""
    ct = df["close_time"].to_numpy()
    start = int(np.searchsorted(ct, HOLDOUT_START)) - 1      # index of the last candle closing before the holdout
    if start < SMA_N:
        raise ValueError("not enough daily history before the holdout for the SMA warm-up")
    steps = np.diff(df["open_time"].to_numpy()[start:])
    if (steps != 86_400_000).any():
        raise ValueError("gap(s) in the holdout daily series")
    out = run_rule(df, start)
    out.pop("results")
    out["window"] = {"first_trade_open": _utc(int(df["open_time"].iloc[start + 1])).isoformat(),
                     "last_close": _utc(int(ct[-1])).isoformat(), "days": int(len(df) - 1 - start)}
    out["script_sha256"] = file_sha256(Path(__file__))
    return out


# --------------------------------------------------------------------------- study
def study(df: pd.DataFrame) -> dict:
    close = df["close"].to_numpy(float)
    start = max(SENSITIVITY_N) - 1          # first close where every variant's SMA exists: one common window
    core = run_rule(df, start)             # the same code the holdout test uses
    h1, bh, ema = (core["results"][k] for k in ("h1", "buy_and_hold", "ema"))
    m, random_summary = core["metrics"], core["random_entries"]
    sens = []
    for n in SENSITIVITY_N:
        for b in SENSITIVITY_BAND:
            r = simulate(df, sma_filter(close, n, b), start)
            mm = metrics(df, r, start)
            sens.append({"sma": n, "band_pct": round(b * 100, 1), **{k: mm[k] for k in (
                "net_return_pct", "max_drawdown_pct", "return_over_max_dd", "trades", "time_in_market_pct")},
                "worst_year_pct": mm["worst_year"]["return_pct"]})
    ct = df["close_time"].to_numpy()
    return {
        "hypothesis": "H1: hold BTC while the daily close > SMA200, else cash; decide at the close, act at the next open",
        "data": {"source": "Binance BTCUSDT 1d (local database, read-only)", "candles_loaded": int(len(df)),
                 "first_candle_open": _utc(int(df["open_time"].iloc[0])).isoformat(),
                 "last_candle_close": _utc(int(ct[-1])).isoformat(), "holdout_start": _utc(HOLDOUT_START).isoformat(),
                 "first_decision_close": _utc(int(ct[start])).isoformat(),
                 "window_days": int(len(df) - 1 - start)},
        "costs": {"fee_pct_per_side": 0.10, "slippage_pct_per_side": 0.05},
        "metrics": m, "random_entries": random_summary,
        "design_check": {**core["evaluation"], "note": "The pre-registered criteria applied to DESIGN data: a check of "
                                                     "the rule's history, not a test (the rule was not tuned on it)."},
        "regimes": {"definition": f"trailing {REGIME_LOOKBACK}-day return at the previous close: > +"
                                  f"{REGIME_THRESHOLD:.0%} bull, < -{REGIME_THRESHOLD:.0%} bear, otherwise sideways",
                    "table": regime_table(df, start, {"h1": h1, "buy_and_hold": bh, "ema": ema})},
        "sensitivity": sens,
        "h1_trades": [{"entry": _utc(int(df["open_time"].iloc[t["entry"]])).strftime("%Y-%m-%d"),
                       "exit": _utc(int(df["open_time"].iloc[t["exit"]])).strftime("%Y-%m-%d") if t["exit"] else "open",
                       "days": t["days"]} for t in h1["trades"]],
        "script_sha256": file_sha256(Path(__file__)),
    }


def to_markdown(r: dict) -> str:
    m, d = r["metrics"], r["data"]
    lines = [f"# H1 research - DESIGN data only\n", f"{r['hypothesis']}.\n",
             f"Data: {d['source']}; {d['candles_loaded']} candles {d['first_candle_open'][:10]} -> "
             f"{d['last_candle_close'][:16]} UTC (holdout starts {d['holdout_start'][:10]}; never loaded). "
             f"Common window: decisions from {d['first_decision_close'][:10]} ({d['window_days']} days). "
             "Costs 0.10% fee + 0.05% slippage per side.\n",
             "| | Net return | Max DD | Return / DD | Trades | Time in market | Worst year (>= 180 days) |",
             "|---|---|---|---|---|---|---|"]
    for key, label in (("h1_sma200", "H1 SMA200"), ("buy_and_hold", "Buy & hold"), ("ema_50_200", "EMA 50/200 (daily)")):
        x = m[key]
        lines.append(f"| {label} | {x['net_return_pct']:+.2f}% | {x['max_drawdown_pct']:.2f}% | {x['return_over_max_dd']} | "
                     f"{x['trades']} | {x['time_in_market_pct']}% | {x['worst_year']['year']}: "
                     f"{x['worst_year']['return_pct']:+.2f}% |")
    rn = r["random_entries"]
    dc = r["design_check"]
    lines += ["", f"Design check against the pre-registered criteria (not a test): **{dc['verdict']}** - " +
              "; ".join(f"{c['name']}: {'pass' if c['passed'] else 'FAIL'} ({c['detail']})" for c in dc["criteria"])]
    lines += ["", f"Random entries ({rn['runs']} runs, same trade count and holding days): median {rn['median_pct']:+.2f}%, "
                  f"p10 {rn['p10_pct']:+.2f}%, p90 {rn['p90_pct']:+.2f}%; H1 beats {rn['h1_percentile']}% of them.\n",
              "Calendar years (%): " + "; ".join(f"{k}: H1 {m['h1_sma200']['calendar_years_pct'][k]:+.1f} / B&H "
                                                 f"{m['buy_and_hold']['calendar_years_pct'][k]:+.1f}"
                                                 for k in m["h1_sma200"]["calendar_years_pct"]) + "\n",
              f"## Regimes ({r['regimes']['definition']})\n",
              "| Regime | Days | H1 | H1 in market | Buy & hold | EMA 50/200 | EMA in market |", "|---|---|---|---|---|---|---|"]
    for name, row in r["regimes"]["table"].items():
        lines.append(f"| {name} | {row['days']} | {row['h1_return_pct']:+.2f}% | {row['h1_in_market_pct']}% | "
                     f"{row['buy_and_hold_return_pct']:+.2f}% | {row['ema_return_pct']:+.2f}% | {row['ema_in_market_pct']}% |")
    lines += ["", "## Sensitivity (a check, not tuning: SMA 200 with no band stays the rule)\n",
              "| SMA | Band | Net return | Max DD | Return / DD | Trades | Time in market | Worst year (>= 180 days) |",
              "|---|---|---|---|---|---|---|---|"]
    for s in r["sensitivity"]:
        lines.append(f"| {s['sma']} | {s['band_pct']}% | {s['net_return_pct']:+.2f}% | {s['max_drawdown_pct']:.2f}% | "
                     f"{s['return_over_max_dd']} | {s['trades']} | {s['time_in_market_pct']}% | {s['worst_year_pct']:+.2f}% |")
    lines += ["", f"H1 trades: " + ", ".join(f"{t['entry']} -> {t['exit']} ({t['days']} d)" for t in r["h1_trades"]),
              "", f"Script SHA-256: `{r['script_sha256']}`", "",
              "Research only, design data. Past results do not predict future returns. Not wired into the live signal."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    sys.path.insert(0, str(ROOT))
    from config import Config
    if "--holdout" in argv:
        # The single pre-registered holdout test. It spends the reserved holdout (single-use for every rule set,
        # the live engine included) and must be run ONCE, only after the owner's sign-off in PREREGISTRATION_H1.md.
        if "--confirm-spend-holdout" not in argv:
            raise SystemExit("Refused: the holdout is single-use. Re-run with --holdout --confirm-spend-holdout only "
                             "after the owner has signed off PREREGISTRATION_H1.md.")
        r = holdout_test(load_all_daily(Config().db_path))
        (ROOT / "data" / "research_h1_holdout.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(json.dumps(r["evaluation"], indent=1))
        return
    df = load_design_daily(Config().db_path)
    r = study(df)
    (ROOT / "data" / "research_h1_design.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    (ROOT / "data" / "research_h1_design.md").write_text(to_markdown(r), encoding="utf-8")
    print(to_markdown(r))


if __name__ == "__main__":
    main()
