"""Liquidity sweep detection on CLOSED 4h candles. Display and shadow evidence only: never a BUY/SELL signal.

No look-ahead, no repainting:
  * a level is a swing high/low (existing fractal rule, k=3) whose k right-side candles had all CLOSED before the
    candle being tested, from the last 50 bars, still untaken (no later high/low beyond it by more than 0.1 x ATR);
    swings within 0.1 x ATR of each other merge into one level (equal highs / equal lows);
  * every status is decided at the CLOSE of the candle that causes it and stored with that candle's close time.

Statuses (buy-side shown; sell-side is the mirror):
  POTENTIAL SWEEP    the candle's high goes >= 0.05 x ATR above the level and the candle closes back below it.
                     A close above the level is a breakout, not a sweep: nothing is recorded.
  CONFIRMED SWEEP    a later candle (within 3) closes below the level without a new high beyond the sweep.
  FAILED SWEEP       a close above the level (acceptance), before or within 3 candles after confirmation.
  RECLAIM CONFIRMED  within 3 candles after confirmation, a close below the sweep candle's low.
  NO CONFIRMATION    3 candles without confirmation or failure, or a missing candle (gap) before confirmation.
One move is one event: while a level's event is open (potential, or confirmed within its 3-candle window), later
wicks through the same level belong to that event and start no new one.
Strength (0-100) is a HEURISTIC, not a probability: penetration 20 + rejection wick 25 + close back inside 15 +
relative volume 20 + confirmation 10 (+5 when a 1h close confirmed first) + 1D context in favour of the reversal 5.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from config import TIMEFRAMES
from services.regime import _upto
from services.technical_analysis import atr, ema, swing_points

STATUSES = ("POTENTIAL SWEEP", "CONFIRMED SWEEP", "FAILED SWEEP", "RECLAIM CONFIRMED", "NO CONFIRMATION")
POTENTIAL, CONFIRMED, FAILED, RECLAIM, NOCONF = STATUSES
ACTIVE = (CONFIRMED, RECLAIM)


@dataclass(frozen=True)
class SweepConfig:
    k: int = 3                  # swing confirmation: k closed candles on each side
    level_lookback: int = 50
    equal_atr: float = 0.1      # equal highs / lows tolerance, and the "taken" tolerance
    min_pen_atr: float = 0.05   # minimum wick beyond the level
    expiry_bars: int = 3
    reclaim_bars: int = 3
    vol_window: int = 20
    vol_full: float = 1.5
    score_bars: int = 6         # shadow component: confirmed in the last 6 bars
    timeframe: str = "4h"


def levels_before(df: pd.DataFrame, t: int, cfg: SweepConfig = SweepConfig(),
                  atr_value: float | None = None) -> tuple[list[dict], list[dict]]:
    """Untaken swing highs and lows confirmed by candles that CLOSED before candle t (index into df)."""
    if t <= 0:
        return [], []
    if atr_value is None:
        atr_value = float(atr(df.iloc[:t], 14).iloc[-1]) if t > 14 else float("nan")
    if not np.isfinite(atr_value) or atr_value <= 0:
        return [], []
    tol = cfg.equal_atr * atr_value
    start = max(0, t - cfg.level_lookback - cfg.k)
    sub = df.iloc[start:t]
    sh, sl = swing_points(sub, cfg.k)        # a swing at i needs i + k < len(sub): confirmed before candle t
    hi, lo = df["high"].to_numpy(float), df["low"].to_numpy(float)
    ot = df["open_time"].to_numpy()

    def keep(points, highs: bool) -> list[dict]:
        out = []
        for i, price in points:
            j = start + i
            if j < t - cfg.level_lookback:
                continue
            later = hi[j + 1:t] if highs else lo[j + 1:t]
            if len(later) and (later.max() > price + tol if highs else later.min() < price - tol):
                continue                     # already taken
            out.append({"level": float(price), "swing_index": j, "swing_time": int(ot[j]), "equal_levels": 1})
        out.sort(key=lambda x: x["level"], reverse=highs)         # most extreme first; neighbours merge into it
        merged: list[dict] = []
        for lv in out:
            if merged and abs(lv["level"] - merged[-1]["level"]) <= tol:
                m = merged[-1]
                outer = lv if (lv["level"] > m["level"] if highs else lv["level"] < m["level"]) else m
                merged[-1] = {**outer, "equal_levels": m["equal_levels"] + 1}
            else:
                merged.append(lv)
        return merged

    return keep(sh, True), keep(sl, False)


def _factors(e: dict, a: float, daily_ok: bool, h1_ok: bool) -> dict[str, float]:
    buy = e["direction"] == "buy_side"
    pen = abs(e["sweep_extreme"] - e["level"]) / a
    pen_pts = 20 if 0.1 <= pen <= 0.75 else 10 if (0.05 <= pen < 0.1 or 0.75 < pen <= 1.5) else 0
    rng = e["candle_high"] - e["candle_low"]
    wick = ((e["candle_high"] - max(e["candle_open"], e["sweep_close"])) if buy
            else (min(e["candle_open"], e["sweep_close"]) - e["candle_low"])) / rng if rng > 0 else 0.0
    inside = abs(e["level"] - e["sweep_close"]) / a
    rv = e["volume_ratio"]
    confirmed = e["status"] in ACTIVE or e.get("confirmed_at") is not None and e["status"] == FAILED
    return {
        "penetration": float(pen_pts),
        "rejection_wick": round(25 * min(1.0, wick / 0.6), 1),
        "close_inside": round(15 * min(1.0, inside / 0.5), 1),
        "volume": 0.0 if rv is None else round(20 * min(1.0, max(0.0, (rv - 1) / 0.5)), 1),
        "confirmation": (10.0 + (5.0 if h1_ok else 0.0)) if confirmed else 0.0,
        "higher_timeframe": 5.0 if daily_ok else 0.0,
    }


def detect_sweeps(c4h: pd.DataFrame, c1h: pd.DataFrame | None = None, c1d: pd.DataFrame | None = None,
                  cfg: SweepConfig = SweepConfig(), now: int | None = None) -> list[dict[str, Any]]:
    """Every sweep event in the closed candles, oldest first, each with its status history."""
    if c4h is None or c4h.empty:
        return []
    df = c4h if now is None else c4h[c4h["close_time"] < now]
    n = len(df)
    if n < 20:
        return []
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    ot, ct = df["open_time"].to_numpy(), df["close_time"].to_numpy()
    atr_arr = atr(df, 14).to_numpy()
    step = TIMEFRAMES[cfg.timeframe] * 1000
    events: list[dict] = []
    tracking: list[dict] = []

    def daily_context(e: dict) -> bool:
        d = _upto(c1d, e["event_time"], 400)
        if d is None or len(d) < 200:
            return False
        dc = d["close"].astype(float)
        above = float(dc.iloc[-1]) > float(ema(dc, 200).iloc[-1])
        return (not above) if e["direction"] == "buy_side" else above      # context in favour of the reversal

    def hourly_first(e: dict, until: int) -> bool:
        if c1h is None or c1h.empty:
            return False
        times = c1h["close_time"].to_numpy()
        j = int(np.searchsorted(times, e["event_time"], side="right"))
        if j >= len(times) or times[j] > until:
            return False
        close = float(c1h["close"].iloc[j])
        return close < e["level"] if e["direction"] == "buy_side" else close > e["level"]

    def add_update(e: dict, status: str, at: int, note: str) -> None:
        e["status"] = status
        if status == CONFIRMED:
            e["confirmed_at"] = int(at)
            e["h1_confirmed_first"] = hourly_first(e, at)
        e["factors"] = _factors(e, e["atr"], e["daily_context"], e.get("h1_confirmed_first", False))
        e["strength"] = round(sum(e["factors"].values()), 1)
        e["updates"].append({"status": status, "at": int(at), "strength": e["strength"],
                             "factors": dict(e["factors"]), "note": note})

    for t in range(1, n):
        contiguous = ot[t] - ot[t - 1] == step
        # Levels whose event was still open when this candle started: the same move, never a second sweep - also
        # for the candle that closes that event (e.g. its third new extreme).
        busy = {(e["direction"], e["level"]) for e in tracking}
        for e in list(tracking):
            buy = e["direction"] == "buy_side"
            beyond = c[t] > e["level"] if buy else c[t] < e["level"]
            if not contiguous:
                if e["status"] == POTENTIAL:
                    add_update(e, NOCONF, ct[t], "missing candle before this one: not confirmed")
                tracking.remove(e)
                continue
            e["seen"] += 1
            if e["status"] == POTENTIAL:
                new_extreme = h[t] > e["sweep_extreme"] if buy else l[t] < e["sweep_extreme"]
                if beyond:
                    add_update(e, FAILED, ct[t], "closed beyond the level: acceptance, not a sweep")
                    tracking.remove(e)
                elif new_extreme:
                    e["sweep_extreme"] = float(h[t] if buy else l[t])
                    if e["seen"] >= cfg.expiry_bars:
                        add_update(e, NOCONF, ct[t], f"no confirmation within {cfg.expiry_bars} candles")
                        tracking.remove(e)
                else:
                    add_update(e, CONFIRMED, ct[t], "closed back inside without a new extreme")
                    e["seen_after"] = 0
            else:
                e["seen_after"] += 1
                reclaim = c[t] < e["candle_low"] if buy else c[t] > e["candle_high"]
                if beyond:
                    add_update(e, FAILED, ct[t], "closed beyond the level after confirmation")
                    tracking.remove(e)
                elif reclaim:
                    add_update(e, RECLAIM, ct[t], "closed beyond the sweep candle's other end")
                    tracking.remove(e)
                elif e["seen_after"] >= cfg.reclaim_bars:
                    tracking.remove(e)

        a = atr_arr[t - 1]
        if not np.isfinite(a) or a <= 0:
            continue
        highs, lows = levels_before(df, t, cfg, float(a))
        prev = v[max(0, t - cfg.vol_window):t]
        prev = prev[np.isfinite(prev) & (prev > 0)]
        rv = float(v[t] / prev.mean()) if np.isfinite(v[t]) and v[t] > 0 and len(prev) else None
        for direction, levels in (("buy_side", highs), ("sell_side", lows)):
            buy = direction == "buy_side"
            hit = [lv for lv in levels if (direction, lv["level"]) not in busy
                   and ((h[t] >= lv["level"] + cfg.min_pen_atr * a and c[t] < lv["level"]) if buy
                       else (l[t] <= lv["level"] - cfg.min_pen_atr * a and c[t] > lv["level"]))]
            if not hit:
                continue
            lv = max(hit, key=lambda x: x["level"]) if buy else min(hit, key=lambda x: x["level"])
            e = {"key": f"{cfg.timeframe}:{direction}:{int(ot[t])}", "timeframe": cfg.timeframe, "direction": direction,
                 "level": lv["level"], "equal_levels": lv["equal_levels"], "swing_time": lv["swing_time"],
                 "event_time": int(ct[t]), "sweep_extreme": float(h[t] if buy else l[t]),
                 "sweep_close": float(c[t]), "reclaim_price": float(c[t]), "candle_open": float(o[t]),
                 "candle_high": float(h[t]), "candle_low": float(l[t]), "volume_ratio": None if rv is None else round(rv, 3),
                 "atr": float(a), "status": POTENTIAL, "confirmed_at": None, "updates": [], "seen": 0}
            e["daily_context"] = daily_context(e)
            add_update(e, POTENTIAL, ct[t], "wick beyond the level, closed back inside")
            events.append(e)
            tracking.append(e)
    for e in events:
        for k in ("seen", "seen_after"):
            e.pop(k, None)
    return events


def status_at(e: dict, at: int) -> dict | None:
    """The event's last update decided at or before `at` (None if the event did not exist yet)."""
    ups = [u for u in e["updates"] if u["at"] <= at]
    return ups[-1] if ups else None


def sweep_score(df: pd.DataFrame, events: list[dict], as_of: int, cfg: SweepConfig = SweepConfig()) -> float:
    """Shadow component: the latest sweep that is CONFIRMED / RECLAIM CONFIRMED as of `as_of` and was confirmed in
    the last `score_bars` bars: +strength/100 for sell-side (bullish), -strength/100 for buy-side. Else 0."""
    step = TIMEFRAMES[cfg.timeframe] * 1000
    best = None
    for e in events:
        u = status_at(e, as_of)
        if u is None or u["status"] not in ACTIVE or e["confirmed_at"] is None:
            continue
        if as_of - e["confirmed_at"] > cfg.score_bars * step:
            continue
        if best is None or e["confirmed_at"] > best[0]["confirmed_at"]:
            best = (e, u)
    if best is None:
        return 0.0
    e, u = best
    conf = next(x for x in e["updates"] if x["status"] == CONFIRMED)
    return (1 if e["direction"] == "sell_side" else -1) * conf["strength"] / 100
