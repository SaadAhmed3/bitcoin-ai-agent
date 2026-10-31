"""Volume profile (VPVR) from closed OHLCV candles. Display and shadow evidence only: never part of the live score.

Allocation (an APPROXIMATION - candles do not say at which price each unit traded): every candle's volume is spread
uniformly over its own low..high range, and each price bin receives the share of that range it overlaps. A candle
with high == low puts all its volume in the bin containing that price. Candles with zero or missing volume are
skipped and counted. Same input -> same output (no randomness, ties resolved by the lower price bin).

Value area: start at the POC bin and repeatedly add the neighbouring bin (above or below) with more volume (both
when equal) until the area holds `value_area` of the total. VAL / VAH are the outer edges of that area.
HVN / LVN: on volume smoothed over 3 bins, local peaks >= hvn_mult x the median bin (HVN) and local troughs
<= lvn_mult x the median bin inside the value area (LVN); the `top` largest / smallest of each.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

METHOD = ("Each closed candle's volume is spread evenly over its low-high range (an approximation: candles carry no "
          "trade-by-trade prices). Candles without volume are skipped.")


@dataclass(frozen=True)
class ProfileConfig:
    lookback: int = 200          # closed candles in the visible range
    bins: int = 50
    value_area: float = 0.70
    hvn_mult: float = 1.5
    lvn_mult: float = 0.5
    top: int = 3


# Visible range per timeframe (4h is the default profile; 1h / 1d are optional context).
LOOKBACK = {"4h": 200, "1h": 240, "1d": 180}


def _closed(df: pd.DataFrame, now: int | None) -> pd.DataFrame:
    return df if now is None or df.empty else df[df["close_time"] < now]


def _empty(status: str, tf: str | None, n: int = 0, skipped: int = 0) -> dict:
    return {"status": status, "timeframe": tf, "bins": [], "poc": None, "vah": None, "val": None, "hvn": [], "lvn": [],
            "total_volume": 0.0, "candles_used": n, "skipped_missing_volume": skipped, "range": None,
            "last_candle_close": None, "method": METHOD}


def volume_profile(df: pd.DataFrame, cfg: ProfileConfig = ProfileConfig(), now: int | None = None,
                   timeframe: str | None = None) -> dict[str, Any]:
    """Profile of the last `cfg.lookback` closed candles of df (columns open_time, high, low, volume, close_time)."""
    df = _closed(df, now)
    if df is None or df.empty:
        return _empty("no_data", timeframe)
    win = df.iloc[-cfg.lookback:]
    hi_all, lo_all = win["high"].to_numpy(float), win["low"].to_numpy(float)
    vol = win["volume"].to_numpy(float)
    used = np.isfinite(vol) & (vol > 0)
    skipped = int((~used).sum())
    rng = {"from_open_time": int(win["open_time"].iloc[0]), "to_close_time": int(win["close_time"].iloc[-1]),
           "low": float(np.nanmin(lo_all)), "high": float(np.nanmax(hi_all))}
    if not used.any():
        out = _empty("no_volume", timeframe, int(used.sum()), skipped)
        out["range"], out["last_candle_close"] = rng, rng["to_close_time"]
        return out
    lo, hi = rng["low"], rng["high"]
    if hi <= lo:
        hi = lo + max(abs(lo) * 1e-6, 1e-9)
    edges = np.linspace(lo, hi, cfg.bins + 1)
    width = edges[1] - edges[0]
    totals = np.zeros(cfg.bins)
    for h, low, v in zip(hi_all[used], lo_all[used], vol[used]):
        if h <= low:                                         # flat candle: one bin
            k = min(int((low - lo) / width), cfg.bins - 1)
            totals[k] += v
            continue
        overlap = np.clip(np.minimum(edges[1:], h) - np.maximum(edges[:-1], low), 0, None)
        totals += v * overlap / (h - low)
    total = float(totals.sum())
    poc_i = int(np.argmax(totals))                           # first maximum = lowest price on a tie
    a, b, acc = poc_i, poc_i, totals[poc_i]
    while acc < cfg.value_area * total - 1e-12 and (a > 0 or b < cfg.bins - 1):
        below = totals[a - 1] if a > 0 else -1.0          # -1: no bin on that side
        above = totals[b + 1] if b < cfg.bins - 1 else -1.0
        if above >= below:
            b += 1
            acc += totals[b]
        if below >= above:
            a -= 1
            acc += totals[a]
    mids = (edges[:-1] + edges[1:]) / 2
    sm = np.convolve(np.pad(totals, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    med = float(np.median(sm))
    hvn, lvn = [], []
    for i in range(cfg.bins):
        left = sm[i - 1] if i > 0 else -math.inf
        right = sm[i + 1] if i < cfg.bins - 1 else -math.inf
        if sm[i] > left and sm[i] >= right and sm[i] >= cfg.hvn_mult * med:
            hvn.append(i)
        if 0 < i < cfg.bins - 1 and a <= i <= b and sm[i] < sm[i - 1] and sm[i] <= sm[i + 1] \
                and sm[i] <= cfg.lvn_mult * med:
            lvn.append(i)
    hvn = sorted(hvn, key=lambda i: (-sm[i], i))[:cfg.top]
    lvn = sorted(lvn, key=lambda i: (sm[i], i))[:cfg.top]
    node = lambda i: {"price": float(mids[i]), "low": float(edges[i]), "high": float(edges[i + 1]),  # noqa: E731
                      "volume": float(totals[i])}
    return {
        "status": "ok", "timeframe": timeframe,
        "bins": [{"low": float(edges[i]), "high": float(edges[i + 1]), "mid": float(mids[i]), "volume": float(totals[i])}
                 for i in range(cfg.bins)],
        "poc": float(mids[poc_i]), "val": float(edges[a]), "vah": float(edges[b + 1]),
        "value_area_pct": round(cfg.value_area * 100, 1), "value_area_share": round(acc / total, 4),
        "hvn": [node(i) for i in hvn], "lvn": [node(i) for i in lvn],
        "total_volume": total, "candles_used": int(used.sum()), "skipped_missing_volume": skipped,
        "range": rng, "last_candle_close": rng["to_close_time"], "method": METHOD,
    }


def profile_at(df: pd.DataFrame, i: int, cfg: ProfileConfig = ProfileConfig(), timeframe: str | None = None) -> dict:
    """Rolling (historical) profile at bar i: only the `lookback` closed candles ending at bar i, nothing later."""
    return volume_profile(df.iloc[max(0, i - cfg.lookback + 1):i + 1], cfg, timeframe=timeframe)


def vp_score(profile: dict, close: float) -> float:
    """Shadow component in [-0.5, +0.5]: +0.5 above VAH, -0.5 below VAL, inside (close - POC) / (VAH - VAL)."""
    if profile.get("status") != "ok" or close is None:
        return 0.0
    vah, val, poc = profile["vah"], profile["val"], profile["poc"]
    if close > vah:
        return 0.5
    if close < val:
        return -0.5
    span = vah - val
    return float(max(-0.5, min(0.5, (close - poc) / span))) if span > 0 else 0.0
