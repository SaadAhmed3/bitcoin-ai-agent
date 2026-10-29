"""Phase 6.2: pre-registered entry rules (breakout / pullback pending orders), the design/holdout split,
the forward-observation gate and the funnel rounding fix. Deterministic, no network."""
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from conftest import make_rows

import services.backtesting as bt
from services.backtesting import (HOLDOUT_MIN_TRADES, HOLDOUT_START, BacktestConfig, Decision, _frame, config_hash,
                                  funnel, replay, run_backtest, simulate)
from services.signal_engine import (BUY, HOLD, PENDING_VALID_BARS, PULLBACK_MAX_DIST_ATR, SELL, EngineConfig,
                                    build_pending_order, generate_signal)

END = 1_760_000_000_000
NOCOST = EngineConfig(fee_pct=0.0, slippage_pct=0.0)
CFG = BacktestConfig(max_hold_bars=18)
GOLDEN = Path(__file__).parent / "fixtures" / "golden_signal16_standard.json"


def bars_df(ohlc, end=END):
    rows, step = [], 4 * 3_600_000
    start = end - len(ohlc) * step
    for k, (o, h, l, c) in enumerate(ohlc):
        t = start + k * step
        rows.append({"open_time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1.0, "close_time": t + step - 1})
    return _frame(rows)


def hold(pending=None, setup=HOLD):
    return Decision(setup, False, 0.0, None, None, pending=pending)


def order(kind, price, stop, target):
    return {"type": kind, "price": price, "stop": stop, "target": target, "rr_t1": 2.0,
            "valid_bars": PENDING_VALID_BARS, "condition": "test", "basis": "test"}


FLAT = (100, 101, 99, 100)


# ------------------------------------------------------------------ standard mode = signal-1.6
def fixture_rows(seed):
    return (make_rows(2800, "1h", seed=seed, end_ms=END), make_rows(700, "4h", seed=seed, end_ms=END),
            make_rows(400, "1d", seed=seed, end_ms=END))


def test_standard_mode_reproduces_signal_16_decisions_and_trades():
    golden = json.loads(GOLDEN.read_text())
    assert golden["engine"] == "signal-1.6"
    for seed, snap in golden["seeds"].items():
        c1h, c4h, c1d = fixture_rows(int(seed))
        ec = EngineConfig(entry_mode="standard")
        cfg = BacktestConfig(window=150)
        bars, decs = replay(_frame(c1h), _frame(c4h), _frame(c1d), ec, {"d": ec.weights}, cfg, 300)
        assert bars == snap["bars"]
        got = [[d.setup, d.tradable, round(d.score, 3), d.stop, d.target, d.raw_setup, d.downgrade, d.rr_t1,
                sorted(d.gate_keys)] for d in decs["d"]]
        assert got == snap["decisions"]
        assert all(d.pending is None for d in decs["d"])
        trades, _ = simulate(_frame(c4h), bars, decs["d"], ec, cfg)
        assert [{k: v for k, v in asdict(t).items() if k != "entry_type"} for t in trades] == snap["trades"]
        assert all(t.entry_type == "standard" for t in trades)


@pytest.fixture(scope="module")
def replays():
    """Seed 4 produces both a breakout fill and pullback fills (checked when the fixtures were chosen)."""
    c1h, c4h, c1d = fixture_rows(4)
    out = {}
    for mode in ("breakout", "pullback"):
        ec = EngineConfig(entry_mode=mode)
        cfg = BacktestConfig(window=150)
        bars, decs = replay(_frame(c1h), _frame(c4h), _frame(c1d), ec, {"d": ec.weights}, cfg, 450)
        stats = {}
        trades, _ = simulate(_frame(c4h), bars, decs["d"], ec, cfg, stats=stats)
        out[mode] = (bars, decs["d"], trades, stats, _frame(c4h))
    return out


def test_pending_orders_only_on_low_rr_downgrades(replays):
    for mode, (_, decs, trades, stats, _) in replays.items():
        with_pending = [d for d in decs if d.pending]
        assert with_pending, f"fixture produced no {mode} pending order"
        assert all(d.downgrade == "low_rr" and d.raw_setup in (BUY, "STRONG BUY SETUP") for d in with_pending)
        assert all(d.setup == HOLD and not d.tradable for d in with_pending)
        assert all(d.pending["type"] == mode and d.pending["rr_t1"] >= 1.5 for d in with_pending)
        assert all(not d.raw_counter_trend and set(d.gate_keys) <= {"not_validated"} for d in with_pending)
        assert stats["filled"] >= 1 and any(t.entry_type == mode for t in trades)


# ------------------------------------------------------------------ order construction
def a4h_atr(atr):
    return {"status": "ok", "indicators": {"atr14": atr}}


def levels(res=(), sup=()):
    lv = {"resistance": list(res), "support": list(sup)}
    lv["major_resistance"] = next((r for r in lv["resistance"] if not r["minor"]), None)
    lv["major_support"] = next((s for s in lv["support"] if not s["minor"]), None)
    return lv


def lvl(lo, hi, touches=3):
    return {"low": lo, "high": hi, "price": (lo + hi) / 2, "touches": touches, "minor": touches < 2}


PLAN = {"targets": [{"price": 112.0, "basis": "lower edge of major resistance 112 (3 touches)"}]}


def test_breakout_order_trigger_stop_and_target():
    lv = levels(res=[lvl(100, 102), lvl(103, 103.5), lvl(110, 111)])
    o, why = build_pending_order("breakout", a4h_atr(4.0), NOCOST, 99.0, lv, PLAN)
    assert why is None
    # trigger = upper edge; stop = min(lower edge - 0.5 ATR, trigger - 1 ATR); target = next major >= trigger + 0.5 ATR
    assert (o["type"], o["price"], o["stop"], o["target"]) == ("breakout", 102.0, 98.0, 110.0)
    assert o["rr_t1"] == pytest.approx(2.0) and o["valid_bars"] == 6 and o["condition"] == "4h close above 102"
    # no major above: first resistance of any strength >= trigger + 1 ATR (ATR 2: stop = min(99, 100) = 99)
    o, _ = build_pending_order("breakout", a4h_atr(2.0), NOCOST, 99.0, levels(res=[lvl(100, 102), lvl(107, 107, 1)]), PLAN)
    assert (o["stop"], o["target"]) == (99.0, 107.0)
    # nothing above: trigger + 2 ATR. A thin zone: 0.5 ATR below it would be closer than 1 ATR -> stop = trigger - 1 ATR
    o, _ = build_pending_order("breakout", a4h_atr(1.0), NOCOST, 99.0, levels(res=[lvl(101.6, 102)]), PLAN)
    assert (o["stop"], o["target"], o["rr_t1"]) == (101.0, 104.0, 2.0)


def test_breakout_needs_a_major_resistance():
    o, why = build_pending_order("breakout", a4h_atr(4.0), NOCOST, 99.0, levels(res=[lvl(100, 102, 1)]), PLAN)
    assert o is None and why == "no_major_resistance"


def test_pullback_limit_must_be_within_3_atr():
    lv = levels(sup=[lvl(95, 96)])
    o, why = build_pending_order("pullback", a4h_atr(1.0), NOCOST, 100.0, lv, PLAN)   # 4 ATR away
    assert PULLBACK_MAX_DIST_ATR == 3.0 and o is None and why == "support_too_far"
    o, why = build_pending_order("pullback", a4h_atr(1.5), NOCOST, 100.0, lv, PLAN)   # 2.67 ATR away
    assert why is None and (o["price"], o["stop"], o["target"]) == (96.0, 94.25, 112.0)
    assert o["condition"] == "price trades below 96"


def test_pending_order_rejected_when_its_own_rr_is_too_low():
    lv = levels(res=[lvl(100, 102), lvl(105, 106)])                # target 105 from 102 with a 4 ATR-ish stop
    o, why = build_pending_order("breakout", a4h_atr(4.0), NOCOST, 99.0, lv, PLAN)
    assert o is None and why == "low_rr"
    o, why = build_pending_order("pullback", a4h_atr(1.0), NOCOST, 100.0, levels(sup=[lvl(97, 98)]),
                                 {"targets": [{"price": 99.0, "basis": "x"}]})
    assert o is None and why == "low_rr"


def test_entry_mode_is_validated():
    with pytest.raises(ValueError):
        EngineConfig(entry_mode="scalp")


# ------------------------------------------------------------------ fills
def test_breakout_fills_only_after_a_close_above_the_trigger_at_next_open():
    ohlc = [FLAT, (100, 103, 99, 101.5), (101.5, 104, 101, 103), (103.5, 106, 103, 105), (105, 106, 104, 105)]
    df = bars_df(ohlc)
    decs = [hold(order("breakout", 102, 96, 120)), hold(), hold(), hold()]
    trades, _ = simulate(df, [0, 1, 2, 3], decs, NOCOST, CFG)
    # bar 1 trades above 102 but CLOSES at 101.5: no trigger. Bar 2 closes 103 > 102 -> buy at bar 3's open 103.5.
    assert len(trades) == 1 and trades[0].entry == 103.5 and trades[0].entry_type == "breakout"
    assert trades[0].entry_bar == 3


def test_pullback_fills_only_below_the_limit_at_min_open_limit():
    df = bars_df([FLAT, (100, 101, 98, 100), (100, 100.5, 97.9, 99), (99, 100, 98.5, 99)])
    decs = [hold(order("pullback", 98, 94, 110)), hold(), hold()]
    stats = {}
    trades, _ = simulate(df, [0, 1, 2], decs, NOCOST, CFG, stats=stats)
    # bar 1 low = 98.0 touches the limit: no fill. Bar 2 low 97.9 < 98: fill at min(open 100, 98) = 98.
    assert len(trades) == 1 and trades[0].entry == 98 and trades[0].entry_bar == 2
    assert trades[0].entry_type == "pullback" and stats["filled"] == 1
    df = bars_df([FLAT, (97, 98, 96, 97.5), FLAT])                  # opens below the limit -> fills at the open
    trades, _ = simulate(df, [0, 1], [hold(order("pullback", 98, 94, 110)), hold()], NOCOST, CFG)
    assert trades[0].entry == 97


def test_fill_beyond_stop_or_target_is_skipped():
    df = bars_df([FLAT, (93, 94, 92, 93), FLAT])
    stats = {}
    trades, _ = simulate(df, [0, 1], [hold(order("pullback", 98, 94, 110)), hold()], NOCOST, CFG, stats=stats)
    assert trades == [] and stats["skipped_gap"] == 1


def test_pending_expires_after_6_bars():
    ohlc = [FLAT] * 7 + [(100, 101, 97, 100), FLAT]
    df = bars_df(ohlc)
    decs = [hold(order("pullback", 98, 94, 110))] + [hold()] * 7
    stats = {}
    trades, _ = simulate(df, list(range(8)), decs, NOCOST, CFG, stats=stats)
    # live in bars 1..6; the dip below 98 comes in bar 7 -> too late
    assert trades == [] and stats["expired"] == 1
    decs = [hold(order("pullback", 98, 94, 110))] + [hold()] * 7
    df = bars_df([FLAT] * 6 + [(100, 101, 97, 100), FLAT, FLAT])     # dip in bar 6 = the 6th live bar -> fills
    trades, _ = simulate(df, list(range(8)), decs, NOCOST, CFG)
    assert len(trades) == 1 and trades[0].entry_bar == 6


def test_sell_setup_cancels_and_newer_order_replaces():
    df = bars_df([FLAT, FLAT, (100, 101, 97, 100), FLAT])
    stats = {}
    decs = [hold(order("pullback", 98, 94, 110)), hold(setup=SELL), hold()]
    trades, _ = simulate(df, [0, 1, 2], decs, NOCOST, CFG, stats=stats)
    assert trades == [] and stats["cancelled_by_sell"] == 1
    stats = {}
    decs = [hold(order("pullback", 98, 94, 110)), hold(order("pullback", 96, 92, 110)), hold()]
    trades, _ = simulate(df, [0, 1, 2], decs, NOCOST, CFG, stats=stats)
    # the newer 96 limit replaced the 98 one, so the dip to 97 in bar 2 does not fill
    assert trades == [] and stats["replaced"] == 1 and stats["created"] == 2


def test_no_target_check_in_the_pullback_fill_bar():
    df = bars_df([FLAT, (100, 115, 97, 101), (101, 102, 100, 101), (101, 115, 100, 112)])
    trades, _ = simulate(df, [0, 1, 2, 3][:3], [hold(order("pullback", 98, 94, 110)), hold(), hold()], NOCOST, CFG)
    # bar 1 fills at 98 and its high (115) is above the target, but the target is not checked in the fill bar
    assert trades[0].entry_bar == 1 and trades[0].reason == "target" and trades[0].exit_bar == 3


def test_stop_is_checked_in_the_pullback_fill_bar():
    df = bars_df([FLAT, (100, 101, 93, 95), FLAT])
    trades, _ = simulate(df, [0, 1], [hold(order("pullback", 98, 94, 110)), hold()], NOCOST, CFG)
    assert trades[0].entry == 98 and trades[0].reason == "stop" and trades[0].exit == 94


def test_one_position_and_time_stop_counts_from_the_fill():
    df = bars_df([FLAT] * 3 + [(100, 101, 97, 100)] + [FLAT] * 10)
    decs = [hold(order("pullback", 98, 90, 130))] + [hold()] * 12
    trades, _ = simulate(df, list(range(13)), decs, NOCOST, BacktestConfig(max_hold_bars=5))
    assert trades[0].entry_bar == 3 and trades[0].reason == "time stop" and trades[0].bars == 5


# ------------------------------------------------------------------ no look-ahead
def test_pending_fills_do_not_depend_on_future_candles(replays):
    bars, decs, trades, _, df = replays["pullback"]
    cut = len(bars) - 60
    part, _ = simulate(df.iloc[:bars[cut] + 1], bars[:cut], decs[:cut], EngineConfig(entry_mode="pullback"),
                       BacktestConfig(window=150))
    closed_before = [t for t in trades if t.exit_bar < bars[cut - 1]]
    assert closed_before, "fixture has no pullback trade before the cut"
    assert [asdict(t) for t in part][:len(closed_before)] == [asdict(t) for t in closed_before]


def test_replay_pending_orders_do_not_depend_on_future_candles():
    c1d = make_rows(300, "1d", seed=4, end_ms=END)
    c4h = make_rows(420, "4h", seed=4, end_ms=END)
    c1h = make_rows(420 * 4, "1h", seed=4, end_ms=END)
    ec, cfg = EngineConfig(entry_mode="pullback"), BacktestConfig(window=120)
    full = replay(_frame(c1h), _frame(c4h), _frame(c1d), ec, {"d": ec.weights}, cfg, 300)
    cut_t = c4h[360]["close_time"]
    part = replay(_frame([r for r in c1h if r["close_time"] <= cut_t]), _frame(c4h[:361]),
                  _frame([r for r in c1d if r["close_time"] <= cut_t]), ec, {"d": ec.weights}, cfg, 300)
    n = len(part[0])
    assert [d.pending for d in part[1]["d"]] == [d.pending for d in full[1]["d"][:n]]


# ------------------------------------------------------------------ periods
def fake_replay(c1h, c4h, c1d, engine_cfg, weight_sets, cfg, start_index, progress=None, cancelled=None, end_index=None):
    """Fast stand-in for the engine: a tradable buy every 8 bars with a 2% stop and 2% target."""
    bars = list(range(start_index, min(len(c4h) - 1, end_index if end_index is not None else len(c4h))))
    c = c4h["close"].to_numpy()
    decs = [Decision(BUY, True, 0.4, c[i] * 0.98, c[i] * 1.02, raw_setup=BUY) if k % 8 == 0
            else Decision(HOLD, False, 0.0, None, None, raw_setup=HOLD) for k, i in enumerate(bars)]
    return bars, {name: list(decs) for name in weight_sets}


def period_rows():
    end = HOLDOUT_START + 120 * 86_400_000
    return (make_rows(4000, "1h", seed=3, end_ms=end), make_rows(1400, "4h", seed=3, end_ms=end),
            make_rows(400, "1d", seed=3, end_ms=end))


def test_holdout_has_no_decisions_or_trades_before_the_cutoff(monkeypatch):
    monkeypatch.setattr(bt, "replay", fake_replay)
    c1h, c4h, c1d = period_rows()
    r = run_backtest(c1h, c4h, c1d, EngineConfig(), BacktestConfig(window=150, years=None, period="holdout",
                                                                   random_runs=10))
    assert r["period"]["start"] >= HOLDOUT_START and r["period_kind"] == "holdout"
    assert r["trades"] and all(t["entry_time"] >= HOLDOUT_START for t in r["trades"])
    enough = next(c for c in r["criteria"] if c["name"] == "Enough trades")
    assert f"need >= {HOLDOUT_MIN_TRADES}" in enough["detail"] and HOLDOUT_MIN_TRADES == 15
    assert r["settings"]["criteria"]["min_trades"] == 15


def test_design_period_ends_before_the_cutoff_and_never_validates(monkeypatch):
    monkeypatch.setattr(bt, "replay", fake_replay)
    c1h, c4h, c1d = period_rows()
    r = run_backtest(c1h, c4h, c1d, EngineConfig(), BacktestConfig(window=150, years=None, period="design",
                                                                   random_runs=10))
    assert r["period"]["end"] < HOLDOUT_START and r["validated"] is False
    assert all(t["exit_time"] < HOLDOUT_START for t in r["trades"])
    assert r["design_badge"].startswith("DESIGN PERIOD") and r["verdict"].startswith("DESIGN PERIOD")
    assert r["settings"]["criteria"]["min_trades"] == 30
    full = run_backtest(c1h, c4h, c1d, EngineConfig(), BacktestConfig(window=150, years=None, random_runs=10))
    assert full["period"]["start"] < HOLDOUT_START < full["period"]["end"]


def test_bad_period_rejected():
    with pytest.raises(ValueError):
        BacktestConfig(period="future")


# ------------------------------------------------------------------ config hash, service, API
def test_entry_mode_changes_config_hash():
    assert config_hash(EngineConfig()) == config_hash(EngineConfig(entry_mode="standard"))
    assert len({config_hash(EngineConfig(entry_mode=m)) for m in ("standard", "breakout", "pullback")}) == 3


def test_design_runs_never_write_validation(config, db):
    from test_app_and_db import fake_result, make_client6
    seen = {}

    def runner(h, f, d, cfg, bcfg, *a, **k):
        seen["mode"], seen["period"] = cfg.entry_mode, bcfg.period
        return fake_result(True, cfg)

    c, svc = make_client6(config, db, runner=runner)
    svc.start(years=1, download=False, background=False, entry_mode="breakout", period="design")
    assert seen == {"mode": "breakout", "period": "design"}
    assert db.get_setting("strategy_validation") is None
    latest = c.get("/api/backtest/latest").get_json()
    assert latest["entry_mode"] == "breakout" and latest["period_kind"] == "design" and latest["validated"] is False
    svc.start(years=1, download=False, background=False, entry_mode="breakout", period="holdout")
    assert db.get_setting("strategy_validation")["period_kind"] == "holdout"


def test_api_rejects_bad_entry_mode_or_period(config, db):
    from test_app_and_db import make_client6
    c, _ = make_client6(config, db)
    assert c.post("/api/backtest/run", json={"years": 1, "download": False, "entry_mode": "scalp"}).status_code == 400
    assert c.post("/api/backtest/run", json={"years": 1, "download": False, "period": "tomorrow"}).status_code == 400
    assert c.put("/api/signal/config", json={"entry_mode": "scalp"}).status_code == 400
    r = c.put("/api/signal/config", json={"entry_mode": "pullback"})
    assert r.status_code == 200 and r.get_json()["config"]["entry_mode"] == "pullback"


def test_forward_observation_gate_lasts_56_days(config, db):
    from database import now_ms
    from test_app_and_db import make_client6
    from services.signal_engine import FORWARD_OBSERVATION_DAYS
    assert FORWARD_OBSERVATION_DAYS == 56
    c, _ = make_client6(config, db)
    day = 86_400_000
    good = {"validated": True, "run_id": 7, "engine": "x", "config_hash": config_hash(EngineConfig()), "note": "ok"}
    db.set_setting("strategy_validation", {**good, "as_of": now_ms() - 10 * day})
    s = c.get("/api/signal").get_json()
    reasons = [r for r in s["no_trade_reasons"] if "forward observation" in r]
    assert s["signal"] == "NO TRADE" and reasons and reasons[0].startswith("Validated on ")
    assert "not_validated" in s["diagnostics"]["gate_keys"]
    db.set_setting("strategy_validation", {**good, "as_of": now_ms() - 57 * day})
    s = c.get("/api/signal").get_json()
    assert "not_validated" not in s["diagnostics"]["gate_keys"]
    assert not any("forward observation" in r for r in s["no_trade_reasons"])


# ------------------------------------------------------------------ funnel
def test_funnel_counts_from_the_unrounded_score():
    D = lambda **k: Decision(**{"setup": HOLD, "tradable": False, "stop": None, "target": None, **k})
    decs = [D(score=0.2496, raw_setup=HOLD),                         # rounds to 0.250 but its setup is HOLD
            D(score=0.30, raw_setup=BUY, downgrade="low_rr", rr_t1=0.4),
            D(score=-0.1, raw_setup=HOLD)]
    f = funnel(decs, [], NOCOST)
    assert f["bars_score_at_or_above_buy"] == f["raw_buy_setups"] == 1
    assert f["rounding_check"]["bars_rounded_score_at_or_above_buy"] == 2
    assert "share_no_room" in f["low_rr_values"] and "share_negative" not in f["low_rr_values"]


def test_replay_score_count_equals_raw_buys(replays):
    _, decs, trades, stats, _ = replays["breakout"]
    f = funnel(decs, trades, NOCOST, stats)
    assert f["bars_score_at_or_above_buy"] == f["raw_buy_setups"]
    assert set(f["pending_orders"]) >= {"created", "replaced", "expired", "cancelled_by_sell", "skipped_gap", "filled"}
    assert sum(f["trades_by_entry"].values()) == f["trades"]


def test_generate_signal_reports_score_raw_and_entry_mode():
    c1h, c4h, c1d = fixture_rows(4)
    from services.technical_analysis import analyze
    an = {"1h": analyze(_frame(c1h[-300:]), "1h"), "4h": analyze(_frame(c4h[-300:]), "4h"),
          "1d": analyze(_frame(c1d[-300:]), "1d")}
    meta = {tf: {"is_stale": False, "gaps": 0, "warnings": []} for tf in ("1h", "4h", "1d")}
    s = generate_signal(an, meta, {"price": c4h[-1]["close"], "warnings": []}, None, [], EngineConfig(), None, END)
    assert s["diagnostics"]["entry_mode"] == "standard" and s["pending_order"] is None
    assert round(s["diagnostics"]["score_raw"], 3) == s["score"]
    assert np.isfinite(s["diagnostics"]["score_raw"])
