"""Holdout-only validation and the holdout run registry (0.9.2, fix g).

Every test here uses synthetic candles or a fake runner. The real holdout (Binance data from HOLDOUT_START)
is never loaded or run.
"""
import sqlite3
import threading
from dataclasses import replace

import pytest
from conftest import FIXED_NOW
from test_app_and_db import fake_result, make_client6
from test_entry_modes import fake_replay, period_rows

import services.backtesting as bt
from database import Database
from services.backtest_service import effective_validation
from services.backtesting import BacktestConfig, config_hash, hypothesis_hash, run_backtest
from services.signal_engine import EngineConfig


def _all_pass(monkeypatch):
    real = bt.evaluate_criteria

    def passing(*a, **k):
        return [{**c, "passed": True} for c in real(*a, **k)]
    monkeypatch.setattr(bt, "evaluate_criteria", passing)


# ------------------------------------------------------------------ run_backtest: only the holdout can validate
def test_full_period_never_validates_even_when_every_criterion_passes(monkeypatch):
    monkeypatch.setattr(bt, "replay", fake_replay)
    _all_pass(monkeypatch)
    c1h, c4h, c1d = period_rows()
    full = run_backtest(c1h, c4h, c1d, EngineConfig(), BacktestConfig(window=150, years=None, random_runs=10))
    assert all(c["passed"] for c in full["criteria"])
    assert full["validated"] is False
    assert full["period_badge"].startswith("FULL PERIOD") and full["verdict"].startswith("FULL PERIOD")
    hold = run_backtest(c1h, c4h, c1d, EngineConfig(),
                        BacktestConfig(window=150, years=None, period="holdout", random_runs=10))
    assert hold["validated"] is True
    assert hold["hypothesis_hash"] == hypothesis_hash(EngineConfig(), BacktestConfig(window=150))   # window is a rule


def test_hypothesis_hash_covers_the_rules_but_not_retry_knobs():
    base = hypothesis_hash(EngineConfig(), BacktestConfig())
    # Changing what is being tested is a new hypothesis ...
    assert hypothesis_hash(EngineConfig(min_rr=2.0), BacktestConfig()) != base
    assert hypothesis_hash(EngineConfig(entry_mode="pullback"), BacktestConfig()) != base
    assert hypothesis_hash(EngineConfig(), BacktestConfig(max_hold_bars=12)) != base
    # ... but re-running with another length, seed, window split or pass mark is NOT.
    for knob in (dict(years=2.0), dict(seed=99), dict(random_runs=50), dict(test_window_days=90),
                 dict(train_window_days=200), dict(criteria=replace(BacktestConfig().criteria, min_trades=1))):
        assert hypothesis_hash(EngineConfig(), BacktestConfig(**knob)) == base, knob
    assert len(base) == 16


# ------------------------------------------------------------------ the registry (service level, fake runner)
def _runner(results):
    """Fake runner: returns the next passed/failed result for whatever period is requested."""
    seq = iter(results)

    def run(h, f, d, cfg, bcfg, *a, **k):
        out = fake_result(next(seq), cfg)
        out.update({"period_kind": bcfg.period, "hypothesis_hash": hypothesis_hash(cfg, bcfg)})
        if bcfg.period != "holdout":
            out["validated"] = False                   # what run_backtest itself now does
        return out
    return run


def test_first_holdout_run_decides_and_repeats_are_stored_but_never_count(config, db):
    c, svc = make_client6(config, db, runner=_runner([True, True, False]))
    for _ in range(3):
        svc.start(years=1, download=False, background=False, period="holdout")
    reg = c.get("/api/backtest/holdout").get_json()["runs"]
    assert [r["counts_for_validation"] for r in reg] == [False, False, True]          # newest first
    assert len({r["hypothesis_hash"] for r in reg}) == 1
    first = reg[-1]
    assert first["engine"] and first["settings"]["engine"]["min_rr"] == 1.5 and first["created_at"] == FIXED_NOW
    assert first["result"]["criteria_passed"] == 1 and first["backtest_run_id"]
    v = db.get_setting("strategy_validation")
    assert v["validated"] is True and v["holdout_run_id"] == first["id"] and v["counts_for_validation"] is True
    later = c.get(f"/api/backtest/{reg[0]['backtest_run_id']}").get_json()
    assert later["validated"] is False and "not valid for validation" in later["verdict"]
    assert later["holdout"]["counts_for_validation"] is False and later["holdout"]["first_run_id"] == first["id"]


def test_a_failed_first_holdout_cannot_be_retried_into_a_pass(config, db):
    c, svc = make_client6(config, db, runner=_runner([False, True]))
    svc.start(years=1, download=False, background=False, period="holdout")
    svc.start(years=3, download=False, background=False, period="holdout")      # other length: same hypothesis
    assert db.get_setting("strategy_validation")["validated"] is False
    s = c.get("/api/signal").get_json()
    assert s["signal"] == "NO TRADE" and "not_validated" in s["diagnostics"]["gate_keys"]


def test_new_settings_are_a_new_hypothesis_with_their_own_first_run(config, db):
    c, svc = make_client6(config, db, runner=_runner([False, True]))
    svc.start(years=1, download=False, background=False, period="holdout")
    assert c.put("/api/signal/config", json={"min_rr": 2}).status_code == 200
    svc.start(years=1, download=False, background=False, period="holdout")
    reg = c.get("/api/backtest/holdout").get_json()["runs"]
    assert [r["counts_for_validation"] for r in reg] == [True, True] and reg[0]["hypothesis_hash"] != reg[1]["hypothesis_hash"]
    assert db.get_setting("strategy_validation")["validated"] is True


def test_full_and_design_runs_never_touch_the_validation_or_the_registry(config, db):
    c, svc = make_client6(config, db, runner=_runner([True, True]))
    svc.start(years=1, download=False, background=False, period="full")
    svc.start(years=1, download=False, background=False, period="design")
    assert db.get_setting("strategy_validation") is None
    assert c.get("/api/backtest/holdout").get_json()["runs"] == []
    assert all(r["validated"] is False for r in c.get("/api/backtest/runs").get_json()["runs"])
    s = c.get("/api/signal").get_json()
    assert s["signal"] == "NO TRADE" and "not_validated" in s["diagnostics"]["gate_keys"]


def test_runs_stored_before_0_9_2_get_their_period_badge_from_the_api(config, db):
    """A 0.9.1 payload has no period_badge: the API derives it (a missing period_kind is a pre-6.2 full run)."""
    c, _ = make_client6(config, db)
    for kind in ("full", None, "design", "holdout"):
        payload = {**fake_result(False, EngineConfig()), "period": {"years": 1}}
        if kind:
            payload["period_kind"] = kind
        rid = db.save_backtest(FIXED_NOW, payload["engine"], payload["config_hash"], False, {}, payload)
        badge = c.get(f"/api/backtest/{rid}").get_json().get("period_badge")
        expected = {"full": "FULL PERIOD", None: "FULL PERIOD", "design": "DESIGN PERIOD", "holdout": None}[kind]
        if expected is None:
            assert badge is None, (kind, badge)
        else:
            assert badge and badge.startswith(expected), (kind, badge)
    assert c.get("/api/backtest/latest").get_json()["period_badge"] is None          # the last one is a holdout


def test_signal_reason_for_a_full_period_record_says_it_cannot_validate(config, db):
    from services.signal_engine import validation_reason
    full = {"validated": False, "run_id": 1, "engine": "signal-1.7", "period_kind": "full", "entry_mode": "standard",
            "failed": ["Enough trades (6 trades (need >= 30))"]}
    expected = ("Not validated: no holdout run yet. Run #1 is a full-period run (it includes design data) "
                "and cannot validate.")
    assert validation_reason(full) == expected
    assert validation_reason({"validated": False, "run_id": 1, "engine": "signal-1.5"}) == expected   # pre-6.2 record
    c, _ = make_client6(config, db)
    db.set_setting("strategy_validation", {**full, "config_hash": config_hash(c.application.config["BTC_SIGNALS"].config())})
    reasons = c.get("/api/signal").get_json()["no_trade_reasons"]
    assert expected in reasons and not any("validation backtest" in r for r in reasons)


# ------------------------------------------------------------------ effective_validation: only registry records lift
@pytest.mark.parametrize("record", [
    {"validated": True, "period_kind": "full"},                                     # a full run (old versions)
    {"validated": True},                                                            # pre-6.2 record, no period
    {"validated": True, "period_kind": "holdout"},                                  # holdout without the registry
    {"validated": True, "period_kind": "holdout", "counts_for_validation": False},   # a repeat
])
def test_only_a_first_holdout_registry_record_can_lift_the_gate(record):
    cfg = EngineConfig()
    v = effective_validation({**record, "config_hash": config_hash(cfg), "as_of": FIXED_NOW}, cfg)
    assert v["validated"] is False and "holdout" in v["note"]
    ok = effective_validation({"validated": True, "period_kind": "holdout", "counts_for_validation": True,
                               "config_hash": config_hash(cfg), "as_of": FIXED_NOW}, cfg)
    assert ok["validated"] is True and ok["observation_until"] > FIXED_NOW


# ------------------------------------------------------------------ database: atomic first run, additive migration
def test_only_one_counting_run_per_hypothesis_even_when_registered_concurrently(db):
    results, barrier = [], threading.Barrier(4)

    def register():
        barrier.wait()
        results.append(db.register_holdout_run(FIXED_NOW, "h" * 16, "c" * 16, "signal-x", {"s": 1}, {"r": 1}, None))
    threads = [threading.Thread(target=register) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["counts_for_validation"] for r in results) == [False, False, False, True]
    first = next(r for r in results if r["counts_for_validation"])
    assert all(r["first_run_id"] == first["id"] for r in results)


def test_migration_is_additive_keeps_runs_and_backs_up_first(tmp_path):
    path = tmp_path / "old.sqlite3"
    Database(str(path)).init()
    with sqlite3.connect(path) as conn:                    # make it look like a 0.9.1 (schema 6) database
        conn.execute("DROP TABLE holdout_runs")
        conn.execute("UPDATE schema_meta SET value='6' WHERE key='schema_version'")
        conn.execute("INSERT INTO backtest_runs (created_at, engine, config_hash, validated, summary, payload) "
                     "VALUES (1, 'signal-1.7', 'abc', 0, '{}', '{}')")
    Database(str(path)).init()
    backups = list(tmp_path.glob("old.sqlite3.backup-*-schema-6"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:              # the backup is the untouched old database
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "6"
        assert conn.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM holdout_runs").fetchone()[0] == 0
        assert int(conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]) > 6
    Database(str(path)).init()                              # no further backup when nothing changes
    assert len(list(tmp_path.glob("old.sqlite3.backup-*"))) == 1


def test_a_new_database_is_not_backed_up(tmp_path):
    Database(str(tmp_path / "new.sqlite3")).init()
    assert not list(tmp_path.glob("*.backup-*"))
