"""Market regime detection on CLOSED candles. Display and shadow evidence only: never part of the live score.

Primary timeframe 4h; 1D (close vs EMA 200) is the long-term context and 1h (EMA 21 vs EMA 50) the short-term
confirmation. EMA 21 is used where the spec says EMA 20: the app already computes EMA 9/21/50/200 and no new EMA is
added. The indicators are the existing ones from technical_analysis (imported, not changed).

Rules (first match wins):
  1. HIGH VOLATILITY   ATR% percentile (200 bars) >= 90
  2. TRENDING BULLISH  >= 4 of 5 checks and ADX >= 20: close > EMA 200; EMA 21 > EMA 50 > EMA 200; EMA 50 slope over
                       10 bars > 0; ADX >= 25; swing structure score >= +0.5
  3. TRENDING BEARISH  the mirror of 2
  4. LOW VOLATILITY    ATR% percentile <= 10 or Bollinger bandwidth percentile <= 10 (squeeze)
  5. SIDEWAYS/RANGING  ADX < 20 and |structure| < 0.6
  6. TRANSITIONAL / UNCERTAIN otherwise
Confidence (0-100) is a HEURISTIC, not a probability: the share of the regime's own checks that hold; a trend loses
15 points for each of 1D and 1h that disagrees. Transitional: 100 - 100 x (larger side's checks / 5).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from services.technical_analysis import adx, atr, bollinger, ema, market_structure, swing_points

REGIMES = ("TRENDING BULLISH", "TRENDING BEARISH", "SIDEWAYS / RANGING", "HIGH VOLATILITY", "LOW VOLATILITY",
           "TRANSITIONAL / UNCERTAIN")
BULL, BEAR, SIDE, HIGH, LOW, TRANS = ("TRENDING BULLISH", "TRENDING BEARISH", "SIDEWAYS / RANGING", "HIGH VOLATILITY",
                                      "LOW VOLATILITY", "TRANSITIONAL / UNCERTAIN")


@dataclass(frozen=True)
class RegimeConfig:
    slope_bars: int = 10
    adx_trend: float = 25.0
    adx_weak: float = 20.0
    vol_high: float = 90.0
    vol_low: float = 10.0
    bb_squeeze: float = 10.0
    pct_window: int = 200
    swing_k: int = 3
    swing_lookback: int = 4
    structure_trend: float = 0.5
    sideways_structure: float = 0.6
    min_checks: int = 4
    conflict_penalty: int = 15
    min_bars: int = 220
    window: int = 500           # same candle window as the live app


def _upto(df: pd.DataFrame | None, as_of: int, tail: int) -> pd.DataFrame | None:
    """Candles of another timeframe that had CLOSED by as_of (binary search: cheap inside a historical loop)."""
    if df is None or df.empty:
        return None
    j = int(np.searchsorted(df["close_time"].to_numpy(), as_of, side="right"))
    return df.iloc[max(0, j - tail):j]


def _pct(series: pd.Series, window: int) -> float | None:
    s = series.dropna().iloc[-window:]
    if len(s) < 50:
        return None
    return round(float((s <= s.iloc[-1]).mean() * 100), 1)


def _num(x: Any, nd: int = 2) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def classify(c4h: pd.DataFrame, c1h: pd.DataFrame | None = None, c1d: pd.DataFrame | None = None,
             cfg: RegimeConfig = RegimeConfig(), now: int | None = None) -> dict[str, Any]:
    """Regime at the last CLOSED 4h candle. `now` (ms) drops a candle that has not closed yet."""
    if c4h is not None and now is not None and not c4h.empty:
        c4h = c4h[c4h["close_time"] < now]
    if c4h is None or c4h.empty:
        return {"status": "no_data", "regime": None, "confidence": None, "timeframe": "4h"}
    if len(c4h) < cfg.min_bars:
        return {"status": "insufficient_data", "regime": None, "confidence": None, "timeframe": "4h",
                "bars": len(c4h), "as_of": int(c4h["close_time"].iloc[-1])}
    df = c4h.iloc[-cfg.window:]
    as_of = int(df["close_time"].iloc[-1])
    c = df["close"].astype(float)
    e21, e50, e200 = ema(c, 21), ema(c, 50), ema(c, 200)
    close, v21, v50, v200 = float(c.iloc[-1]), float(e21.iloc[-1]), float(e50.iloc[-1]), float(e200.iloc[-1])
    slope = (v50 / float(e50.iloc[-1 - cfg.slope_bars]) - 1) * 100
    adx_v = float(adx(df, 14)["adx"].iloc[-1])
    atr_pct = _pct(atr(df, 14) / c * 100, cfg.pct_window)
    bbw_pct = _pct(bollinger(c)["bb_width"], cfg.pct_window)
    sh, sl = swing_points(df, cfg.swing_k)
    st = market_structure(sh, sl, cfg.swing_lookback)
    struct = st["score"]
    s = 0.0 if struct is None else float(struct)

    bull = {"close_above_ema200": close > v200, "ema_alignment": v21 > v50 > v200, "ema50_rising": slope > 0,
            "adx_trending": adx_v >= cfg.adx_trend, "structure": s >= cfg.structure_trend}
    bear = {"close_above_ema200": close < v200, "ema_alignment": v21 < v50 < v200, "ema50_rising": slope < 0,
            "adx_trending": adx_v >= cfg.adx_trend, "structure": s <= -cfg.structure_trend}
    nb, nr = sum(bull.values()), sum(bear.values())
    is_bull = nb >= cfg.min_checks and adx_v >= cfg.adx_weak
    is_bear = nr >= cfg.min_checks and adx_v >= cfg.adx_weak
    direction = "bullish" if is_bull else "bearish" if is_bear else "neutral"

    # Context timeframes, closed by as_of only.
    d = _upto(c1d, as_of, cfg.window)
    daily = None
    if d is not None and len(d) >= 200:
        dc = d["close"].astype(float)
        daily = "bullish" if float(dc.iloc[-1]) > float(ema(dc, 200).iloc[-1]) else "bearish"
    h = _upto(c1h, as_of, cfg.window)
    hourly = None
    if h is not None and len(h) >= 60:
        hc = h["close"].astype(float)
        hourly = "bullish" if float(ema(hc, 21).iloc[-1]) > float(ema(hc, 50).iloc[-1]) else "bearish"

    vol_cls = "high" if atr_pct is not None and atr_pct >= cfg.vol_high else \
        "low" if atr_pct is not None and atr_pct <= cfg.vol_low else "normal"
    strength = "strong" if adx_v >= cfg.adx_trend else "moderate" if adx_v >= cfg.adx_weak else "weak"
    TXT = {"close_above_ema200": ("close above EMA 200", "close below EMA 200"),
           "ema_alignment": ("EMA 21 > 50 > 200", "EMA 21 < 50 < 200"),
           "ema50_rising": (f"EMA 50 rising ({slope:+.2f}% over {cfg.slope_bars} bars)",
                            f"EMA 50 falling ({slope:+.2f}% over {cfg.slope_bars} bars)"),
           "adx_trending": (f"ADX {adx_v:.1f} >= {cfg.adx_trend:g}", f"ADX {adx_v:.1f} >= {cfg.adx_trend:g}"),
           "structure": (f"higher highs / higher lows (structure {s:+.2f})",
                         f"lower highs / lower lows (structure {s:+.2f})")}
    evidence: list[str] = []
    conflicts: list[str] = []
    side = bull if nb >= nr else bear
    k = 0 if nb >= nr else 1

    if atr_pct is not None and atr_pct >= cfg.vol_high:
        regime = HIGH
        checks = [atr_pct >= cfg.vol_high, bbw_pct is not None and bbw_pct >= cfg.vol_high]
        conf = 100 * sum(checks) / 2
        evidence.append(f"ATR% at the {atr_pct:g}th percentile of {cfg.pct_window} bars")
        if checks[1]:
            evidence.append(f"Bollinger bandwidth expanding ({bbw_pct:g}th percentile)")
        else:
            conflicts.append(f"Bollinger bandwidth only at the {bbw_pct}th percentile")
    elif is_bull or is_bear:
        regime = BULL if is_bull else BEAR
        side, k = (bull, 0) if is_bull else (bear, 1)
        conf = 100 * sum(side.values()) / 5
        evidence += [TXT[n][k] for n, ok in side.items() if ok]
        conflicts += [f"not: {TXT[n][k]}" for n, ok in side.items() if not ok]
        want = "bullish" if is_bull else "bearish"
        for name, view, what in (("1D", daily, "close vs EMA 200"), ("1h", hourly, "EMA 21 vs EMA 50")):
            if view is None:
                evidence.append(f"{name}: not enough closed candles to compare")
            elif view == want:
                evidence.append(f"{name} agrees ({what} {view})")
            else:
                conf -= cfg.conflict_penalty
                conflicts.append(f"{name} disagrees ({what} {view})")
    elif (atr_pct is not None and atr_pct <= cfg.vol_low) or (bbw_pct is not None and bbw_pct <= cfg.bb_squeeze):
        regime = LOW
        checks = [atr_pct is not None and atr_pct <= cfg.vol_low, bbw_pct is not None and bbw_pct <= cfg.bb_squeeze]
        conf = 100 * sum(checks) / 2
        if checks[0]:
            evidence.append(f"ATR% at the {atr_pct:g}th percentile of {cfg.pct_window} bars")
        if checks[1]:
            evidence.append(f"Bollinger squeeze (bandwidth {bbw_pct:g}th percentile)")
        conflicts += [] if all(checks) else ["only one of ATR% and Bollinger bandwidth is low"]
    elif adx_v < cfg.adx_weak and abs(s) < cfg.sideways_structure:
        regime = SIDE
        mixed = not (v21 > v50 > v200) and not (v21 < v50 < v200)
        checks = [True, True, mixed, abs(slope) < 0.5]
        conf = 100 * sum(checks) / 4
        evidence += [f"ADX {adx_v:.1f} < {cfg.adx_weak:g}: weak trend", f"no clear swing structure ({s:+.2f})"]
        evidence += ["EMAs not aligned"] if mixed else []
        conflicts += [] if mixed else ["EMAs still aligned"]
        evidence += [f"EMA 50 flat ({slope:+.2f}%)"] if checks[3] else []
        conflicts += [] if checks[3] else [f"EMA 50 still moving ({slope:+.2f}%)"]
    else:
        regime = TRANS
        conf = 100 - 100 * max(nb, nr) / 5
        lean = "bullish" if nb >= nr else "bearish"
        evidence.append(f"bullish checks {nb}/5, bearish checks {nr}/5: leaning {lean}" if nb != nr
                        else f"bullish checks {nb}/5, bearish checks {nr}/5: no lean")
        evidence += [TXT[n][k] for n, ok in side.items() if ok]
        conflicts.append(f"no side has {cfg.min_checks} of 5 checks with ADX >= {cfg.adx_weak:g}")
        conflicts += [f"not: {TXT[n][k]}" for n, ok in side.items() if not ok]

    return {
        "status": "ok", "regime": regime, "confidence": int(round(max(0.0, min(100.0, conf)))),
        "direction": direction, "trend_strength": strength, "volatility": vol_cls,
        "evidence": evidence, "conflicts": conflicts, "checks": dict(side),
        "bullish_checks": nb, "bearish_checks": nr,
        "metrics": {"adx": _num(adx_v, 1), "atr_pct_percentile": atr_pct, "bb_width_percentile": bbw_pct,
                    "ema21": _num(v21), "ema50": _num(v50), "ema200": _num(v200), "close": _num(close),
                    "ema50_slope_pct": _num(slope, 3), "structure": struct},
        "context": {"1d": daily, "1h": hourly},
        "timeframe": "4h", "bars": len(df), "as_of": as_of,
        "heuristic": "Confidence is a heuristic score of how many checks agree, not a probability.",
    }


def regime_at(c4h: pd.DataFrame, i: int, c1h: pd.DataFrame | None = None, c1d: pd.DataFrame | None = None,
              cfg: RegimeConfig = RegimeConfig()) -> dict[str, Any]:
    """Historical regime at bar i: only 4h candles up to i and 1h/1D candles closed by bar i's close."""
    return classify(c4h.iloc[max(0, i - cfg.window + 1):i + 1], c1h, c1d, cfg)


def regime_history(c4h: pd.DataFrame, c1h: pd.DataFrame | None = None, c1d: pd.DataFrame | None = None,
                   cfg: RegimeConfig = RegimeConfig(), bars: int = 60) -> dict[str, Any]:
    """Labels of the last `bars` closed 4h candles (each from its own past only), the current run's start and the
    regime before it. changed_at is None when the current regime held for the whole span."""
    n = len(c4h)
    labels: dict[int, str | None] = {}
    for i in range(max(0, n - bars), n):
        labels[int(c4h["close_time"].iloc[i])] = regime_at(c4h, i, c1h, c1d, cfg)["regime"]
    seq = list(labels.items())
    if not seq or seq[-1][1] is None:
        return {"labels": labels, "current": None, "previous": None, "changed_at": None}
    cur = seq[-1][1]
    j = len(seq) - 1
    while j > 0 and seq[j - 1][1] == cur:
        j -= 1
    return {"labels": {t: r for t, r in labels.items() if r is not None}, "current": cur,
            "previous": seq[j - 1][1] if j > 0 else None, "changed_at": seq[j][0] if j > 0 else None}


def regime_score(result: dict) -> float:
    """Shadow component: +confidence/100 in a bullish trend, -confidence/100 in a bearish trend, else 0."""
    if result.get("status") != "ok":
        return 0.0
    if result["regime"] == BULL:
        return result["confidence"] / 100
    if result["regime"] == BEAR:
        return -result["confidence"] / 100
    return 0.0
