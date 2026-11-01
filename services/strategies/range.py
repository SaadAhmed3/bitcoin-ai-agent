"""Strategy 3: Range trading (4h, long only). Research only, simulated only.

Rules fixed in PREREGISTRATION_RANGE.md before the first backtest. At the CLOSE of 4h bar i (closed candles only):
  regime    = the existing 4h market regime (services/regime.py, regime_at) is SIDEWAYS / RANGING and ADX(14) < 20
  range     = highest high / lowest low of the `lookback` (50) closed 4h bars BEFORE bar i
  valid     = each boundary has >= 2 touches and the width >= 3 x ATR(14). A touch is a bar whose high (low) is within
              0.25 x ATR(14) of the range high (low); touching bars next to each other count as one touch.
  trigger   = (a) bar i traded below the range low (its low below it) and closed back above it, or
              (b) bar i-1 closed in the bottom 20% of the range and bar i closed bullish (close > open)
  BUY SETUP = regime and valid and trigger, and the close is below the range midpoint -> buy at the NEXT open
  stop      = range low - 0.5 x ATR(14); target = range midpoint (sensitivity: the range high)
  exits     = stop (first if a bar touches both), target, the regime leaving SIDEWAYS at a close -> next open,
              30 x 4h bars -> next open. 1% planned risk, never more than 100% of the account.

Self-contained on purpose: its forward-test fingerprint covers this module and the tracker core only, so nothing here
depends on the other strategy modules. It depends on services/regime.py, which is pinned below: if that file changes,
results and the forward test are flagged "dependency changed".
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.backtesting import HOLDOUT_START
from services.regime import regime_at
from services.strategies.base import DataQuality, StrategyResult, config_hash
from services.technical_analysis import adx, atr

STRATEGY_ID = "range"
VERSION = "range-1"
PREREGISTRATION = "PREREGISTRATION_RANGE.md"
EVAL_TIMEFRAMES = ("4h",)
BACKTEST_TIMEFRAMES = ("4h",)
FORWARD_TIMEFRAME = "4h"
SIDEWAYS = "SIDEWAYS / RANGING"
REGIME_FILE = Path(__file__).resolve().parents[1] / "regime.py"
REGIME_SHA256 = "c70265f88c17281ab588508874fdf47dda9304f4700eb3debf22c54bb8559bcd"     # services/regime.py, 2026-11-01

DEFAULTS: dict[str, Any] = {"lookback": 50, "adx_max": 20.0, "touch_atr": 0.25, "min_touches": 2,
                            "min_width_atr": 3.0, "bottom_zone": 0.2, "stop_atr": 0.5, "target": "mid",
                            "time_limit_bars": 30, "risk_pct": 1.0, "fee_pct": 0.10, "slippage_pct": 0.05}
FIELDS: dict[str, dict] = {
    "lookback": {"kind": "int", "lo": 10, "hi": 200, "help": "Range = high / low of this many closed 4h bars"},
    "adx_max": {"kind": "float", "lo": 5, "hi": 40, "help": "ADX(14) must be below this"},
    "touch_atr": {"kind": "float", "lo": 0, "hi": 2, "help": "A touch is within this many ATR(14) of a boundary"},
    "min_touches": {"kind": "int", "lo": 1, "hi": 10, "help": "Touches needed on each boundary"},
    "min_width_atr": {"kind": "float", "lo": 0.5, "hi": 20, "help": "Range width at least this many ATR(14)"},
    "bottom_zone": {"kind": "float", "lo": 0.05, "hi": 0.5, "help": "Bottom zone of the range (share of the width)"},
    "stop_atr": {"kind": "float", "lo": 0, "hi": 3, "help": "Stop this many ATR(14) below the range low"},
    "target": {"kind": "choice", "choices": ("mid", "top"), "help": "Target: range midpoint or range high"},
    "time_limit_bars": {"kind": "int", "lo": 5, "hi": 200, "help": "Exit after this many 4h bars"},
    "risk_pct": {"kind": "float", "lo": 0.1, "hi": 2, "help": "Planned loss to the stop, % of the account (max 2)"},
    "fee_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Fee per side (%)"},
    "slippage_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Slippage per side (%)"},
}
LIMITATIONS = [
    "Design data only (before 2025-10-29): evidence about the past, not validation.",
    "One asset and one price source (Binance BTCUSDT 4h); fixed costs; fills at the next open.",
    "The regime label comes from services/regime.py (pinned by SHA-256); its own thresholds are part of these rules.",
    "Long only: only the bottom of a range is bought; no short at the top.",
]


def regime_dependency_ok() -> bool:
    return hashlib.sha256(REGIME_FILE.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == REGIME_SHA256


def validate_config(payload: dict | None) -> dict[str, Any]:
    payload = dict(payload or {})
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown range setting(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for k, default in DEFAULTS.items():
        v, f = payload.get(k, default), FIELDS[k]
        try:
            if f["kind"] == "choice":
                if v not in f["choices"]:
                    raise ValueError
            elif isinstance(v, bool):
                raise ValueError
            elif f["kind"] == "int":
                if float(v) != int(float(v)):
                    raise ValueError
                v = int(float(v))
            else:
                v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"'{k}' has the wrong value (expected {f['kind']})") from None
        if f["kind"] != "choice" and not f["lo"] <= v <= f["hi"]:
            raise ValueError(f"'{k}' must be between {f['lo']} and {f['hi']}")
        out[k] = v
    return out


# --------------------------------------------------------------------------- features
def regime_labels(df4: pd.DataFrame, upto: int | None = None, start: int = 0,
                  progress: Callable[[int, int], None] | None = None) -> list[str | None]:
    """The existing 4h regime label at each bar, each from its own past only (regime_at)."""
    n = len(df4) if upto is None else upto + 1
    out: list[str | None] = [None] * len(df4)
    for i in range(start, n):
        out[i] = regime_at(df4, i)["regime"]
        if progress and i % 500 == 0:
            progress(i, n)
    return out


def _touches(values: np.ndarray, hit: np.ndarray) -> int:
    """Touch episodes: touching bars next to each other count once."""
    count, prev = 0, False
    for h in hit:
        if h and not prev:
            count += 1
        prev = bool(h)
    return count


def features(df4: pd.DataFrame, labels: list[str | None], cfg: dict) -> pd.DataFrame:
    """Per closed 4h bar: the range of the `lookback` bars before it, checks, trigger, stop and target."""
    d = df4.sort_values("open_time").reset_index(drop=True)[["open_time", "open", "high", "low", "close", "close_time"]].astype(
        {"open": float, "high": float, "low": float, "close": float})
    n, lb = len(d), cfg["lookback"]
    d["atr"] = atr(d, 14)
    d["adx"] = adx(d, 14)["adx"]
    d["regime"] = labels[:n] if len(labels) >= n else labels + [None] * (n - len(labels))
    d["rh"] = d["high"].rolling(lb, min_periods=lb).max().shift(1)
    d["rl"] = d["low"].rolling(lb, min_periods=lb).min().shift(1)
    d["width"] = d["rh"] - d["rl"]
    d["mid"] = (d["rh"] + d["rl"]) / 2
    hi, lo, a = d["high"].to_numpy(), d["low"].to_numpy(), d["atr"].to_numpy()
    rh, rl = d["rh"].to_numpy(), d["rl"].to_numpy()
    top, bot = np.zeros(n, int), np.zeros(n, int)
    for i in range(lb, n):
        if np.isnan(rh[i]) or np.isnan(a[i]):
            continue
        tol = cfg["touch_atr"] * a[i]
        top[i] = _touches(hi[i - lb:i], hi[i - lb:i] >= rh[i] - tol)
        bot[i] = _touches(lo[i - lb:i], lo[i - lb:i] <= rl[i] + tol)
    d["touches_top"], d["touches_bottom"] = top, bot
    d["regime_ok"] = d["regime"] == SIDEWAYS
    d["adx_ok"] = d["adx"] < cfg["adx_max"]
    d["width_ok"] = d["width"] >= cfg["min_width_atr"] * d["atr"]
    d["touch_ok"] = (d["touches_top"] >= cfg["min_touches"]) & (d["touches_bottom"] >= cfg["min_touches"])
    d["valid"] = d["regime_ok"] & d["adx_ok"] & d["width_ok"] & d["touch_ok"]
    trig_a = (d["low"] < d["rl"]) & (d["close"] > d["rl"])
    trig_b = (d["close"].shift(1) <= d["rl"] + cfg["bottom_zone"] * d["width"]) & (d["close"] > d["open"])
    d["trigger"] = trig_a | trig_b
    d["trigger_kind"] = np.where(trig_a, "reclaim of the range low", np.where(trig_b, "bullish close from the bottom zone", ""))
    d["stop_plan"] = d["rl"] - cfg["stop_atr"] * d["atr"]
    d["target_plan"] = d["mid"] if cfg["target"] == "mid" else d["rh"]
    d["buy"] = d["valid"] & d["trigger"] & (d["close"] < d["mid"]) & (d["stop_plan"] < d["close"])
    d["regime_exit"] = ~d["regime_ok"]
    return d


# --------------------------------------------------------------------------- one bar of the simulation
def new_state(equity: float = 1.0) -> dict:
    return {"cash": equity, "units": 0.0, "fill": None, "stop": None, "target": None, "entry_time": None,
            "bars": 0, "exit_next": None, "pending": None, "trades": [], "equity": equity}


def step(state: dict, bar: dict, decision: dict, cfg: dict) -> tuple[dict, list[dict]]:
    """Advance one 4h bar. decision: this bar's CLOSE decision (buy with stop_plan/target_plan, regime_exit).
    The entry / exit decided at the previous close executes at this bar's open."""
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
                 "stop": st["stop"], "target": st["target"], "units": st["units"], "pnl": pnl,
                 "r": pnl / risk if risk > 0 else 0.0, "reason": reason, "bars": st["bars"],
                 "return": pnl / st["equity_at_entry"], "fees": st["units"] * st["fill"] * f + fee,
                 "slippage": st["units"] * (st["fill"] - st["fill"] / (1 + s)) + st["units"] * (px - exec_px)}
        st["cash"] += proceeds - fee
        st.update(units=0.0, fill=None, stop=None, target=None, entry_time=None, bars=0, exit_next=None)
        st["trades"] = st["trades"] + [trade]
        events.append({"kind": "exit", **trade})

    if st["units"] and st["exit_next"]:
        close_position(o, st["exit_next"])
    elif not st["units"] and st["pending"] is not None:
        stop, target = st["pending"]
        fill = o * (1 + s)
        if stop < fill < target:
            equity = st["cash"]
            units = min((cfg["risk_pct"] / 100 * equity) / (fill - stop), equity / (fill * (1 + f)))
            st["cash"] -= units * fill * (1 + f)
            st.update(units=units, fill=fill, stop=stop, target=target, entry_time=int(bar["open_time"]), bars=0,
                      equity_at_entry=equity)
            events.append({"kind": "entry", "time": int(bar["open_time"]), "fill": fill, "stop": stop, "target": target,
                           "units": units})
        else:
            events.append({"kind": "skipped", "time": int(bar["open_time"]),
                           "reason": "opened at or beyond the stop or the target"})
    st["pending"] = None

    if st["units"]:
        st["bars"] += 1
        if o <= st["stop"] and st["bars"] > 1:
            close_position(o, "stop (gap)")
        elif l <= st["stop"]:
            close_position(st["stop"], "stop")
        elif h >= st["target"]:
            close_position(max(st["target"], o) if st["bars"] > 1 else st["target"], "target")

    if st["units"]:
        if decision.get("regime_exit"):
            st["exit_next"] = "regime left SIDEWAYS"
        elif st["bars"] >= cfg["time_limit_bars"]:
            st["exit_next"] = f"time limit ({cfg['time_limit_bars']} bars)"
    elif decision.get("buy"):
        st["pending"] = (float(decision["stop_plan"]), float(decision["target_plan"]))
    st["equity"] = st["cash"] + st["units"] * c * (1 - f - s)
    return st, events


def _decision(r) -> dict:
    return {"buy": bool(r["buy"]), "stop_plan": r["stop_plan"], "target_plan": r["target_plan"],
            "regime_exit": bool(r["regime_exit"])}


def simulate(ft: pd.DataFrame, cfg: dict, start: int, end: int) -> dict[str, Any]:
    st, curve = new_state(), []
    for i in range(start, end + 1):
        r = ft.iloc[i]
        d = _decision(r)
        d["buy"] = d["buy"] and i < end
        st, _ = step(st, r, d, cfg)
        curve.append(st["equity"])
    if st["units"]:                            # still open at the end: closed at the last close, with costs
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
        "avg_planned_rr": float(np.mean([(t["target"] - t["fill"]) / (t["fill"] - t["stop"]) for t in trades])) if trades else None,
        "exposure_pct": sum(t["bars"] for t in trades) / max(1, bars) * 100,
        "avg_bars_held": float(np.mean([t["bars"] for t in trades])) if trades else None,
        "fees_pct": sum(t["fees"] for t in trades) * 100, "slippage_pct": sum(t["slippage"] for t in trades) * 100,
        "exit_reasons": {k: sum(1 for t in trades if t["reason"].startswith(k)) for k in
                         ("stop", "target", "regime left", "time limit", "end of design data")},
    }


def buy_and_hold(ft: pd.DataFrame, start: int, end: int, cfg: dict) -> dict:
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    units = (1 - f) / (float(ft["open"].iloc[start]) * (1 + s))
    curve = [units * c * (1 - f - s) for c in ft["close"].iloc[start:end + 1]]
    return {"net_return_pct": (curve[-1] - 1) * 100, "max_drawdown_pct": max_dd(curve)}


def random_entries(ft: pd.DataFrame, trades: list[dict], cfg: dict, start: int, end: int, runs: int = 1000,
                   seed: int = 7) -> dict:
    """Same number of trades, each keeping a strategy trade's holding time as its maximum, entered at random
    non-overlapping bars where the same formulas give stop < open < target, with the same stop/target, sizing, costs."""
    rng = np.random.default_rng(seed)
    holds = [max(1, int(t["bars"])) for t in trades]
    n = len(holds)
    if n == 0:
        return {"runs": 0, "returns_pct": [], "median_return_pct": None}
    rec = ft.to_dict("records")
    ok = np.zeros(len(rec), bool)                               # entry bar e usable: formulas of bar e-1 bracket open[e]
    for e in range(max(1, start), end + 1):
        p = rec[e - 1]
        sp, tp = p["stop_plan"], p["target_plan"]
        if sp == sp and tp == tp and sp < rec[e]["open"] < tp:
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
            st["pending"] = (float(p["stop_plan"]), float(p["target_plan"]))
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
        {"id": "C4", "rule": "beats >= 90% of 1,000 random-entry runs (same trades, holding times, stop/target rules)",
         "value": beaten_pct, "passed": beaten_pct is not None and beaten_pct >= 90},
    ]
    if m["trades"] < 30:
        return crit, "INCONCLUSIVE"
    return crit, "PASS" if all(c["passed"] for c in crit) else "FAIL"


WARMUP = 300                                   # bars before the first decision (regime needs 220, the range up to 80)


def backtest(rows4: list[dict], cfg: dict, holdout_start: int = HOLDOUT_START, random_runs: int = 1000,
             progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    df4 = pd.DataFrame(rows4).sort_values("open_time")
    df4 = df4[df4["close_time"] < holdout_start].reset_index(drop=True)
    if len(df4) < WARMUP + 200:
        raise ValueError("Not enough 4h candles before the holdout start.")
    assert int(df4["close_time"].max()) < holdout_start, "design data reaches the holdout"
    start, end = WARMUP, len(df4) - 1
    labels = regime_labels(df4, start=start - 1, progress=progress)
    ft = features(df4, labels, cfg)
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
    for lb in (30, 50, 80):
        for a in (15.0, 20.0, 25.0):
            c2 = {**cfg, "lookback": lb, "adx_max": a}
            s2 = simulate(features(df4, labels, c2), c2, start, end)
            m2 = metrics(s2["trades"], s2["equity"], end - start + 1)
            sens.append({"lookback": lb, "adx_max": a, "target": cfg["target"], "trades": m2["trades"],
                         "expectancy_r": m2["expectancy_r"], "profit_factor": m2["profit_factor"],
                         "max_drawdown_pct": m2["max_drawdown_pct"], "net_return_pct": m2["net_return_pct"],
                         "is_default": lb == cfg["lookback"] and a == cfg["adx_max"]})
    c3 = {**cfg, "target": "top"}
    s3 = simulate(features(df4, labels, c3), c3, start, end)
    m3 = metrics(s3["trades"], s3["equity"], end - start + 1)
    sens.append({"lookback": cfg["lookback"], "adx_max": cfg["adx_max"], "target": "top", "trades": m3["trades"],
                 "expectancy_r": m3["expectancy_r"], "profit_factor": m3["profit_factor"],
                 "max_drawdown_pct": m3["max_drawdown_pct"], "net_return_pct": m3["net_return_pct"], "is_default": False})
    bh = buy_and_hold(ft, start, end, cfg)
    curve = sim["equity"]
    idx = np.linspace(0, len(curve) - 1, min(400, len(curve))).astype(int)
    return {
        "strategy": STRATEGY_ID, "version": VERSION, "config": cfg, "config_hash": config_hash(cfg),
        "regime_module_sha256": REGIME_SHA256,
        "period": {"kind": "design", "first_day": int(ft["open_time"].iloc[start]), "last_day": int(ft["open_time"].iloc[end]),
                   "last_day_close": int(ct[end]), "holdout_start": holdout_start, "bars": end - start + 1},
        "metrics": {**m, "avg_reward_risk": m["avg_planned_rr"], "buy_hold_return_pct": bh["net_return_pct"],
                    "costs_usd": None, "costs_pct": m["fees_pct"] + m["slippage_pct"]},
        "criteria": criteria, "verdict": verdict,
        "sample_warning": (f"Only {m['trades']} trades (fewer than 30): too few to judge; the verdict is INCONCLUSIVE."
                           if m["trades"] < 30 else None),
        "random_entries": {"runs": rnd["runs"], "beaten_pct": beaten, "median_return_pct": rnd["median_return_pct"]},
        "buy_and_hold": bh, "walk_forward": windows, "sensitivity": sens,
        "regime_share_pct": float(np.mean(ft["regime_ok"].iloc[start:end + 1])) * 100,
        "valid_range_share_pct": float(np.mean(ft["valid"].iloc[start:end + 1])) * 100,
        "trade_list": [{k: t[k] for k in ("entry_time", "exit_time", "fill", "exit", "stop", "target", "r", "reason",
                                          "bars", "return")} for t in sim["trades"]],
        "equity_sample": [{"time": int(ft["open_time"].iloc[start + i]), "equity": curve[i]} for i in idx],
        "limitations": LIMITATIONS,
    }


# --------------------------------------------------------------------------- live research signal
def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def evaluate_frames(frames: dict[str, pd.DataFrame], cfg: dict, now: int, data: DataQuality, regime: str | None = None,
                    config_version: int = 1) -> StrategyResult:
    df4 = frames.get("4h")
    f, s = cfg["fee_pct"], cfg["slippage_pct"]
    base = dict(strategy_id=STRATEGY_ID, strategy_version=VERSION, evaluated_at=now, asset="BTCUSDT", timeframe="4h",
                fees_pct=f, slippage_pct=s, data=data, config_version=config_version, config_hash=config_hash(cfg))
    labels = (["LIVE DATA"] if not data.is_stale else []) + ["CALCULATED", "MODEL ESTIMATE"]
    none = dict(strength=None, entry_zone=None, stop=None, targets=[], reward_risk=None)
    dep = [] if regime_dependency_ok() else ["services/regime.py changed since this strategy was pre-registered"]
    if df4 is None or len(df4) < 240:
        return StrategyResult(**base, **none, regime=regime, signal="NO TRADE", supporting=[], contradictory=dep,
                              explanation="Not enough closed 4h candles for the regime and the range.",
                              rejection="Not enough data.", labels=labels)
    d = df4.sort_values("open_time").reset_index(drop=True)
    n = len(d)
    labs: list[str | None] = [None] * n
    labs[n - 2], labs[n - 1] = regime_at(d, n - 2)["regime"], regime_at(d, n - 1)["regime"]
    ft = features(d, labs, cfg)
    r, p = ft.iloc[-1], ft.iloc[-2]
    problems = (["4h data is stale"] if data.is_stale else []) + (
        [f"{data.missing_candles} missing 4h candle(s)"] if data.missing_candles else []) + dep
    checks = [(f"regime SIDEWAYS (now {r['regime']})", bool(r["regime_ok"])),
              (f"ADX14 {r['adx']:.1f} < {cfg['adx_max']:g}", bool(r["adx_ok"])),
              (f"range width >= {cfg['min_width_atr']:g} x ATR", bool(r["width_ok"])),
              (f"{int(r['touches_top'])} touches of the range high (>= {cfg['min_touches']})",
               bool(r["touches_top"] >= cfg["min_touches"])),
              (f"{int(r['touches_bottom'])} touches of the range low (>= {cfg['min_touches']})",
               bool(r["touches_bottom"] >= cfg["min_touches"]))]
    supporting = [x for x, ok in checks if ok]
    contradictory = [f"not: {x}" for x, ok in checks if not ok] + problems
    strength = int(round(100 * sum(ok for _, ok in checks) / len(checks)))
    when = _utc(int(r["close_time"]) + 1)
    rng = (f" Range {r['rl']:,.2f} to {r['rh']:,.2f} (midpoint {r['mid']:,.2f}) over {cfg['lookback']} bars."
           if not math.isnan(r["rh"]) else "")
    base["regime"] = r["regime"]
    if problems:
        return StrategyResult(**base, **{**none, "strength": strength}, signal="NO TRADE", supporting=supporting,
                              contradictory=contradictory, explanation="Data or dependency problem: no research signal.",
                              rejection="; ".join(problems), labels=labels)
    if bool(p["regime_ok"]) and not bool(r["regime_ok"]):
        return StrategyResult(**base, **{**none, "strength": strength}, signal="EXIT / REDUCE EXPOSURE",
                              supporting=supporting, contradictory=contradictory,
                              explanation=f"The 4h regime left SIDEWAYS ({r['regime']}). A simulated position would exit "
                                          f"at the next open ({when})." + rng, rejection=None, labels=labels)
    if bool(r["buy"]):
        entry, stop, target = float(r["close"]), float(r["stop_plan"]), float(r["target_plan"])
        cost = entry * 2 * (f + s) / 100
        rr = ((target - entry) - cost) / ((entry - stop) + cost)
        return StrategyResult(**base, strength=strength, entry_zone={"low": round(entry, 2), "high": round(entry, 2)},
                              stop=round(stop, 2), targets=[round(target, 2)], reward_risk=round(rr, 2),
                              signal="BUY SETUP", supporting=supporting + [str(r["trigger_kind"])],
                              contradictory=contradictory,
                              explanation=f"Research setup ({r['trigger_kind']}): buy at the next open ({when}), stop "
                                          f"{stop:,.2f} (range low - {cfg['stop_atr']} ATR), target {target:,.2f} "
                                          f"(range {'midpoint' if cfg['target'] == 'mid' else 'high'}). Reward/risk "
                                          f"after costs {rr:.2f}." + rng, rejection=None, labels=labels)
    if bool(r["valid"]):
        return StrategyResult(**base, **{**none, "strength": strength}, signal="HOLD", supporting=supporting,
                              contradictory=contradictory,
                              explanation="A valid sideways range, but no entry trigger in its lower part." + rng,
                              rejection="No entry trigger on the last closed 4h bar.", labels=labels)
    return StrategyResult(**base, **{**none, "strength": strength}, signal="NO TRADE", supporting=supporting,
                          contradictory=contradictory, explanation="No valid sideways range." + rng,
                          rejection="Range filter not met: " + ", ".join(x for x, ok in checks if not ok) + ".",
                          labels=labels)


# --------------------------------------------------------------------------- forward test (strategy_forward.py)
def forward_step(cfg: dict, ctx: dict) -> tuple[list[dict], dict]:
    """One closed 4h bar of the forward test, with exactly the backtest's features() and step()."""
    rows4, k = ctx["rows"], ctx["k"]
    lo = max(0, k - 1500)                      # enough history that Wilder smoothing (ATR, ADX) has settled
    d = pd.DataFrame(rows4[lo:k + 1]).reset_index(drop=True)
    labs: list[str | None] = [None] * len(d)
    labs[-1] = regime_at(d, len(d) - 1)["regime"]
    r = features(d, labs, cfg).iloc[-1]
    state = ctx["state"] or new_state()
    state, evs = step(state, r, _decision(r), cfg)
    out = [{"kind": "buy", "detail": e} for e in evs if e["kind"] == "entry"]
    out.append({"kind": "mark", "detail": {"close": float(r["close"]), "equity": state["equity"], "regime": r["regime"],
                                           "state": {k2: v for k2, v in state.items() if k2 != "trades"},
                                           "trades": state["trades"], "events": evs}})
    return out, state


def forward_state(events: list[dict]) -> dict | None:
    marks = [e for e in events if e["kind"] == "mark"]
    if not marks:
        return None
    d = marks[-1]["detail"]
    st = {**d["state"], "trades": d["trades"]}
    if st.get("pending") is not None:
        st["pending"] = tuple(st["pending"])
    return st


def forward_summary(events: list[dict], cfg: dict, done: bool) -> dict:
    marks = [e for e in events if e["kind"] == "mark"]
    out = {"trades": 0, "net_return_pct": None, "expectancy_r": None, "max_drawdown_pct": None, "criteria": [],
           "verdict": None, "dependency_changed": not regime_dependency_ok()}
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
        return "n/a" if v is None else f"{v:.{d}f}"

    m = r["metrics"]
    day = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    L = ["# Range trading design backtest (design-data evidence only)", "",
         f"Pre-registered in `{PREREGISTRATION}` (config hash `{r['config_hash']}`), run once. 4h decisions "
         f"{day(r['period']['first_day'])} to {day(r['period']['last_day'])} ({r['period']['bars']:,} bars), all closed "
         f"before the holdout start {day(r['period']['holdout_start'])}. Simulated, long only, "
         f"{r['config']['risk_pct']:g}% planned risk per trade. Regime SIDEWAYS on {r['regime_share_pct']:.1f}% of the "
         f"bars; a valid range (all filters) on {r['valid_range_share_pct']:.1f}%.", ""]
    if r["sample_warning"]:
        L += [f"**{r['sample_warning']}**", ""]
    L += [f"## Verdict: {r['verdict']} (design data, not validation)", "", "| # | Rule | Value | Passed |", "|---|---|---|---|"]
    for c in r["criteria"]:
        L.append(f"| {c['id']} | {c['rule']} | {n(c['value'])} | {'yes' if c['passed'] else 'no'} |")
    L += ["", "## Results", "", "| | Range | Buy & hold |", "|---|---|---|",
          f"| Net return | {p(m['net_return_pct'])} | {p(r['buy_and_hold']['net_return_pct'])} |",
          f"| Max drawdown | {m['max_drawdown_pct']:.2f}% | {r['buy_and_hold']['max_drawdown_pct']:.2f}% |",
          f"| Trades | {m['trades']} | 1 |", "",
          f"Win rate {n(m['win_rate_pct'], 1)}%, profit factor {n(m['profit_factor'])}, expectancy {n(m['expectancy_r'])}R, "
          f"average planned reward/risk {n(m['avg_planned_rr'])}, average trade {p(m['avg_trade_pct'])}, exposure "
          f"{m['exposure_pct']:.1f}%, average hold {n(m['avg_bars_held'], 1)} bars, fees {m['fees_pct']:.2f}% and "
          f"slippage {m['slippage_pct']:.2f}% of the account. Exits: "
          + ", ".join(f"{k} {v}" for k, v in m["exit_reasons"].items()) + ".", "",
          f"Random entries ({r['random_entries']['runs']} runs): the strategy beat {n(r['random_entries']['beaten_pct'], 1)}% "
          f"of them (median random run {p(r['random_entries']['median_return_pct'])}).", "",
          "## Walk-forward windows (182 days, fixed rules, reported only)", "", "| Window | Trades | Return | Sum of R |",
          "|---|---|---|---|"]
    for w in r["walk_forward"]:
        L.append(f"| {day(w['start'])} to {day(w['end'])} | {w['trades']} | {p(w['return_pct'])} | {w['sum_r']:+.2f} |")
    L += ["", "## Sensitivity (reported only, never used to choose parameters)", "",
          "| Lookback | ADX max | Target | Trades | Expectancy | Profit factor | Max DD | Net return |",
          "|---|---|---|---|---|---|---|---|"]
    for x in r["sensitivity"]:
        L.append(f"| {x['lookback']}{' (default)' if x['is_default'] else ''} | {x['adx_max']:g} | {x['target']} | "
                 f"{x['trades']} | {n(x['expectancy_r'])}R | {n(x['profit_factor'])} | {x['max_drawdown_pct']:.2f}% | "
                 f"{p(x['net_return_pct'])} |")
    L += ["", "## Limitations", ""] + [f"- {x}" for x in r["limitations"]] + [""]
    return "\n".join(L)
