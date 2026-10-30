"""0.9.4: the holdout verdict is stored at run time and never recalculated; backtesting.py is part of the rule code.

Synthetic data and fake runners only: the real holdout is never run.
"""
import shutil
import sqlite3

import pytest
from test_app_and_db import fake_result, make_client6

import services.backtesting as bt
from database import Database
from services.backtest_service import current_validation
from services.backtesting import HOLDOUT_START, code_fingerprint

DAY = 86_400_000
CRITERIA = [{"name": "Enough trades", "passed": False, "detail": "9 trades (need >= 15)"},
            {"name": "Profit factor", "passed": True, "detail": "1.41 (need >= 1.2)"}]
THRESHOLDS = {"min_trades": 15, "min_profit_factor": 1.2}


def client(config, db, passes):
    """Fake runner with real-looking criteria (values + thresholds), and a clock inside the holdout."""
    seq = iter(passes)

    def runner(h, f, d, cfg, bcfg, *a, **k):
        ok = next(seq)
        r = fake_result(ok, cfg)
        r["criteria"] = [dict(c, passed=True) if ok else dict(c) for c in CRITERIA]
        r["settings"] = {"criteria": dict(THRESHOLDS)}
        return r

    c, svc = make_client6(config, db, runner=runner)
    svc.clock = lambda: HOLDOUT_START + 400 * DAY
    return c, svc


def holdout(svc):
    svc.start(years=1, download=False, background=False, period="holdout")


def test_holdout_verdict_and_criteria_values_are_stored_at_run_time(config, db):
    c, svc = client(config, db, [False])
    holdout(svc)
    run = c.get("/api/backtest/holdout").get_json()["runs"][0]
    assert run["verdict"] == "FAIL"
    assert run["criteria"] == {"thresholds": THRESHOLDS, "results": CRITERIA}
    v = db.get_setting("strategy_validation")
    assert v["verdict"] == "FAIL" and v["criteria"] == run["criteria"]
    assert c.get("/api/backtest/status").get_json()["holdout"]["verdict"] == "FAIL"


def test_a_spent_fail_can_never_become_a_pass(config, db):
    """Regression: the gate trusted the 'validated' flag in a settings record. A later re-evaluation (e.g. with relaxed
    pass criteria) that rewrote that record would have lifted NO TRADE on a holdout that FAILED at run time."""
    c, svc = client(config, db, [False])
    holdout(svc)
    cfg = c.application.config["BTC_SIGNALS"].config()
    stored = db.get_setting("strategy_validation")
    db.set_setting("strategy_validation", {**stored, "validated": True, "verdict": "PASS", "failed": []})
    v = current_validation(db, cfg)
    assert v["validated"] is False and "FAIL" in v["note"]
    s = c.get("/api/signal").get_json()
    assert s["signal"] == "NO TRADE" and "not_validated" in s["diagnostics"]["gate_keys"]


def test_a_pass_recorded_at_run_time_still_counts(config, db):
    c, svc = client(config, db, [True])
    holdout(svc)
    cfg = c.application.config["BTC_SIGNALS"].config()
    assert c.get("/api/backtest/holdout").get_json()["runs"][0]["verdict"] == "PASS"
    assert current_validation(db, cfg)["validated"] is True


def test_holdout_registry_rows_are_write_once(db):
    r = db.register_holdout_run(1, "h" * 16, "c", "e", {}, {}, None, verdict="FAIL",
                                criteria={"thresholds": THRESHOLDS, "results": CRITERIA})
    for sql in ("UPDATE holdout_runs SET verdict='PASS' WHERE id=?",
                "UPDATE holdout_runs SET criteria='{}' WHERE id=?",
                "UPDATE holdout_runs SET result='{}' WHERE id=?",
                "UPDATE holdout_runs SET counts_for_validation=0 WHERE id=?",
                "UPDATE holdout_runs SET holdout_start=holdout_start+1 WHERE id=?",
                "DELETE FROM holdout_runs WHERE id=?"):
        with pytest.raises(sqlite3.DatabaseError, match="write-once"):
            with db.connect() as conn:
                conn.execute(sql, (r["id"],))
    db.link_holdout_run(r["id"], 5)                                   # linking the stored backtest run is allowed
    row = db.holdout_runs()[0]
    assert row["verdict"] == "FAIL" and row["backtest_run_id"] == 5 and row["counts_for_validation"] is True
    with pytest.raises(sqlite3.DatabaseError):
        db.register_holdout_run(2, "i" * 16, "c", "e", {}, {}, None, verdict="MAYBE", criteria={})


def test_code_fingerprint_includes_backtesting_py(tmp_path, monkeypatch):
    assert {p.name for p in bt.RULE_FILES} == {"signal_engine.py", "technical_analysis.py", "backtesting.py"}
    copies = []
    for p in bt.RULE_FILES:
        q = tmp_path / p.name
        shutil.copy(p, q)
        copies.append(q)
    monkeypatch.setattr(bt, "RULE_FILES", tuple(copies))
    fp = code_fingerprint()
    src = (tmp_path / "backtesting.py").read_bytes()
    assert b"min_trades: int = 30" in src
    (tmp_path / "backtesting.py").write_bytes(src.replace(b"min_trades: int = 30", b"min_trades: int = 10"))
    assert code_fingerprint() != fp                                   # relaxing the criteria in code = other rules


def test_migration_from_schema_8_adds_verdict_columns_and_keeps_rows(tmp_path):
    path = tmp_path / "v8.sqlite3"
    d = Database(str(path))
    d.init()
    with sqlite3.connect(path) as conn:          # a 0.9.3 database: schema 8, no verdict/criteria, no triggers
        conn.execute("DROP TRIGGER IF EXISTS holdout_runs_no_update")
        conn.execute("DROP TRIGGER IF EXISTS holdout_runs_no_delete")
        conn.execute("ALTER TABLE holdout_runs DROP COLUMN verdict")
        conn.execute("ALTER TABLE holdout_runs DROP COLUMN criteria")
        conn.execute("INSERT INTO holdout_runs (created_at, hypothesis_hash, config_hash, engine, settings, result, "
                     "counts_for_validation) VALUES (1, ?, 'c', 'e', '{}', ?, 1)",
                     ("h" * 16, '{"all_criteria_passed": false}'))
        conn.execute("UPDATE schema_meta SET value='8' WHERE key='schema_version'")
    Database(str(path)).init()
    assert len(list(tmp_path.glob("v8.sqlite3.backup-*-schema-8"))) == 1
    row = Database(str(path)).holdout_runs()[0]
    assert row["verdict"] == "FAIL"                  # the run-time outcome stored in its result, not recalculated
    with pytest.raises(sqlite3.DatabaseError, match="write-once"):
        with Database(str(path)).connect() as conn:
            conn.execute("UPDATE holdout_runs SET result='{}' WHERE id=?", (row["id"],))
