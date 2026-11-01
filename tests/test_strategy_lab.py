"""Strategy Lab (round 1): five strategy slots, DCA implemented, versioned configs, append-only records, API."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from conftest import FakeProvider
from test_app_and_db import make_client

ROOT = Path(__file__).resolve().parents[1]


def test_schema_14_strategy_tables_are_append_only(db):
    from database.db import SCHEMA_VERSION
    assert SCHEMA_VERSION == 14
    db.strategy_config_add("dca", {"amount_usd": 100.0}, "h1", "pre-registered defaults", now=1)
    db.strategy_signal_add("dca", 1, "h1", day=0, signal="WAIT", result={}, now=1)
    db.strategy_backtest_add("dca", 1, "h1", "design", "abc", {"verdict": "PASS"}, now=1)
    with pytest.raises(sqlite3.IntegrityError):
        db.strategy_backtest_add("dca", 1, "h1", "design", "abc", {"verdict": "FAIL"}, now=2)   # run once
    with pytest.raises(sqlite3.IntegrityError):
        db.strategy_backtest_add("dca", 1, "h2", "full", "abc", {}, now=2)                      # design only
    for table in ("strategy_configs", "strategy_signals", "strategy_backtests"):
        for sql in (f"UPDATE {table} SET created_at = 9", f"DELETE FROM {table}"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                with db.connect() as conn:
                    conn.execute(sql)


def test_preregistration_file_holds_the_default_config_hash():
    from services.strategies import dca
    from services.strategies.base import config_hash
    text = (ROOT / "PREREGISTRATION_DCA.md").read_text(encoding="utf-8")
    assert f"Config hash: `{config_hash(dca.DEFAULTS)}`" in text
    for k, v in dca.DEFAULTS.items():
        assert f"`{k}` | `{v}`" in text


def test_api_lists_five_strategies_dca_live_the_rest_planned(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    d = c.get("/api/strategies").get_json()
    assert [s["id"] for s in d["strategies"]] == ["dca", "trend", "range", "breakout", "rotation"]
    dca = d["strategies"][0]
    assert dca["implemented"] is True and dca["result"]["signal"] in ("ACCUMULATE", "WAIT", "PAUSE")
    assert dca["result"]["schema"] == "strategy-result-1" and dca["config"]["version"] == 1
    assert dca["validation"]["status"] == "NOT VALIDATED" and "SIMULATED" in dca["result"]["labels"]
    for s in d["strategies"][1:]:
        assert s["implemented"] is False and s["result"] is None and s["planned_round"] in (2, 3, 4, 5)
    cmp = d["comparison"]
    assert [r["metric"] for r in cmp["rows"]][:3] == ["Net return", "Win rate", "Profit factor"]
    assert all(r["values"]["trend"] == "N/A" for r in cmp["rows"])
    assert "not ranked" in cmp["note"]
    assert c.get("/api/strategies/nope").status_code == 404


def test_changing_settings_makes_a_new_version_marked_not_validated_and_never_touches_signal_1_8(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    before = c.get("/api/signal/config").get_json()["config"]
    db.strategy_backtest_add("dca", 1, c.get("/api/strategies/dca/config").get_json()["hash"], "design", "abc",
                             {"verdict": "PASS", "metrics": {"net_return_pct": 10.0}}, now=1)
    assert c.get("/api/strategies/dca").get_json()["validation"]["status"] == "BACKTESTED (design data)"
    assert c.put("/api/strategies/dca/config", json={"amount_usd": -5}).status_code == 400
    assert c.put("/api/strategies/dca/config", json={"weights": {"trend": 1}}).status_code == 400
    r = c.put("/api/strategies/dca/config", json={"interval": "monthly"}).get_json()
    assert r["version"] == 2 and r["config"]["interval"] == "monthly" and r["settings_changed"] is True
    d = c.get("/api/strategies/dca").get_json()
    assert d["validation"]["status"] == "NOT VALIDATED (settings changed)"
    assert d["backtest"] is None                                      # never inherits the old result
    assert [v["version"] for v in d["config_history"]] == [2, 1]
    back = c.post("/api/strategies/dca/config/reset").get_json()
    assert back["version"] == 3 and back["settings_changed"] is False
    assert c.get("/api/strategies/dca").get_json()["validation"]["status"] == "BACKTESTED (design data)"
    assert c.get("/api/signal/config").get_json()["config"] == before  # signal-1.8 settings untouched
    from services.signal_engine import ENGINE_VERSION
    assert ENGINE_VERSION == "signal-1.8"


def test_monitor_records_one_signal_per_day_and_starts_the_strategy_forward_tracker(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    mon = c.application.config["BTC_MONITOR"]
    mon.run_once()
    mon.run_once()
    hist = c.get("/api/strategies/dca").get_json()["history"]
    assert len(hist) == 1 and hist[0]["signal"] in ("ACCUMULATE", "WAIT", "PAUSE")
    fwd = c.get("/api/strategies/dca").get_json()["forward"]
    assert fwd["state"] in ("waiting", "running") and fwd["fingerprint_now"]
    with db.connect() as conn:                                        # the H1/H2 tracker is separate
        assert conn.execute("SELECT COUNT(*) FROM strategy_forward_tests").fetchone()[0] == 1


def test_comparison_shows_backtest_numbers_only_when_they_exist(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    h = c.get("/api/strategies/dca/config").get_json()["hash"]
    db.strategy_backtest_add("dca", 1, h, "design", "abc", {"verdict": "FAIL", "metrics": {
        "net_return_pct": 12.5, "win_rate_pct": None, "profit_factor": None, "max_drawdown_pct": 20.1, "trades": 52,
        "avg_reward_risk": None, "costs_usd": 7.8, "buy_hold_return_pct": 30.0}}, now=1)
    rows = {r["metric"]: r["values"]["dca"] for r in c.get("/api/strategies").get_json()["comparison"]["rows"]}
    assert rows["Net return"] == "+12.50%" and rows["Win rate"] == "N/A" and rows["Number of trades"] == "52"
    assert rows["Maximum drawdown"] == "20.10%" and rows["Validation status"] == "BACKTESTED (design data)"
