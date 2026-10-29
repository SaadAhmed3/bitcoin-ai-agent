"""Backtester tests: no look-ahead, fill rules, costs, metrics, baselines, criteria (no network)."""
import json

import numpy as np
import pandas as pd
import pytest
from conftest import make_rows

from services.backtesting import (BacktestConfig, Decision, ValidationCriteria, _frame, buy_and_hold, config_hash,
                                  evaluate_criteria, max_drawdown, metrics, replay, run_backtest, simulate)
from services.signal_engine import BUY, HOLD, SELL, EngineConfig

END = 1_760_000_000_000
NOCOST = EngineConfig(fee_pct=0.0, slippage_pct=0.0)


def bars_df(ohlc):
    """ohlc: list of (open, high, low, close). 4h bars ending at END."""
    rows, step = [], 4 * 3_600_000
    start = END - len(ohlc) * step
    for k, (o, h, l, c) in enumerate(ohlc):
        t = start + k * step
        rows.append({"open_time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1.0, "close_time": t + step - 1})
    return _frame(rows)


def buy(stop, target):
    return Decision(BUY, True, 0.4, stop, target)


def hold():
    return Decision(HOLD, False, 0.0, None, None)


CFG = BacktestConfig(max_hold_bars=18)


# ------------------------------------------------------------------ fills
def test_entry_at_next_open_and_target_exit():
    df = bars_df([(100, 101, 99, 100), (100, 103, 99.5, 102), (102, 111, 101, 110), (110, 111, 109, 110)])
    bars = [0, 1, 2]
    trades, eq = simulate(df, bars, [buy(95, 110), hold(), hold()], NOCOST, CFG)
    t = trades[0]
    assert (t.entry, t.exit, t.reason) == (100, 110, "target")   # entered at bar 1 open (100)
    assert t.ret == pytest.approx(0.10) and t.r_multiple == pytest.approx(2.0)


def test_stop_assumed_first_when_both_touched():
    df = bars_df([(100, 101, 99, 100), (100, 112, 94, 100), (100, 101, 99, 100)])
    trades, _ = simulate(df, [0, 1], [buy(95, 110), hold()], NOCOST, CFG)
    assert trades[0].reason == "stop" and trades[0].exit == 95


def test_gap_below_stop_fills_at_worse_open():
    df = bars_df([(100, 101, 99, 100), (100, 101, 99, 100), (90, 91, 88, 89), (89, 90, 88, 89)])
    trades, _ = simulate(df, [0, 1, 2], [buy(95, 110), hold(), hold()], NOCOST, CFG)
    assert trades[0].reason == "stop (gap)" and trades[0].exit == 90


def test_entry_skipped_when_open_gaps_through_stop():
    df = bars_df([(100, 101, 99, 100), (94, 95, 93, 94), (94, 95, 93, 94)])
    trades, _ = simulate(df, [0, 1], [buy(95, 110), hold()], NOCOST, CFG)
    assert trades == []


def test_time_stop_and_sell_exit():
    flat = [(100, 101, 99.5, 100)] * 30
    df = bars_df(flat)
    trades, _ = simulate(df, list(range(29)), [buy(90, 120)] + [hold()] * 28, NOCOST, BacktestConfig(max_hold_bars=5))
    assert trades[0].reason == "time stop" and trades[0].bars == 5
    decs = [buy(90, 120), hold(), Decision(SELL, False, -0.4, None, None)] + [hold()] * 26
    trades, _ = simulate(df, list(range(29)), decs, NOCOST, CFG)
    assert trades[0].reason == "sell setup"


def test_costs_charged_both_sides():
    df = bars_df([(100, 101, 99, 100), (100, 111, 99.5, 110), (110, 111, 109, 110)])
    trades, _ = simulate(df, [0, 1], [buy(95, 110), hold()], EngineConfig(fee_pct=0.1, slippage_pct=0.05), CFG)
    assert trades[0].ret == pytest.approx(110 * 0.9985 / (100 * 1.0015) - 1)


def test_one_position_at_a_time():
    df = bars_df([(100, 101, 99, 100)] * 10)
    trades, _ = simulate(df, list(range(9)), [buy(90, 150)] * 9, NOCOST, BacktestConfig(max_hold_bars=100))
    assert len(trades) == 1   # stays in the first trade until the window ends


# ------------------------------------------------------------------ metrics & baselines
def test_max_drawdown():
    assert max_drawdown(np.array([1.0, 1.2, 0.9, 1.3, 1.04])) == pytest.approx(25.0)


def test_buy_and_hold_with_costs():
    df = bars_df([(100, 100, 100, 100), (100, 100, 100, 120)])
    m, eq = buy_and_hold(df, 0, 1, NOCOST)
    assert m["net_return_pct"] == pytest.approx(20.0)


def test_metrics_profit_factor_and_win_rate():
    df = bars_df([(100, 101, 99, 100), (100, 111, 99.5, 110), (110, 111, 109, 110),
                  (110, 111, 104, 105), (105, 106, 99, 100), (100, 100, 100, 100)])
    decs = [buy(95, 110), hold(), buy(104, 120), hold(), hold()]
    trades, eq = simulate(df, list(range(5)), decs, NOCOST, CFG)
    m = metrics(trades, eq, len(eq), NOCOST)
    assert m["trades"] == 2 and m["win_rate_pct"] == 50.0
    assert m["profit_factor"] == pytest.approx(0.10 / (6 / 110), abs=1e-3)


def test_criteria_all_reported_and_strict():
    m = {"trades": 40, "profit_factor": 1.5, "net_return_pct": 30.0, "max_drawdown_pct": 12.0, "return_over_max_dd": 2.5}
    ema_m = {"return_over_max_dd": 1.0}
    rnd = {"values": list(np.linspace(-20, 20, 100))}
    res = evaluate_criteria(m, ema_m, rnd, 0.7, ValidationCriteria())
    assert len(res) == 7 and all(r["passed"] for r in res)
    res = evaluate_criteria({**m, "trades": 5}, ema_m, rnd, 0.7, ValidationCriteria())
    assert not all(r["passed"] for r in res)
    res = evaluate_criteria({**m, "max_drawdown_pct": 40.0}, ema_m, rnd, 0.7, ValidationCriteria())
    assert not [r for r in res if r["name"] == "Max drawdown"][0]["passed"]


def test_config_hash_changes_with_weights_and_costs():
    a = config_hash(EngineConfig())
    assert a == config_hash(EngineConfig())
    assert a != config_hash(EngineConfig(fee_pct=0.2))
    assert a != config_hash(EngineConfig(weights={**EngineConfig().weights, "news": 0.0}))
    assert a == config_hash(EngineConfig(require_validated_edge=False))   # the gate itself is not part of the hash


# ------------------------------------------------------------------ no look-ahead
def test_replay_decisions_do_not_depend_on_future_candles():
    c1d = make_rows(300, "1d", seed=9, end_ms=END)
    c4h = make_rows(420, "4h", seed=9, end_ms=END)
    c1h = make_rows(420 * 4, "1h", seed=9, end_ms=END)
    cfg = BacktestConfig(window=120)
    full = replay(_frame(c1h), _frame(c4h), _frame(c1d), EngineConfig(), {"d": EngineConfig().weights}, cfg, 300)
    # Truncate every timeframe at the close of 4h bar 360: decisions up to bar 359 must be identical.
    cut_t = c4h[360]["close_time"]
    t1h = [r for r in c1h if r["close_time"] <= cut_t]
    t1d = [r for r in c1d if r["close_time"] <= cut_t]
    part = replay(_frame(t1h), _frame(c4h[:361]), _frame(t1d), EngineConfig(), {"d": EngineConfig().weights}, cfg, 300)
    n = len(part[0])
    assert part[0] == full[0][:n]
    for a, b in zip(part[1]["d"], full[1]["d"][:n]):
        assert (a.setup, a.score, a.stop, a.target) == (b.setup, b.score, b.stop, b.target)


def test_replay_uses_only_closed_daily_candles():
    # The daily candle containing a 4h close must not be visible until it closes.
    c1d = make_rows(260, "1d", seed=4, end_ms=END)
    c4h = make_rows(300, "4h", seed=4, end_ms=END)
    c1h = make_rows(1200, "1h", seed=4, end_ms=END)
    import services.backtesting as bt
    seen = []
    real = bt.analyze

    def spy(df, tf):
        if tf == "1d" and len(df):
            seen.append(int(df["close_time"].iloc[-1]))
        return real(df, tf)

    bt.analyze = spy
    try:
        bars, _ = replay(_frame(c1h), _frame(c4h), _frame(c1d), EngineConfig(), {"d": EngineConfig().weights},
                         BacktestConfig(window=120), 200)
    finally:
        bt.analyze = real
    closes = [c4h[i]["close_time"] for i in bars]
    assert seen and max(seen) <= max(closes)
    assert all(any(s <= c for c in closes) for s in seen)


# ------------------------------------------------------------------ end to end
def test_run_backtest_end_to_end_small():
    c1d = make_rows(400, "1d", seed=5, end_ms=END)
    c4h = make_rows(700, "4h", seed=5, end_ms=END)
    c1h = make_rows(2800, "1h", seed=5, end_ms=END)
    r = run_backtest(c1h, c4h, c1d, EngineConfig(),
                     BacktestConfig(window=150, years=None, random_runs=20, test_window_days=20, train_window_days=40))
    json.dumps(r, allow_nan=False)
    for key in ("strategy", "baselines", "walk_forward", "hit_rate", "criteria", "validated", "limitations", "equity"):
        assert key in r
    assert len(r["criteria"]) == 7 and isinstance(r["validated"], bool)
    assert "News is not replayed" in r["limitations"][0]


def test_run_backtest_rejects_missing_history():
    with pytest.raises(ValueError, match="Download history"):
        run_backtest([], [], [], EngineConfig())


def test_funnel_counts_where_setups_are_lost():
    from services.backtesting import funnel
    from services.signal_engine import STRONG_BUY
    D = lambda **k: Decision(**{"setup": HOLD, "tradable": False, "score": 0.0, "stop": None, "target": None, **k})
    decs = [D(score=0.1),
            D(score=0.3, raw_setup=BUY, downgrade="low_rr", rr_t1=0.7),
            D(score=0.3, raw_setup=BUY, downgrade="low_rr", rr_t1=1.1, raw_counter_trend=True),
            D(score=0.35, raw_setup=BUY, downgrade="low_rr", rr_t1=1.3),
            D(setup=BUY, score=0.4, raw_setup=BUY, rr_t1=2.0, gate_keys=("counter_trend", "not_validated"), raw_counter_trend=True),
            D(setup=STRONG_BUY, tradable=True, score=0.6, raw_setup=STRONG_BUY, rr_t1=2.5, stop=1, target=2),
            D(setup=SELL, score=-0.4, raw_setup=SELL)]
    f = funnel(decs, [], NOCOST)
    assert f["raw_buy_setups"] == 5 and f["downgraded_low_rr"] == 3 and f["buy_setups_after_rr"] == 2
    assert f["blocked_by_safeguard"] == {"counter_trend": 1}          # validation gate not counted
    assert f["tradable_buy_bars"] == 1 and f["bars_score_at_or_below_sell"] == 1
    assert f["what_if_min_rr"]["min_rr_1.5"] == 1                     # only the clean 2.5 (2.0 is counter-trend)
    assert f["what_if_min_rr"]["min_rr_1.0"] == 2                     # + 1.3 (1.1 is counter-trend)
    assert f["low_rr_values"]["median"] == 1.1
