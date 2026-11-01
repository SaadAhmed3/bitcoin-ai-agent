"""Strategy 4: Breakout trading (4h, long only). Research only, simulated only. Few filters on purpose.

Rules fixed in PREREGISTRATION_BREAKOUT.md before the first backtest. At the CLOSE of 4h bar i (closed candles only):
  level     = highest HIGH of the 20 closed 4h bars before bar i (bars i-20 .. i-1)
  signal    = bar i CLOSES above the level (a wick alone never counts) and its volume >= 1.2 x the average volume of
              the 20 bars before it
  entry     = the next bar's open; skipped if that open is at or below the stop
  stop      = level - 1.0 x ATR(14) of the signal bar (the initial stop; R is measured from it)
  exits     = the stop (intrabar; a gap below it exits at the open); a 4h close below the lowest low of the 10 bars
              before it -> exit at the next open (trailing); after 60 bars -> exit at the next open. No fixed target.
  size      = 1% planned risk to the stop, never more than 100% of the account; one position at a time.
Diagnostics (reported, never a filter): a trade "failed" if a close was back below the level within its first 3 bars;
it "retested" if, within its first 10 bars and without such a close, a low came within 0.25 x ATR(14) of the level.

Self-contained for its own rules (its forward fingerprint = this module + the tracker core). The trend module is
imported only for the reported comparisons (the H2 rule and trend over the same window), never for a rule.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from services.backtesting import HOLDOUT_START
from services.strategies.base import DataQuality, StrategyResult, config_hash
from services.technical_analysis import atr

STRATEGY_ID = "breakout"
VERSION = "breakout-1"
PREREGISTRATION = "PREREGISTRATION_BREAKOUT.md"
EVAL_TIMEFRAMES = ("4h",)
BACKTEST_TIMEFRAMES = ("4h", "1d")
FORWARD_TIMEFRAME = "4h"
WARMUP = 300

DEFAULTS: dict[str, Any] = {"lookback": 20, "volume_mult": 1.2, "volume_window": 20, "stop_atr": 1.0, "trail_bars": 10,
                            "time_limit_bars": 60, "risk_pct": 1.0, "fee_pct": 0.10, "slippage_pct": 0.05}
FIELDS: dict[str, dict] = {
    "lookback": {"kind": "int", "lo": 5, "hi": 200, "help": "Level = highest high of this many closed 4h bars"},
    "volume_mult": {"kind": "float", "lo": 0.5, "hi": 5, "help": "Breakout volume at least this many times the average"},
    "volume_window": {"kind": "int", "lo": 5, "hi": 100, "help": "Bars in the volume average"},
    "stop_atr": {"kind": "float", "lo": 0.1, "hi": 5, "help": "Stop this many ATR(14) below the level"},
    "trail_bars": {"kind": "int", "lo": 2, "hi": 50, "help": "Exit on a close below the lowest low of this many bars"},
    "time_limit_bars": {"kind": "int", "lo": 5, "hi": 300, "help": "Exit after this many 4h bars"},
    "risk_pct": {"kind": "float", "lo": 0.1, "hi": 2, "help": "Planned loss to the stop, % of the account (max 2)"},
    "fee_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Fee per side (%)"},
    "slippage_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Slippage per side (%)"},
}
LIMITATIONS = [
    "Design data only (before 2025-10-29): evidence about the past, not validation.",
    "One asset and one price source (Binance BTCUSDT 4h); fixed costs; fills at the next open.",
    "Long only: a breakdown below support gives NO TRADE, not a short.",
    "No fixed target: R/R is unknown at entry; R is measured from the initial stop.",
]


def validate_config(payload: dict | None) -> dict[str, Any]:
    payload = dict(payload or {})
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown breakout setting(s): {', '.join(sorted(unknown))}")
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
    return out


# --------------------------------------------------------------------------- features (vectorised, causal)
def features(df4: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    d = df4.sort_values("open_time").reset_index(drop=True)[["open_time", "open", "high", "low", "close", "volume",
                                                             "close_time"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float})
    d["atr"] = atr(d, 14)
    d["level"] = d["high"].rolling(cfg["lookback"], min_periods=cfg["lookback"]).max().shift(1)
    d["vol_avg"] = d["volume"].rolling(cfg["volume_window"], min_periods=cfg["volume_window"]).mean().shift(1)
    d["close_above"] = d["close"] > d["level"]
    d["volume_ok"] = d["volume"] >= cfg["volume_mult"] * d["vol_avg"]
    d["stop_plan"] = d["level"] - cfg["stop_atr"] * d["atr"]
    d["buy"] = d["close_above"] & d["volume_ok"] & (d["stop_plan"] < d["close"]) & d["stop_plan"].notna()
    d["trail_low"] = d["low"].rolling(cfg["trail_bars"], min_periods=cfg["trail_bars"]).min().shift(1)
    d["trail_exit"] = d["close"] < d["trail_low"]
    return d


def _decision(r) -> dict:
    return {"buy": bool(r["buy"]), "stop_plan": None if pd.isna(r["stop_plan"]) else float(r["stop_plan"]),
            "level": None if pd.isna(r["level"]) else float(r["level"]),
            "atr": None if pd.isna(r["atr"]) else float(r["atr"]), "trail_exit": bool(r["trail_exit"])}


# --------------------------------------------------------------------------- one bar of the simulation
def new_state(equity: float = 1.0) -> dict:
    return {"cash": equity, "units": 0.0, "fill": None, "stop": None, "level": None, "atr": None, "entry_time": None,
            "bars": 0, "exit_next": None, "pending": None, "failed": False, "retest": False, "trades": [],
            "equity": equity}


def step(state: dict, bar: dict, decision: dict, cfg: dict) -> tuple[dict, list[dict]]:
    """Advance one 4h bar; the entry / exit decided at the previous close executes at this open."""
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    st = dict(state)
    events: list[dict] = []
    o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])

    def close_position(px: float, reason: str) -> None:
        exec_px = px * (1 - s)
        proceeds = st["units"] * exec_px
        fee = proceeds * f
        pnl = proceeds - fee - st["units"] * st["fill"] * (1 + f)
        risk = st["units"] * (st["fill"] - st["stop"])
        trade = {"entry_time": st["entry_time"], "exit_time": int(bar["open_time"]), "fill": st["fill"], "exit": exec_px,
                 "stop": st["stop"], "target": None, "level": st["level"], "units": st["units"], "pnl": pnl,
                 "r": pnl / risk if risk > 0 else 0.0, "reason": reason, "bars": st["bars"],
                 "return": pnl / st["equity_at_entry"], "fees": st["units"] * st["fill"] * f + fee,
                 "slippage": st["units"] * (st["fill"] - st["fill"] / (1 + s)) + st["units"] * (px - exec_px),
                 "failed_breakout": st["failed"], "retest": st["retest"]}
        st["cash"] += proceeds - fee
        st.update(units=0.0, fill=None, stop=None, level=None, atr=None, entry_time=None, bars=0, exit_next=None,
                  failed=False, retest=False)
        st["trades"] = st["trades"] + [trade]
        events.append({"kind": "exit", **trade})

    if st["units"] and st["exit_next"]:
        close_position(o, st["exit_next"])
    elif not st["units"] and st["pending"] is not None:
        p = st["pending"]
        fill = o * (1 + s)
        if o > p["stop"]:
            equity = st["cash"]
            units = min((cfg["risk_pct"] / 100 * equity) / (fill - p["stop"]), equity / (fill * (1 + f)))
            st["cash"] -= units * fill * (1 + f)
            st.update(units=units, fill=fill, stop=p["stop"], level=p["level"], atr=p["atr"],
                      entry_time=int(bar["open_time"]), bars=0, equity_at_entry=equity, failed=False, retest=False)
            events.append({"kind": "entry", "time": int(bar["open_time"]), "fill": fill, "stop": p["stop"], "units": units})
        else:
            events.append({"kind": "skipped", "time": int(bar["open_time"]), "reason": "opened at or below the stop"})
    st["pending"] = None

    if st["units"]:
        st["bars"] += 1
        if o <= st["stop"] and st["bars"] > 1:
            close_position(o, "stop (gap)")
        elif l <= st["stop"]:
            close_position(st["stop"], "stop")

    if st["units"]:
        if st["level"] is not None:                    # diagnostics only
            if st["bars"] <= 3 and c < st["level"]:
                st["failed"] = True
            if (st["bars"] <= 10 and not st["failed"] and st["atr"] is not None
                    and l <= st["level"] + 0.25 * st["atr"]):
                st["retest"] = True
        if decision.get("trail_exit"):
            st["exit_next"] = f"trailing exit (close below the {cfg['trail_bars']}-bar low)"
        elif st["bars"] >= cfg["time_limit_bars"]:
            st["exit_next"] = f"time limit ({cfg['time_limit_bars']} bars)"
    elif decision.get("buy"):
        st["pending"] = {"stop": float(decision["stop_plan"]), "level": decision.get("level"), "atr": decision.get("atr")}
    st["equity"] = st["cash"] + st["units"] * c * (1 - f - s)
    return st, events


def simulate(ft: pd.DataFrame, cfg: dict, start: int, end: int) -> dict[str, Any]:
    st, curve = new_state(), []
    for i in range(start, end + 1):
        r = ft.iloc[i]
        d = _decision(r)
        d["buy"] = d["buy"] and i < end
        st, _ = step(st, r, d, cfg)
        curve.append(st["equity"])
    if st["units"]:
        px = float(ft["close"].iloc[end])
        st["exit_next"] = "end of design data"
        st, _ = step(st, {"open_time": int(ft["close_time"].iloc[end]) + 1, "open": px, "high": px, "low": px,
                          "close": px}, {}, cfg)
        curve[-1] = st["equity"]
    return {"trades": st["trades"], "equity": curve}


# --------------------------------------------------------------------------- metrics, baselines, criteria
def max_dd(curve) -> float:
    v = np.asarray(curve, float)
    if not len(v):
        return 0.0
    peak = np.maximum.accumulate(v)
    return float(np.max((peak - v) / peak)) * 100


def metrics(trades: list[dict], curve: list[float], bars: int) -> dict[str, Any]:
    pnl = [t["pnl"] for t in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    return {
        "trades": len(trades), "net_return_pct": (curve[-1] - 1) * 100 if curve else 0.0,
        "max_drawdown_pct": max_dd(curve),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else None,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None,
        "expectancy_r": float(np.mean([t["r"] for t in trades])) if trades else None,
        "avg_trade_pct": float(np.mean([t["return"] for t in trades])) * 100 if trades else None,
        "avg_win_pct": float(np.mean([t["return"] for t in trades if t["pnl"] > 0])) * 100 if wins else None,
        "avg_loss_pct": float(np.mean([t["return"] for t in trades if t["pnl"] <= 0])) * 100 if losses else None,
        "avg_win_r": float(np.mean([t["r"] for t in trades if t["pnl"] > 0])) if wins else None,
        "exposure_pct": sum(t["bars"] for t in trades) / max(1, bars) * 100,
        "avg_bars_held": float(np.mean([t["bars"] for t in trades])) if trades else None,
        "fees_pct": sum(t["fees"] for t in trades) * 100, "slippage_pct": sum(t["slippage"] for t in trades) * 100,
        "failed_breakouts": sum(1 for t in trades if t["failed_breakout"]),
        "retests": sum(1 for t in trades if t["retest"]),
        "exit_reasons": {k: sum(1 for t in trades if t["reason"].startswith(k)) for k in
                         ("stop", "trailing exit", "time limit", "end of design data")},
    }


def buy_and_hold(ft: pd.DataFrame, start: int, end: int, cfg: dict) -> dict:
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    units = (1 - f) / (float(ft["open"].iloc[start]) * (1 + s))
    curve = [units * c * (1 - f - s) for c in ft["close"].iloc[start:end + 1]]
    return {"net_return_pct": (curve[-1] - 1) * 100, "max_drawdown_pct": max_dd(curve)}


def random_entries(ft: pd.DataFrame, trades: list[dict], cfg: dict, start: int, end: int, runs: int = 1000,
                   seed: int = 7) -> dict:
    """Same number of trades, each keeping a strategy trade's holding time as its maximum, entered at random
    non-overlapping bars where the same stop formula gives a stop below the open, with the same stop, sizing, costs."""
    rng = np.random.default_rng(seed)
    holds = [max(1, int(t["bars"])) for t in trades]
    if not holds:
        return {"runs": 0, "returns_pct": [], "median_return_pct": None}
    rec = ft.to_dict("records")
    ok = np.zeros(len(rec), bool)
    for e in range(max(1, start), end + 1):
        sp = rec[e - 1]["stop_plan"]
        if sp == sp and sp < rec[e]["open"]:
            ok[e] = True
    results = []
    for _ in range(runs):
        eq, used = 1.0, np.zeros(len(rec), bool)
        for hold in rng.permutation(holds):
            for _try in range(50):
                e = int(rng.integers(start + 1, end + 1))
                last = min(end, e + hold - 1)
                if ok[e] and not used[e:last + 2].any():
                    break
            else:
                continue
            used[e:last + 2] = True
            p = rec[e - 1]
            st = new_state(eq)
            st["pending"] = {"stop": float(p["stop_plan"]), "level": None, "atr": None}
            for i in range(e, last + 1):
                st, _ = step(st, rec[i], {}, {**cfg, "time_limit_bars": hold})
                if not st["units"] and st["trades"]:
                    break
            if st["units"]:
                st["exit_next"] = "time"
                nxt = rec[last + 1] if last + 1 <= end else {**rec[last], "open": rec[last]["close"],
                                                             "high": rec[last]["close"], "low": rec[last]["close"]}
                st, _ = step(st, nxt, {}, cfg)
            eq = st["cash"]
        results.append((eq - 1) * 100)
    return {"runs": len(results), "returns_pct": results, "median_return_pct": float(np.median(results))}


def evaluate_criteria(m: dict, beaten_pct: float | None) -> tuple[list[dict], str]:
    pf = m["profit_factor"]
    crit = [
        {"id": "C1", "rule": "expectancy >= +0.15R per trade after fees and slippage", "value": m["expectancy_r"],
         "passed": m["expectancy_r"] is not None and m["expectancy_r"] >= 0.15},
        {"id": "C2", "rule": "profit factor >= 1.3", "value": pf, "passed": pf is not None and pf >= 1.3},
        {"id": "C3", "rule": "max drawdown <= 15% of the account", "value": m["max_drawdown_pct"],
         "passed": m["max_drawdown_pct"] <= 15},
        {"id": "C4", "rule": "beats >= 90% of 1,000 random-entry runs (same trades, holding times, stop rule)",
         "value": beaten_pct, "passed": beaten_pct is not None and beaten_pct >= 90},
    ]
    if m["trades"] < 30:
        return crit, "INCONCLUSIVE"
    return crit, "PASS" if all(c["passed"] for c in crit) else "FAIL"


def _design_frames(rows4, rows1, holdout_start):
    df4 = pd.DataFrame(rows4).sort_values("open_time")
    df4 = df4[df4["close_time"] < holdout_start].reset_index(drop=True)
    df1 = pd.DataFrame(rows1).sort_values("open_time") if rows1 else pd.DataFrame()
    if len(df1):
        df1 = df1[df1["close_time"] < holdout_start].reset_index(drop=True)
    return df4, df1


def backtest(rows4: list[dict], rows1: list[dict], cfg: dict, holdout_start: int = HOLDOUT_START,
             random_runs: int = 1000) -> dict[str, Any]:
    from services.strategies import trend           # comparisons only (reported), never a rule
    df4, df1 = _design_frames(rows4, rows1, holdout_start)
    if len(df4) < WARMUP + 200:
        raise ValueError("Not enough 4h candles before the holdout start.")
    assert int(df4["close_time"].max()) < holdout_start, "design data reaches the holdout"
    start, end = WARMUP, len(df4) - 1
    ft = features(df4, cfg)
    sim = simulate(ft, cfg, start, end)
    m = metrics(sim["trades"], sim["equity"], end - start + 1)
    rnd = random_entries(ft, sim["trades"], cfg, start, end, runs=random_runs)
    beaten = float(np.mean([m["net_return_pct"] > x for x in rnd["returns_pct"]])) * 100 if rnd["returns_pct"] else None
    criteria, verdict = evaluate_criteria(m, beaten)
    ct = ft["close_time"].to_numpy()
    eq = np.asarray(sim["equity"])
    windows, k0 = [], start
    while k0 <= end:
        k1 = min(end, int(np.searchsorted(ct, ct[k0] + 182 * 86_400_000, side="right")) - 1)
        e0 = eq[k0 - start - 1] if k0 > start else 1.0
        tr = [t for t in sim["trades"] if ft["open_time"].iloc[k0] <= t["entry_time"] <= ft["open_time"].iloc[k1]]
        windows.append({"start": int(ft["open_time"].iloc[k0]), "end": int(ct[k1]), "trades": len(tr),
                        "return_pct": (eq[k1 - start] / e0 - 1) * 100, "sum_r": float(sum(t["r"] for t in tr))})
        k0 = k1 + 1
    sens = []
    for lb in (20, 40, 55):
        for vm in (1.0, 1.2, 1.5):
            c2 = {**cfg, "lookback": lb, "volume_mult": vm}
            s2 = simulate(features(df4, c2), c2, start, end)
            m2 = metrics(s2["trades"], s2["equity"], end - start + 1)
            sens.append({"lookback": lb, "volume_mult": vm, "trades": m2["trades"], "expectancy_r": m2["expectancy_r"],
                         "profit_factor": m2["profit_factor"], "max_drawdown_pct": m2["max_drawdown_pct"],
                         "net_return_pct": m2["net_return_pct"],
                         "is_default": lb == cfg["lookback"] and vm == cfg["volume_mult"]})
    first_t, last_t = int(ft["open_time"].iloc[start]), int(ft["open_time"].iloc[end])
    bh = buy_and_hold(ft, start, end, cfg)
    h2 = trend.h2_rule(df1, first_t, last_t, cfg) if len(df1) else None
    tcfg = trend.validate_config({})
    tsim = trend.simulate(trend.indicators(df4, df1 if len(df1) else None, tcfg), tcfg, start, end)
    tm = trend.metrics(tsim["trades"], tsim["equity"], end - start + 1)
    curve = sim["equity"]
    idx = np.linspace(0, len(curve) - 1, min(400, len(curve))).astype(int)
    return {
        "strategy": STRATEGY_ID, "version": VERSION, "config": cfg, "config_hash": config_hash(cfg),
        "period": {"kind": "design", "first_day": first_t, "last_day": last_t, "last_day_close": int(ct[end]),
                   "holdout_start": holdout_start, "bars": end - start + 1},
        "metrics": {**m, "avg_reward_risk": None, "buy_hold_return_pct": bh["net_return_pct"],
                    "costs_usd": None, "costs_pct": m["fees_pct"] + m["slippage_pct"]},
        "criteria": criteria, "verdict": verdict,
        "sample_warning": (f"Only {m['trades']} trades (fewer than 30): too few to judge; the verdict is INCONCLUSIVE."
                           if m["trades"] < 30 else None),
        "random_entries": {"runs": rnd["runs"], "beaten_pct": beaten, "median_return_pct": rnd["median_return_pct"]},
        "buy_and_hold": bh, "h2_rule": h2,
        "trend_same_window": {"net_return_pct": tm["net_return_pct"], "max_drawdown_pct": tm["max_drawdown_pct"],
                              "trades": tm["trades"], "expectancy_r": tm["expectancy_r"],
                              "profit_factor": tm["profit_factor"]},
        "diagnostics": {"failed_breakouts": m["failed_breakouts"], "retests": m["retests"], "trades": m["trades"]},
        "walk_forward": windows, "sensitivity": sens,
        "trade_list": [{k: t[k] for k in ("entry_time", "exit_time", "fill", "exit", "stop", "target", "r", "reason",
                                          "bars", "return", "failed_breakout", "retest")} for t in sim["trades"]],
        "equity_sample": [{"time": int(ft["open_time"].iloc[start + i]), "equity": curve[i]} for i in idx],
        "limitations": LIMITATIONS,
    }


# --------------------------------------------------------------------------- live research signal
def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def evaluate_frames(frames: dict[str, pd.DataFrame], cfg: dict, now: int, data: DataQuality, regime: str | None = None,
                    config_version: int = 1) -> StrategyResult:
    """The live signal replays the strategy's own step() over the recent closed bars from a flat start, so a live
    BUY SETUP / HOLD / EXIT is exactly what the backtest would have done on these bars."""
    df4 = frames.get("4h")
    f, s = cfg["fee_pct"], cfg["slippage_pct"]
    base = dict(strategy_id=STRATEGY_ID, strategy_version=VERSION, evaluated_at=now, asset="BTCUSDT", timeframe="4h",
                fees_pct=f, slippage_pct=s, data=data, regime=regime, config_version=config_version,
                config_hash=config_hash(cfg))
    labels = (["LIVE DATA"] if not data.is_stale else []) + ["CALCULATED", "MODEL ESTIMATE"]
    none = dict(strength=None, entry_zone=None, stop=None, targets=[], reward_risk=None)
    need = max(cfg["lookback"], cfg["volume_window"], cfg["trail_bars"]) + 20
    if df4 is None or len(df4) < need:
        return StrategyResult(**base, **none, signal="NO TRADE", supporting=[], contradictory=[],
                              explanation="Not enough closed 4h candles.", rejection="Not enough data.", labels=labels)
    ft = features(df4, cfg)
    r = ft.iloc[-1]
    checks = [(f"close {r['close']:,.2f} above the {cfg['lookback']}-bar high {r['level']:,.2f}", bool(r["close_above"])),
              (f"volume >= {cfg['volume_mult']:g} x the {cfg['volume_window']}-bar average", bool(r["volume_ok"])),
              ("stop below the close", bool(r["stop_plan"] < r["close"]) if not pd.isna(r["stop_plan"]) else False)]
    supporting = [x for x, ok in checks if ok]
    contradictory = [f"not: {x}" for x, ok in checks if not ok]
    strength = int(round(100 * sum(ok for _, ok in checks) / len(checks)))
    problems = (["4h data is stale"] if data.is_stale else []) + (
        [f"{data.missing_candles} missing 4h candle(s)"] if data.missing_candles else [])
    if problems:
        return StrategyResult(**base, **{**none, "strength": strength}, signal="NO TRADE", supporting=supporting,
                              contradictory=contradictory + problems, explanation="Data problem: no research signal.",
                              rejection="; ".join(problems), labels=labels)
    lo = max(0, len(ft) - cfg["time_limit_bars"] - 10)
    st = new_state()
    for i in range(lo, len(ft)):
        st, _ = step(st, ft.iloc[i], _decision(ft.iloc[i]), cfg)
    when = _utc(int(r["close_time"]) + 1)
    if st["units"] and st["exit_next"]:
        return StrategyResult(**base, **{**none, "strength": strength}, signal="EXIT / REDUCE EXPOSURE",
                              supporting=supporting, contradictory=contradictory,
                              explanation=f"{st['exit_next'].capitalize()}: a simulated position from the breakout at "
                                          f"{_utc(st['entry_time'])} would exit at the next open ({when}).",
                              rejection=None, labels=labels)
    if st["units"]:
        return StrategyResult(**base, **{**none, "strength": strength}, signal="HOLD", supporting=supporting,
                              contradictory=contradictory,
                              explanation=f"A simulated position from the breakout at {_utc(st['entry_time'])} is still "
                                          f"open: stop {st['stop']:,.2f}, trailing exit below the {cfg['trail_bars']}-bar "
                                          "low.", rejection="Already in a position: no new entry.", labels=labels)
    if st["pending"] is not None:
        entry, stop = float(r["close"]), float(st["pending"]["stop"])
        return StrategyResult(**base, strength=strength, entry_zone={"low": round(entry, 2), "high": round(entry, 2)},
                              stop=round(stop, 2), targets=[], reward_risk=None, signal="BUY SETUP",
                              supporting=supporting, contradictory=contradictory,
                              explanation=f"Research setup: a 4h close above the {cfg['lookback']}-bar high on volume; "
                                          f"buy at the next open ({when}), stop {stop:,.2f} (level - {cfg['stop_atr']:g} "
                                          f"ATR). No fixed target: trailing exit below the {cfg['trail_bars']}-bar low or "
                                          f"after {cfg['time_limit_bars']} bars.", rejection=None, labels=labels)
    return StrategyResult(**base, **{**none, "strength": strength}, signal="NO TRADE", supporting=supporting,
                          contradictory=contradictory, explanation="No breakout on the last closed 4h bar.",
                          rejection="Breakout rule not met: " + (", ".join(x for x, ok in checks if not ok) or
                                                                 "a position from these bars just closed") + ".",
                          labels=labels)


# --------------------------------------------------------------------------- forward test (strategy_forward.py)
def forward_step(cfg: dict, ctx: dict) -> tuple[list[dict], dict]:
    rows4, k = ctx["rows"], ctx["k"]
    lo = max(0, k - 1500)
    r = features(pd.DataFrame(rows4[lo:k + 1]), cfg).iloc[-1]
    state = ctx["state"] or new_state()
    state, evs = step(state, r, _decision(r), cfg)
    out = [{"kind": "buy", "detail": e} for e in evs if e["kind"] == "entry"]
    out.append({"kind": "mark", "detail": {"close": float(r["close"]), "equity": state["equity"],
                                           "state": {k2: v for k2, v in state.items() if k2 != "trades"},
                                           "trades": state["trades"], "events": evs}})
    return out, state


def forward_state(events: list[dict]) -> dict | None:
    marks = [e for e in events if e["kind"] == "mark"]
    if not marks:
        return None
    d = marks[-1]["detail"]
    return {**d["state"], "trades": d["trades"]}


def forward_summary(events: list[dict], cfg: dict, done: bool) -> dict:
    marks = [e for e in events if e["kind"] == "mark"]
    out = {"trades": 0, "net_return_pct": None, "expectancy_r": None, "max_drawdown_pct": None, "criteria": [],
           "verdict": None}
    if not marks:
        return out
    trades = marks[-1]["detail"]["trades"]
    m = metrics(trades, [x["detail"]["equity"] for x in marks], len(marks))
    out.update(trades=m["trades"], net_return_pct=m["net_return_pct"], expectancy_r=m["expectancy_r"],
               max_drawdown_pct=m["max_drawdown_pct"])
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
        return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{d}f}"

    m, dg = r["metrics"], r["diagnostics"]
    day = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    h2, tw = r["h2_rule"] or {}, r["trend_same_window"]
    L = ["# Breakout trading design backtest (design-data evidence only)", "",
         f"Pre-registered in `{PREREGISTRATION}` (config hash `{r['config_hash']}`), run once. 4h decisions "
         f"{day(r['period']['first_day'])} to {day(r['period']['last_day'])} ({r['period']['bars']:,} bars), all closed "
         f"before the holdout start {day(r['period']['holdout_start'])}. Simulated, long only, "
         f"{r['config']['risk_pct']:g}% planned risk per trade, no fixed target.", ""]
    if r["sample_warning"]:
        L += [f"**{r['sample_warning']}**", ""]
    L += [f"## Verdict: {r['verdict']} (design data, not validation)", "", "| # | Rule | Value | Passed |", "|---|---|---|---|"]
    for c in r["criteria"]:
        L.append(f"| {c['id']} | {c['rule']} | {n(c['value'])} | {'yes' if c['passed'] else 'no'} |")
    L += ["", "## Results and comparisons (comparisons reported only)", "",
          "| | Breakout | Buy & hold | H2 rule | Trend (same window) |", "|---|---|---|---|---|",
          f"| Net return | {p(m['net_return_pct'])} | {p(r['buy_and_hold']['net_return_pct'])} | {p(h2.get('net_return_pct'))} | {p(tw['net_return_pct'])} |",
          f"| Max drawdown | {m['max_drawdown_pct']:.2f}% | {r['buy_and_hold']['max_drawdown_pct']:.2f}% | {n(h2.get('max_drawdown_pct'))}% | {tw['max_drawdown_pct']:.2f}% |",
          f"| Trades | {m['trades']} | 1 | {h2.get('entries', 'n/a')} entries | {tw['trades']} |", "",
          f"Win rate {n(m['win_rate_pct'], 1)}%, profit factor {n(m['profit_factor'])}, expectancy {n(m['expectancy_r'])}R "
          f"(average win {n(m['avg_win_r'])}R), average trade {p(m['avg_trade_pct'])}, exposure {m['exposure_pct']:.1f}%, "
          f"average hold {n(m['avg_bars_held'], 1)} bars, fees {m['fees_pct']:.2f}% and slippage {m['slippage_pct']:.2f}% "
          "of the account. Exits: " + ", ".join(f"{k} {v}" for k, v in m["exit_reasons"].items()) + ".", "",
          f"Diagnostics (not filters): {dg['failed_breakouts']} of {dg['trades']} trades closed back below the level within "
          f"3 bars (failed breakouts); {dg['retests']} retested the level within 10 bars without such a close.", "",
          f"Random entries ({r['random_entries']['runs']} runs): the strategy beat {n(r['random_entries']['beaten_pct'], 1)}% "
          f"of them (median random run {p(r['random_entries']['median_return_pct'])}).", "",
          "## Walk-forward windows (182 days, fixed rules, reported only)", "", "| Window | Trades | Return | Sum of R |",
          "|---|---|---|---|"]
    for w in r["walk_forward"]:
        L.append(f"| {day(w['start'])} to {day(w['end'])} | {w['trades']} | {p(w['return_pct'])} | {w['sum_r']:+.2f} |")
    L += ["", "## Sensitivity (reported only, never used to choose parameters)", "",
          "| Lookback | Volume | Trades | Expectancy | Profit factor | Max DD | Net return |", "|---|---|---|---|---|---|---|"]
    for x in r["sensitivity"]:
        L.append(f"| {x['lookback']}{' (default)' if x['is_default'] else ''} | {x['volume_mult']:g}x | {x['trades']} | "
                 f"{n(x['expectancy_r'])}R | {n(x['profit_factor'])} | {x['max_drawdown_pct']:.2f}% | {p(x['net_return_pct'])} |")
    L += ["", "## Limitations", ""] + [f"- {x}" for x in r["limitations"]] + [""]
    return "\n".join(L)
