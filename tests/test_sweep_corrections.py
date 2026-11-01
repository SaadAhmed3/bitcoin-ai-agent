"""Append-only correction records for stored sweep events: the 0.9.8 duplicate is superseded, never deleted."""
from __future__ import annotations

import sqlite3

import pytest

from conftest import FakeProvider
from test_app_and_db import make_client

DUP = "4h:buy_side:1793289600000"
FIRST = "4h:buy_side:1793275200000"
REASON = "duplicate of the same move (open-event rule, 0.9.8 follow-up)"


def stored_pair(db):
    """The two stored rows of the real 2026-10-29 move at the 81,823 level, as the 0.9.8 monitor wrote them."""
    for key, t in ((FIRST, 1_793_289_599_999), (DUP, 1_793_303_999_999)):
        db.sweep_add({"key": key, "timeframe": "4h", "direction": "buy_side", "level": 81823.0, "equal_levels": 1,
                      "swing_time": 1_793_174_400_000, "event_time": t, "sweep_extreme": 81971.0,
                      "reclaim_price": 80848.64},
                     [{"status": "POTENTIAL SWEEP", "at": t, "strength": 50.0, "factors": {}, "note": ""}], now=1)


def test_schema_13_adds_a_write_once_corrections_table(db):
    from database.db import SCHEMA_VERSION
    assert SCHEMA_VERSION >= 13              # 14: Strategy Lab tables
    stored_pair(db)
    assert db.sweep_correct(DUP, "superseded", REASON, "0.9.8", now=5) is True
    assert db.sweep_correct(DUP, "superseded", REASON, "0.9.8", now=6) is False          # once per event
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.connect() as conn:
            conn.execute("UPDATE sweep_corrections SET reason = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.connect() as conn:
            conn.execute("DELETE FROM sweep_corrections")
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:                                                       # only 'superseded'
            conn.execute("INSERT INTO sweep_corrections (event_id, action, reason, corrected_at, by_version) "
                         "VALUES (1, 'deleted', 'x', 1, 'v')")
    with pytest.raises(LookupError):
        db.sweep_correct("4h:buy_side:0", "superseded", REASON, "0.9.8", now=7)          # unknown event


def test_the_known_duplicate_is_superseded_once_and_the_row_itself_is_untouched(db):
    from services.advanced_analysis import SWEEP_CORRECTIONS, apply_sweep_corrections
    assert SWEEP_CORRECTIONS == [(DUP, "superseded", REASON)]
    assert apply_sweep_corrections(db, "0.9.8 (advanced analysis)", now=9) == 0          # not stored here: no-op
    stored_pair(db)
    assert apply_sweep_corrections(db, "0.9.8 (advanced analysis)", now=10) == 1
    assert apply_sweep_corrections(db, "0.9.8 (advanced analysis)", now=11) == 0
    with db.connect() as conn:
        row = conn.execute("SELECT c.action, c.reason, c.by_version, c.corrected_at, e.event_key FROM sweep_corrections c "
                           "JOIN sweep_events e ON e.id = c.event_id").fetchone()
        assert tuple(row) == ("superseded", REASON, "0.9.8 (advanced analysis)", 10, DUP)
        assert conn.execute("SELECT COUNT(*) FROM sweep_events").fetchone()[0] == 2       # kept, not deleted


def test_readers_never_see_a_superseded_event(db):
    from services.advanced_analysis import apply_sweep_corrections
    stored_pair(db)
    apply_sweep_corrections(db, "0.9.8", now=10)
    assert [e["event_key"] for e in db.sweep_events(10)] == [FIRST]
    assert db.superseded_sweep_keys() == {DUP}
    assert {e["event_key"] for e in db.sweep_events(10, include_superseded=True)} == {FIRST, DUP}   # audit only


def test_app_start_applies_the_correction_and_the_panels_drop_superseded_keys(config, db, monkeypatch):
    stored_pair(db)
    import services.advanced_analysis as aa
    real = aa.detect_sweeps

    def with_duplicate(*a, **k):                     # as if detection produced the superseded key again
        ev = real(*a, **k)
        if ev:
            ev.append({**ev[-1], "key": DUP})
        return ev

    monkeypatch.setattr(aa, "detect_sweeps", with_duplicate)
    c = make_client(config, db, [FakeProvider("fake1")])
    assert db.superseded_sweep_keys() == {DUP}       # create_app applied it
    d = c.get("/api/advanced").get_json()
    assert d["sweeps"]["recent"] and DUP not in {e["key"] for e in d["sweeps"]["recent"]}
    adv = c.application.config["BTC_ADVANCED"]
    assert adv.record() is True
    assert DUP not in db.shadow_rows(1)[0]["detail"]["active_sweeps"]


def test_research_reruns_exclude_superseded_keys():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("ra", Path(__file__).resolve().parents[1] / "scripts" / "research_advanced.py")
    ra = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ra)
    events = [{"key": FIRST}, {"key": DUP}]
    assert ra.drop_superseded(events, {DUP}) == [{"key": FIRST}]
