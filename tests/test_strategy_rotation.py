"""Strategy Lab round 5: weekly relative-strength rotation between BTC, ETH, SOL and BNB (daily closed candles)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services.backtesting import HOLDOUT_START

ROOT = Path(__file__).resolve().parents[1]
D = 86_400_000
MON = 1_619_999_000_000 // (7 * D) * (7 * D) + 4 * D          # a Monday 00:00 UTC (1970-01-01 was a Thursday)
SYMS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")


def cfg(**kw):
    from services.strategies.rotation import validate_config
    return validate_config(kw)


def daily(closes, start=MON, opens=None, volume=1.0):
    n = len(closes)
    closes = np.asarray(closes, float)
    opens = np.asarray(opens if opens is not None else np.concatenate([[closes[0]], closes[:-1]]), float)
    t = start + np.arange(n) * D
    return pd.DataFrame({"open_time": t, "open": opens, "high": np.maximum(opens, closes) * 1.01,
                         "low": np.minimum(opens, closes) * 0.99, "close": closes, "volume": volume,
                         "close_time": t + D - 1})


def market(n=900, start=MON, seed=5, drifts=(0.0006, 0.0009, 0.0012, 0.0003), late=None):
    """Four random walks with different drifts and regimes; `late` = {symbol: first day index} starts an asset later."""
    rng = np.random.default_rng(seed)
    out = {}
    for k, (sym, mu) in enumerate(zip(SYMS, drifts)):
        regime = np.sin(np.arange(n) / (40 + 13 * k)) * 0.004
        closes = 100 * (k + 1) * np.exp(np.cumsum(mu + regime + rng.normal(0, 0.025, n)))
        df = daily(closes, start)
        if late and sym in late:
            df = df.iloc[late[sym]:].reset_index(drop=True)
        out[sym] = df
    return out


def frames_of(m):
    return {"1d": m["BTCUSDT"], **{f"1d:{s}": m[s] for s in SYMS[1:]}}


def rows_of(m):
    return [m[s].to_dict("records") for s in SYMS]


def shifted(n=1400, **kw):
    """Bar 1300 is the last daily candle that closes before the holdout; days stay Monday-aligned."""
    m = market(n=n, **kw)
    shift = (HOLDOUT_START - int(m["BTCUSDT"]["close_time"].iloc[1300]) - 1) // (7 * D) * (7 * D)
    for df in m.values():
        df["open_time"] += shift
        df["close_time"] += shift
    return m


# ---------------------------------------------------------------- configuration and pre-registration
def test_defaults_and_the_preregistration():
    from services.strategies import rotation as rot
    from services.strategies.base import config_hash
    assert rot.DEFAULTS == {"lookback_days": 28, "cash_filter": "on", "neutral_band_pct": 2.0, "fee_pct": 0.1,
                            "slippage_pct": 0.05}
    assert rot.validate_config({}) == rot.DEFAULTS and rot.ASSETS == SYMS
    for bad in ({"lookback_days": 3}, {"cash_filter": "maybe"}, {"news": 1}, {"neutral_band_pct": -1}):
        with pytest.raises(ValueError):
            rot.validate_config(bad)
    text = (ROOT / "PREREGISTRATION_ROTATION.md").read_text(encoding="utf-8")
    assert f"Config hash: `{config_hash(rot.DEFAULTS)}`" in text
    for k, v in rot.DEFAULTS.items():
        assert f"`{k}` | `{v}`" in text
    for must in ("BTC, ETH, SOL, BNB", "Monday 00:00 UTC", "28-day return", "closes up to Sunday", "≤ 0", "cash",
                 "BTC STRONGER", "ALTCOIN STRENGTH", "NEUTRAL", "NO TRADE", "within 2", "switches ≥ 30",
                 "INCONCLUSIVE", "return / max drawdown", "90%", "random weekly rotations", "lookback 14 / 28 / 56",
                 "no-cash", "survivorship bias", "SOL and BNB were picked knowing they survived",
                 "never in the backtest", "volume", "N/A"):
        assert must in text, must


# ---------------------------------------------------------------- the weekly decision
def test_the_decision_rule():
    from services.strategies.rotation import decide
    c = cfg()
    r = {"BTCUSDT": 0.10, "ETHUSDT": 0.05, "SOLUSDT": None, "BNBUSDT": -0.02}
    assert decide(r, None, c) == ("BTCUSDT", "BTC STRONGER")
    assert decide({**r, "ETHUSDT": 0.20}, "BTCUSDT", c) == ("ETHUSDT", "ALTCOIN STRENGTH")
    assert decide({k: (v if v is None else -abs(v) - 0.01) for k, v in r.items()}, "ETHUSDT", c) == (None, "NO TRADE")
    assert decide({k: None for k in r}, "BTCUSDT", c) == (None, "NO TRADE")             # nothing to rank: cash
    near = {**r, "ETHUSDT": 0.085}                                                      # top two within 2 points
    assert decide(near, "ETHUSDT", c) == ("ETHUSDT", "NEUTRAL")                          # keep the current holding
    assert decide(near, "BTCUSDT", c) == ("BTCUSDT", "NEUTRAL")
    assert decide(near, None, c) == ("BTCUSDT", "NEUTRAL")                               # from cash: the top
    assert decide(near, "BNBUSDT", c) == ("BTCUSDT", "NEUTRAL")                          # held one is not top two
    assert decide({**r, "BTCUSDT": 0.0, "ETHUSDT": -0.1}, None, c) == (None, "NO TRADE")  # <= 0 -> cash
    no_cash = {**r, "BTCUSDT": -0.01, "ETHUSDT": -0.1, "BNBUSDT": -0.2}
    assert decide(no_cash, None, cfg(cash_filter="off")) == ("BTCUSDT", "BTC STRONGER")  # no-cash variant: stay in


def test_returns_use_closes_up_to_sunday_only():
    from services.strategies.rotation import panel, returns_at
    m = market(n=120)
    p = panel(frames_of(m))
    monday = MON + 70 * D
    r = returns_at(p, monday, 28)
    btc = m["BTCUSDT"].set_index("open_time")["close"]
    assert r["BTCUSDT"] == pytest.approx(btc[monday - D] / btc[monday - 29 * D] - 1)
    m["ETHUSDT"].loc[m["ETHUSDT"]["open_time"] >= monday, ["open", "high", "low", "close"]] *= 50   # Monday on: no effect
    assert returns_at(panel(frames_of(m)), monday, 28) == r


def test_an_asset_without_history_is_n_a_not_zero():
    from services.strategies.rotation import panel, returns_at
    m = market(n=200, late={"SOLUSDT": 100})
    p = panel(frames_of(m))
    assert returns_at(p, MON + 98 * D, 28)["SOLUSDT"] is None
    assert returns_at(p, MON + 133 * D, 28)["SOLUSDT"] is not None


# ---------------------------------------------------------------- simulation
def test_switches_happen_at_the_monday_open_with_costs_on_each_side():
    from services.strategies.rotation import panel, simulate
    n = 140
    flat = np.full(n, 100.0)
    up = 100 * np.exp(np.arange(n) * 0.01)
    m = {"BTCUSDT": daily(flat * 1.0001 ** np.arange(n)), "ETHUSDT": daily(up), "SOLUSDT": daily(flat * 0.999 ** np.arange(n)),
         "BNBUSDT": daily(flat * 0.998 ** np.arange(n))}
    p = panel(frames_of(m))
    sim = simulate(p, cfg(), MON + 35 * D, MON + 90 * D)
    first = sim["weeks"][0]
    assert first["monday"] == MON + 35 * D and first["holding"] == "ETHUSDT" and first["signal"] == "ALTCOIN STRENGTH"
    sw = sim["switches"][0]
    eth_open = float(m["ETHUSDT"].set_index("open_time")["open"][MON + 35 * D])
    assert sw["time"] == MON + 35 * D and sw["to"] == "ETHUSDT" and sw["fill"] == pytest.approx(eth_open * 1.0005)
    assert sim["equity"][0]["equity"] == pytest.approx(
        (1 - 0.001) / (eth_open * 1.0005) * float(m["ETHUSDT"]["close"].iloc[35]) * (1 - 0.001 - 0.0005))
    assert all(w["monday"] % (7 * D) == MON % (7 * D) for w in sim["weeks"])          # Mondays only
    assert len(sim["switches"]) == 1                                                     # ETH stays on top


def test_criteria():
    from services.strategies.rotation import evaluate_criteria
    m = {"switches": 40, "net_return_pct": 80.0, "max_drawdown_pct": 30.0}
    btc = {"net_return_pct": 60.0, "max_drawdown_pct": 50.0}
    assert evaluate_criteria(m, btc, 95)[1] == "PASS"
    assert evaluate_criteria({**m, "switches": 29}, btc, 95)[1] == "INCONCLUSIVE"
    assert evaluate_criteria({**m, "max_drawdown_pct": 55.0, "net_return_pct": 200.0}, btc, 95)[1] == "FAIL"   # deeper DD
    assert evaluate_criteria({**m, "net_return_pct": 30.0}, btc, 95)[1] == "FAIL"                              # ratio 1.0 < 1.2
    assert evaluate_criteria(m, btc, 89.9)[1] == "FAIL"


def test_design_backtest_stops_before_the_holdout_and_reports_everything():
    from services.strategies.rotation import backtest, markdown
    m = shifted(late={"SOLUSDT": 300})
    r = backtest(*rows_of(m), cfg(), random_runs=20)
    assert r["period"]["kind"] == "design" and r["period"]["last_day_close"] < HOLDOUT_START
    assert r["period"]["first_monday"] % (7 * D) == MON % (7 * D)
    assert {"btc_buy_and_hold", "asset_buy_and_hold", "random_rotations", "criteria", "sensitivity", "weeks",
            "switch_list", "equity_sample", "survivorship_note"} <= set(r)
    assert r["asset_buy_and_hold"]["SOLUSDT"] is None                                   # not there at the start: N/A
    assert [(x["lookback_days"], x["cash_filter"]) for x in r["sensitivity"]] == [
        (lb, cf) for lb in (14, 28, 56) for cf in ("on", "off")]
    assert [x["is_default"] for x in r["sensitivity"]].count(True) == 1
    assert r["metrics"]["trades"] == r["metrics"]["switches"] == len(r["switch_list"])
    assert all(w["monday"] < HOLDOUT_START for w in r["weeks"])
    text = markdown(r)
    assert "survivorship" in text.lower() and "Sensitivity" in text


# ---------------------------------------------------------------- live signal and forward test
def test_live_signal_is_the_rule_on_the_last_closed_day():
    from services.strategies import rotation as rot
    from services.strategies.base import DataQuality
    m = market(n=200, late={"BNBUSDT": 190})
    last = int(m["BTCUSDT"]["close_time"].iloc[-1])
    dq = DataQuality("binance", 1, last, False, 0, [])
    r = rot.evaluate_frames(frames_of(m), cfg(), now=last + 1, data=dq).to_dict()
    p = rot.panel(frames_of(m))
    ret = rot.returns_at(p, last + 1, 28)
    assert r["signal"] == rot.decide(ret, rot.replay_holding(p, last + 1, cfg()), cfg())[1]
    assert any("BNBUSDT" in x and "N/A" in x for x in r["contradictory"])
    assert r["targets"] == [] and r["stop"] is None
    stale = DataQuality("binance", 1, last, True, 0, [])
    assert rot.evaluate_frames(frames_of(m), cfg(), now=last + 1, data=stale).signal == "NO TRADE"


def test_forward_step_matches_the_simulation_and_waits_for_every_asset(db):
    from services.strategies import rotation as rot
    from services.strategy_forward import StrategyForward
    m = market(n=300)
    rows = {"1d": m["BTCUSDT"].to_dict("records"), **{f"1d:{s}": m[s].to_dict("records") for s in SYMS[1:]}}
    missing_day = MON + 260 * D
    gappy = {**rows, "1d:SOLUSDT": [r for r in rows["1d:SOLUSDT"] if r["open_time"] != missing_day]}
    start = MON + 210 * D
    db.strategy_forward_start("rotation", "h", cfg(), "fp", start_day=start, now=1)
    clock = lambda: int(m["BTCUSDT"]["close_time"].iloc[280]) + 1  # noqa: E731
    StrategyForward(db, clock=clock, candles=lambda tf: gappy.get(tf, [])).process()
    tid = db.strategy_forward_test("rotation", "h")["id"]
    marks = [e for e in db.strategy_forward_events(tid) if e["kind"] == "mark"]
    assert marks[-1]["day"] == missing_day - D                                          # waits at the missing day
    StrategyForward(db, clock=clock, candles=lambda tf: rows.get(tf, [])).process()     # the candle arrives
    marks = [e for e in db.strategy_forward_events(tid) if e["kind"] == "mark"]
    assert len(marks) == 71 and [m_["day"] for m_ in marks] == [start + i * D for i in range(71)]
    sim = rot.simulate(rot.panel(frames_of(m)), cfg(), start, start + 70 * D)
    assert [round(x["detail"]["equity"], 9) for x in marks] == [round(e["equity"], 9) for e in sim["equity"]]


def test_its_forward_fingerprint_is_its_own():
    from services.strategies import ENGINES
    from services.strategy_forward import fingerprint_files
    assert [p.name for p in fingerprint_files("rotation")] == ["rotation.py", "strategy_forward.py"]
    assert "rotation" in ENGINES
