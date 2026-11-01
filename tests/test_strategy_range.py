"""Strategy Lab round 3: Range trading (4h, long only, only in the existing SIDEWAYS regime)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services.backtesting import HOLDOUT_START

ROOT = Path(__file__).resolve().parents[1]
H4 = 4 * 3_600_000
T0 = 1_700_000_000_000 // H4 * H4
SIDE = "SIDEWAYS / RANGING"


def cfg(**kw):
    from services.strategies.range import validate_config
    return validate_config(kw)


def frame(closes, highs=None, lows=None, opens=None, t0=T0):
    n = len(closes)
    closes = np.asarray(closes, float)
    opens = np.asarray(opens if opens is not None else np.concatenate([[closes[0]], closes[:-1]]), float)
    highs = np.asarray(highs if highs is not None else np.maximum(opens, closes) + 0.5, float)
    lows = np.asarray(lows if lows is not None else np.minimum(opens, closes) - 0.5, float)
    return pd.DataFrame({"open_time": t0 + np.arange(n) * H4, "open": opens, "high": highs, "low": lows, "close": closes,
                         "volume": 1.0, "close_time": t0 + (np.arange(n) + 1) * H4 - 1})


def box(n=120, lo=95.0, hi=105.0, period=20):
    """A clean range between lo and hi that touches both boundaries several times."""
    i = np.arange(n)
    mid, amp = (lo + hi) / 2, (hi - lo) / 2 - 0.5
    closes = mid + amp * np.sin(2 * np.pi * i / period)
    return frame(closes)


# ---------------------------------------------------------------- configuration and pre-registration
def test_defaults_and_the_preregistration():
    from services.strategies import range as rng
    from services.strategies.base import config_hash
    assert rng.validate_config({}) == rng.DEFAULTS
    for bad in ({"lookback": 5}, {"target": "far"}, {"risk_pct": 3}, {"rsi": 30}):
        with pytest.raises(ValueError):
            rng.validate_config(bad)
    text = (ROOT / "PREREGISTRATION_RANGE.md").read_text(encoding="utf-8")
    assert f"Config hash: `{config_hash(rng.DEFAULTS)}`" in text
    for k, v in rng.DEFAULTS.items():
        assert f"`{k}` | `{v}`" in text
    for must in ("SIDEWAYS", "ADX(14) < 20", "50 closed 4h bars", ">= 2 touches", "3 × ATR(14)", "bottom 20%",
                 "range low − 0.5 × ATR(14)", "midpoint", "30 × 4h bars", "INCONCLUSIVE", "+0.15R", "1.3", "15%",
                 "90%", "lookback 30 / 50 / 80", "ADX 15 / 20 / 25", "range high", "regime.py"):
        assert must in text, must


def test_the_regime_dependency_is_pinned():
    from services.strategies import range as rng
    assert rng.regime_dependency_ok() is True


# ---------------------------------------------------------------- range features and setups
def test_range_touches_width_and_the_reclaim_trigger():
    from services.strategies.range import features
    df = box()
    last = len(df) - 1
    df.loc[last, ["open", "low", "close", "high"]] = [95.2, 93.0, 95.6, 96.0]       # wick below the range low, close back above
    ft = features(df, [SIDE] * len(df), cfg())
    r = ft.iloc[-1]
    win = df.iloc[last - 50:last]
    assert r["rh"] == pytest.approx(win["high"].max()) and r["rl"] == pytest.approx(win["low"].min())
    assert r["touches_top"] >= 2 and r["touches_bottom"] >= 2 and r["width_ok"]
    assert r["trigger"] and r["trigger_kind"] == "reclaim of the range low"
    assert r["stop_plan"] == pytest.approx(r["rl"] - 0.5 * r["atr"]) and r["target_plan"] == pytest.approx(r["mid"])
    assert bool(r["buy"]) is bool(r["adx"] < 20)
    top = features(df, [SIDE] * len(df), cfg(target="top")).iloc[-1]
    assert top["target_plan"] == pytest.approx(r["rh"])


def test_no_setup_outside_sideways_or_with_a_narrow_range():
    from services.strategies.range import features
    df = box()
    df.loc[len(df) - 1, ["open", "low", "close", "high"]] = [95.2, 93.0, 95.6, 96.0]
    assert not features(df, ["TRENDING BULLISH"] * len(df), cfg()).iloc[-1]["buy"]
    assert not features(df, [SIDE] * len(df), cfg(min_width_atr=20)).iloc[-1]["buy"]
    assert not features(df, [SIDE] * len(df), cfg(min_touches=10)).iloc[-1]["buy"]


def test_touches_next_to_each_other_count_once():
    from services.strategies.range import _touches
    assert _touches(None, np.array([True, True, False, True, False, False, True, True])) == 3


def test_the_range_uses_only_bars_before_the_signal_bar():
    from services.strategies.range import features
    df = box()
    a = features(df, [SIDE] * len(df), cfg())
    b = df.copy()
    b.loc[80:, ["high", "low", "close", "open"]] *= 3                              # the future changes completely
    fb = features(b, [SIDE] * len(df), cfg())
    cols = ["rh", "rl", "touches_top", "touches_bottom", "stop_plan", "target_plan", "trigger"]
    assert a.loc[:79, cols].equals(fb.loc[:79, cols])                             # bars before the change: identical
    assert a.loc[80, ["rh", "rl"]].equals(fb.loc[80, ["rh", "rl"]])               # bar 80's range uses bars 30..79 only


# ---------------------------------------------------------------- one bar of the simulation
def test_entry_stop_target_regime_exit_and_time_limit():
    from services.strategies.range import new_state, step
    c = cfg()
    def opened():
        st, _ = step(new_state(), {"open_time": 0, "open": 99, "high": 100, "low": 98, "close": 99},
                     {"buy": True, "stop_plan": 94.0, "target_plan": 100.0}, c)
        st, ev = step(st, {"open_time": H4, "open": 96, "high": 97, "low": 95.5, "close": 96.5}, {}, c)
        assert ev[0]["kind"] == "entry" and st["units"] == pytest.approx(0.01 / (96 * 1.0005 - 94))
        return st
    st, ev = step(opened(), {"open_time": 2 * H4, "open": 96, "high": 101, "low": 93, "close": 97}, {}, c)
    assert ev[-1]["reason"] == "stop"                                              # both touched: stop first
    st, ev = step(opened(), {"open_time": 2 * H4, "open": 97, "high": 100.5, "low": 96, "close": 99}, {}, c)
    assert ev[-1]["reason"] == "target" and ev[-1]["exit"] == pytest.approx(100 * 0.9995)
    st, _ = step(opened(), {"open_time": 2 * H4, "open": 97, "high": 98, "low": 96, "close": 97}, {"regime_exit": True}, c)
    st, ev = step(st, {"open_time": 3 * H4, "open": 97.5, "high": 98, "low": 97, "close": 97.5}, {}, c)
    assert ev[0]["reason"] == "regime left SIDEWAYS" and ev[0]["exit"] == pytest.approx(97.5 * 0.9995)
    st = opened()
    c5 = cfg(time_limit_bars=5)
    for k in range(2, 6):
        st, _ = step(st, {"open_time": k * H4, "open": 97, "high": 98, "low": 96, "close": 97}, {}, c5)
    st, ev = step(st, {"open_time": 6 * H4, "open": 97, "high": 98, "low": 96, "close": 97}, {}, c5)
    assert ev[0]["reason"].startswith("time limit") and ev[0]["bars"] == 5


def test_no_entry_when_the_open_is_already_beyond_stop_or_target():
    from services.strategies.range import new_state, step
    st, _ = step(new_state(), {"open_time": 0, "open": 99, "high": 100, "low": 98, "close": 99},
                 {"buy": True, "stop_plan": 94.0, "target_plan": 100.0}, cfg())
    st, ev = step(st, {"open_time": H4, "open": 101, "high": 102, "low": 100, "close": 101}, {}, cfg())
    assert ev[0]["kind"] == "skipped" and st["units"] == 0


# ---------------------------------------------------------------- criteria, design window, live signal, forward
def test_criteria_are_the_trend_ones():
    from services.strategies.range import evaluate_criteria
    good = {"trades": 40, "expectancy_r": 0.2, "profit_factor": 1.5, "max_drawdown_pct": 10.0}
    assert evaluate_criteria(good, 95)[1] == "PASS"
    assert evaluate_criteria({**good, "trades": 29}, 95)[1] == "INCONCLUSIVE"
    for bad in ({"expectancy_r": 0.1}, {"profit_factor": 1.2}, {"max_drawdown_pct": 16.0}):
        assert evaluate_criteria({**good, **bad}, 95)[1] == "FAIL"
    assert evaluate_criteria(good, 80)[1] == "FAIL"


def long_box(n=900):
    rng = np.random.default_rng(3)
    i = np.arange(n)
    closes = 100 + 4 * np.sin(2 * np.pi * i / 24) + rng.normal(0, 0.6, n)
    df = frame(closes)
    shift = (HOLDOUT_START - int(df["close_time"].iloc[700]) - 1) // H4 * H4
    df["open_time"] += shift
    df["close_time"] += shift
    return df


def test_design_backtest_stops_before_the_holdout_and_reports_everything():
    from services.strategies.range import backtest
    r = backtest(long_box().to_dict("records"), cfg(), random_runs=20)
    assert r["period"]["last_day_close"] < HOLDOUT_START and r["period"]["kind"] == "design"
    assert {"buy_and_hold", "walk_forward", "sensitivity", "random_entries", "criteria", "regime_share_pct"} <= set(r)
    assert len(r["sensitivity"]) == 10 and r["sensitivity"][-1]["target"] == "top"
    assert sum(x["is_default"] for x in r["sensitivity"]) == 1
    assert r["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE") and r["regime_module_sha256"]


def test_live_signal_and_forward_step_match_the_backtest(db):
    from services.strategies import range as rng
    from services.strategies.base import DataQuality
    from services.strategy_forward import StrategyForward
    df = long_box()
    dq = DataQuality("binance", 1, int(df["close_time"].iloc[-1]), False, 0, [])
    r = rng.evaluate_frames({"4h": df}, cfg(), now=1, data=dq)
    assert r.signal in ("BUY SETUP", "HOLD", "NO TRADE", "EXIT / REDUCE EXPOSURE") and r.regime
    assert rng.evaluate_frames({"4h": df.iloc[:100]}, cfg(), now=1, data=dq).signal == "NO TRADE"
    rows = df.to_dict("records")
    start = int(df["open_time"].iloc[600])
    db.strategy_forward_start("range", "h", cfg(), "fp", start_day=start, now=1)
    fwd = StrategyForward(db, clock=lambda: int(df["close_time"].iloc[650]) + 1, candles=lambda tf: rows)
    assert fwd.process() > 0
    marks = [e for e in db.strategy_forward_events(db.strategy_forward_test("range", "h")["id"]) if e["kind"] == "mark"]
    assert len(marks) == 51
    labels = rng.regime_labels(df, upto=650, start=590)
    sim = rng.simulate(rng.features(df, labels, cfg()), cfg(), 600, 650)
    assert [round(m["detail"]["equity"], 9) for m in marks[:-1]] == [round(x, 9) for x in sim["equity"][:-1]]
