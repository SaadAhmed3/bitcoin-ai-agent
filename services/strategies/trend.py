"""Strategy 2: Trend following (4h pullback continuation). Research only, spot long only, simulated only.

Defined DIFFERENTLY from H2 (daily EMA 50/200 crossover, always 100% in or out, no stops): 4h candles, full EMA
alignment + slope + ADX + daily confirmation, an entry only on a pullback-and-resume trigger, a structure/ATR stop,
a fixed R target, a trend-break exit, a time limit and 1% planned risk per trade. Rules fixed in
PREREGISTRATION_TREND.md before the first backtest.

At the CLOSE of 4h bar i (closed candles only; the daily filter uses daily candles closed by then):
  trend_ok  = EMA21 > EMA50 > EMA200, EMA50 rising over 10 bars, ADX14 >= adx_min, last daily close > daily EMA50
  trigger   = the previous bar's low touched EMA21 (low[i-1] <= EMA21[i-1]) and close[i] > high[i-1]
  stop      = lowest low of the last 5 closed bars (i-4..i) - 0.25 x ATR14[i]       (must be below the entry)
  BUY SETUP = trend_ok and trigger                -> buy at the NEXT bar's open
  Exits: stop (gap below -> at the open), target = fill + target_r x (fill - stop), the same bar touching both ->
  stop first; a 4h close below EMA50 -> exit at the next open; after time_limit_bars bars -> exit at the next open.
  Size: planned loss to the stop = risk_pct of the account, never more than 100% of the account (no leverage).
  Costs per side: fill = price x (1 +/- slippage), fee = notional x fee. Marked at liquidation value.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from services.backtesting import HOLDOUT_START
from services.strategies.base import DataQuality, StrategyResult, config_hash
from services.technical_analysis import adx, atr, ema

STRATEGY_ID = "trend"
VERSION = "trend-1"
PREREGISTRATION = "PREREGISTRATION_TREND.md"
EVAL_TIMEFRAMES = ("4h", "1d")
BACKTEST_TIMEFRAMES = ("4h", "1d")
FORWARD_TIMEFRAME = "4h"
BAR = 4 * 3_600_000
DAY = 86_400_000

DEFAULTS: dict[str, Any] = {"ema_fast": 21, "ema_mid": 50, "ema_slow": 200, "slope_bars": 10, "adx_min": 20.0,
                            "htf_ema": 50, "swing_bars": 5, "stop_atr_buffer": 0.25, "target_r": 2.0,
                            "time_limit_bars": 30, "risk_pct": 1.0, "fee_pct": 0.10, "slippage_pct": 0.05}
FIELDS: dict[str, dict] = {
    "ema_fast": {"kind": "int", "lo": 5, "hi": 50, "help": "Fast EMA (4h); price pulls back to it"},
    "ema_mid": {"kind": "int", "lo": 20, "hi": 100, "help": "Middle EMA (4h); a close below it ends the trend"},
    "ema_slow": {"kind": "int", "lo": 100, "hi": 300, "help": "Slow EMA (4h)"},
    "slope_bars": {"kind": "int", "lo": 1, "hi": 50, "help": "EMA-mid must be higher than this many bars ago"},
    "adx_min": {"kind": "float", "lo": 0, "hi": 50, "help": "Minimum ADX14 (trend strength)"},
    "htf_ema": {"kind": "int", "lo": 10, "hi": 200, "help": "Daily EMA the last daily close must be above"},
    "swing_bars": {"kind": "int", "lo": 2, "hi": 20, "help": "Stop below the lowest low of this many bars"},
    "stop_atr_buffer": {"kind": "float", "lo": 0, "hi": 2, "help": "Extra stop distance in ATR14"},
    "target_r": {"kind": "float", "lo": 1, "hi": 5, "help": "Target = entry + this many times the risk"},
    "time_limit_bars": {"kind": "int", "lo": 5, "hi": 200, "help": "Exit after this many 4h bars"},
    "risk_pct": {"kind": "float", "lo": 0.1, "hi": 2, "help": "Planned loss to the stop, % of the account (max 2)"},
    "fee_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Fee per side (%)"},
    "slippage_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Slippage per side (%)"},
}
LIMITATIONS = [
    "Design data only (before 2025-10-29): evidence about the past, not validation.",
    "One asset and one price source (Binance BTCUSDT 4h and 1d); fixed costs; fills at the next open.",
    "Indicators are computed over the stored history; the live view uses the app's 500-candle window, so values can "
    "differ slightly.",
    "Long only (no shorting): a bearish trend gives NO TRADE, not a short.",
]


def validate_config(payload: dict | None) -> dict[str, Any]:
    payload = dict(payload or {})
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown trend setting(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for k, default in DEFAULTS.items():
        v, f = payload.get(k, default), FIELDS[k]
        try:
            if isinstance(v, bool):
                raise ValueError
            if f["kind"] == "int":
                if float(v) != int(float(v)):
                    raise ValueError
                v = int(float(v))
            else:
                v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"'{k}' has the wrong value (expected {f['kind']})") from None
        if not f["lo"] <= v <= f["hi"]:
            raise ValueError(f"'{k}' must be between {f['lo']} and {f['hi']}")
        out[k] = v
    if not out["ema_fast"] < out["ema_mid"] < out["ema_slow"]:
        raise ValueError("EMAs must satisfy ema_fast < ema_mid < ema_slow")
    return out


# --------------------------------------------------------------------------- indicators and setups (vectorised)
def indicators(df4: pd.DataFrame, df1: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    """Causal indicators per closed 4h bar (each value uses only that bar and earlier ones)."""
    d = df4.sort_values("open_time").reset_index(drop=True)[["open_time", "open", "high", "low", "close", "volume",
                                                             "close_time"]].astype({"open": float, "high": float,
                                                                                    "low": float, "close": float})
    c = d["close"]
    d["ema_f"], d["ema_m"], d["ema_s"] = ema(c, cfg["ema_fast"]), ema(c, cfg["ema_mid"]), ema(c, cfg["ema_slow"])
    d["slope_ok"] = d["ema_m"] > d["ema_m"].shift(cfg["slope_bars"])
    d["adx"] = adx(d, 14)["adx"]
    d["atr"] = atr(d, 14)
    d["htf_ok"] = False
    d["htf_ready"] = False
    if df1 is not None and len(df1):
        day = df1.sort_values("open_time")
        dc = day["close"].astype(float).reset_index(drop=True)
        dema = ema(dc, cfg["htf_ema"]).to_numpy()
        dct = day["close_time"].to_numpy()
        j = np.searchsorted(dct, d["close_time"].to_numpy(), side="right") - 1      # last daily candle CLOSED by then
        ok = np.zeros(len(d), bool)
        valid = j >= 0
        jj = j[valid]
        ok[valid] = (dc.to_numpy()[jj] > dema[jj]) & ~np.isnan(dema[jj])
        ready = np.zeros(len(d), bool)
        ready[valid] = ~np.isnan(dema[jj])
        d["htf_ok"], d["htf_ready"] = ok, ready
    d["align"] = (d["ema_f"] > d["ema_m"]) & (d["ema_m"] > d["ema_s"])
    d["trend_ok"] = d["align"] & d["slope_ok"] & (d["adx"] >= cfg["adx_min"]) & d["htf_ok"]
    prev_low, prev_high, prev_ema = d["low"].shift(1), d["high"].shift(1), d["ema_f"].shift(1)
    d["trigger"] = (prev_low <= prev_ema) & (d["close"] > prev_high)
    d["stop_plan"] = d["low"].rolling(cfg["swing_bars"], min_periods=cfg["swing_bars"]).min() - cfg["stop_atr_buffer"] * d["atr"]
    d["buy"] = d["trend_ok"] & d["trigger"] & (d["stop_plan"] < d["close"]) & d["stop_plan"].notna()
    d["trend_break"] = d["close"] < d["ema_m"]
    return d


# --------------------------------------------------------------------------- one bar of the simulation
def new_state(equity: float = 1.0) -> dict:
    return {"cash": equity, "units": 0.0, "fill": None, "stop": None, "target": None, "entry_time": None,
            "bars": 0, "exit_next": None, "pending_stop": None, "trades": []}


def step(state: dict, bar: dict, decision: dict, cfg: dict) -> tuple[dict, list[dict]]:
    """Advance one 4h bar. bar: this bar's open_time/open/high/low/close. decision: this bar's CLOSE decision
    (buy, stop_plan, trend_break). The pending entry / exit decided at the previous close executes at this open.
    Returns (state, events) where events are entries / exits that happened in this bar."""
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    st = dict(state)
    events: list[dict] = []
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

    def close_position(px: float, reason: str) -> None:
        exec_px = px * (1 - s)
        proceeds = st["units"] * exec_px
        fee = proceeds * f
        cost_in = st["units"] * st["fill"] * (1 + f)
        pnl = proceeds - fee - cost_in
        risk = st["units"] * (st["fill"] - st["stop"])
        trade = {"entry_time": st["entry_time"], "exit_time": bar["open_time"], "fill": st["fill"], "exit": exec_px,
                 "stop": st["stop"], "target": st["target"], "units": st["units"], "pnl": pnl,
                 "r": pnl / risk if risk > 0 else 0.0, "reason": reason, "bars": st["bars"],
                 "equity_before": st["equity_at_entry"], "return": pnl / st["equity_at_entry"],
                 "fees": st["units"] * st["fill"] * f + fee,
                 "slippage": st["units"] * (st["fill"] - st["fill"] / (1 + s)) + st["units"] * (px - exec_px)}
        st["cash"] += proceeds - fee
        st.update(units=0.0, fill=None, stop=None, target=None, entry_time=None, bars=0, exit_next=None)
        st["trades"] = st["trades"] + [trade]
        events.append({"kind": "exit", **trade})

    # 1) at this open: an exit decided at the previous close, else a pending entry
    if st["units"] and st["exit_next"]:
        close_position(o, st["exit_next"])
    elif not st["units"] and st["pending_stop"] is not None:
        fill = o * (1 + s)
        stop = st["pending_stop"]
        if fill > stop:
            equity = st["cash"]
            risk_units = (cfg["risk_pct"] / 100 * equity) / (fill - stop)
            units = min(risk_units, equity / (fill * (1 + f)))
            st["cash"] -= units * fill * (1 + f)
            st.update(units=units, fill=fill, stop=stop, target=fill + cfg["target_r"] * (fill - stop),
                      entry_time=bar["open_time"], bars=0, equity_at_entry=equity)
            events.append({"kind": "entry", "time": bar["open_time"], "fill": fill, "stop": stop,
                           "target": st["target"], "units": units})
        else:
            events.append({"kind": "skipped", "time": bar["open_time"], "reason": "opened at or below the stop"})
    st["pending_stop"] = None

    # 2) inside this bar: stop first (pessimistic), then target
    if st["units"]:
        st["bars"] += 1
        if o <= st["stop"] and st["bars"] > 1:
            close_position(o, "stop (gap)")
        elif l <= st["stop"]:
            close_position(st["stop"], "stop")
        elif h >= st["target"]:
            close_position(max(st["target"], o) if st["bars"] > 1 else st["target"], "target")

    # 3) at this close: decide the next open
    if st["units"]:
        if decision.get("trend_break"):
            st["exit_next"] = "trend break (close below EMA mid)"
        elif st["bars"] >= cfg["time_limit_bars"]:
            st["exit_next"] = f"time limit ({cfg['time_limit_bars']} bars)"
    elif decision.get("buy"):
        st["pending_stop"] = float(decision["stop_plan"])
    st["equity"] = st["cash"] + st["units"] * c * (1 - f - s)
    return st, events


def simulate(ind: pd.DataFrame, cfg: dict, start: int, end: int) -> dict[str, Any]:
    """Bars start..end (indexes into ind). An open trade at the end is closed at the last close."""
    st = new_state()
    curve = []
    for i in range(start, end + 1):
        r = ind.iloc[i]
        decision = {"buy": bool(r["buy"]) and i < end, "stop_plan": r["stop_plan"], "trend_break": bool(r["trend_break"])}
        st, _ = step(st, {"open_time": int(r["open_time"]), "open": r["open"], "high": r["high"], "low": r["low"],
                          "close": r["close"]}, decision, cfg)
        curve.append(st["equity"])
    if st["units"]:                            # still open at the end: closed at the last close, with costs
        px = float(ind["close"].iloc[end])
        st["exit_next"] = "end of design data"
        st, _ = step(st, {"open_time": int(ind["close_time"].iloc[end]) + 1, "open": px, "high": px, "low": px,
                          "close": px}, {}, cfg)
        curve[-1] = st["equity"]
    return {"trades": st["trades"], "equity": curve}


# --------------------------------------------------------------------------- metrics, baselines, criteria
def max_dd(curve: list[float] | np.ndarray) -> float:
    v = np.asarray(curve, float)
    if not len(v):
        return 0.0
    peak = np.maximum.accumulate(v)
    return float(np.max((peak - v) / peak)) * 100


def metrics(trades: list[dict], curve: list[float], bars: int) -> dict[str, Any]:
    r = [t["r"] for t in trades]
    pnl = [t["pnl"] for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    return {
        "trades": len(trades),
        "net_return_pct": (curve[-1] - 1) * 100 if curve else 0.0,
        "max_drawdown_pct": max_dd(curve),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else None,
        # None (shown as N/A) when there is no losing trade: a ratio to zero is not a number
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None,
        "expectancy_r": float(np.mean(r)) if r else None,
        "avg_trade_pct": float(np.mean([t["return"] for t in trades])) * 100 if trades else None,
        "avg_win_pct": float(np.mean([t["return"] for t in trades if t["pnl"] > 0])) * 100 if wins else None,
        "avg_loss_pct": float(np.mean([t["return"] for t in trades if t["pnl"] <= 0])) * 100 if losses else None,
        "exposure_pct": sum(t["bars"] for t in trades) / max(1, bars) * 100,
        "avg_bars_held": float(np.mean([t["bars"] for t in trades])) if trades else None,
        "fees_pct": sum(t["fees"] for t in trades) * 100,
        "slippage_pct": sum(t["slippage"] for t in trades) * 100,
        "exit_reasons": {k: sum(1 for t in trades if t["reason"].startswith(k)) for k in
                         ("stop", "target", "trend break", "time limit", "end of design data")},
    }


def buy_and_hold(ind: pd.DataFrame, start: int, end: int, cfg: dict) -> dict:
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    units = (1 - f) / (float(ind["open"].iloc[start]) * (1 + s))
    curve = [units * c * (1 - f - s) for c in ind["close"].iloc[start:end + 1]]
    return {"net_return_pct": (curve[-1] - 1) * 100, "max_drawdown_pct": max_dd(curve)}


def h2_rule(df1: pd.DataFrame, first_time: int, last_time: int, cfg: dict) -> dict:
    """H2 as defined in PREREGISTRATION_H2.md (daily EMA 50 > EMA 200, act at the next open, 100% in or out),
    using the forward tracker's own rule function, over the same period. Reported for comparison only."""
    from services.forward_tests import RULES
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    day = df1.sort_values("open_time").reset_index(drop=True)
    closes = day["close"].astype(float).to_numpy()
    eq, units, cash, in_mkt, curve, trades = 1.0, 0.0, 1.0, False, [], 0
    idx = [k for k in range(len(day)) if first_time <= int(day["open_time"].iloc[k]) <= last_time]
    for k in idx:
        o, c = float(day["open"].iloc[k]), float(day["close"].iloc[k])
        want = bool(RULES["H2"](closes[:k])["in_market"]) if k >= 200 else False     # decided at the previous close
        if want and not in_mkt:
            units, cash, in_mkt, trades = cash * (1 - f) / (o * (1 + s)), 0.0, True, trades + 1
        elif not want and in_mkt:
            cash, units, in_mkt = units * o * (1 - s) * (1 - f), 0.0, False
        curve.append(cash + units * c * (1 - f - s))
    return {"net_return_pct": (curve[-1] - 1) * 100 if curve else None, "max_drawdown_pct": max_dd(curve),
            "entries": trades}


def random_entries(ind: pd.DataFrame, trades: list[dict], cfg: dict, start: int, end: int, runs: int = 1000,
                   seed: int = 7) -> dict:
    """Same number of trades, each keeping the strategy trade's holding time as its maximum, entered at random bars
    (non-overlapping), with the SAME stop formula, target, sizing and costs. Returns the share of runs the strategy
    beat."""
    rng = np.random.default_rng(seed)
    holds = [max(1, int(t["bars"])) for t in trades]
    n = len(holds)
    if n == 0:
        return {"runs": 0, "beaten_pct": None, "median_return_pct": None}
    rec = ind.to_dict("records")
    results = []
    for _ in range(runs):
        span = end - start + 1
        if sum(holds) + n > span:
            break
        free = span - sum(holds) - n
        cuts = np.sort(rng.integers(0, free + 1, n))
        order = rng.permutation(n)
        eq = 1.0
        for k, slot in enumerate(order):
            e = start + int(cuts[k]) + sum(holds[order[m]] + 1 for m in range(k)) + 1
            hold = holds[slot]
            if e > end or e - 1 < 0:
                continue
            stop = rec[e - 1]["stop_plan"]
            if stop is None or (isinstance(stop, float) and math.isnan(stop)):
                continue
            st = new_state(eq)
            st["pending_stop"] = float(stop)
            last = min(end, e + hold - 1)
            for i in range(e, last + 1):
                b = rec[i]
                st, _ = step(st, b, {}, {**cfg, "time_limit_bars": hold})
                if not st["units"] and st["trades"]:
                    break
            if st["units"]:
                st["exit_next"] = "time"
                b = rec[min(end, last + 1)] if last + 1 <= end else rec[last]
                st, _ = step(st, b if last + 1 <= end else {**b, "open": b["close"], "high": b["close"],
                                                            "low": b["close"]}, {}, cfg)
            eq = st["cash"]
        results.append((eq - 1) * 100)
    return {"runs": len(results), "returns_pct": results,
            "median_return_pct": float(np.median(results)) if results else None}


def evaluate_criteria(m: dict, beaten_pct: float | None) -> tuple[list[dict], str]:
    pf = m["profit_factor"]
    crit = [
        {"id": "C1", "rule": "expectancy >= +0.15R per trade after fees and slippage", "value": m["expectancy_r"],
         "passed": m["expectancy_r"] is not None and m["expectancy_r"] >= 0.15},
        {"id": "C2", "rule": "profit factor >= 1.3", "value": pf, "passed": pf is not None and pf >= 1.3},
        {"id": "C3", "rule": "max drawdown <= 15% of the account", "value": m["max_drawdown_pct"],
         "passed": m["max_drawdown_pct"] <= 15},
        {"id": "C4", "rule": "beats >= 90% of 1,000 random-entry runs (same trades, holding times, stop/target rules)",
         "value": beaten_pct, "passed": beaten_pct is not None and beaten_pct >= 90},
    ]
    if m["trades"] < 30:
        return crit, "INCONCLUSIVE"
    return crit, "PASS" if all(c["passed"] for c in crit) else "FAIL"


def design_span(ind: pd.DataFrame, holdout_start: int, cfg: dict) -> tuple[int, int]:
    """First bar with a full warm-up (EMA slow + 100 bars and the daily filter), last bar closed before the holdout."""
    ct = ind["close_time"].to_numpy()
    end = int(np.searchsorted(ct, holdout_start, side="left")) - 1
    ready = np.flatnonzero(ind["htf_ready"].to_numpy())
    start = max(cfg["ema_slow"] + 100, int(ready[0]) if len(ready) else len(ind))
    return start, end


def backtest(rows4: list[dict], rows1: list[dict], cfg: dict, holdout_start: int = HOLDOUT_START,
             random_runs: int = 1000) -> dict[str, Any]:
    df4 = pd.DataFrame(rows4).sort_values("open_time")
    df1 = pd.DataFrame(rows1).sort_values("open_time")
    df4 = df4[df4["close_time"] < holdout_start].reset_index(drop=True)
    df1 = df1[df1["close_time"] < holdout_start].reset_index(drop=True)
    if len(df4) < cfg["ema_slow"] + 200:
        raise ValueError("Not enough 4h candles before the holdout start.")
    ind = indicators(df4, df1, cfg)
    start, end = design_span(ind, holdout_start, cfg)
    assert int(ind["close_time"].iloc[end]) < holdout_start, "design data reaches the holdout"
    sim = simulate(ind, cfg, start, end)
    m = metrics(sim["trades"], sim["equity"], end - start + 1)
    rnd = random_entries(ind, sim["trades"], cfg, start, end, runs=random_runs)
    beaten = (float(np.mean([m["net_return_pct"] > x for x in rnd["returns_pct"]])) * 100) if rnd.get("returns_pct") else None
    criteria, verdict = evaluate_criteria(m, beaten)
    # walk-forward: consecutive 182-day windows (no fitting: every window uses the same fixed rules)
    ct = ind["close_time"].to_numpy()
    eq = np.asarray(sim["equity"])
    windows, k0 = [], start
    while k0 <= end:
        k1 = min(end, int(np.searchsorted(ct, ct[k0] + 182 * DAY, side="right")) - 1)
        e0 = eq[k0 - start - 1] if k0 > start else 1.0
        tr = [t for t in sim["trades"] if ind["open_time"].iloc[k0] <= t["entry_time"] <= ind["open_time"].iloc[k1]]
        windows.append({"start": int(ind["open_time"].iloc[k0]), "end": int(ct[k1]), "trades": len(tr),
                        "return_pct": (eq[k1 - start] / e0 - 1) * 100, "sum_r": float(sum(t["r"] for t in tr))})
        k0 = k1 + 1
    sens = []
    for a in (15.0, 20.0, 25.0):
        for t in (1.5, 2.0, 3.0):
            c2 = {**cfg, "adx_min": a, "target_r": t}
            i2 = indicators(df4, df1, c2)
            s2 = simulate(i2, c2, start, end)
            m2 = metrics(s2["trades"], s2["equity"], end - start + 1)
            sens.append({"adx_min": a, "target_r": t, "trades": m2["trades"], "expectancy_r": m2["expectancy_r"],
                         "profit_factor": m2["profit_factor"], "max_drawdown_pct": m2["max_drawdown_pct"],
                         "net_return_pct": m2["net_return_pct"], "is_default": a == cfg["adx_min"] and t == cfg["target_r"]})
    first_t, last_t = int(ind["open_time"].iloc[start]), int(ind["open_time"].iloc[end])
    bh = buy_and_hold(ind, start, end, cfg)
    h2 = h2_rule(df1, first_t, last_t, cfg)
    curve = sim["equity"]
    idx = np.linspace(0, len(curve) - 1, min(400, len(curve))).astype(int)
    return {
        "strategy": STRATEGY_ID, "version": VERSION, "config": cfg, "config_hash": config_hash(cfg),
        "period": {"kind": "design", "first_day": first_t, "last_day": last_t, "last_day_close": int(ct[end]),
                   "holdout_start": holdout_start, "bars": end - start + 1},
        "metrics": {**m, "avg_reward_risk": cfg["target_r"], "buy_hold_return_pct": bh["net_return_pct"],
                    "costs_usd": None, "costs_pct": m["fees_pct"] + m["slippage_pct"]},
        "criteria": criteria, "verdict": verdict,
        "sample_warning": (f"Only {m['trades']} trades (fewer than 30): too few to judge; the verdict is INCONCLUSIVE."
                           if m["trades"] < 30 else None),
        "random_entries": {"runs": rnd["runs"], "beaten_pct": beaten, "median_return_pct": rnd["median_return_pct"]},
        "buy_and_hold": bh, "h2_rule": h2, "walk_forward": windows, "sensitivity": sens,
        "trade_list": [{k: t[k] for k in ("entry_time", "exit_time", "fill", "exit", "stop", "target", "r", "reason",
                                          "bars", "return")} for t in sim["trades"]],
        "equity_sample": [{"time": int(ind["open_time"].iloc[start + i]), "equity": curve[i]} for i in idx],
        "limitations": LIMITATIONS,
    }


# --------------------------------------------------------------------------- live research signal
def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def evaluate_frames(frames: dict[str, pd.DataFrame], cfg: dict, now: int, data: DataQuality, regime: str | None = None,
                    config_version: int = 1) -> StrategyResult:
    df4, df1 = frames.get("4h"), frames.get("1d")
    f, s = cfg["fee_pct"], cfg["slippage_pct"]
    base = dict(strategy_id=STRATEGY_ID, strategy_version=VERSION, evaluated_at=now, asset="BTCUSDT", timeframe="4h",
                fees_pct=f, slippage_pct=s, data=data, regime=regime, config_version=config_version,
                config_hash=config_hash(cfg))
    labels = (["LIVE DATA"] if not data.is_stale else []) + ["CALCULATED", "MODEL ESTIMATE"]
    none = dict(strength=None, entry_zone=None, stop=None, targets=[], reward_risk=None)
    if df4 is None or df4.empty or len(df4) < cfg["ema_slow"] + 20:
        return StrategyResult(**base, **none, signal="NO TRADE", supporting=[], contradictory=[],
                              explanation="Not enough closed 4h candles for EMA 200.", rejection="Not enough data.",
                              labels=labels)
    problems = (["4h data is stale"] if data.is_stale else []) + (
        [f"{data.missing_candles} missing 4h candle(s)"] if data.missing_candles else [])
    ind = indicators(df4, df1, cfg)
    r, p = ind.iloc[-1], ind.iloc[-2]
    checks = [("EMA {0} > {1} > {2}".format(cfg["ema_fast"], cfg["ema_mid"], cfg["ema_slow"]), bool(r["align"])),
              (f"EMA {cfg['ema_mid']} rising over {cfg['slope_bars']} bars", bool(r["slope_ok"])),
              (f"ADX14 {r['adx']:.1f} >= {cfg['adx_min']:g}", bool(r["adx"] >= cfg["adx_min"])),
              (f"daily close above daily EMA {cfg['htf_ema']}", bool(r["htf_ok"]))]
    supporting = [name for name, ok in checks if ok]
    contradictory = [f"not: {name}" for name, ok in checks if not ok] + problems
    strength = int(round(100 * sum(ok for _, ok in checks) / len(checks)))
    when = _utc(int(r["close_time"]) + 1)
    if problems:
        return StrategyResult(**base, **{**none, "strength": strength}, signal="NO TRADE", supporting=supporting,
                              contradictory=contradictory, explanation="Data problem: no research signal.",
                              rejection="; ".join(problems), labels=labels)
    if bool(p["trend_ok"]) and bool(r["trend_break"]):
        return StrategyResult(**base, **{**none, "strength": strength}, signal="EXIT / REDUCE EXPOSURE",
                              supporting=supporting, contradictory=contradictory,
                              explanation=f"Trend break: the 4h close is below EMA {cfg['ema_mid']}. A simulated "
                                          f"position would exit at the next open ({when}).", rejection=None,
                              labels=labels)
    if bool(r["buy"]):
        entry, stop = float(r["close"]), float(r["stop_plan"])
        target = entry + cfg["target_r"] * (entry - stop)
        cost = entry * 2 * (f + s) / 100
        rr_net = ((target - entry) - cost) / ((entry - stop) + cost)
        return StrategyResult(**base, strength=strength, entry_zone={"low": round(entry, 2), "high": round(entry, 2)},
                              stop=round(stop, 2), targets=[round(target, 2)], reward_risk=round(rr_net, 2),
                              signal="BUY SETUP", supporting=supporting + ["pullback to EMA 21 and a close above the "
                                                                           "previous high"],
                              contradictory=contradictory,
                              explanation=f"Research setup: buy at the next open ({when}), stop {stop:,.2f} (lowest "
                                          f"low of {cfg['swing_bars']} bars - {cfg['stop_atr_buffer']} ATR), target "
                                          f"{cfg['target_r']:g}R. Planned risk {cfg['risk_pct']:g}% of the account. "
                                          f"Reward/risk after costs {rr_net:.2f}.", rejection=None, labels=labels)
    if bool(r["trend_ok"]):
        return StrategyResult(**base, **{**none, "strength": strength}, signal="HOLD", supporting=supporting,
                              contradictory=contradictory,
                              explanation="Trend intact, but no entry trigger: waiting for a pullback to EMA 21 and a "
                                          "close above the previous high. A simulated position would keep its stop.",
                              rejection="No entry trigger on the last closed 4h bar.", labels=labels)
    return StrategyResult(**base, **{**none, "strength": strength}, signal="NO TRADE", supporting=supporting,
                          contradictory=contradictory, explanation="No confirmed uptrend: at least one trend check fails.",
                          rejection="Trend filter not met: " + ", ".join(n for n, ok in checks if not ok) + ".",
                          labels=labels)


# --------------------------------------------------------------------------- forward test (strategy_forward.py)
FORWARD_CRITERIA = "F1 net return > 0 and F2 expectancy >= +0.15R over the 365 forward days; fewer than 30 trades: INCONCLUSIVE"


def forward_step(cfg: dict, ctx: dict) -> tuple[list[dict], dict]:
    """One closed 4h bar of the forward test, with exactly the backtest's step()."""
    rows4, k = ctx["rows"], ctx["k"]
    lo = max(0, k - 1200)
    df4 = pd.DataFrame(rows4[lo:k + 1])
    rows1 = [r for r in ctx["candles"]("1d") if r["close_time"] <= rows4[k]["close_time"]]
    ind = indicators(df4, pd.DataFrame(rows1) if rows1 else None, cfg)
    r = ind.iloc[-1]
    state = ctx["state"] or new_state()
    state, evs = step(state, {"open_time": int(r["open_time"]), "open": r["open"], "high": r["high"], "low": r["low"],
                              "close": r["close"]},
                      {"buy": bool(r["buy"]), "stop_plan": None if pd.isna(r["stop_plan"]) else float(r["stop_plan"]),
                       "trend_break": bool(r["trend_break"])}, cfg)
    out = [{"kind": "buy", "detail": e} for e in evs if e["kind"] == "entry"]
    out.append({"kind": "mark", "detail": {"close": float(r["close"]), "equity": state["equity"], "state": {
        k2: v for k2, v in state.items() if k2 != "trades"}, "trades": state["trades"], "events": evs}})
    return out, state


def forward_state(events: list[dict]) -> dict | None:
    marks = [e for e in events if e["kind"] == "mark"]
    if not marks:
        return None
    d = marks[-1]["detail"]
    return {**d["state"], "trades": d["trades"]}


def forward_summary(events: list[dict], cfg: dict, done: bool) -> dict:
    marks = [e for e in events if e["kind"] == "mark"]
    if not marks:
        return {"trades": 0, "net_return_pct": None, "expectancy_r": None, "max_drawdown_pct": None, "criteria": [],
                "verdict": None}
    trades = marks[-1]["detail"]["trades"]
    curve = [m["detail"]["equity"] for m in marks]
    m = metrics(trades, curve, len(curve))
    out = {"trades": m["trades"], "net_return_pct": m["net_return_pct"], "expectancy_r": m["expectancy_r"],
           "max_drawdown_pct": m["max_drawdown_pct"], "criteria": [], "verdict": None}
    if done:
        crit = [{"id": "F1", "rule": "net return > 0", "passed": m["net_return_pct"] > 0, "value": m["net_return_pct"]},
                {"id": "F2", "rule": "expectancy >= +0.15R", "value": m["expectancy_r"],
                 "passed": m["expectancy_r"] is not None and m["expectancy_r"] >= 0.15}]
        out["criteria"] = crit
        out["verdict"] = "INCONCLUSIVE" if m["trades"] < 30 else ("PASS" if all(c["passed"] for c in crit) else "FAIL")
    return out


def markdown(r: dict) -> str:
    def p(v):
        return "n/a" if v is None else f"{v:+.2f}%"

    def n(v, d=2):
        return "n/a" if v is None else ("inf" if v == math.inf else f"{v:.{d}f}")

    m = r["metrics"]
    day = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    L = ["# Trend following design backtest (design-data evidence only)", "",
         f"Pre-registered in `{PREREGISTRATION}` (config hash `{r['config_hash']}`), run once. 4h decisions "
         f"{day(r['period']['first_day'])} to {day(r['period']['last_day'])} ({r['period']['bars']:,} bars), all closed "
         f"before the holdout start {day(r['period']['holdout_start'])}. Simulated, long only, "
         f"{r['config']['risk_pct']:g}% planned risk per trade.", ""]
    if r["sample_warning"]:
        L += [f"**{r['sample_warning']}**", ""]
    L += [f"## Verdict: {r['verdict']} (design data, not validation)", "", "| # | Rule | Value | Passed |", "|---|---|---|---|"]
    for c in r["criteria"]:
        v = c["value"]
        L.append(f"| {c['id']} | {c['rule']} | {n(v)} | {'yes' if c['passed'] else 'no'} |")
    L += ["", "## Results", "", "| | Trend | Buy & hold | H2 rule (daily EMA 50/200) |", "|---|---|---|---|",
          f"| Net return | {p(m['net_return_pct'])} | {p(r['buy_and_hold']['net_return_pct'])} | {p(r['h2_rule']['net_return_pct'])} |",
          f"| Max drawdown | {m['max_drawdown_pct']:.2f}% | {r['buy_and_hold']['max_drawdown_pct']:.2f}% | {r['h2_rule']['max_drawdown_pct']:.2f}% |",
          f"| Trades | {m['trades']} | 1 | {r['h2_rule']['entries']} entries |", "",
          f"Win rate {n(m['win_rate_pct'], 1)}%, profit factor {n(m['profit_factor'])}, expectancy {n(m['expectancy_r'])}R, "
          f"average trade {p(m['avg_trade_pct'])}, average win {p(m['avg_win_pct'])}, average loss {p(m['avg_loss_pct'])}, "
          f"exposure {m['exposure_pct']:.1f}%, average hold {n(m['avg_bars_held'], 1)} bars, fees {m['fees_pct']:.2f}% "
          f"and slippage {m['slippage_pct']:.2f}% of the account. Exits: "
          + ", ".join(f"{k} {v}" for k, v in m["exit_reasons"].items()) + ".",
          "", f"Random entries ({r['random_entries']['runs']} runs): the strategy beat {n(r['random_entries']['beaten_pct'], 1)}% "
          f"of them (median random run {p(r['random_entries']['median_return_pct'])}).", "",
          "## Walk-forward windows (182 days, fixed rules, reported only)", "", "| Window | Trades | Return | Sum of R |",
          "|---|---|---|---|"]
    for w in r["walk_forward"]:
        L.append(f"| {day(w['start'])} to {day(w['end'])} | {w['trades']} | {p(w['return_pct'])} | {w['sum_r']:+.2f} |")
    L += ["", "## Sensitivity (reported only, never used to choose parameters)", "",
          "| ADX min | Target | Trades | Expectancy | Profit factor | Max DD | Net return |", "|---|---|---|---|---|---|---|"]
    for x in r["sensitivity"]:
        L.append(f"| {x['adx_min']:g}{' (default)' if x['is_default'] and x['target_r'] == r['config']['target_r'] else ''} | "
                 f"{x['target_r']:g}R | {x['trades']} | {n(x['expectancy_r'])}R | {n(x['profit_factor'])} | "
                 f"{x['max_drawdown_pct']:.2f}% | {p(x['net_return_pct'])} |")
    L += ["", "## Limitations", ""] + [f"- {x}" for x in r["limitations"]] + [""]
    return "\n".join(L)
