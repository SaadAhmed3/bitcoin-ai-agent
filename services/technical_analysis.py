"""Technical analysis engine (pure pandas / NumPy).

pandas-ta was evaluated but not used: its releases have lagged NumPy 2 / pandas 3
compatibility, and every formula here is short enough to implement and test
directly. Wilder smoothing is used for RSI, ATR and ADX to match common charting
platforms.

Every reading is *evidence*, not a prediction. Input frames must contain only
closed candles (the market data layer guarantees this).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

MIN_BARS = {"rsi": 15, "macd": 35, "ema200": 200, "sma200": 200, "adx": 28, "bb": 20, "atr": 15}


# --------------------------------------------------------------------------- indicators
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (RMA), seeded with the SMA of the first n valid values.

    Matches Wilder's original definition and TradingView's ta.rma. Uses only past
    values, so appending future bars never changes earlier results.
    """
    v = s.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    valid = np.flatnonzero(~np.isnan(v))
    if len(valid) == 0:
        return pd.Series(out, index=s.index)
    start = valid[0]
    seed_end = start + n - 1
    if seed_end >= len(v) or np.isnan(v[start:seed_end + 1]).any():
        return pd.Series(out, index=s.index)
    out[seed_end] = v[start:seed_end + 1].mean()
    for i in range(seed_end + 1, len(v)):
        x = v[i]
        out[i] = out[i - 1] if np.isnan(x) else (out[i - 1] * (n - 1) + x) / n
    return pd.Series(out, index=s.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), n)
    loss = wilder(-delta.clip(upper=0), n)
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    out = out.where(loss != 0, 100.0)              # only gains -> 100
    out = out.where(~((gain == 0) & (loss == 0)), 50.0)  # flat market -> 50
    return out.where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * std, mid - k * std
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
                         "bb_width": (upper - lower) / mid, "bb_pct_b": (close - lower) / (upper - lower)})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                      (df["low"] - prev_close).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(plus_dm, n) / tr
    minus_di = 100 * wilder(minus_dm, n) / tr
    di_sum = plus_di + minus_di
    dx = (100 * (plus_di - minus_di).abs() / di_sum).where(di_sum != 0, 0.0)
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": plus_di, "minus_di": minus_di})


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP anchored to each UTC day (meaningful for intraday timeframes only)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.floor("D")
    pv = (typical * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum()
    return (pv / vol).where(vol > 0)


def add_indicators(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    out["rsi14"] = rsi(c, 14)
    out = out.join(macd(c))
    for n in (9, 21, 50, 200):
        out[f"ema{n}"] = ema(c, n)
    for n in (50, 200):
        out[f"sma{n}"] = sma(c, n)
    out = out.join(bollinger(c))
    out["atr14"] = atr(out, 14)
    out["atr_pct"] = out["atr14"] / c * 100
    out = out.join(adx(out, 14))
    out["vwap"] = session_vwap(out) if timeframe != "1d" else np.nan
    out["vol_sma20"] = sma(out["volume"], 20)
    out["vol_ratio"] = out["volume"] / out["vol_sma20"]
    return out


# --------------------------------------------------------------------------- structure
def swing_points(df: pd.DataFrame, k: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Confirmed fractal swings: a high/low that is the extreme of k bars on each side."""
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    sh, sl = [], []
    for i in range(k, len(df) - k):
        win_h = highs[i - k:i + k + 1]
        win_l = lows[i - k:i + k + 1]
        if highs[i] == win_h.max() and (win_h == highs[i]).sum() == 1:
            sh.append((i, float(highs[i])))
        if lows[i] == win_l.min() and (win_l == lows[i]).sum() == 1:
            sl.append((i, float(lows[i])))
    return sh, sl


def market_structure(swing_highs: list, swing_lows: list, lookback: int = 4) -> dict:
    """Trend from the sequence of the last `lookback` confirmed swing highs and lows.

    Each step between consecutive swings scores +1 (higher) or -1 (lower); the
    structure score is the average over all steps, in [-1, +1]. One higher high
    inside a run of lower highs therefore nets out near zero instead of reading
    as a full uptrend.
    """
    # last_high_step / last_low_step describe only the most recent pair of swings (display only);
    # "trend" and "score" come from the whole sequence and are what the signal uses.
    res: dict[str, Any] = {"last_high_step": None, "last_low_step": None, "trend": "undetermined", "score": None,
                           "high_sequence": [round(p, 2) for _, p in swing_highs[-lookback:]],
                           "low_sequence": [round(p, 2) for _, p in swing_lows[-lookback:]]}
    if len(swing_highs) >= 2:
        res["last_high_step"] = "higher_high" if swing_highs[-1][1] > swing_highs[-2][1] else "lower_high"
    if len(swing_lows) >= 2:
        res["last_low_step"] = "higher_low" if swing_lows[-1][1] > swing_lows[-2][1] else "lower_low"
    hs = [p for _, p in swing_highs[-lookback:]]
    ls = [p for _, p in swing_lows[-lookback:]]
    steps = [1 if b > a else -1 for a, b in zip(hs, hs[1:])] + [1 if b > a else -1 for a, b in zip(ls, ls[1:])]
    if len(hs) >= 2 and len(ls) >= 2:
        score = sum(steps) / len(steps)
        res["score"] = round(score, 3)
        res["trend"] = "uptrend" if score >= 0.6 else "downtrend" if score <= -0.6 else "range / transition"
    return res


def support_resistance(df: pd.DataFrame, swing_highs: list, swing_lows: list, atr_value: float | None,
                       max_levels: int = 3, recent_bars: int = 200) -> dict:
    """Horizontal levels from clusters of recent swing points.

    A swing joins a cluster only if it is within 0.5 ATR of the cluster's CENTRE and
    the cluster stays at most 1 ATR wide, so levels cannot chain into a wide band.
    A cluster whose range contains the current price is the 'current zone', never
    support or resistance.
    """
    price = float(df["close"].iloc[-1])
    atr_v = atr_value or price * 0.01
    tol = 0.5 * atr_v
    max_width = 1.0 * atr_v
    start = max(0, len(df) - recent_bars)
    points = sorted(p for i, p in swing_highs + swing_lows if i >= start)
    clusters: list[list[float]] = []
    for p in points:
        if clusters:
            c = clusters[-1]
            centre = sum(c) / len(c)
            if abs(p - centre) <= tol and (max(c + [p]) - min(c + [p])) <= max_width:
                c.append(p)
                continue
        clusters.append([p])
    levels = [{"price": round(float(np.mean(c)), 2), "low": round(min(c), 2), "high": round(max(c), 2),
               "touches": len(c)} for c in clusters]
    support = sorted([l for l in levels if l["high"] < price], key=lambda l: price - l["price"])[:max_levels]
    resistance = sorted([l for l in levels if l["low"] > price], key=lambda l: l["price"] - price)[:max_levels]
    zone = [l for l in levels if l["low"] <= price <= l["high"]]
    return {"support": support, "resistance": resistance, "current_zone": zone[0] if zone else None,
            "lookback_bars": min(recent_bars, len(df)), "basis": "last closed candle",
            "basis_price": round(price, 2)}


def breakout_state(df: pd.DataFrame, lookback: int = 20) -> dict:
    """Close beyond the prior `lookback`-bar range; 'false' if the prior bar broke out and this one closed back inside."""
    if len(df) < lookback + 2:
        return {"state": "insufficient_data"}
    close, vol_ratio = df["close"], df["vol_ratio"]

    def rng(end: int) -> tuple[float, float]:
        w = df.iloc[end - lookback:end]
        return float(w["high"].max()), float(w["low"].min())

    n = len(df)
    hi_now, lo_now = rng(n - 1)
    hi_prev, lo_prev = rng(n - 2)
    c, c_prev = float(close.iloc[-1]), float(close.iloc[-2])
    vr = vol_ratio.iloc[-1]
    vol_ok = bool(vr >= 1.5) if pd.notna(vr) else False
    if c > hi_now:
        return {"state": "bullish_breakout", "level": round(hi_now, 2), "volume_confirmed": vol_ok}
    if c < lo_now:
        return {"state": "bearish_breakdown", "level": round(lo_now, 2), "volume_confirmed": vol_ok}
    if c_prev > hi_prev and c <= hi_prev:
        return {"state": "false_breakout_up", "level": round(hi_prev, 2)}
    if c_prev < lo_prev and c >= lo_prev:
        return {"state": "false_breakdown", "level": round(lo_prev, 2)}
    return {"state": "inside_range", "range_high": round(hi_now, 2), "range_low": round(lo_now, 2)}


def volatility_regime(atr_pct: pd.Series, window: int = 200) -> dict:
    s = atr_pct.dropna()
    if len(s) < 50:
        return {"regime": "insufficient_data", "percentile": None}
    hist = s.iloc[-window:]
    pct = float((hist <= hist.iloc[-1]).mean() * 100)
    regime = "low" if pct < 20 else "normal" if pct < 70 else "high" if pct < 95 else "extreme"
    return {"regime": regime, "percentile": round(pct, 1), "atr_pct": round(float(s.iloc[-1]), 3)}


def detect_divergence(df: pd.DataFrame, swing_highs: list, swing_lows: list, max_age: int = 30) -> dict:
    """Regular RSI divergence between the last two confirmed swings. Experimental: noisy by nature."""
    n = len(df)
    r = df["rsi14"]
    out = {"bullish": False, "bearish": False, "note": "Experimental - regular divergence on confirmed swings only."}
    if len(swing_lows) >= 2:
        (i1, p1), (i2, p2) = swing_lows[-2], swing_lows[-1]
        if n - i2 <= max_age and pd.notna(r.iloc[i1]) and pd.notna(r.iloc[i2]):
            out["bullish"] = bool(p2 < p1 and r.iloc[i2] > r.iloc[i1])
    if len(swing_highs) >= 2:
        (i1, p1), (i2, p2) = swing_highs[-2], swing_highs[-1]
        if n - i2 <= max_age and pd.notna(r.iloc[i1]) and pd.notna(r.iloc[i2]):
            out["bearish"] = bool(p2 > p1 and r.iloc[i2] < r.iloc[i1])
    return out


# --------------------------------------------------------------------------- analysis
def _num(x: Any, nd: int = 2) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _reading(name: str, value: Any, bias: int, text: str) -> dict:
    return {"name": name, "value": value, "bias": bias, "interpretation": text}


def analyze(df: pd.DataFrame, timeframe: str) -> dict:
    """Full analysis of one timeframe. Returns a JSON-safe dict."""
    warnings: list[str] = []
    if df is None or len(df) < MIN_BARS["rsi"]:
        return {"timeframe": timeframe, "status": "insufficient_data", "bars": 0 if df is None else len(df),
                "warnings": ["Not enough closed candles to analyse this timeframe."], "readings": []}
    for key, need in (("ema200", 200), ("adx", 28), ("macd", 35)):
        if len(df) < need:
            warnings.append(f"{key.upper()} needs {need} bars; only {len(df)} available - value omitted.")

    d = add_indicators(df, timeframe)
    last = d.iloc[-1]
    price = float(last["close"])
    sh, sl = swing_points(d)
    structure = market_structure(sh, sl)
    levels = support_resistance(d, sh, sl, _num(last["atr14"]))
    readings: list[dict] = []

    # RSI
    v = _num(last["rsi14"])
    if v is not None:
        if v >= 70:
            readings.append(_reading("RSI 14", v, -1, "Overbought (>=70): stretched; pullback risk rises."))
        elif v <= 30:
            readings.append(_reading("RSI 14", v, 1, "Oversold (<=30): stretched to the downside."))
        else:
            readings.append(_reading("RSI 14", v, 1 if v > 50 else -1 if v < 50 else 0,
                                     "Above 50: bullish momentum." if v > 50 else "Below 50: bearish momentum."
                                     if v < 50 else "Neutral at 50."))
    # MACD
    if pd.notna(last["macd_hist"]):
        h = float(last["macd_hist"])
        prev_h = d["macd_hist"].iloc[-2]
        cross = ""
        if pd.notna(prev_h) and (prev_h <= 0 < h):
            cross = " Fresh bullish crossover."
        elif pd.notna(prev_h) and (prev_h >= 0 > h):
            cross = " Fresh bearish crossover."
        readings.append(_reading("MACD histogram", _num(h), 1 if h > 0 else -1,
                                 ("MACD above signal." if h > 0 else "MACD below signal.") + cross))
    # EMA stack
    emas = {n: last[f"ema{n}"] for n in (9, 21, 50, 200)}
    if pd.notna(emas[9]) and pd.notna(emas[21]) and pd.notna(emas[50]):
        if emas[9] > emas[21] > emas[50]:
            readings.append(_reading("EMA 9/21/50", "bullish stack", 1, "Short EMAs stacked above longer: uptrend alignment."))
        elif emas[9] < emas[21] < emas[50]:
            readings.append(_reading("EMA 9/21/50", "bearish stack", -1, "Short EMAs stacked below longer: downtrend alignment."))
        else:
            readings.append(_reading("EMA 9/21/50", "mixed", 0, "EMAs not aligned: no clean trend."))
    if pd.notna(emas[200]):
        above = price > emas[200]
        readings.append(_reading("Price vs EMA 200", _num(emas[200]), 1 if above else -1,
                                 "Price above 200 EMA: long-term bias up." if above else
                                 "Price below 200 EMA: long-term bias down."))
    if pd.notna(last["sma50"]) and pd.notna(last["sma200"]):
        golden = last["sma50"] > last["sma200"]
        readings.append(_reading("SMA 50 vs 200", "50 > 200" if golden else "50 < 200", 1 if golden else -1,
                                 "Golden-cross regime." if golden else "Death-cross regime."))
    # Bollinger
    if pd.notna(last["bb_pct_b"]):
        pb = float(last["bb_pct_b"])
        txt = ("Closed above upper band: strong push or overextension." if pb > 1 else
               "Closed below lower band: strong selling or overextension." if pb < 0 else
               "Inside the bands.")
        readings.append(_reading("Bollinger %B", _num(pb, 3), 0, txt))
    # ADX
    if pd.notna(last["adx"]) and len(df) >= 28:
        a = float(last["adx"])
        bias = (1 if last["plus_di"] > last["minus_di"] else -1) if a >= 20 else 0
        strength = "strong" if a >= 25 else "developing" if a >= 20 else "weak / ranging"
        side = "buyers (+DI)" if last["plus_di"] > last["minus_di"] else "sellers (-DI)"
        readings.append(_reading("ADX 14", _num(a), bias, f"Trend strength {strength}; {side} in control."))
    # VWAP
    if pd.notna(last.get("vwap", np.nan)):
        above = price > last["vwap"]
        readings.append(_reading("Session VWAP (UTC day)", _num(last["vwap"]), 1 if above else -1,
                                 "Price above VWAP." if above else "Price below VWAP."))
    # Volume
    if pd.notna(last["vol_ratio"]):
        vr = float(last["vol_ratio"])
        up_bar = last["close"] > last["open"]
        if vr >= 1.5:
            readings.append(_reading("Volume vs 20-bar avg", _num(vr), 1 if up_bar else -1,
                                     f"Volume {vr:.1f}x average on a {'rising' if up_bar else 'falling'} candle: "
                                     "move is volume-confirmed."))
        else:
            readings.append(_reading("Volume vs 20-bar avg", _num(vr), 0,
                                     "Volume near/below average: weak confirmation." if vr < 1 else "Volume slightly above average."))

    breakout = breakout_state(d)
    vol = volatility_regime(d["atr_pct"])
    divergence = detect_divergence(d, sh, sl)
    if divergence["bullish"]:
        readings.append(_reading("RSI divergence", "bullish", 1, "Price made a lower low while RSI made a higher low."))
    if divergence["bearish"]:
        readings.append(_reading("RSI divergence", "bearish", -1, "Price made a higher high while RSI made a lower high."))

    biases = [r["bias"] for r in readings]
    bull, bear = biases.count(1), biases.count(-1)
    return {
        "timeframe": timeframe,
        "status": "ok",
        "bars": int(len(df)),
        "as_of_candle_close": int(df["close_time"].iloc[-1]),
        "price": _num(price),
        "indicators": {
            "rsi14": _num(last["rsi14"]), "macd": _num(last["macd"]), "macd_signal": _num(last["macd_signal"]),
            "macd_hist": _num(last["macd_hist"]), "ema9": _num(last["ema9"]), "ema21": _num(last["ema21"]),
            "ema50": _num(last["ema50"]), "ema200": _num(last["ema200"]), "sma50": _num(last["sma50"]),
            "sma200": _num(last["sma200"]), "bb_upper": _num(last["bb_upper"]), "bb_mid": _num(last["bb_mid"]),
            "bb_lower": _num(last["bb_lower"]), "atr14": _num(last["atr14"]), "atr_pct": _num(last["atr_pct"], 3),
            "adx": _num(last["adx"]) if len(df) >= 28 else None, "plus_di": _num(last["plus_di"]),
            "minus_di": _num(last["minus_di"]), "vwap": _num(last.get("vwap")), "vol_ratio": _num(last["vol_ratio"]),
            "bb_pct_b": _num(last["bb_pct_b"], 3), "last_candle_up": bool(last["close"] >= last["open"]),
        },
        "readings": readings,
        "tally": {"bullish": bull, "bearish": bear, "neutral": biases.count(0)},
        "structure": structure,
        "levels": levels,
        "breakout": breakout,
        "volatility": vol,
        "divergence": divergence,
        "warnings": warnings,
        "disclaimer": "Indicator readings are evidence, not guarantees. The tally is a count, not a probability.",
    }


def chart_series(df: pd.DataFrame, timeframe: str, tail: int = 300) -> dict:
    """Candles plus overlays for the chart, JSON-safe."""
    if df is None or df.empty:
        return {"time": []}
    d = add_indicators(df, timeframe).iloc[-tail:]

    def col(name: str) -> list:
        return [_num(x) for x in d[name].tolist()]

    return {
        "time": [t.isoformat() for t in d.index],
        "open": col("open"), "high": col("high"), "low": col("low"), "close": col("close"),
        "volume": [_num(x, 4) for x in d["volume"].tolist()],
        "ema9": col("ema9"), "ema21": col("ema21"), "ema50": col("ema50"), "ema200": col("ema200"),
        "bb_upper": col("bb_upper"), "bb_lower": col("bb_lower"), "vwap": col("vwap"),
        "rsi14": col("rsi14"), "macd": col("macd"), "macd_signal": col("macd_signal"), "macd_hist": col("macd_hist"),
    }
