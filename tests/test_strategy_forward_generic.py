"""Strategy forward tracker, round 2: one generic core for every strategy, per-strategy fingerprints that later
rounds do not disturb, and a one-time fingerprint re-base that is only possible before the first processed day."""
from __future__ import annotations

import sqlite3

import pytest

from test_strategy_trend import H4, T0, uptrend

OLD_DCA_FP = "f576a36a9cb5fdeacb3a99a6e63c0915b0817a4b5295000e8f85d6bc9cd5d4f6"   # round-1 scheme, stored on the real DB


def test_fingerprint_covers_the_strategy_module_and_the_tracker_core_only():
    import services.strategy_forward as sf
    assert [p.name for p in sf.fingerprint_files("dca")] == ["dca.py", "strategy_forward.py"]
    assert [p.name for p in sf.fingerprint_files("trend")] == ["trend.py", "strategy_forward.py"]
    assert sf.strategy_fingerprint("dca") != sf.strategy_fingerprint("trend")


def test_rebase_only_before_the_first_processed_day_and_status_uses_it(db):
    from services.strategy_forward import StrategyForward, strategy_fingerprint
    t = db.strategy_forward_start("dca", "h", {}, OLD_DCA_FP, start_day=T0, now=1)
    fwd = StrategyForward(db, clock=lambda: T0 - 1, candles=lambda tf: [])
    assert fwd.status("dca", "h")["code_changed"] is True                 # old scheme != new scheme
    db.strategy_forward_rebase(t["id"], OLD_DCA_FP, strategy_fingerprint("dca"), "tracker made generic", now=2)
    st = fwd.status("dca", "h")
    assert st["code_changed"] is False and st["rebased"]["reason"] == "tracker made generic"
    db.strategy_forward_add_events([{"test_id": t["id"], "day": T0, "kind": "mark", "detail": {}}], now=3)
    with pytest.raises(sqlite3.DatabaseError, match="before its first processed day"):
        db.strategy_forward_rebase(t["id"], OLD_DCA_FP, "x", "late", now=4)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.connect() as conn:
            conn.execute("DELETE FROM strategy_forward_rebases")


def test_the_known_dca_rebase_is_applied_once_and_only_while_no_day_was_processed(db):
    from services.strategy_forward import strategy_fingerprint
    from services.strategy_lab import KNOWN_REBASES, apply_rebases
    assert [(r[0], r[1], r[2]) for r in KNOWN_REBASES] == [("dca", "f626cffd73fb1e10", OLD_DCA_FP)]
    assert apply_rebases(db, now=1) == 0                                 # not registered here: no-op
    t = db.strategy_forward_start("dca", "f626cffd73fb1e10", {}, OLD_DCA_FP, start_day=T0, now=1)
    assert apply_rebases(db, now=2) == 1 and apply_rebases(db, now=3) == 0
    assert db.strategy_forward_rebases(t["id"])[0]["new_fingerprint"] == strategy_fingerprint("dca")
    late = db.strategy_forward_start("dca", "other", {}, OLD_DCA_FP, start_day=T0, now=1)
    db.strategy_forward_add_events([{"test_id": late["id"], "day": T0, "kind": "mark", "detail": {}}], now=3)


def test_trend_forward_test_uses_the_backtest_step_bar_by_bar(db):
    from services.strategies import trend
    from services.strategy_forward import StrategyForward
    df4, df1 = uptrend(n=900)
    rows4, rows1 = df4.to_dict("records"), df1.to_dict("records")
    start = int(df4["open_time"].iloc[500])
    cfg = trend.validate_config({})
    db.strategy_forward_start("trend", "h", cfg, "fp", start_day=start, now=1)
    now = int(df4["close_time"].iloc[-1]) + 1
    fwd = StrategyForward(db, clock=lambda: now, candles=lambda tf: rows4 if tf == "4h" else rows1)
    added = fwd.process()
    assert added > 0 and fwd.process() == 0
    st = fwd.status("trend", "h")
    assert st["state"] == "running" and st["days_elapsed"] == 400 // 6 and st["timeframe"] == "4h"
    ind = trend.indicators(df4, df1, cfg)
    sim = trend.simulate(ind, cfg, 500, 899)
    marks = [e for e in db.strategy_forward_events(db.strategy_forward_test("trend", "h")["id"]) if e["kind"] == "mark"]
    assert len(marks) == 400
    assert [round(m["detail"]["equity"], 9) for m in marks[:-1]] == [round(x, 9) for x in sim["equity"][:-1]]
    assert st["trades"] == len([t for t in sim["trades"] if t["reason"] != "end of design data"])
