"""Chart overlays for Market & chart: regime shading, liquidity-sweep markers and the chart's volume profile.

Visualisation only. Nothing here detects anything new: regime labels come from regime_at() (each closed 4h bar from
its own past), sweeps are the events STORED by the background monitor (superseded ones excluded by the database
reader), and the profile is volume_profile() on the chart's own closed candles. Closed candles only, no look-ahead:
  * a 4h bar is shaded only once it has closed by the chart's last closed candle, over its full 4h period;
  * a sweep is drawn only if it was detected by then, with its status AS OF the chart's last closed candle (never
    "confirmed" before its confirmation time), at the chart candle where the actual high / low happened.
"""
from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pandas as pd

from config import TIMEFRAMES
from services.regime import regime_at

H4 = TIMEFRAMES["4h"] * 1000
SHADE = {"TRENDING BULLISH": "bullish", "TRENDING BEARISH": "bearish", "SIDEWAYS / RANGING": "sideways",
         "HIGH VOLATILITY": "high_vol"}
MAX_REGIME_BARS = 600          # 100 days of 4h bars: enough for a 300-candle chart up to 1 day per candle / 3
STYLE = {"POTENTIAL SWEEP": "potential", "CONFIRMED SWEEP": "confirmed", "RECLAIM CONFIRMED": "confirmed",
         "FAILED SWEEP": "failed", "NO CONFIRMATION": "failed"}


class RegimeCache:
    """Regime label per closed 4h bar (keyed by its close time). A closed bar's label never changes."""

    def __init__(self, max_items: int = 5000):
        self._d: dict[int, dict] = {}
        self._lock = threading.Lock()
        self.max_items = max_items

    def get(self, key: int) -> dict | None:
        with self._lock:
            return self._d.get(key)

    def put(self, key: int, value: dict) -> None:
        with self._lock:
            if len(self._d) >= self.max_items:
                for k in sorted(self._d)[: self.max_items // 5]:
                    del self._d[k]
            self._d[key] = value


def regime_bars(c4h: pd.DataFrame, c1h: pd.DataFrame | None, c1d: pd.DataFrame | None,
                chart_start: int, chart_end: int, cache: RegimeCache) -> list[dict]:
    """Closed 4h bars that overlap [chart_start, chart_end] and closed by chart_end, each with its own regime."""
    if c4h is None or c4h.empty:
        return []
    ot, ct = c4h["open_time"].to_numpy(), c4h["close_time"].to_numpy()
    idx = np.flatnonzero((ct + 1 > chart_start) & (ct <= chart_end))[-MAX_REGIME_BARS:]
    out = []
    for i in idx:
        key = int(ct[i])
        r = cache.get(key)
        if r is None:
            full = regime_at(c4h, int(i), c1h, c1d)
            r = {"regime": full["regime"], "confidence": full.get("confidence"), "checks_used": full.get("checks_used")}
            cache.put(key, r)
        if r["regime"] is None:
            continue
        out.append({"start": int(ot[i]), "end": key + 1, **r})
    return out


def regime_segments(bars: list[dict]) -> list[dict]:
    """Consecutive bars with the same regime merged into one shaded span (shade None = not shaded)."""
    out: list[dict] = []
    for b in bars:
        if out and out[-1]["regime"] == b["regime"] and out[-1]["end"] == b["start"]:
            out[-1]["end"] = b["end"]
        else:
            out.append({"regime": b["regime"], "start": b["start"], "end": b["end"], "shade": SHADE.get(b["regime"])})
    return out


def regime_transitions(bars: list[dict]) -> list[dict]:
    return [{"time": b["start"], "from": a["regime"], "to": b["regime"], "confidence": b["confidence"],
             "checks_used": b["checks_used"]}
            for a, b in zip(bars, bars[1:]) if a["regime"] != b["regime"] and a["end"] == b["start"]]


def _status_as_of(e: dict, as_of: int) -> dict | None:
    ups = [u for u in e["updates"] if u["at"] <= as_of]
    return ups[-1] if ups else None


def sweep_markers(events: list[dict], chart: pd.DataFrame, chart_tf: str, as_of: int) -> list[dict[str, Any]]:
    """Stored sweep events as markers on the chart's own closed candles (see the module docstring)."""
    if chart is None or chart.empty:
        return []
    ot, ct = chart["open_time"].to_numpy(), chart["close_time"].to_numpy()
    hi, lo = chart["high"].to_numpy(float), chart["low"].to_numpy(float)
    step = TIMEFRAMES[chart_tf] * 1000
    out = []
    for e in events:
        if e["event_time"] > as_of:
            continue
        u = _status_as_of(e, as_of)
        if u is None:
            continue
        buy = e["direction"] == "buy_side"
        s_open, s_close = e["event_time"] + 1 - H4, e["event_time"]
        if step <= H4:      # chart candles inside the 4h sweep candle: the one with the actual high / low
            idx = np.flatnonzero((ot >= s_open) & (ct <= s_close))
        else:               # a higher timeframe: the CLOSED chart candle that contains the sweep candle
            idx = np.flatnonzero((ot <= s_open) & (ct >= s_close) & (ct <= as_of))
        if len(idx) == 0:
            continue
        j = int(idx[np.argmax(hi[idx])] if buy else idx[np.argmin(lo[idx])])
        confirmed = next((x["at"] for x in e["updates"] if x["status"] == "CONFIRMED SWEEP" and x["at"] <= as_of), None)
        out.append({
            "key": e["event_key"], "direction": e["direction"], "status": u["status"], "style": STYLE[u["status"]],
            "level": e["level"], "equal_levels": e.get("equal_levels"), "swing_time": e["swing_time"],
            "event_time": e["event_time"], "confirmed_at": confirmed, "status_at": u["at"],
            "sweep_extreme": e["sweep_extreme"], "reclaim_price": e["reclaim_price"],
            "volume_ratio": e.get("volume_ratio"), "strength": u.get("strength"),
            # rounded like the drawn candles (chart_series), so the arrow sits exactly on the wick
            "marker_time": int(ot[j]), "marker_price": round(float(hi[j] if buy else lo[j]), 2),
            "line_start": max(int(e["swing_time"]), int(ot[0])), "line_end": max(int(u["at"]), int(ot[j])),
        })
    return out
