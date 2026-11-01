"""Breakout-0.5R: a FORWARD-ONLY variant of Breakout (identical rules, 0.5% risk), chosen after the design results.

It never gets a design verdict, its own fingerprint pins the breakout rules it reuses, and the original Breakout keeps
its FAIL verdict unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_strategy_breakout import stairs

ROOT = Path(__file__).resolve().parents[1]
LABEL = "chosen after design results: forward test only, no design verdict"


def test_identical_rules_half_the_risk_and_a_preregistration_saying_so():
    from services.strategies import breakout as bo, breakout_05r as v
    from services.strategies.base import config_hash
    assert v.DEFAULTS == {**bo.DEFAULTS, "risk_pct": 0.5} and v.validate_config({}) == v.DEFAULTS
    assert v.validate_config({"risk_pct": 1.0})["risk_pct"] == 1.0           # a change is possible, and versioned
    assert v.FORWARD_ONLY is True and v.VARIANT_OF == "breakout" and v.LABEL == LABEL
    assert not hasattr(v, "backtest")                                          # no design backtest, ever
    text = (ROOT / "PREREGISTRATION_BREAKOUT_05R.md").read_text(encoding="utf-8")
    assert f"Config hash: `{config_hash(v.DEFAULTS)}`" in text and LABEL in text
    for must in ("after the Breakout design result", "FAIL", "no design verdict", "`risk_pct` | `0.5`",
                 "identical", "breakout.py", v.RULES_SHA256):
        assert must in text, must


def test_the_live_signal_is_breakouts_own_under_its_own_name():
    from services.strategies import breakout as bo, breakout_05r as v
    from services.strategies.base import DataQuality
    df4, _ = stairs(n=900)
    ft = bo.features(df4, bo.validate_config({}))
    for i in (int(ft.index[ft["buy"]][-1]), len(df4) - 1):
        frames = {"4h": df4.iloc[:i + 1]}
        dq = DataQuality("binance", 1, int(df4["close_time"].iloc[i]), False, 0, [])
        a = bo.evaluate_frames(frames, bo.validate_config({}), now=1, data=dq).to_dict()
        b = v.evaluate_frames(frames, v.validate_config({}), now=1, data=dq).to_dict()
        assert b["strategy_id"] == "breakout_05r" and b["strategy_version"] == v.VERSION
        for k in ("signal", "stop", "entry_zone", "targets", "supporting", "contradictory"):
            assert a[k] == b[k], k


def test_forward_positions_are_half_the_size(db):
    from services.strategies import breakout as bo, breakout_05r as v
    from services.strategy_forward import StrategyForward
    df4, _ = stairs(n=900)
    rows = df4.to_dict("records")
    start = int(df4["open_time"].iloc[600])
    for sid, mod in (("breakout", bo), ("breakout_05r", v)):
        db.strategy_forward_start(sid, "h", mod.validate_config({}), "fp", start_day=start, now=1)
    StrategyForward(db, clock=lambda: int(df4["close_time"].iloc[800]) + 1, candles=lambda tf: rows).process()
    buys = {sid: [e["detail"] for e in db.strategy_forward_events(db.strategy_forward_test(sid, "h")["id"]) if e["kind"] == "buy"]
            for sid in ("breakout", "breakout_05r")}
    assert buys["breakout"] and [b["time"] for b in buys["breakout"]][:1] == [b["time"] for b in buys["breakout_05r"]][:1]
    first, half = buys["breakout"][0], buys["breakout_05r"][0]
    assert half["units"] == pytest.approx(first["units"] / 2) and half["stop"] == first["stop"]


def test_its_own_fingerprint_pins_the_breakout_rules(db, monkeypatch):
    from services.strategies import breakout_05r as v
    from services.strategies.base import DataQuality
    from services.strategy_forward import fingerprint_files, strategy_fingerprint
    assert [p.name for p in fingerprint_files("breakout_05r")] == ["breakout_05r.py", "strategy_forward.py"]
    assert strategy_fingerprint("breakout_05r") != strategy_fingerprint("breakout")
    assert v.rules_dependency_ok() is True
    monkeypatch.setattr(v, "RULES_SHA256", "0" * 64)                          # breakout.py changed
    df4, _ = stairs(n=900)
    dq = DataQuality("binance", 1, int(df4["close_time"].iloc[-1]), False, 0, [])
    r = v.evaluate_frames({"4h": df4}, v.validate_config({}), now=1, data=dq)
    assert r.signal == "NO TRADE" and "breakout.py changed" in r.rejection
    events, state = v.forward_step(v.validate_config({}), {"rows": df4.to_dict("records"), "k": 700, "state": None})
    assert events == [] and state is None                                      # paused: never other rules silently
    assert v.forward_summary([], v.validate_config({}), False)["dependency_changed"] is True


def test_the_lab_labels_it_everywhere_and_breakout_keeps_its_fail(db):
    from services.strategies import breakout as bo
    from services.strategy_forward import StrategyForward
    from services.strategy_lab import StrategyLab
    lab = StrategyLab(db, None, StrategyForward(db, clock=lambda: 1_800_000_000_000), clock=lambda: 1_800_000_000_000)
    lab.ensure_defaults()
    cfg = lab.config("breakout")
    db.strategy_backtest_add("breakout", 1, cfg["hash"], "design", "sha", {"verdict": "FAIL", "metrics": {}}, now=1)
    assert lab.validation("breakout", cfg)["label"] == "FAILED on design data · not validated"
    vc = lab.config("breakout_05r")
    st = lab.validation("breakout_05r", vc)
    assert LABEL in st["label"].lower() and st["design_verdict"] is None and "FAILED" not in st["label"]
    lab.forward_job()
    t = db.strategy_forward_test("breakout_05r", vc["preregistered_hash"])
    assert t["config"]["risk_pct"] == 0.5 and t["start_day"] == (1_800_000_000_000 // 86_400_000 + 1) * 86_400_000
    assert db.strategy_forward_test("breakout", cfg["preregistered_hash"])["config"]["risk_pct"] == bo.DEFAULTS["risk_pct"]
