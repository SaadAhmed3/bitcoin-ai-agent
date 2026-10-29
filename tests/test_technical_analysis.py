import json

import numpy as np
import pandas as pd
import pytest
from conftest import make_rows

from services.market_data import rows_to_frame
from services.technical_analysis import (adx, analyze, atr, bollinger, chart_series, ema, macd,
                                         market_structure, rsi, sma, support_resistance, swing_points)


def frame(n=400, tf="1h", seed=7):
    return rows_to_frame(make_rows(n, tf, seed=seed))


def test_sma_and_ema_known_values():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0]
    e = ema(s, 3)
    # adjust=False, alpha=0.5: 1, 1.5, 2.25, 3.125, 4.0625; first 2 masked by min_periods
    assert np.isnan(e.iloc[1])
    assert e.iloc[2] == pytest.approx(2.25) and e.iloc[4] == pytest.approx(4.0625)


def test_rsi_extremes_and_bounds():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert rsi(up).iloc[-1] == 100
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert rsi(down).iloc[-1] == pytest.approx(0)
    flat = pd.Series([5.0] * 30)
    assert rsi(flat).iloc[-1] == 50
    r = rsi(frame()["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    assert rsi(up).iloc[:14].isna().all()


def test_rsi_wilder_reference_value():
    # Classic textbook series. First RSI uses SMA seeds of the 14 changes:
    # avg gain = 3.34/14 = 0.238571, avg loss = 1.40/14 = 0.1 -> RS 2.38571 -> RSI 70.464.
    # (StockCharts prints 70.53 because it rounds the intermediate averages.)
    closes = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
                        45.89, 46.03, 45.61, 46.28, 46.28])
    assert rsi(closes).iloc[-1] == pytest.approx(70.464, abs=0.001)


def test_macd_relationship():
    m = macd(frame()["close"]).dropna()
    assert np.allclose(m["macd_hist"], m["macd"] - m["macd_signal"])


def test_bollinger_contains_mid():
    b = bollinger(frame()["close"]).dropna()
    assert (b["bb_upper"] >= b["bb_mid"]).all() and (b["bb_lower"] <= b["bb_mid"]).all()


def test_atr_constant_range():
    n = 40
    df = pd.DataFrame({"open": [100.0] * n, "high": [105.0] * n, "low": [95.0] * n, "close": [100.0] * n})
    assert atr(df).iloc[-1] == pytest.approx(10.0)


def test_adx_bounds():
    a = adx(frame()).dropna()
    assert ((a["adx"] >= 0) & (a["adx"] <= 100)).all()


def test_swing_and_structure():
    highs = [1, 2, 5, 2, 1, 2, 7, 2, 1, 0, 1]
    lows = [0.5, 1, 3, 1, 0.5, 1, 4, 1, 0.8, -0.0 + 0.1, 0.9]
    df = pd.DataFrame({"high": highs, "low": lows, "close": highs})
    sh, sl = swing_points(df, k=2)
    assert [i for i, _ in sh] == [2, 6]
    s = market_structure([(0, 10), (5, 12)], [(1, 8), (6, 9)])
    assert s["trend"] == "uptrend"
    s = market_structure([(0, 12), (5, 10)], [(1, 9), (6, 8)])
    assert s["trend"] == "downtrend"


def test_support_resistance_sides():
    df = frame()
    sh, sl = swing_points(df)
    lv = support_resistance(df, sh, sl, atr_value=200)
    price = df["close"].iloc[-1]
    assert all(l["price"] < price for l in lv["support"])
    assert all(l["price"] > price for l in lv["resistance"])


def test_analyze_is_json_safe_and_complete():
    out = analyze(frame(), "1h")
    json.dumps(out, allow_nan=False)  # raises on NaN/inf
    assert out["status"] == "ok" and out["readings"]
    assert out["indicators"]["ema200"] is not None
    assert sum(out["tally"].values()) == len(out["readings"])
    assert "not a probability" in out["disclaimer"]


def test_analyze_insufficient_data_warns():
    out = analyze(frame(10), "1h")
    assert out["status"] == "insufficient_data"
    out = analyze(frame(100), "1h")
    assert out["indicators"]["ema200"] is None
    assert any("EMA200" in w for w in out["warnings"])
    json.dumps(out, allow_nan=False)


def test_analyze_daily_has_no_vwap():
    out = analyze(frame(250, "1d"), "1d")
    assert out["indicators"]["vwap"] is None


def test_no_lookahead_in_indicators():
    """Indicator values at bar i must not change when future bars are appended."""
    full = frame(400)
    from services.technical_analysis import add_indicators
    a = add_indicators(full.iloc[:300], "1h")
    b = add_indicators(full, "1h").iloc[:300]
    cols = ["rsi14", "macd", "ema50", "ema200", "bb_upper", "atr14", "adx", "vwap", "vol_ratio"]
    pd.testing.assert_frame_equal(a[cols], b[cols])


def test_chart_series_lengths():
    s = chart_series(frame(), "1h", tail=120)
    assert len(s["time"]) == 120 and len(s["close"]) == 120 and len(s["rsi14"]) == 120
    json.dumps(s, allow_nan=False)
