"""Single-use, global holdout (0.9.3, batch 1b).

The first counting holdout run of ANY rule set spends the holdout period; later rule sets need a new holdout start
after it (forward data only). The rule-set hash includes a fingerprint of the rule code. Synthetic data and fake
runners only: the real holdout is never run.
"""
import datetime as dt
import shutil
import sqlite3
import threading

import pytest
from conftest import FIXED_NOW, make_rows
from test_app_and_db import fake_result, make_client6
from test_entry_modes import fake_replay

import services.backtesting as bt
from database import Database
from services.backtest_service import current_validation
from services.backtesting import (HOLDOUT_START, BacktestConfig, code_fingerprint, config_hash, hypothesis_hash,
                                  run_backtest)
from services.signal_engine import EngineConfig

DAY = 86_400_000


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def client(config, db, passes):
    """App + backtest service with a fake runner (results from `passes`) and a clock the test can move."""
    seen, seq = [], iter(passes)

    def runner(h, f, d, cfg, bcfg, *a, **k):
        seen.append(bcfg)
        return fake_result(next(seq), cfg)

    c, svc = make_client6(config, db, runner=runner)
    t = {"now": HOLDOUT_START + 400 * DAY}              # a year of holdout data exists
    svc.clock = lambda: t["now"]
    return c, svc, t, seen


def holdout(svc, period="holdout"):
    svc.start(years=1, download=False, background=False, period=period)


# ------------------------------------------------------------------ one counting run per holdout period, any rules
def test_first_counting_run_spends_the_holdout_for_every_rule_set(config, db):
    c, svc, t, _ = client(config, db, [True, True])
    holdout(svc)                                                          # rules A: the first counting run
    first_bt = c.get("/api/backtest/runs").get_json()["runs"][0]["id"]
    spent_on = iso(t["now"])
    t["now"] += DAY
    assert c.put("/api/signal/config", json={"min_rr": 2}).status_code == 200   # rules B: a different rule set
    holdout(svc)
    reg = c.get("/api/backtest/holdout").get_json()["runs"]
    assert [r["counts_for_validation"] for r in reg] == [False, True]
    assert reg[0]["hypothesis_hash"] != reg[1]["hypothesis_hash"]
    later = c.get(f"/api/backtest/{reg[0]['backtest_run_id']}").get_json()
    assert f"not valid for validation: holdout already spent on {spent_on}, run #{first_bt}" in later["verdict"]
    assert later["validated"] is False and later["period_badge"].startswith("HOLDOUT SPENT")
    assert db.get_setting("strategy_validation")["holdout_run_id"] == reg[1]["id"]   # still the first run's record


def test_status_reports_untouched_then_spent_on_date_by_run(config, db):
    c, svc, t, _ = client(config, db, [False])
    h = c.get("/api/backtest/status").get_json()["holdout"]
    assert h["status"] == "untouched" and h["start"] == HOLDOUT_START and h["made"] == 0
    assert h["label"] == "untouched"
    holdout(svc)                                                          # a FAILED first run spends it too
    run_id = c.get("/api/backtest/runs").get_json()["runs"][0]["id"]
    h = c.get("/api/backtest/status").get_json()["holdout"]
    assert h["status"] == "spent" and h["spent_at"] == t["now"] and h["spent_by_run"] == run_id
    assert h["label"] == f"spent on {iso(t['now'])} by run #{run_id}"
    assert h["earliest_new_start"] == (t["now"] // DAY + 1) * DAY and h["made"] == 1 and h["counted"] == 1


# ------------------------------------------------------------------ a new holdout start: only after the spent period
def test_a_new_holdout_start_must_begin_after_the_spent_period(config, db):
    c, svc, t, seen = client(config, db, [False, True, True])

    def post(start):
        return c.post("/api/backtest/holdout_start", json={"start": start})

    r = post(iso(HOLDOUT_START + 500 * DAY))
    assert r.status_code == 400 and "untouched" in r.get_json()["error"]   # nothing to replace yet
    holdout(svc)                                                          # spends the default period (and fails)
    spent_at = t["now"]
    for bad in (iso(spent_at), iso(spent_at - 30 * DAY), iso(HOLDOUT_START), "2026-13-01", "tomorrow", 12, None):
        assert post(bad).status_code == 400, bad
    new = (spent_at // DAY + 1) * DAY
    r = post(iso(new))
    assert r.status_code == 200
    h = r.get_json()["holdout"]
    assert h["start"] == new and h["status"] == "untouched" and h["previous"][-1]["start"] == HOLDOUT_START
    t["now"] = new + 200 * DAY                                            # forward data has accumulated
    holdout(svc)                                                          # same rules, NEW period: this one counts
    assert seen[-1].holdout_start == new
    reg = c.get("/api/backtest/holdout").get_json()["runs"]
    assert reg[0]["counts_for_validation"] is True and reg[0]["holdout_start"] == new
    v = db.get_setting("strategy_validation")
    assert v["validated"] is True and v["holdout_start"] == new
    holdout(svc, period="design")
    assert seen[-1].holdout_start == new                                  # design runs now end at the new start
    assert c.get("/api/backtest/status").get_json()["holdout"]["status"] == "spent"


def test_run_backtest_splits_at_the_configured_holdout_start(monkeypatch):
    monkeypatch.setattr(bt, "replay", fake_replay)
    new, end = HOLDOUT_START + 60 * DAY, HOLDOUT_START + 120 * DAY
    rows = (make_rows(4000, "1h", seed=3, end_ms=end), make_rows(1400, "4h", seed=3, end_ms=end),
            make_rows(400, "1d", seed=3, end_ms=end))
    h = run_backtest(*rows, EngineConfig(), BacktestConfig(window=150, years=None, period="holdout", random_runs=10,
                                                          holdout_start=new))
    assert h["period"]["start"] >= new and h["holdout_start"] == new
    assert h["trades"] and all(tr["entry_time"] >= new for tr in h["trades"])
    d = run_backtest(*rows, EngineConfig(), BacktestConfig(window=150, years=None, period="design", random_runs=10,
                                                          holdout_start=new))
    assert HOLDOUT_START < d["period"]["end"] < new                      # the spent period is design data now


# ------------------------------------------------------------------ code fingerprint in the rule-set hash
def test_code_fingerprint_changes_when_rule_code_changes_without_a_version_bump(tmp_path, monkeypatch):
    assert {p.name for p in bt.RULE_FILES} == {"signal_engine.py", "technical_analysis.py"}
    copies = []
    for p in bt.RULE_FILES:
        q = tmp_path / p.name
        shutil.copy(p, q)
        copies.append(q)
    monkeypatch.setattr(bt, "RULE_FILES", tuple(copies))
    fp, hyp, cfg_hash = code_fingerprint(), hypothesis_hash(EngineConfig(), BacktestConfig()), config_hash(EngineConfig())
    se = tmp_path / "signal_engine.py"
    src = se.read_bytes().replace(b"\r\n", b"\n")
    se.write_bytes(src.replace(b"\n", b"\r\n"))                         # CRLF vs LF checkout: same code
    assert code_fingerprint() == fp
    assert b"stop = entry - 2 * atr" in src
    se.write_bytes(src.replace(b"stop = entry - 2 * atr", b"stop = entry - 2.1 * atr"))   # ATR 2.0 -> 2.1, no bump
    assert code_fingerprint() != fp
    assert hypothesis_hash(EngineConfig(), BacktestConfig()) != hyp
    assert config_hash(EngineConfig()) == cfg_hash                       # the settings fingerprint alone misses it


def test_a_validation_from_other_rule_code_or_not_the_spender_does_not_count(config, db):
    c, svc, t, _ = client(config, db, [True])
    holdout(svc)
    cfg = c.application.config["BTC_SIGNALS"].config()
    assert current_validation(db, cfg)["validated"] is True
    stored = db.get_setting("strategy_validation")
    db.set_setting("strategy_validation", {**stored, "code_fingerprint": "0" * 16})
    v = current_validation(db, cfg)
    assert v["validated"] is False and v.get("stale") and "code" in v["note"]
    db.set_setting("strategy_validation", {**stored, "holdout_run_id": stored["holdout_run_id"] + 1})
    v = current_validation(db, cfg)
    assert v["validated"] is False and "spent" in v["note"]
    db.set_setting("strategy_validation", {k: x for k, x in stored.items() if k != "code_fingerprint"})  # 0.9.2 record
    assert current_validation(db, cfg)["validated"] is False
    db.set_setting("strategy_validation", stored)
    s = c.get("/api/signal").get_json()
    assert not any(r.startswith("Not validated") for r in s["no_trade_reasons"])     # the gate reads the same rule


# ------------------------------------------------------------------ database
def test_database_default_holdout_start_is_the_backtest_constant():
    from database.db import DEFAULT_HOLDOUT_START
    assert DEFAULT_HOLDOUT_START == HOLDOUT_START


def test_concurrent_holdout_runs_of_different_rules_spend_the_period_once(db):
    results, barrier = [], threading.Barrier(4)

    def register(i):
        barrier.wait()
        results.append(db.register_holdout_run(FIXED_NOW, str(i) * 16, "c" * 16, "e", {}, {}, None))
    threads = [threading.Thread(target=register, args=(i,)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(r["counts_for_validation"] for r in results) == [False, False, False, True]


def test_database_allows_one_counting_run_per_holdout_period(db):
    insert = ("INSERT INTO holdout_runs (created_at, hypothesis_hash, config_hash, engine, settings, result, "
              "counts_for_validation, holdout_start) VALUES (1, ?, 'c', 'e', '{}', '{}', 1, ?)")
    with db.connect() as conn:
        conn.execute(insert, ("a" * 16, HOLDOUT_START))
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(insert, ("b" * 16, HOLDOUT_START))
    with db.connect() as conn:                                            # another period is fine
        conn.execute(insert, ("b" * 16, HOLDOUT_START + DAY))


def test_migration_from_schema_7_keeps_rows_and_the_earliest_counting_run_spends(tmp_path):
    path = tmp_path / "v7.sqlite3"
    Database(str(path)).init()
    with sqlite3.connect(path) as conn:        # a 0.9.2 database: schema 7, holdout_runs without holdout_start
        conn.execute("DROP TABLE holdout_runs")
        conn.execute("""CREATE TABLE holdout_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL,
                        hypothesis_hash TEXT NOT NULL, config_hash TEXT NOT NULL, engine TEXT NOT NULL,
                        settings TEXT NOT NULL, result TEXT NOT NULL, backtest_run_id INTEGER,
                        counts_for_validation INTEGER NOT NULL CHECK (counts_for_validation IN (0, 1)))""")
        conn.execute("CREATE UNIQUE INDEX ux_holdout_first ON holdout_runs (hypothesis_hash) WHERE counts_for_validation = 1")
        for i, h in enumerate(("a" * 16, "b" * 16)):   # 0.9.2 allowed one counting run PER RULE SET in a period
            conn.execute("INSERT INTO holdout_runs (created_at, hypothesis_hash, config_hash, engine, settings, result, "
                         "backtest_run_id, counts_for_validation) VALUES (?,?,?,?,?,?,?,1)",
                         (1000 + i, h, "c", "e", "{}", "{}", 10 + i))
        conn.execute("UPDATE schema_meta SET value='7' WHERE key='schema_version'")
    d = Database(str(path))
    d.init()                                                              # must not fail on the old duplicates
    assert len(list(tmp_path.glob("v7.sqlite3.backup-*-schema-7"))) == 1
    rows = d.holdout_runs()
    assert len(rows) == 2 and {r["holdout_start"] for r in rows} == {HOLDOUT_START}
    st = d.holdout_state(HOLDOUT_START)
    assert st["status"] == "spent" and st["spent_by_holdout_run"] == min(r["id"] for r in rows) and st["spent_by_run"] == 10
    assert d.register_holdout_run(5000, "c" * 16, "c", "e", {}, {}, None)["counts_for_validation"] is False
