"""The 2025-10-29 holdout is contaminated (owner's decision, 2026-10-31): a full run displayed the live engine and its
baselines through it. Contamination is recorded append-only; a contaminated period can never validate, and validation
needs forward data only (the forward tracker, or a new holdout start after today). Synthetic data and fake runners.
"""
import sqlite3
from datetime import datetime, timezone

import pytest
from test_app_and_db import fake_result, make_client6

from database import Database
from services.backtest_service import current_validation, record_contaminations
from services.backtesting import HOLDOUT_START, code_fingerprint

DAY = 86_400_000
NOW = HOLDOUT_START + 400 * DAY                       # a year after the holdout start


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def client(config, db, passes, now=NOW):
    """Fake runner: full runs end 'now' (they overlap the holdout), design runs end before it."""
    seq, t = iter(passes), {"now": now}

    def runner(h, f, d, cfg, bcfg, *a, **k):
        r = fake_result(next(seq), cfg)
        end = t["now"] if bcfg.period != "design" else bcfg.holdout_start - DAY
        r["period"] = {"kind": bcfg.period, "start": end - 365 * DAY, "end": end, "years": 1}
        r["criteria"] = [{**c, "detail": "synthetic"} for c in r["criteria"]]
        return r

    c, svc = make_client6(config, db, runner=runner)
    svc.clock = lambda: t["now"]
    return c, svc, t


def run(svc, period):
    svc.start(years=1, download=False, background=False, period=period)


# ------------------------------------------------------------------ marking
def test_a_full_run_over_the_holdout_contaminates_it_and_no_later_run_can_validate(config, db):
    c, svc, t = client(config, db, [False, True, True])
    run(svc, "full")
    full_id = c.get("/api/backtest/runs").get_json()["runs"][0]["id"]
    h = c.get("/api/backtest/status").get_json()["holdout"]
    assert h["status"] == "contaminated"
    assert h["label"] == f"contaminated (seen via full run #{full_id} on {iso(NOW)}), cannot validate"
    t["now"] += DAY
    run(svc, "holdout")                                                 # a PASS on a contaminated period
    reg = c.get("/api/backtest/holdout").get_json()["runs"]
    assert reg[0]["counts_for_validation"] is False
    later = c.get(f"/api/backtest/{reg[0]['backtest_run_id']}").get_json()
    assert later["validated"] is False and later["period_badge"].startswith("HOLDOUT CONTAMINATED")
    assert f"not valid for validation: holdout contaminated (seen via full run #{full_id} on {iso(NOW)})" in later["verdict"]
    assert db.get_setting("strategy_validation") is None                 # nothing was validated
    run(svc, "holdout")
    assert all(r["counts_for_validation"] is False for r in c.get("/api/backtest/holdout").get_json()["runs"])


def test_a_design_run_does_not_contaminate(config, db):
    c, svc, t = client(config, db, [True])
    run(svc, "design")
    assert c.get("/api/backtest/status").get_json()["holdout"]["status"] == "untouched"


def test_existing_full_runs_are_recorded_at_startup(config, db):
    """The real run #1 (full period, stored before this change) must be found and recorded when the app starts."""
    seen_at = HOLDOUT_START + 365 * DAY
    rid = db.save_backtest(seen_at, "signal-1.7", "abc", False,
                           {"period_kind": "full", "period": {"kind": "full", "start": HOLDOUT_START - 1000 * DAY,
                                                              "end": seen_at}}, {})
    db.save_backtest(seen_at, "signal-1.7", "abc", False,                 # design: ends before the holdout
                     {"period_kind": "design", "period": {"kind": "design", "end": HOLDOUT_START - DAY}}, {})
    c, _svc, _t = client(config, db, [])                                 # create_app runs record_contaminations
    h = c.get("/api/backtest/status").get_json()["holdout"]
    assert h["status"] == "contaminated" and h["label"] == f"contaminated (seen via full run #{rid} on {iso(seen_at)}), cannot validate"
    assert record_contaminations(db) == 0                                # idempotent


def test_contamination_rows_are_append_only(db):
    db.mark_holdout_contaminated(HOLDOUT_START, "test", 1, NOW, NOW)
    for sql in ("UPDATE holdout_contaminations SET reason = 'x'", "DELETE FROM holdout_contaminations"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            with db.connect() as conn:
                conn.execute(sql)


# ------------------------------------------------------------------ validation needs forward data
def test_a_stored_validation_from_a_contaminated_period_does_not_count(config, db):
    c, svc, t = client(config, db, [True])
    run(svc, "holdout")                                                  # validated before the contamination
    cfg = c.application.config["BTC_SIGNALS"].config()
    assert current_validation(db, cfg)["validated"] is True
    db.mark_holdout_contaminated(HOLDOUT_START, "full run displayed the holdout", 99, NOW, NOW)
    v = current_validation(db, cfg)
    assert v["validated"] is False and "contaminated" in v["note"]


def test_the_no_trade_reason_says_validation_needs_forward_data(config, db):
    c, svc, t = client(config, db, [False])
    run(svc, "full")
    reasons = c.get("/api/signal").get_json()["no_trade_reasons"]
    r = next(x for x in reasons if x.startswith("Not validated"))
    assert "contaminated (seen via full run #" in r and "forward data" in r and "Forward tests" in r


def test_a_new_holdout_start_must_be_after_today_once_contaminated(config, db):
    c, svc, t = client(config, db, [False, True])
    run(svc, "full")

    def post(day):
        return c.post("/api/backtest/holdout_start", json={"start": day})

    today = (t["now"] // DAY) * DAY
    for bad in (iso(today), iso(today - DAY), iso(HOLDOUT_START + DAY)):
        r = post(bad)
        assert r.status_code == 400 and "after today" in r.get_json()["error"], bad
    r = post(iso(today + DAY))
    assert r.status_code == 200
    h = r.get_json()["holdout"]
    assert h["status"] == "untouched" and h["start"] == today + DAY
    assert h["previous"][-1]["contaminated"].startswith("seen via full run #")
    t["now"] = today + 200 * DAY                                         # forward data has accumulated
    run(svc, "holdout")
    assert c.get("/api/backtest/holdout").get_json()["runs"][0]["counts_for_validation"] is True
    assert current_validation(db, c.application.config["BTC_SIGNALS"].config())["validated"] is True


# ------------------------------------------------------------------ migration
def test_migration_from_schema_10_is_additive_with_a_backup(tmp_path):
    path = tmp_path / "v10.sqlite3"
    Database(str(path)).init()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE holdout_contaminations")
        conn.execute("UPDATE schema_meta SET value='10' WHERE key='schema_version'")
    d = Database(str(path))
    d.init()
    assert len(list(tmp_path.glob("v10.sqlite3.backup-*-schema-10"))) == 1
    assert d.holdout_contamination(HOLDOUT_START) is None
    assert code_fingerprint()                                            # unchanged import path
