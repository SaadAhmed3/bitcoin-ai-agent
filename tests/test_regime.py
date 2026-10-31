"""Market regime detection (display only, closed candles only, no look-ahead)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.regime import REGIMES, RegimeConfig, classify, regime_at, regime_history, regime_score

H4 = 4 * 3_600_000
T0 = 1_700_000_000_000 // H4 * H4


def frame(close: np.ndarray, rf: np.ndarray | float = 0.004, vol: float = 100.0, start: int = T0,
          step: int = H4) -> pd.DataFrame:
    """Candles from a close path: open = previous close, wicks = rf x the candle's own close beyond the body (so two
    neighbouring candles never share the exact same high: equal highs are not swing points)."""
    n = len(close)
    rf = np.broadcast_to(np.asarray(rf, dtype=float), (n,))
    opens = np.concatenate([[close[0]], close[:-1]])
    rows = []
    for i in range(n):
        o, c = float(opens[i]), float(close[i])
        rows.append({"open_time": start + i * step, "open": o, "high": max(o, c) + c * rf[i],
                     "low": min(o, c) - c * rf[i], "close": c, "volume": vol, "close_time": start + (i + 1) * step - 1})
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


def trend(n=600, drift=0.003, amp=0.012, period=24):
    i = np.arange(n)
    return 30_000 * np.exp(drift * i) * (1 + amp * np.sin(2 * np.pi * i / period))


def ranging(n=600, amp=0.01, period=24):
    i = np.arange(n)
    return 30_000 * (1 + amp * np.sin(2 * np.pi * i / period))


def mean_reverting(seed: int, n=600, sd=0.006, phi=0.85):
    """A seeded AR(1) walk in log price around 30,000: no trend, irregular swings."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal(0, sd)
    return 30_000 * np.exp(x)


DECLINING = np.linspace(0.005, 0.003, 600)       # volatility drifts down: trends are not "high volatility"


def test_bullish_trend():
    r = classify(frame(trend(), DECLINING))
    assert r["status"] == "ok" and r["regime"] == "TRENDING BULLISH" and r["direction"] == "bullish"
    assert 60 <= r["confidence"] <= 100 and r["evidence"] and r["timeframe"] == "4h"
    assert r["checks"]["ema_alignment"] is True and r["metrics"]["adx"] >= 20
    assert regime_score(r) == pytest.approx(r["confidence"] / 100)


def test_bearish_trend():
    r = classify(frame(trend(drift=-0.003), DECLINING))
    assert r["regime"] == "TRENDING BEARISH" and r["direction"] == "bearish"
    assert regime_score(r) == pytest.approx(-r["confidence"] / 100)


def test_sideways_market():
    r = classify(frame(mean_reverting(3), 0.004))
    assert r["regime"] == "SIDEWAYS / RANGING" and r["direction"] == "neutral"
    assert r["metrics"]["adx"] < 20 and regime_score(r) == 0


def test_high_volatility_comes_first():
    rf = np.full(600, 0.004)
    rf[-12:] = 0.03                                     # the last two days: ranges 7x wider
    r = classify(frame(trend(), rf))
    assert r["regime"] == "HIGH VOLATILITY" and r["volatility"] == "high"
    assert r["metrics"]["atr_pct_percentile"] >= 90


def test_low_volatility():
    close = ranging()
    close[-60:] = 30_000 * (1 + 0.001 * np.sin(2 * np.pi * np.arange(60) / 24))
    rf = np.full(600, 0.004)
    rf[-60:] = 0.0005
    r = classify(frame(close, rf))
    assert r["regime"] == "LOW VOLATILITY" and r["volatility"] == "low"


def test_transitional_when_evidence_is_mixed():
    r = classify(frame(mean_reverting(1), 0.004))      # ADX 23, 0 bullish / 3 bearish checks: neither side has 4
    assert r["regime"] == "TRANSITIONAL / UNCERTAIN"
    assert r["confidence"] == 40 and r["conflicts"]   # says what disagrees


def test_insufficient_data():
    r = classify(frame(trend(n=150)))
    assert r["status"] == "insufficient_data" and r["regime"] is None and regime_score(r) == 0


def test_incomplete_candle_is_excluded():
    df = frame(trend(), DECLINING)
    now = int(df["close_time"].iloc[-1]) - 1_000         # the last candle has not closed yet
    spike = df.copy()
    spike.iloc[-1, spike.columns.get_loc("high")] *= 1.5     # an in-progress spike must not count
    assert classify(spike, now=now) == classify(df.iloc[:-1])


def test_higher_timeframe_conflict_lowers_confidence_and_is_listed():
    c4 = frame(trend(), DECLINING)
    agree = classify(c4, c1d=frame(trend(n=400), step=86_400_000, start=T0 - 400 * 86_400_000))
    disagree = classify(c4, c1d=frame(trend(n=400, drift=-0.003), step=86_400_000, start=T0 - 400 * 86_400_000))
    assert agree["regime"] == disagree["regime"] == "TRENDING BULLISH"
    assert disagree["confidence"] == agree["confidence"] - RegimeConfig().conflict_penalty
    assert any("1D" in c for c in disagree["conflicts"])


def test_regime_at_never_reads_future_candles():
    df = frame(trend(), DECLINING)
    i = 450
    future = df.copy()
    future.iloc[i + 1:, future.columns.get_loc("close")] *= 0.5     # crash after bar i
    future.iloc[i + 1:, future.columns.get_loc("low")] *= 0.5
    assert regime_at(df, i) == regime_at(future, i)
    assert regime_at(df, i)["as_of"] == int(df["close_time"].iloc[i])


def test_history_reports_previous_regime_and_change_time():
    close = trend(n=600)
    close[-40:] = close[-41] * mean_reverting(1, n=40, sd=0.004) / 30_000     # a trend that stalls
    df = frame(close)
    h = regime_history(df, bars=60)
    times = sorted(h["labels"])
    assert len(times) == 60 and all(r in REGIMES for r in h["labels"].values())
    for t in times[::7]:                                              # each label from its own past only
        i = int(np.flatnonzero(df["close_time"].to_numpy() == t)[0])
        assert h["labels"][t] == regime_at(df, i)["regime"]
    assert h["current"] == h["labels"][times[-1]] and h["changed_at"] in times
    run = [t for t in times if t >= h["changed_at"]]
    assert all(h["labels"][t] == h["current"] for t in run)          # the current run starts at changed_at
    before = times[times.index(h["changed_at"]) - 1]
    assert h["previous"] == h["labels"][before] != h["current"]
