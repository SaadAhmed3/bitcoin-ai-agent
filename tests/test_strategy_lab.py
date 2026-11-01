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
    assert SCHEMA_VERSION >= 14              # 15: strategy_forward_rebases
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
    assert [s["id"] for s in d["strategies"]] == ["dca", "trend", "range", "breakout", "breakout_05r", "rotation"]
    dca = d["strategies"][0]
    assert dca["implemented"] is True and dca["result"]["signal"] in ("ACCUMULATE", "WAIT", "PAUSE")
    assert dca["result"]["schema"] == "strategy-result-1" and dca["config"]["version"] == 1
    assert dca["validation"]["status"] == "NOT VALIDATED" and "SIMULATED" in dca["result"]["labels"]
    trend = d["strategies"][1]                                           # round 2
    assert trend["implemented"] is True and trend["result"]["signal"] in (
        "BUY SETUP", "HOLD", "NO TRADE", "EXIT / REDUCE EXPOSURE") and trend["result"]["timeframe"] == "4h"
    rng = d["strategies"][2]                                             # round 3
    assert rng["implemented"] is True and rng["result"]["timeframe"] == "4h" and rng["result"]["regime"]
    bo = d["strategies"][3]                                              # round 4
    assert bo["implemented"] is True and bo["result"]["timeframe"] == "4h" and bo["result"]["targets"] == []
    var = d["strategies"][4]                                             # forward-only variant
    assert var["implemented"] is True and var["backtest"] is None and var["forward_only"] is True
    assert "chosen after design results: forward test only, no design verdict" in var["validation"]["label"].lower()
    assert var["result"]["design_warning"].lower() == "chosen after design results: forward test only, no design verdict"
    rot = d["strategies"][5]
    assert rot["implemented"] is False and rot["result"] is None and rot["planned_round"] == 5
    cmp = d["comparison"]
    assert [r["metric"] for r in cmp["rows"]][:3] == ["Net return", "Win rate", "Profit factor"]
    assert all(r["values"]["rotation"] == "N/A" for r in cmp["rows"])
    assert all(r["values"]["breakout_05r"] == "N/A" for r in cmp["rows"][:-1])        # no design numbers, ever
    assert "no design verdict" in cmp["rows"][-1]["values"]["breakout_05r"]
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
    assert r["order"] == ["amount_usd", "interval", "weekday", "month_day", "fee_pct", "slippage_pct", "horizon_days"]
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
        assert [r[0] for r in conn.execute("SELECT strategy_id FROM strategy_forward_tests ORDER BY id")] == ["dca", "trend", "range", "breakout", "breakout_05r"]


def test_comparison_shows_backtest_numbers_only_when_they_exist(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    h = c.get("/api/strategies/dca/config").get_json()["hash"]
    db.strategy_backtest_add("dca", 1, h, "design", "abc", {"verdict": "FAIL", "metrics": {
        "net_return_pct": 12.5, "win_rate_pct": None, "profit_factor": None, "max_drawdown_pct": 20.1, "trades": 52,
        "avg_reward_risk": None, "costs_usd": 7.8, "buy_hold_return_pct": 30.0}}, now=1)
    rows = {r["metric"]: r["values"]["dca"] for r in c.get("/api/strategies").get_json()["comparison"]["rows"]}
    assert rows["Net return"] == "+12.50%" and rows["Win rate"] == "N/A" and rows["Number of trades"] == "52"
    assert rows["Maximum drawdown"] == "20.10%" and rows["Validation status"] == "FAILED on design data · not validated"
    assert rows["Buy-and-hold comparison"] == "+30.00% (lump sum, same budget)"                 # DCA's basis


def test_trend_backtest_numbers_show_in_the_comparison(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    h = c.get("/api/strategies/trend/config").get_json()["hash"]
    db.strategy_backtest_add("trend", 1, h, "design", "abc", {"verdict": "INCONCLUSIVE", "metrics": {
        "net_return_pct": -3.2, "win_rate_pct": 41.0, "profit_factor": 0.9, "max_drawdown_pct": 6.5, "trades": 12,
        "avg_reward_risk": 2.0, "costs_usd": None, "costs_pct": 1.25, "buy_hold_return_pct": 80.0}}, now=1)
    rows = {r["metric"]: r["values"]["trend"] for r in c.get("/api/strategies").get_json()["comparison"]["rows"]}
    assert rows["Net return"] == "-3.20%" and rows["Profit factor"] == "0.90" and rows["Number of trades"] == "12"
    assert rows["Fees and slippage"] == "1.25% of the account"
    assert rows["Validation status"] == "INCONCLUSIVE on design data · not validated"
    assert rows["Buy-and-hold comparison"] == "+80.00% (buy & hold, same window)"              # trend's basis


def test_status_labels_carry_the_design_verdict():
    from services.strategies.base import validation_status
    lab = lambda v, fw=None: validation_status("h", "h", {"verdict": v} if v else None, fw)["label"]  # noqa: E731
    assert lab("PASS") == "PASSED on design data · not validated"
    assert lab("FAIL") == "FAILED on design data · not validated"
    assert lab("INCONCLUSIVE") == "INCONCLUSIVE on design data · not validated"
    assert lab(None) == "Not validated"
    fw = {"state": "running", "days_elapsed": 9, "target_days": 365, "verdict": None, "code_changed": False}
    assert lab("FAIL", fw) == "FAILED on design data · forward testing, day 9 of 365 · not validated"
    assert validation_status("h", "x", {"verdict": "PASS"}, None)["label"] == "Settings changed · not validated"
    assert validation_status("h", "h", {"verdict": "FAIL"}, None)["design_verdict"] == "FAIL"


def test_every_live_result_of_a_failed_strategy_says_research_only(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    h = c.get("/api/strategies/trend/config").get_json()["hash"]
    card = next(s for s in c.get("/api/strategies").get_json()["strategies"] if s["id"] == "trend")
    assert card["result"].get("design_warning") is None                      # no backtest yet: no claim either way
    db.strategy_backtest_add("trend", 1, h, "design", "abc", {"verdict": "FAIL", "metrics": {"trades": 94}}, now=1)
    card = next(s for s in c.get("/api/strategies").get_json()["strategies"] if s["id"] == "trend")
    assert card["result"]["design_warning"] == "Failed on design data: research only"
    assert card["validation"]["label"] == "FAILED on design data · not validated"
    c.application.config["BTC_MONITOR"].run_once()                            # the stored daily record says it too
    rec = db.strategy_signals("trend", 1)[0]["result"]
    assert rec["design_warning"] == "Failed on design data: research only"
