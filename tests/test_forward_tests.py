"""Forward paper tracker for H1 and H2 (research-1). Synthetic candles only; no live-signal change, no real orders.

Each closed Binance daily candle after the start is processed once, in order: fill yesterday's decision at today's
open, mark at today's close, decide at today's close. Every row is append-only.
"""
import importlib.util
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from conftest import FIXED_NOW, FakeProvider
from test_app_and_db import make_client

DAY = 86_400_000
COST = 0.0015
NOW = 1_793_458_800_000                         # 2026-10-31 15:00 UTC
START = 1_793_491_200_000                       # 2026-11-01 00:00 UTC: the next UTC midnight


def research():
    spec = importlib.util.spec_from_file_location(
        "research_h1", Path(__file__).resolve().parents[1] / "scripts" / "research_h1.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def daily(n_before=320, n_after=4, seed=5, drift=0.002, start=START):
    """Synthetic closed daily candles: n_before days before the start day, n_after from it (opens = previous close)."""
    rng = np.random.default_rng(seed)
    closes = 30_000 * np.exp(np.cumsum(rng.normal(drift, 0.02, n_before + n_after)))
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        ot = start + (i - n_before) * DAY
        rows.append({"open_time": ot, "open": float(prev), "high": float(max(prev, c)), "low": float(min(prev, c)),
                     "close": float(c), "volume": 1.0, "close_time": ot + DAY - 1})
        prev = c
    return rows


def tracker(db, rows, now=NOW, start=True):
    """Tracker whose clock the test moves. With start=True it is started at NOW (2026-10-31 15:00, so the start day
    is 2026-11-01) and the clock is then moved to `now`, as in real life."""
    from services.forward_tests import ForwardTests
    t = {"now": NOW}
    ft = ForwardTests(db, clock=lambda: t["now"], candles=lambda: [r for r in rows if r["close_time"] < t["now"]])
    if start:
        ft.ensure_started()
    t["now"] = now
    return ft, t


# ------------------------------------------------------------------ the rules are the pre-registered ones
def test_rules_match_the_research_script():
    from services.forward_tests import RULES
    rows = daily(n_before=450, n_after=0, drift=0.0, seed=11)
    close = np.array([r["close"] for r in rows])
    rs = research()
    h1 = [RULES["H1"](close[: i + 1])["in_market"] for i in range(len(close))]
    h2 = [RULES["H2"](close[: i + 1])["in_market"] for i in range(len(close))]
    assert h1 == list(rs.sma_filter(close, 200, 0.0))
    assert h2 == list(rs.ema_cross(close))
    assert all(RULES["BH"](close[: i + 1])["in_market"] for i in range(len(close)))
    assert any(h1[200:]) and not all(h1[200:]) and any(h2[200:]) and not all(h2[200:])   # the series crosses


# ------------------------------------------------------------------ start: the next UTC day, fixed, fingerprinted
def test_start_is_the_next_utc_midnight_fixed_and_fingerprinted(db):
    from services.forward_tests import current_fingerprint
    ft, t = tracker(db, daily(), start=False)
    info = ft.ensure_started()
    assert info["start_day"] == START and {x["name"] for x in info["tests"]} == {"H1", "H2", "BH"}
    assert all(x["fingerprint"] == current_fingerprint() and len(x["fingerprint"]) == 64 for x in info["tests"])
    t["now"] += 5 * DAY
    assert ft.ensure_started()["start_day"] == START                    # never moves (e.g. after a restart)


# ------------------------------------------------------------------ daily processing
def test_each_closed_day_after_the_start_is_processed_once_in_order(db):
    rows = daily(n_after=4)
    ft, t = tracker(db, rows, now=START + 4 * DAY + 60_000)             # days START .. START+3 have closed
    ft.ensure_started()
    added = ft.process()
    ev = {name: db.forward_events(name) for name in ("H1", "H2", "BH")}
    assert added == len(ev["H1"]) + len(ev["H2"]) + len(ev["BH"])
    for name in ("H1", "H2", "BH"):
        days = sorted({e["day"] for e in ev[name]})
        assert days == [START, START + DAY, START + 2 * DAY, START + 3 * DAY]   # nothing before the start
    bh = ev["BH"]
    assert [e["kind"] for e in bh if e["day"] == START] == ["mark", "decision"]      # flat on day 1, decide at close
    fill = next(e for e in bh if e["kind"] == "fill")
    day2 = next(r for r in rows if r["open_time"] == START + DAY)
    assert fill["day"] == START + DAY and fill["price"] == pytest.approx(day2["open"])  # next open, never the close
    units = 1 / (day2["open"] * (1 + COST))
    mark = next(e for e in bh if e["kind"] == "mark" and e["day"] == START + DAY)
    assert mark["equity"] == pytest.approx(units * day2["close"] * (1 - COST))
    assert ft.process() == 0                                             # idempotent: nothing added twice


def test_a_missing_day_stops_processing_until_it_arrives(db):
    rows = daily(n_after=4)
    gap = [r for r in rows if r["open_time"] != START + 2 * DAY]
    ft, t = tracker(db, gap, now=START + 4 * DAY + 60_000)
    ft.ensure_started()
    ft.process()
    assert max(e["day"] for e in db.forward_events("H1")) == START + DAY            # waits at the gap
    st = ft.status()
    assert st["state"] == "waiting" and "2026-11-03" in st["waiting_for"]
    gap.insert(next(i for i, r in enumerate(gap) if r["open_time"] > START + 2 * DAY), rows[-2])
    ft.process()
    assert max(e["day"] for e in db.forward_events("H1")) == START + 3 * DAY


def test_forward_rows_are_append_only(db):
    ft, _ = tracker(db, daily(), now=START + 2 * DAY + 60_000)
    ft.ensure_started()
    ft.process()
    for sql in ("UPDATE forward_events SET price = 1", "DELETE FROM forward_events",
                "UPDATE forward_tests SET start_day = 0", "DELETE FROM forward_tests"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            with db.connect() as conn:
                conn.execute(sql)


# ------------------------------------------------------------------ status, fingerprint, evaluation
def test_status_reports_progress_positions_and_a_code_change(db, monkeypatch):
    import services.forward_tests as fwd
    ft, t = tracker(db, daily(n_after=4), now=START + 4 * DAY + 60_000, start=False)
    assert ft.status()["state"] == "not_started"
    t["now"] = NOW
    ft.ensure_started()
    t["now"] = START + 4 * DAY + 60_000
    ft.process()
    st = ft.status()
    assert st["state"] == "running" and st["days_elapsed"] == 4 and st["target_days"] == 365
    bh = next(s for s in st["strategies"] if s["name"] == "BH")
    assert bh["position"] == "in" and bh["trades"] == 1 and bh["time_in_market_pct"] == 75.0
    assert all(s["code_changed"] is False for s in st["strategies"])
    assert next(s for s in st["strategies"] if s["name"] == "H2")["evaluation"]["verdict"] == "NOT YET"
    monkeypatch.setattr(fwd, "current_fingerprint", lambda: "f" * 64)
    assert all(s["code_changed"] is True for s in ft.status()["strategies"])


def test_h2_verdict_uses_exactly_the_first_365_days_and_never_changes_after(db):
    """PREREGISTRATION_H2.md: one verdict, on processed days 1-365; days after it never change that verdict."""
    rows = daily(n_after=380, drift=0.0005, seed=9)
    ft, t = tracker(db, rows, now=START + 370 * DAY + 60_000)
    ft.process()
    first = next(s for s in ft.status()["strategies"] if s["name"] == "H2")["evaluation"]
    t["now"] = START + 380 * DAY + 60_000
    ft.process()
    st = ft.status()
    later = next(s for s in st["strategies"] if s["name"] == "H2")["evaluation"]
    assert st["days_elapsed"] == 380 and later == first and first["days"] == 365
    ev = first
    assert ev["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE")
    assert ev["window"] == {"first_day": "2026-11-01", "last_day": "2027-10-31"}
    if ev["verdict"] != "INCONCLUSIVE":
        assert [c["name"] for c in ev["criteria"]] == ["Drawdown at most 60% of buy & hold",
                                                       "Return/drawdown at least buy & hold's",
                                                       "Beats >= 90% of random entries"]


# ------------------------------------------------------------------ app wiring
def test_get_api_forward_is_read_only_and_the_monitor_starts_the_tracker(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    st = c.get("/api/forward").get_json()
    assert st["state"] == "not_started"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM forward_tests").fetchone()[0] == 0      # GET created nothing
    c.application.config["BTC_MONITOR"].run_once()
    st = c.get("/api/forward").get_json()
    assert st["state"] in ("waiting", "running") and st["start_day"] % DAY == 0 and st["start_day"] > FIXED_NOW - DAY
    assert {s["name"] for s in st["strategies"]} == {"H1", "H2", "BH"}
