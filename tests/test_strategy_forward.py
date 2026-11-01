"""The separate strategy forward tracker: own tables, own fingerprint, own start date, append-only."""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from test_strategy_dca import MON, daily

D = 86_400_000


def tracker(db, rows, now):
    from services.strategy_forward import StrategyForward
    return StrategyForward(db, clock=lambda: now, candles=lambda tf: rows)


def test_starts_the_next_utc_midnight_once_with_its_own_fingerprint(db):
    from services.strategy_forward import strategy_fingerprint
    t = tracker(db, [], now=MON - D + 5 * 3_600_000)               # Sunday 05:00 UTC
    started = t.ensure_started("dca", {"amount_usd": 100.0}, "cfg1")
    assert started["start_day"] == MON and started["fingerprint"] == strategy_fingerprint("dca")
    again = tracker(db, [], now=MON + 3 * D).ensure_started("dca", {"amount_usd": 100.0}, "cfg1")
    assert again["start_day"] == MON and again["id"] == started["id"]   # never moved
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.connect() as conn:
            conn.execute("UPDATE strategy_forward_tests SET start_day = 0")


def test_processes_closed_days_in_order_buys_on_schedule_and_appends_only(db):
    from services.strategies.dca import DEFAULTS
    rows = daily(10).to_dict("records")
    t = tracker(db, rows, now=MON - D)
    t.ensure_started("dca", dict(DEFAULTS), "h")
    t2 = tracker(db, rows, now=MON + 10 * D + 1)
    assert t2.process() > 0
    assert t2.process() == 0                                         # nothing twice
    st = t2.status("dca", "h")
    assert st["state"] == "running" and st["days_elapsed"] == 10 and st["target_days"] == 365
    assert st["buys"] == 2 and st["code_changed"] is False        # Mondays: day 0 and day 7
    assert st["dca_return_pct"] is not None and st["lump_return_pct"] is not None
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.connect() as conn:
            conn.execute("DELETE FROM strategy_forward_events")


def test_a_missing_day_stops_processing_until_it_arrives(db):
    from services.strategies.dca import DEFAULTS
    rows = daily(10, gap=(4,)).to_dict("records")
    tracker(db, rows, now=MON - D).ensure_started("dca", dict(DEFAULTS), "h")
    t = tracker(db, rows, now=MON + 10 * D + 1)
    t.process()
    st = t.status("dca", "h")
    assert st["days_elapsed"] == 4 and "waiting" in st["waiting_for"]


def test_code_change_is_reported_and_the_other_tracker_is_untouched(db, monkeypatch):
    import services.strategy_forward as sf
    from services.forward_tests import current_fingerprint
    tracker(db, [], now=MON - D).ensure_started("dca", {}, "h")
    monkeypatch.setattr(sf, "strategy_fingerprint", lambda sid: "different")
    assert tracker(db, [], now=MON).status("dca", "h")["code_changed"] is True
    assert current_fingerprint() == "5e4b8a0ebba56ae03631b37acc114ae63c1cb8d6a1ab69cc32f1f5f6e5b0a450"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM forward_tests").fetchone()[0] == 0


def test_verdict_after_365_days_is_fixed(db):
    from services.strategies.dca import DEFAULTS
    rows = daily(380, step=0.3).to_dict("records")
    tracker(db, rows, now=MON - D).ensure_started("dca", dict(DEFAULTS), "h")
    t = tracker(db, rows, now=MON + 380 * D + 1)
    t.process()
    st = t.status("dca", "h")
    assert st["state"] == "done" and st["days_elapsed"] == 365 and st["verdict"] in ("PASS", "FAIL")
    assert st["criteria"][0]["id"] == "F1"
