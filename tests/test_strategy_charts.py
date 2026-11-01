"""Strategy charts: live (same rule code), history (stored results, verified recomputation), forward, comparison.

Charts never write results: a missing series is recomputed in memory with the locked configuration and shown only
when every metric matches the stored result exactly.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from services.backtesting import HOLDOUT_START
from test_strategy_dca import daily
from test_strategy_trend import H4, uptrend

D = 86_400_000


def shifted_trend():
    df4, df1 = uptrend(n=1500)
    shift = (HOLDOUT_START - int(df4["close_time"].iloc[1200]) - 1) // H4 * H4
    for df in (df4, df1):
        df["open_time"] += shift
        df["close_time"] += shift
    return df4, df1


@pytest.fixture
def stored(db):
    """Design candles in the database and each strategy's locked result stored, as the research script does."""
    from functools import partial

    from services.strategies import dca, range as rng, trend
    from test_strategy_range import long_box
    df4, df1 = shifted_trend()
    db.upsert_candles("binance", "BTCUSDT", "4h", df4.to_dict("records"), 1)
    db.upsert_candles("binance", "BTCUSDT", "1d", df1.to_dict("records"), 1)
    out = {}
    for sid, mod, args in (("trend", trend, (df4.to_dict("records"), df1.to_dict("records"))),):
        cfg = mod.validate_config({})
        res = partial(mod.backtest, random_runs=20)(*args, cfg)
        db.strategy_backtest_add(sid, 1, res["config_hash"], "design", "sha", res, now=1)
        out[sid] = res
    return out


def charts(db, market=None):
    from services.strategy_charts import StrategyCharts
    from services.strategy_forward import StrategyForward
    from services.strategy_lab import StrategyLab
    lab = StrategyLab(db, market, StrategyForward(db))
    lab.ensure_defaults()
    return StrategyCharts(db, lab)


def table_snapshot(db):
    with db.connect() as conn:
        return [tuple(r) for r in conn.execute("SELECT id, strategy_id, config_hash, result FROM strategy_backtests ORDER BY id")]


# ---------------------------------------------------------------- history: stored results, verified recomputation
def test_trend_history_markers_are_the_stored_trades_and_metrics_verify_exactly(db, stored):
    before = table_snapshot(db)
    h = charts(db).history("trend", wait=True)
    res = stored["trend"]
    assert h["status"] == "ok" and h["verified"] is True and h["mismatches"] == []
    assert [(t["entry_time"], t["exit_time"]) for t in h["trades"]] == [(t["entry_time"], t["exit_time"]) for t in res["trade_list"]]
    for t, s in zip(h["trades"], res["trade_list"]):
        for k in ("fill", "exit", "stop", "target", "r", "reason", "bars"):
            assert t[k] == s[k]
        assert t["fees_pct"] > 0 and t["win"] == (s["return"] > 0)
    assert h["metrics"] == res["metrics"]                                 # shown as stored
    assert h["equity"]["value"][-1] == pytest.approx(1 + res["metrics"]["net_return_pct"] / 100, rel=0, abs=1e-12)
    assert max(h["drawdown"]["value"]) == pytest.approx(res["metrics"]["max_drawdown_pct"], rel=0, abs=1e-12)
    assert h["buy_hold"]["value"][-1] == pytest.approx(1 + res["buy_and_hold"]["net_return_pct"] / 100, abs=1e-12)
    times = set(h["price"]["time"])
    assert all(t["entry_time"] in times for t in h["trades"])            # markers sit on real bars
    assert table_snapshot(db) == before                                   # nothing written


def test_a_mismatch_is_reported_and_no_recomputed_series_is_shown(db, stored):
    res = json.loads(json.dumps(stored["trend"]))
    res["metrics"]["net_return_pct"] += 0.01                              # the stored result no longer matches the code
    with db.connect() as conn:                                           # a second stored row for another config hash
        pass
    db.strategy_backtest_add("trend", 2, "otherhash", "design", "sha", res, now=2)
    c = charts(db)
    h = c.compute_history("trend", res)
    assert h["verified"] is False and any("net_return_pct" in m for m in h["mismatches"])
    assert h["buy_hold"] is None and h["drawdown"]["source"] == "stored equity sample"
    assert [t["entry_time"] for t in h["trades"]] == [t["entry_time"] for t in res["trade_list"]]
    assert all(t["fees_pct"] is None for t in h["trades"])               # no unverified numbers


def test_dca_history_invested_value_and_worst_point(db):
    from services.strategies import dca
    rows = daily(900, start=HOLDOUT_START - 700 * D, price=20_000, step=15).to_dict("records")
    db.upsert_candles("binance", "BTCUSDT", "1d", rows, 1)
    res = dca.backtest(rows, dca.validate_config({}))
    db.strategy_backtest_add("dca", 1, res["config_hash"], "design", "sha", res, now=1)
    h = charts(db).history("dca", wait=True)
    assert h["verified"] is True and h["mismatches"] == []
    inv = h["invested"]["value"]
    assert inv == sorted(inv) and inv[-1] == res["full"]["invested_usd"]  # a step line that only rises
    assert len(h["buys"]) == res["metrics"]["trades"]
    roi = [(v - i) / i for v, i in zip(h["value"]["value"], inv) if i]
    assert h["worst"]["roi_pct"] == pytest.approx(min(roi) * 100)


def test_range_history_verifies(db):
    from services.strategies import range as rng
    from test_strategy_range import long_box
    df = long_box()
    db.upsert_candles("binance", "BTCUSDT", "4h", df.to_dict("records"), 1)
    res = rng.backtest(df.to_dict("records"), rng.validate_config({}), random_runs=20)
    db.strategy_backtest_add("range", 1, res["config_hash"], "design", "sha", res, now=1)
    h = charts(db).history("range", wait=True)
    assert h["verified"] is True and h["mismatches"] == []
    assert [t["entry_time"] for t in h["trades"]] == [t["entry_time"] for t in res["trade_list"]]


def test_breakout_history_verifies_and_keeps_the_diagnostics(db):
    from services.strategies import breakout as bo
    from test_strategy_breakout import stairs
    df4, df1 = stairs()
    db.upsert_candles("binance", "BTCUSDT", "4h", df4.to_dict("records"), 1)
    db.upsert_candles("binance", "BTCUSDT", "1d", df1.to_dict("records"), 1)
    res = bo.backtest(df4.to_dict("records"), df1.to_dict("records"), bo.validate_config({}), random_runs=20)
    db.strategy_backtest_add("breakout", 1, res["config_hash"], "design", "sha", res, now=1)
    h = charts(db).history("breakout", wait=True)
    assert h["verified"] is True and h["mismatches"] == []
    assert [t["entry_time"] for t in h["trades"]] == [t["entry_time"] for t in res["trade_list"]]
    assert all("failed_breakout" in t and t["target"] is None for t in h["trades"])


# ---------------------------------------------------------------- live: the strategy's own rule code
def test_live_trend_checklist_and_plan_come_from_the_strategy_result():
    from services.strategies import trend
    from services.strategies.base import DataQuality
    from services.strategy_charts import trend_live
    df4, df1 = uptrend()
    cfg = trend.validate_config({})
    ind = trend.indicators(df4, df1, cfg)
    for i in (ind.index[ind["buy"]].tolist()[-1], len(df4) - 1):
        frames = {"4h": df4.iloc[:i + 1], "1d": df1}
        dq = DataQuality("binance", 1, int(df4["close_time"].iloc[i]), False, 0, [])
        result = trend.evaluate_frames(frames, cfg, now=1, data=dq).to_dict()
        live = trend_live(frames, cfg, result)
        checks = live["checklist"]
        assert [c["rule"] for c in checks][:4] == ["EMA 21 > 50 > 200", "EMA 50 rising over 10 bars",
                                                   "ADX(14) >= 20", "daily close > daily EMA 50"]
        assert all(c["met"] for c in checks) == (result["signal"] == "BUY SETUP")
        r = ind.iloc[i]
        assert [c["met"] for c in checks][:6] == [bool(r["align"]), bool(r["slope_ok"]), bool(r["adx"] >= 20),
                                                  bool(r["htf_ok"]), bool(r["trigger"]), bool(r["stop_plan"] < r["close"])]
        if result["signal"] == "BUY SETUP":
            assert live["plan"]["stop"] == result["stop"] and live["plan"]["targets"] == result["targets"]
            assert live["plan"]["rr"] == result["reward_risk"] and live["plan"]["entry"] == result["entry_zone"]
        else:
            assert live["plan"] is None
        assert {"ema_f", "ema_m", "ema_s", "adx", "daily_ema"} <= set(live["series"])
        assert len(live["series"]["time"]) == min(200, i + 1)


def test_live_range_box_and_touches_match_the_features():
    from services.strategies import range as rng
    from services.strategies.base import DataQuality
    from services.strategy_charts import range_live
    from test_strategy_range import long_box
    df = long_box().iloc[:650]
    cfg = rng.validate_config({})
    dq = DataQuality("binance", 1, int(df["close_time"].iloc[-1]), False, 0, [])
    result = rng.evaluate_frames({"4h": df}, cfg, now=1, data=dq).to_dict()
    live = range_live({"4h": df}, cfg, result)
    labels = rng.regime_labels(df, start=len(df) - 200)
    ft = rng.features(df, labels, cfg)
    r = ft.iloc[-1]
    assert live["box"] == {"high": r["rh"], "low": r["rl"], "mid": r["mid"]}
    assert live["regime"]["label"][-1] == r["regime"] and len(live["regime"]["label"]) == len(live["series"]["time"])
    assert [c["met"] for c in live["checklist"]][:8] == [
        bool(r["regime_ok"]), bool(r["adx_ok"]), bool(r["width_ok"]), bool(r["touches_top"] >= 2),
        bool(r["touches_bottom"] >= 2), bool(r["trigger"]), bool(r["close"] < r["mid"]), bool(r["stop_plan"] < r["close"])]
    assert all(c["met"] for c in live["checklist"]) == (result["signal"] == "BUY SETUP")
    assert live["touches"]["top"] and live["touches"]["bottom"]


def test_live_breakout_level_trail_and_checklist_come_from_its_features():
    from services.strategies import breakout as bo
    from services.strategies.base import DataQuality
    from services.strategy_charts import breakout_live
    from test_strategy_breakout import stairs
    df4, _ = stairs(n=900)
    c = bo.validate_config({})
    ft = bo.features(df4, c)
    for i in (int(ft.index[ft["buy"]][-1]), len(df4) - 1):
        frames = {"4h": df4.iloc[:i + 1]}
        dq = DataQuality("binance", 1, int(df4["close_time"].iloc[i]), False, 0, [])
        result = bo.evaluate_frames(frames, c, now=1, data=dq).to_dict()
        live = breakout_live(frames, c, result)
        r = ft.iloc[i]
        assert live["series"]["level"][-1] == r["level"] and live["series"]["trail_low"][-1] == r["trail_low"]
        assert live["series"]["vol_line"][-1] == pytest.approx(c["volume_mult"] * r["vol_avg"])
        assert [x["met"] for x in live["checklist"]][:3] == [bool(r["close_above"]), bool(r["volume_ok"]),
                                                              bool(r["stop_plan"] < r["close"])]
        assert all(x["met"] for x in live["checklist"]) == (result["signal"] == "BUY SETUP")
        if result["signal"] == "BUY SETUP":
            assert live["plan"]["stop"] == result["stop"] and live["plan"]["targets"] == []
        assert len(live["series"]["time"]) == min(200, i + 1)


def test_live_rotation_ranking_comes_from_the_strategy():
    from services.strategies import rotation as rot
    from services.strategies.base import DataQuality
    from services.strategy_charts import rotation_live
    from test_strategy_rotation import frames_of, market
    m = market(n=200, late={"SOLUSDT": 190})
    fr = frames_of(m)
    last = int(m["BTCUSDT"]["close_time"].iloc[-1])
    c = rot.validate_config({})
    result = rot.evaluate_frames(fr, c, now=last + 1, data=DataQuality("binance", 1, last, False, 0, [])).to_dict()
    live = rotation_live(fr, c, result)
    ret = rot.returns_at(rot.panel(fr), last + 1, 28)
    assert {x["symbol"]: x["return_pct"] for x in live["ranking"]} == {
        s: (None if v is None else v * 100) for s, v in ret.items()}
    assert [x["symbol"] for x in live["ranking"] if x["return_pct"] is not None][0] == max(
        (s for s in ret if ret[s] is not None), key=lambda s: ret[s])
    assert live["series"]["SOLUSDT"][0] is None and len(live["series"]["time"]) == 90       # N/A before it exists
    assert all(v is None or v > 0 for v in live["series"]["BTCUSDT"])


def test_rotation_history_verifies(db):
    from services.strategies import rotation as rot
    from test_strategy_rotation import SYMS, rows_of, shifted
    m = shifted()
    for s in SYMS:
        db.upsert_candles("binance", s, "1d", m[s].to_dict("records"), 1)
    res = rot.backtest(*rows_of(m), rot.validate_config({}), random_runs=20)
    db.strategy_backtest_add("rotation", 1, res["config_hash"], "design", "sha", res, now=1)
    h = charts(db).history("rotation", wait=True)
    assert h["verified"] is True and h["mismatches"] == []
    assert [w["holding"] for w in h["weeks"]] == [w["holding"] for w in res["weeks"]]
    assert h["btc_buy_hold"]["value"][-1] == pytest.approx(1 + res["btc_buy_and_hold"]["net_return_pct"] / 100, abs=1e-12)


def test_live_dca_marks_scheduled_days_and_the_average_price():
    from services.strategies import dca
    from services.strategies.base import DataQuality
    from services.strategy_charts import dca_live
    df = daily(120, start=1_793_577_600_000 - 119 * D)
    cfg = dca.validate_config({})
    dq = DataQuality("binance", 1, int(df["close_time"].iloc[-1]), False, 0, [])
    result = dca.evaluate(df, cfg, now=1, data=dq).to_dict()
    live = dca_live({"1d": df}, cfg, result)
    days = dca.scheduled_days(int(df["open_time"].iloc[0]), int(df["open_time"].iloc[-1]), cfg)
    assert live["buy_days"] == days
    sim = dca.simulate(df, cfg, int(df["open_time"].iloc[0]), int(df["open_time"].iloc[-1]))
    assert live["avg_price"] == sim["avg_price"]
    assert [c["met"] for c in live["checklist"]][-1] == (result["signal"] == "ACCUMULATE")


# ---------------------------------------------------------------- forward and comparison
def test_forward_chart_is_empty_until_day_one_then_fills(db):
    from services.strategies import trend
    from services.strategy_forward import StrategyForward
    c = charts(db)
    f = c.forward("trend")
    assert f["status"] == "empty" and "until" in f["note"]
    df4, df1 = uptrend(n=700)
    h = c.lab.config("trend")["hash"]
    db.strategy_forward_start("trend", h, trend.validate_config({}), "fp", start_day=int(df4["open_time"].iloc[600]), now=1)
    fwd = StrategyForward(db, clock=lambda: int(df4["close_time"].iloc[-1]) + 1,
                          candles=lambda tf: (df4 if tf == "4h" else df1).to_dict("records"))
    fwd.process()
    f = c.forward("trend")
    assert f["status"] == "ok" and len(f["equity"]["time"]) == 100 and len(f["price"]["close"]) == 100


def test_comparison_curves_start_at_100_and_the_scatter_uses_stored_metrics(db, stored):
    cmp = charts(db).compare()
    assert cmp["label"] == "HYPOTHETICAL" and "not ranked" in cmp["note"]
    curve = next(c for c in cmp["curves"] if c["id"] == "trend")
    assert curve["value"][0] == 100
    pt = next(p for p in cmp["scatter"] if p["id"] == "trend")
    assert (pt["net_return_pct"], pt["max_drawdown_pct"]) == (stored["trend"]["metrics"]["net_return_pct"],
                                                              stored["trend"]["metrics"]["max_drawdown_pct"])


# ---------------------------------------------------------------- derived cache of verified series (data/cache/)
def test_a_second_start_reuses_the_cache_and_does_not_recompute(db, stored, tmp_path, monkeypatch):
    from services.strategy_charts import StrategyCharts
    from services.strategy_forward import StrategyForward
    from services.strategy_lab import StrategyLab
    lab = StrategyLab(db, None, StrategyForward(db))
    lab.ensure_defaults()
    before = table_snapshot(db)
    first = StrategyCharts(db, lab, cache_dir=tmp_path / "cache")
    h1 = first.history("trend", wait=True)
    files = list((tmp_path / "cache").glob("trend-*.json"))
    assert h1["verified"] and len(files) == 1
    second = StrategyCharts(db, lab, cache_dir=tmp_path / "cache")             # "app restart"
    monkeypatch.setattr(second, "compute_history", lambda *a, **k: (_ for _ in ()).throw(AssertionError("recomputed")))
    second.warm()
    h2 = second.history("trend")
    assert h2["status"] == "ok" and h2["from_cache"] is True
    assert h2["equity"] == h1["equity"] and h2["trades"] == h1["trades"] and h2["metrics"] == stored["trend"]["metrics"]
    assert table_snapshot(db) == before                                       # results untouched


def test_the_cache_key_changes_with_the_result_and_bad_files_are_ignored(db, stored, tmp_path):
    import json as _json
    from services.strategy_charts import StrategyCharts, cache_key
    from services.strategy_forward import StrategyForward
    from services.strategy_lab import StrategyLab
    lab = StrategyLab(db, None, StrategyForward(db))
    lab.ensure_defaults()
    res = lab.backtest_for("trend", lab.config("trend")["hash"])
    other = {**res, "verdict": "PASS"}
    assert cache_key("trend", res) != cache_key("trend", other)               # a different result -> a new key
    c = StrategyCharts(db, lab, cache_dir=tmp_path / "cache")
    c.history("trend", wait=True)
    f = next((tmp_path / "cache").glob("trend-*.json"))
    f.write_text("{not json", encoding="utf-8")                                # a broken cache file
    again = StrategyCharts(db, lab, cache_dir=tmp_path / "cache").history("trend", wait=True)
    assert again["verified"] is True and again.get("from_cache") is not True   # recomputed, not trusted
    assert _json.loads(f.read_text(encoding="utf-8"))["key"] == cache_key("trend", res)   # and rewritten


def test_unverified_series_are_never_cached(db, stored, tmp_path):
    import json as _json
    from services.strategy_charts import StrategyCharts
    from services.strategy_forward import StrategyForward
    from services.strategy_lab import StrategyLab
    lab = StrategyLab(db, None, StrategyForward(db))
    lab.ensure_defaults()
    res = _json.loads(_json.dumps(stored["trend"]))
    res["metrics"]["net_return_pct"] += 0.5
    c = StrategyCharts(db, lab, cache_dir=tmp_path / "cache")
    h = c._compute_and_cache("trend", {**res, "id": 99})
    assert h["verified"] is False and not list((tmp_path / "cache").glob("trend-*.json"))


# ---------------------------------------------------------------- forward-only variant (Breakout-0.5R)
def test_the_forward_only_variant_charts_say_so_and_have_no_history(db):
    from services.strategies import breakout_05r as v
    from services.strategies.base import DataQuality
    from services.strategy_charts import LIVE, breakout_live
    from test_strategy_breakout import stairs
    assert LIVE["breakout_05r"] is breakout_live                                 # the same rule code, not a copy
    c = charts(db)
    h = c.history("breakout_05r", wait=True)
    assert h["status"] == "forward_only" and v.LABEL in h["note"].lower()
    assert all(x["id"] != "breakout_05r" for x in c.compare()["scatter"])
    df4, _ = stairs(n=900)
    dq = DataQuality("binance", 1, int(df4["close_time"].iloc[-1]), False, 0, [])
    live = LIVE["breakout_05r"]({"4h": df4}, v.validate_config({}),
                                v.evaluate_frames({"4h": df4}, v.validate_config({}), now=1, data=dq).to_dict())
    assert live["names"]["level"] == "20-bar high (level)"
