"""Strategy Lab round 4: Breakout trading (4h, long only, few filters, no fixed target)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services.backtesting import HOLDOUT_START
from test_strategy_trend import H4, T0, uptrend

ROOT = Path(__file__).resolve().parents[1]


def cfg(**kw):
    from services.strategies.breakout import validate_config
    return validate_config(kw)


def flat(n=60, price=100.0, vol=1.0):
    """A quiet 4h series: every bar high 101, low 99 (level = 101), constant volume."""
    i = np.arange(n)
    return pd.DataFrame({"open_time": T0 + i * H4, "open": price, "high": price + 1, "low": price - 1, "close": price,
                         "volume": vol, "close_time": T0 + (i + 1) * H4 - 1})


def stairs(n=1500, seed=4):
    """A rising series with pullbacks and varying volume, shifted so bar 1200 (or the last bar) closes before the
    holdout."""
    df4, df1 = uptrend(n=n, seed=seed)
    df4["volume"] = np.random.default_rng(seed).lognormal(0, 0.45, n)
    shift = (HOLDOUT_START - int(df4["close_time"].iloc[min(1200, n - 1)]) - 1) // H4 * H4
    for df in (df4, df1):
        df["open_time"] += shift
        df["close_time"] += shift
    return df4, df1


# ---------------------------------------------------------------- configuration and pre-registration
def test_defaults_and_the_preregistration():
    from services.strategies import breakout as bo
    from services.strategies.base import config_hash
    assert bo.validate_config({}) == bo.DEFAULTS
    assert bo.DEFAULTS["lookback"] == 20 and bo.DEFAULTS["volume_mult"] == 1.2 and bo.DEFAULTS["stop_atr"] == 1.0
    assert bo.DEFAULTS["trail_bars"] == 10 and bo.DEFAULTS["time_limit_bars"] == 60 and bo.DEFAULTS["risk_pct"] == 1.0
    assert "target_r" not in bo.DEFAULTS                                          # no fixed target
    for bad in ({"lookback": 2}, {"risk_pct": 3}, {"target_r": 2}, {"volume_mult": "x"}, {"lookback": True}):
        with pytest.raises(ValueError):
            bo.validate_config(bad)
    text = (ROOT / "PREREGISTRATION_BREAKOUT.md").read_text(encoding="utf-8")
    assert f"Config hash: `{config_hash(bo.DEFAULTS)}`" in text
    for k, v in bo.DEFAULTS.items():
        assert f"`{k}` | `{v}`" in text
    for must in ("highest HIGH of the previous 20 closed 4h bars", "a wick alone never counts", "1.2 ×",
                 "next bar's open", "breakout level − 1.0 × ATR(14)", "lowest low of the last 10 bars", "60 bars",
                 "No fixed target", "initial stop", "INCONCLUSIVE", "+0.15R", "1.3", "15%", "90%",
                 "lookback 20 / 40 / 55", "volume 1.0 / 1.2 / 1.5", "failed", "retest", "H2", "Trend",
                 "differs from Trend"):
        assert must in text, must


# ---------------------------------------------------------------- the level and the signal
def test_the_level_excludes_the_signal_bar_and_a_wick_alone_never_counts():
    from services.strategies.breakout import features
    df = flat()
    k = len(df) - 1
    df.loc[k, ["high", "close", "volume"]] = [110.0, 100.5, 5.0]                  # wick far above, close below 101
    r = features(df, cfg()).iloc[-1]
    assert r["level"] == pytest.approx(101.0)                                     # its own high 110 is not the level
    assert not r["close_above"] and not r["buy"]
    df.loc[k, ["close"]] = [101.5]                                                # a CLOSE above the level
    r = features(df, cfg()).iloc[-1]
    assert r["close_above"] and r["volume_ok"] and r["buy"]
    assert r["stop_plan"] == pytest.approx(101.0 - 1.0 * r["atr"])


def test_volume_must_be_at_least_1_2_times_its_20_bar_average():
    from services.strategies.breakout import features
    df = flat()
    k = len(df) - 1
    df.loc[k, ["high", "close"]] = [102.0, 101.5]
    for vol, ok in ((1.19, False), (1.2, True), (3.0, True)):
        df.loc[k, "volume"] = vol
        r = features(df, cfg()).iloc[-1]
        assert r["vol_avg"] == pytest.approx(1.0) and bool(r["buy"]) is ok, vol


def test_features_use_only_bars_up_to_the_signal_bar():
    from services.strategies.breakout import features
    df4, _ = stairs(n=400)
    a = features(df4, cfg())
    b = df4.copy()
    b.loc[300:, ["open", "high", "low", "close"]] *= 2
    b.loc[300:, "volume"] *= 9
    fb = features(b, cfg())
    cols = ["level", "vol_avg", "atr", "stop_plan", "buy", "trail_low", "trail_exit"]
    assert a.loc[:299, cols].equals(fb.loc[:299, cols])
    assert a.loc[300, ["level", "vol_avg", "trail_low"]].equals(fb.loc[300, ["level", "vol_avg", "trail_low"]])


# ---------------------------------------------------------------- one bar of the simulation
def bar(k, o, h, low, c):
    return {"open_time": k * H4, "open": o, "high": h, "low": low, "close": c}


def opened(c=None):
    from services.strategies.breakout import new_state, step
    c = c or cfg()
    st, _ = step(new_state(), bar(0, 100, 102, 99, 101.5), {"buy": True, "stop_plan": 98.0, "level": 101.0, "atr": 3.0}, c)
    st, ev = step(st, bar(1, 102, 103, 102, 102.5), {}, c)       # low stays > level + 0.25 ATR
    assert ev[0]["kind"] == "entry" and ev[0]["time"] == H4
    fill = 102 * 1.0005
    assert st["fill"] == pytest.approx(fill) and st["stop"] == 98.0
    assert st["units"] == pytest.approx(0.01 / (fill - 98.0))                     # 1% risk to the initial stop
    return st


def test_entry_at_the_next_open_and_skipped_when_that_open_is_past_the_stop():
    from services.strategies.breakout import new_state, step
    opened()
    st, _ = step(new_state(), bar(0, 100, 102, 99, 101.5), {"buy": True, "stop_plan": 98.0, "level": 101.0, "atr": 3.0}, cfg())
    st, ev = step(st, bar(1, 97.5, 99, 97, 98), {}, cfg())
    assert ev[0]["kind"] == "skipped" and st["units"] == 0


def test_stop_trailing_exit_and_time_limit_with_r_from_the_initial_stop():
    from services.strategies.breakout import step
    st, ev = step(opened(), bar(2, 101, 101.5, 97, 98.5), {}, cfg())
    t = ev[-1]
    assert t["reason"] == "stop" and t["exit"] == pytest.approx(98 * 0.9995) and t["target"] is None
    assert t["r"] == pytest.approx(t["pnl"] / (t["units"] * (t["fill"] - 98.0)))
    st, ev = step(opened(), bar(2, 101, 101.5, 99, 100), {"trail_exit": True}, cfg())
    assert not ev and st["exit_next"].startswith("trailing exit")                # decided at the close ...
    st, ev = step(st, bar(3, 100.5, 101, 100, 100.5), {"buy": True, "stop_plan": 1.0}, cfg())
    assert ev[0]["reason"].startswith("trailing exit") and ev[0]["exit"] == pytest.approx(100.5 * 0.9995)  # ... next open
    st = opened(cfg(time_limit_bars=5))
    for k in range(2, 6):
        st, _ = step(st, bar(k, 103, 104, 102, 103), {}, cfg(time_limit_bars=5))
    st, ev = step(st, bar(6, 103, 104, 102, 103), {}, cfg(time_limit_bars=5))
    assert ev[0]["reason"].startswith("time limit") and ev[0]["bars"] == 5
    st, ev = step(opened(), bar(2, 300, 900, 299, 800), {}, cfg())
    assert not ev and st["units"] > 0                                             # no target: a huge run stays open


def test_failed_breakouts_and_retests_are_diagnostics_only():
    from services.strategies.breakout import step
    st, _ = step(opened(), bar(2, 102, 102.5, 100, 100.5), {}, cfg())             # close back below 101 within 3 bars
    assert st["failed"] is True and st["units"] > 0                               # still in the trade: not a filter
    st, ev = step(st, bar(3, 100, 100, 97, 97.5), {}, cfg())
    assert ev[-1]["failed_breakout"] is True and ev[-1]["retest"] is False
    st, _ = step(opened(), bar(2, 103, 104, 101.5, 103.5), {}, cfg())             # low within 0.25 ATR of the level
    st, ev = step(st, bar(3, 103, 103, 97, 97.5), {}, cfg())
    assert ev[-1]["retest"] is True and ev[-1]["failed_breakout"] is False


# ---------------------------------------------------------------- criteria, design backtest, live, forward
def test_criteria_are_the_pre_registered_ones():
    from services.strategies.breakout import evaluate_criteria
    good = {"trades": 40, "expectancy_r": 0.2, "profit_factor": 1.5, "max_drawdown_pct": 10.0}
    assert evaluate_criteria(good, 95)[1] == "PASS"
    assert evaluate_criteria({**good, "trades": 29}, 95)[1] == "INCONCLUSIVE"
    for bad in ({"expectancy_r": 0.14}, {"profit_factor": 1.29}, {"max_drawdown_pct": 15.01}):
        assert evaluate_criteria({**good, **bad}, 95)[1] == "FAIL"
    assert evaluate_criteria(good, 89.9)[1] == "FAIL"


def test_design_backtest_stops_before_the_holdout_and_reports_everything():
    from services.strategies.breakout import backtest, markdown
    df4, df1 = stairs()
    r = backtest(df4.to_dict("records"), df1.to_dict("records"), cfg(), random_runs=20)
    assert r["period"]["last_day_close"] < HOLDOUT_START and r["period"]["kind"] == "design"
    assert r["metrics"]["trades"] > 0 and r["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE")
    assert {"buy_and_hold", "h2_rule", "trend_same_window", "walk_forward", "random_entries", "diagnostics"} <= set(r)
    assert [(x["lookback"], x["volume_mult"]) for x in r["sensitivity"]] == [
        (lb, vm) for lb in (20, 40, 55) for vm in (1.0, 1.2, 1.5)]
    assert [x["is_default"] for x in r["sensitivity"]].count(True) == 1
    d = r["diagnostics"]
    assert d["failed_breakouts"] == sum(t["failed_breakout"] for t in r["trade_list"])
    assert d["retests"] == sum(t["retest"] for t in r["trade_list"])
    assert all(t["target"] is None for t in r["trade_list"])
    assert all(t["entry_time"] < HOLDOUT_START for t in r["trade_list"])
    text = markdown(r)
    assert "Breakout" in text and "Sensitivity" in text and "failed breakouts" in text


def test_live_signal_and_forward_step_match_the_backtest(db):
    from services.strategies import breakout as bo
    from services.strategies.base import DataQuality
    from services.strategy_forward import StrategyForward
    df4, _ = stairs(n=900)
    ft = bo.features(df4, cfg())
    i = int(ft.index[ft["buy"]][-1])
    frames = {"4h": df4.iloc[:i + 1]}
    dq = DataQuality("binance", 1, int(df4["close_time"].iloc[i]), False, 0, [])
    r = bo.evaluate_frames(frames, cfg(), now=1, data=dq)
    assert r.signal in ("BUY SETUP", "HOLD") and r.targets == []
    if r.signal == "BUY SETUP":
        assert r.stop == round(float(ft["stop_plan"].iloc[i]), 2)
    assert bo.evaluate_frames({"4h": df4.iloc[:20]}, cfg(), now=1, data=dq).signal == "NO TRADE"
    rows = df4.to_dict("records")
    start = int(df4["open_time"].iloc[600])
    db.strategy_forward_start("breakout", "h", cfg(), "fp", start_day=start, now=1)
    fwd = StrategyForward(db, clock=lambda: int(df4["close_time"].iloc[700]) + 1, candles=lambda tf: rows)
    assert fwd.process() > 0
    marks = [e for e in db.strategy_forward_events(db.strategy_forward_test("breakout", "h")["id"]) if e["kind"] == "mark"]
    assert len(marks) == 101
    sim = bo.simulate(ft, cfg(), 600, 700)
    assert [round(m["detail"]["equity"], 9) for m in marks[:-1]] == [round(x, 9) for x in sim["equity"][:-1]]


def test_its_forward_fingerprint_is_its_own_and_the_others_are_unchanged():
    from services.strategy_forward import fingerprint_files
    assert [p.name for p in fingerprint_files("breakout")] == ["breakout.py", "strategy_forward.py"]
    from services.strategies import ENGINES
    assert list(ENGINES)[:4] == ["dca", "trend", "range", "breakout"]
