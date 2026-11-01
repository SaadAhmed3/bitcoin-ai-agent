"""Strategy Lab round 2: Trend following (4h pullback continuation), defined differently from H2."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services.backtesting import HOLDOUT_START

ROOT = Path(__file__).resolve().parents[1]
H4, D = 4 * 3_600_000, 86_400_000
T0 = 1_700_000_000_000 // D * D


def cfg(**kw):
    from services.strategies.trend import validate_config
    return validate_config(kw)


def bar(i, o, h, l, c, t0=T0):
    return {"open_time": t0 + i * H4, "open": o, "high": h, "low": l, "close": c}


# ---------------------------------------------------------------- configuration and pre-registration
def test_defaults_ranges_and_the_preregistration():
    from services.strategies import trend
    from services.strategies.base import config_hash
    assert trend.validate_config({}) == trend.DEFAULTS
    for bad in ({"risk_pct": 2.5}, {"ema_fast": 60, "ema_mid": 50}, {"target_r": 0.5}, {"rsi_min": 45}):
        with pytest.raises(ValueError):
            trend.validate_config(bad)
    text = (ROOT / "PREREGISTRATION_TREND.md").read_text(encoding="utf-8")
    assert f"Config hash: `{config_hash(trend.DEFAULTS)}`" in text
    for k, v in trend.DEFAULTS.items():
        assert f"`{k}` | `{v}`" in text
    for must in ("H2", "differs", "30 × 4h bars", "lowest low of the last 5 closed 4h bars", "0.25 × ATR(14)",
                 "INCONCLUSIVE", "+0.15R", "1.3", "15%", "90%", "ADX 15 / 20 / 25", "1.5R / 2R / 3R"):
        assert must in text, must
    assert "RSI" not in text.split("## 2.")[1].split("## 3.")[0]           # no RSI / volume filter in the rules


# ---------------------------------------------------------------- one simulated bar (shared with the forward test)
def test_entry_at_the_next_open_with_costs_and_one_percent_risk():
    from services.strategies.trend import new_state, step
    c = cfg()
    st, ev = step(new_state(), bar(0, 100, 101, 99, 100), {"buy": True, "stop_plan": 95.0}, c)
    assert ev == [] and st["pending_stop"] == 95.0
    st, ev = step(st, bar(1, 100, 101, 99.5, 100.5), {}, c)
    fill = 100 * 1.0005
    units = 0.01 / (fill - 95)
    assert ev[0]["kind"] == "entry" and ev[0]["fill"] == pytest.approx(fill) and st["units"] == pytest.approx(units)
    assert st["target"] == pytest.approx(fill + 2 * (fill - 95))
    assert st["cash"] == pytest.approx(1 - units * fill * 1.001)


def test_position_size_never_exceeds_the_account():
    from services.strategies.trend import new_state, step
    st, _ = step(new_state(), bar(0, 100, 100, 100, 100), {"buy": True, "stop_plan": 99.99}, cfg())
    st, _ = step(st, bar(1, 100, 100.1, 99.995, 100.1), {}, cfg())      # below the 2R target (100.17)
    assert st["units"] * st["fill"] * 1.001 == pytest.approx(1.0) and st["cash"] == pytest.approx(0, abs=1e-12)


def _open(stop=95.0, **c):
    from services.strategies.trend import new_state, step
    k = cfg(**c)
    st, _ = step(new_state(), bar(0, 100, 101, 99, 100), {"buy": True, "stop_plan": stop}, k)
    st, _ = step(st, bar(1, 100, 101, 99.5, 100.5), {}, k)
    return st, k


def test_stop_target_same_bar_gap_trend_break_and_time_limit():
    from services.strategies.trend import step
    st, k = _open()
    s2, ev = step(st, bar(2, 100, 120, 94, 100), {}, k)                 # touches both: stop first
    assert ev[-1]["reason"] == "stop" and ev[-1]["exit"] == pytest.approx(95 * 0.9995)
    assert s2["trades"][0]["r"] < -1                                      # -1R plus costs
    st, k = _open()
    s2, ev = step(st, bar(2, 90, 91, 89, 90), {}, k)                     # gap below the stop: the open
    assert ev[-1]["reason"] == "stop (gap)" and ev[-1]["exit"] == pytest.approx(90 * 0.9995)
    st, k = _open()
    s2, ev = step(st, bar(2, 101, 120, 100, 110), {}, k)
    assert ev[-1]["reason"] == "target" and ev[-1]["exit"] == pytest.approx(st["target"] * 0.9995)
    st, k = _open()
    s2, _ = step(st, bar(2, 100.5, 101, 100, 100.2), {"trend_break": True}, k)
    s3, ev = step(s2, bar(3, 99, 99.5, 98, 99), {}, k)                  # exits at the NEXT open
    assert ev[0]["reason"].startswith("trend break") and ev[0]["exit"] == pytest.approx(99 * 0.9995)
    st, k = _open(time_limit_bars=5)
    for i in range(2, 6):                                                 # bars 1..5 held
        st, ev = step(st, bar(i, 100.5, 101, 100, 100.5), {}, k)
    assert st["exit_next"].startswith("time limit")
    st, ev = step(st, bar(6, 100.4, 101, 100, 100.5), {}, k)
    assert ev[0]["reason"].startswith("time limit") and ev[0]["bars"] == 5


# ---------------------------------------------------------------- setups on candles
def uptrend(n=700, seed=2):
    """A rising 4h series with regular pullbacks (deterministic)."""
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    close = 20_000 * np.exp(0.0015 * i) * (1 + 0.015 * np.sin(2 * np.pi * i / 30)) * np.exp(rng.normal(0, 0.002, n))
    opens = np.concatenate([[close[0]], close[:-1]])
    hi = np.maximum(opens, close) * (1 + 0.003)
    lo = np.minimum(opens, close) * (1 - 0.003)
    df4 = pd.DataFrame({"open_time": T0 + i * H4, "open": opens, "high": hi, "low": lo, "close": close,
                        "volume": 1.0, "close_time": T0 + (i + 1) * H4 - 1})
    days = np.arange(-120, n // 6 + 1)
    dclose = 20_000 * np.exp(0.0015 * 6 * days)
    df1 = pd.DataFrame({"open_time": T0 + days * D, "open": dclose, "high": dclose, "low": dclose, "close": dclose,
                        "volume": 1.0, "close_time": T0 + (days + 1) * D - 1})
    return df4, df1


def test_setups_follow_the_rules_and_the_stop_formula():
    from services.strategies.trend import indicators
    df4, df1 = uptrend()
    ind = indicators(df4, df1, cfg())
    buys = ind.index[ind["buy"]].tolist()
    assert buys, "no setup in a clean uptrend with pullbacks"
    i = buys[-1]
    r = ind.iloc[i]
    assert r["ema_f"] > r["ema_m"] > r["ema_s"] and r["adx"] >= 20 and r["htf_ok"] and r["slope_ok"]
    assert ind["low"].iloc[i - 1] <= ind["ema_f"].iloc[i - 1] and r["close"] > ind["high"].iloc[i - 1]
    assert r["stop_plan"] == pytest.approx(ind["low"].iloc[i - 4:i + 1].min() - 0.25 * r["atr"])
    strict = indicators(df4, df1, cfg(adx_min=45))                       # the ADX threshold is respected
    assert (strict.loc[strict["buy"], "adx"] >= 45).all() and strict["buy"].sum() < ind["buy"].sum()


def test_the_daily_filter_only_uses_daily_candles_closed_by_the_4h_close():
    from services.strategies.trend import indicators
    df4, df1 = uptrend()
    a = indicators(df4, df1, cfg())
    crash = df1.copy()
    t = int(df4["close_time"].iloc[400])
    crash.loc[crash["close_time"] > t, "close"] = 1.0                     # future days collapse
    b = indicators(df4, crash, cfg())
    assert (a["htf_ok"].iloc[:401] == b["htf_ok"].iloc[:401]).all()


def test_live_signals():
    from services.strategies.base import DataQuality
    from services.strategies.trend import evaluate_frames, indicators
    df4, df1 = uptrend()
    dq = DataQuality("binance", 1, int(df4["close_time"].iloc[-1]), False, 0, [])
    ind = indicators(df4, df1, cfg())
    i = ind.index[ind["buy"]].tolist()[-1]
    r = evaluate_frames({"4h": df4.iloc[:i + 1], "1d": df1}, cfg(), now=1, data=dq)
    assert r.signal == "BUY SETUP" and r.stop is not None and r.targets and r.reward_risk is not None
    assert r.entry_zone and r.strength == 100 and "not a probability" in r.to_dict()["strength_note"]
    down = df4.copy()
    down[["open", "high", "low", "close"]] = down[["open", "high", "low", "close"]].to_numpy()[::-1]
    assert evaluate_frames({"4h": down, "1d": df1}, cfg(), now=1, data=dq).signal in ("NO TRADE", "EXIT / REDUCE EXPOSURE")
    stale = DataQuality("binance", 1, 1, True, 0, ["stale"])
    assert evaluate_frames({"4h": df4, "1d": df1}, cfg(), now=1, data=stale).signal == "NO TRADE"
    assert evaluate_frames({"4h": df4.iloc[:50], "1d": df1}, cfg(), now=1, data=dq).signal == "NO TRADE"


# ---------------------------------------------------------------- criteria, baselines, design window
def test_criteria_and_the_sample_size_rule():
    from services.strategies.trend import evaluate_criteria
    good = {"trades": 40, "expectancy_r": 0.2, "profit_factor": 1.5, "max_drawdown_pct": 10.0}
    assert evaluate_criteria(good, 95)[1] == "PASS"
    assert evaluate_criteria({**good, "trades": 29}, 95)[1] == "INCONCLUSIVE"
    assert evaluate_criteria({**good, "expectancy_r": 0.149}, 95)[1] == "FAIL"
    assert evaluate_criteria({**good, "profit_factor": 1.29}, 95)[1] == "FAIL"
    assert evaluate_criteria({**good, "max_drawdown_pct": 15.01}, 95)[1] == "FAIL"
    assert evaluate_criteria({**good, "profit_factor": None}, 95)[1] == "FAIL"
    assert evaluate_criteria(good, 89.9)[1] == "FAIL"


def test_design_backtest_stops_before_the_holdout_and_reports_everything():
    from services.strategies.trend import backtest
    df4, df1 = uptrend(n=1500)
    shift4 = HOLDOUT_START - int(df4["close_time"].iloc[1200]) - 1         # bar 1200 closes just before the holdout
    df4["open_time"] += shift4 // H4 * H4
    df4["close_time"] += shift4 // H4 * H4
    df1["open_time"] += shift4 // H4 * H4
    df1["close_time"] += shift4 // H4 * H4
    r = backtest(df4.to_dict("records"), df1.to_dict("records"), cfg(), random_runs=50)
    assert r["period"]["last_day_close"] < HOLDOUT_START and r["period"]["kind"] == "design"
    assert all(t["exit_time"] <= r["period"]["last_day_close"] + 1 for t in r["trade_list"])
    assert {"buy_and_hold", "h2_rule", "walk_forward", "sensitivity", "random_entries", "criteria"} <= set(r)
    assert len(r["sensitivity"]) == 9 and sum(x["is_default"] for x in r["sensitivity"]) == 1
    assert r["random_entries"]["runs"] == 50
    assert (r["sample_warning"] is not None) == (r["metrics"]["trades"] < 30)
    assert r["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE")


def test_random_entries_are_deterministic_and_use_the_same_rules():
    from services.strategies.trend import indicators, random_entries, simulate
    df4, df1 = uptrend(n=900)
    c = cfg()
    ind = indicators(df4, df1, c)
    sim = simulate(ind, c, 400, 899)
    a = random_entries(ind, sim["trades"], c, 400, 899, runs=20, seed=7)
    b = random_entries(ind, sim["trades"], c, 400, 899, runs=20, seed=7)
    assert a == b and a["runs"] == 20
