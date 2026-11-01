"""Strategy Lab round 1: the shared result schema and the DCA (dollar-cost averaging) engine."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.backtesting import HOLDOUT_START

D = 86_400_000
MON = 1_793_577_600_000                          # 2026-11-02 00:00 UTC, a Monday


def daily(n, start=MON, price=100.0, step=1.0, gap=()):
    """Daily candles: open = price + i*step, close = open + 0.5, high/low around them, volume 10."""
    rows = []
    for i in range(n):
        if i in gap:
            continue
        o = price + i * step
        rows.append({"open_time": start + i * D, "open": o, "high": o + 2, "low": o - 2, "close": o + 0.5,
                     "volume": 10.0, "close_time": start + (i + 1) * D - 1})
    return pd.DataFrame(rows)


def quality(**kw):
    from services.strategies.base import DataQuality
    base = {"source": "binance", "retrieved_at": MON, "last_candle_close": MON - 1, "is_stale": False,
            "missing_candles": 0, "warnings": []}
    return DataQuality(**{**base, **kw})


# ---------------------------------------------------------------- shared schema
def test_result_schema_has_every_field_and_strength_is_not_a_probability():
    from services.strategies.base import SCHEMA_VERSION, StrategyResult
    r = StrategyResult(strategy_id="dca", strategy_version="dca-1", evaluated_at=1, asset="BTCUSDT", timeframe="1d",
                       signal="WAIT", strength=None, supporting=["a"], contradictory=[], entry_zone=None, stop=None,
                       targets=[], reward_risk=None, fees_pct=0.1, slippage_pct=0.05, data=quality(), regime=None,
                       explanation="x", rejection=None, config_version=1, config_hash="h", labels=["SIMULATED"])
    d = r.to_dict()
    for k in ("strategy_id", "strategy_version", "evaluated_at", "asset", "timeframe", "signal", "strength",
              "supporting", "contradictory", "entry_zone", "stop", "targets", "reward_risk", "fees_pct",
              "slippage_pct", "data", "regime", "explanation", "rejection", "config_version", "config_hash",
              "labels", "schema", "strength_note"):
        assert k in d
    assert d["schema"] == SCHEMA_VERSION and "not a probability" in d["strength_note"]
    assert set(d["data"]) == {"source", "retrieved_at", "last_candle_close", "is_stale", "missing_candles", "warnings"}
    with pytest.raises(ValueError):
        StrategyResult(**{**r.__dict__, "signal": "BUY SETUP"})                 # DCA never says BUY SETUP
    with pytest.raises(ValueError):
        StrategyResult(**{**r.__dict__, "strength": 101})


def test_five_strategies_and_one_forward_only_variant_are_declared_with_their_own_signal_words():
    from services.strategies.base import SIGNALS, STRATEGIES
    assert [s["id"] for s in STRATEGIES] == ["dca", "trend", "range", "breakout", "breakout_05r", "rotation"]
    assert [s["id"] for s in STRATEGIES if s.get("forward_only")] == ["breakout_05r"]
    assert SIGNALS["breakout_05r"] == SIGNALS["breakout"]
    assert SIGNALS["dca"] == ("ACCUMULATE", "WAIT", "PAUSE")
    assert SIGNALS["trend"] == SIGNALS["range"] == SIGNALS["breakout"] == ("BUY SETUP", "HOLD", "NO TRADE",
                                                                            "EXIT / REDUCE EXPOSURE")
    assert SIGNALS["rotation"] == ("BTC STRONGER", "ALTCOIN STRENGTH", "NEUTRAL", "NO TRADE")


def test_validation_status_rules():
    from services.strategies.base import validation_status
    assert validation_status("h", "h", None, None)["status"] == "NOT VALIDATED"
    assert validation_status("h", "x", {"verdict": "PASS"}, None)["status"] == "NOT VALIDATED (settings changed)"
    bt = {"verdict": "PASS"}
    assert validation_status("h", "h", bt, None)["status"] == "BACKTESTED (design data)"
    fw = {"state": "running", "days_elapsed": 9, "target_days": 365, "verdict": None, "code_changed": False}
    s = validation_status("h", "h", bt, fw)
    assert s["status"] == "FORWARD TESTING" and "day 9 of 365" in s["detail"]
    assert validation_status("h", "h", bt, {**fw, "state": "done", "verdict": "PASS"})["status"] == "FORWARD TESTED: PASS"
    assert validation_status("h", "h", bt, {**fw, "code_changed": True})["status"] == "NOT VALIDATED (code changed)"


# ---------------------------------------------------------------- DCA config and schedule
def test_config_defaults_ranges_and_hash():
    from services.strategies import dca
    from services.strategies.base import config_hash
    cfg = dca.validate_config({})
    assert cfg == dca.DEFAULTS
    assert config_hash(cfg) == config_hash(dict(reversed(list(cfg.items()))))      # order does not matter
    for bad in ({"amount_usd": 0}, {"interval": "hourly"}, {"weekday": 7}, {"month_day": 29}, {"fee_pct": 5},
                {"amount_usd": "x"}, {"unknown": 1}):
        with pytest.raises(ValueError):
            dca.validate_config(bad)


def test_schedule_daily_weekly_monthly():
    from services.strategies.dca import scheduled_days, validate_config
    days = scheduled_days(MON, MON + 40 * D, validate_config({"interval": "weekly", "weekday": 0}))
    assert days[:3] == [MON, MON + 7 * D, MON + 14 * D] and len(days) == 6
    assert len(scheduled_days(MON, MON + 9 * D, validate_config({"interval": "daily"}))) == 10
    m = scheduled_days(MON, MON + 70 * D, validate_config({"interval": "monthly", "month_day": 1}))
    assert [pd.Timestamp(x, unit="ms", tz="UTC").strftime("%Y-%m-%d") for x in m] == ["2026-12-01", "2027-01-01"]


# ---------------------------------------------------------------- DCA simulation
def test_buys_at_the_open_with_fee_and_slippage_and_marks_at_liquidation_value():
    from services.strategies.dca import simulate, validate_config
    cfg = validate_config({"interval": "weekly", "amount_usd": 100})
    df = daily(15)                                                   # Mondays at index 0, 7, 14
    r = simulate(df, cfg, MON, MON + 14 * D)
    assert [b["day"] for b in r["buys"]] == [MON, MON + 7 * D, MON + 14 * D]
    b = r["buys"][1]                                                 # open 107
    assert b["fill_price"] == pytest.approx(107 * 1.0005)
    assert b["fee_usd"] == pytest.approx(0.10) and b["btc"] == pytest.approx(99.9 / (107 * 1.0005))
    assert b["slippage_usd"] == pytest.approx(99.9 - b["btc"] * 107)
    assert r["invested_usd"] == 300 and r["budget_usd"] == 300 and r["realized_pnl_usd"] == 0
    btc = sum(x["btc"] for x in r["buys"])
    assert r["btc"] == pytest.approx(btc) and r["avg_price"] == pytest.approx(300 / btc)
    last_close = 114.5
    assert r["value_usd"] == pytest.approx(btc * last_close * (1 - 0.0015))
    assert r["unrealized_pnl_usd"] == pytest.approx(r["value_usd"] - 300)
    assert r["fees_usd"] == pytest.approx(0.3) and r["slippage_usd"] == pytest.approx(sum(x["slippage_usd"] for x in r["buys"]))
    assert len(r["equity"]) == 15                                    # cash + marked BTC, on the whole budget
    assert r["equity"][0]["equity"] == pytest.approx(200 + r["buys"][0]["btc"] * 100.5 * (1 - 0.0015))
    lump = r["lump_sum"]
    assert lump["btc"] == pytest.approx(300 * 0.999 / (100 * 1.0005))
    assert lump["value_usd"] == pytest.approx(lump["btc"] * last_close * (1 - 0.0015))


def test_no_look_ahead_the_equity_up_to_a_day_ignores_later_candles():
    from services.strategies.dca import simulate, validate_config
    cfg = validate_config({"interval": "daily"})
    df = daily(30)
    crash = df.copy()
    crash.loc[20:, ["open", "high", "low", "close"]] *= 0.5
    a = simulate(df, cfg, MON, MON + 29 * D)["equity"][:20]
    b = simulate(crash, cfg, MON, MON + 29 * D)["equity"][:20]
    assert a == b


def test_a_missing_candle_on_a_buy_day_is_a_missed_buy_not_an_invented_price():
    from services.strategies.dca import simulate, validate_config
    r = simulate(daily(15, gap=(7,)), validate_config({"interval": "weekly"}), MON, MON + 14 * D)
    assert [b["day"] for b in r["buys"]] == [MON, MON + 14 * D]
    assert r["missed_buys"] == [MON + 7 * D] and any("missing" in w for w in r["warnings"])
    assert r["budget_usd"] == 300 and r["invested_usd"] == 200                # the missed 100 stays as cash


def test_design_backtest_stops_before_the_holdout_and_reports_periods():
    from services.strategies.dca import backtest, validate_config
    n = 900
    start = HOLDOUT_START - 700 * D
    rows = daily(n, start=start, price=20_000, step=15).to_dict("records")
    r = backtest(rows, validate_config({}))
    assert r["period"]["last_day_close"] < HOLDOUT_START and r["period"]["kind"] == "design"
    assert r["period"]["first_day"] == start
    assert {"full", "rolling", "by_year", "criteria", "verdict", "lump_sum", "metrics"} <= set(r)
    assert r["rolling"]["windows"] > 10 and r["verdict"] in ("PASS", "FAIL")
    assert [c["id"] for c in r["criteria"]] == ["C1", "C2"]
    assert r["walk_forward"] == "not applicable: DCA has no fitted parameters"


# ---------------------------------------------------------------- DCA live signal
def test_signal_accumulate_wait_pause():
    from services.strategies.dca import evaluate, validate_config
    cfg = validate_config({"interval": "weekly", "weekday": 0})
    df = daily(7)                                                   # last closed: Sunday -> next open is a Monday
    r = evaluate(df, cfg, now=MON + 7 * D + 3_600_000, data=quality(last_candle_close=MON + 7 * D - 1))
    assert r.signal == "ACCUMULATE" and r.strength is None and r.stop is None and r.targets == []
    assert "not a prediction" in r.explanation
    w = evaluate(df.iloc[:5], cfg, now=MON + 5 * D + 3_600_000, data=quality(last_candle_close=MON + 5 * D - 1))
    assert w.signal == "WAIT" and "2026-11-09" in w.explanation and w.rejection
    p = evaluate(df, cfg, now=MON + 7 * D, data=quality(is_stale=True, warnings=["stale"]))
    assert p.signal == "PAUSE" and "stale" in p.rejection
    g = evaluate(df, cfg, now=MON + 7 * D, data=quality(missing_candles=2))
    assert g.signal == "PAUSE"
    assert evaluate(df.iloc[:0], cfg, now=MON, data=quality(last_candle_close=None)).signal == "PAUSE"
