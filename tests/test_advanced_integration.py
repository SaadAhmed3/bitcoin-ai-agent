"""Advanced analysis (regime, sweeps, volume profile): display + SHADOW mode only.

The live signal stays signal-1.8 and its score, weights, thresholds and NO TRADE gates are untouched; the rule code
(rule-set hash) and the forward tracker code (fingerprint) are not modified.
"""
from __future__ import annotations

import sqlite3

import pytest

from conftest import FIXED_NOW, FakeProvider, local_client  # noqa: F401
from test_app_and_db import make_client

RULE_CODE_FINGERPRINT = "404e15bcd900001e"     # services.backtesting.code_fingerprint() on main before this feature
FORWARD_FINGERPRINT = "5e4b8a0ebba56ae03631b37acc114ae63c1cb8d6a1ab69cc32f1f5f6e5b0a450"
LIVE_KEYS = ("signal", "setup", "score", "engine", "confidence")


def names(sig: dict) -> set:
    return {c["name"] for c in sig.get("components") or []}


def live(sig: dict) -> dict:
    return {k: sig.get(k) for k in LIVE_KEYS} | {"components": [(c["name"], c["score"], c["weight"])
                                                                for c in sig.get("components") or []]}


def test_protected_code_is_unchanged():
    from services.backtesting import code_fingerprint
    from services.forward_tests import current_fingerprint
    from services.signal_engine import DEFAULT_WEIGHTS, ENGINE_VERSION
    assert ENGINE_VERSION == "signal-1.8"
    assert code_fingerprint() == RULE_CODE_FINGERPRINT
    assert current_fingerprint() == FORWARD_FINGERPRINT
    assert not {"regime", "liquidity_sweep", "volume_profile"} & set(DEFAULT_WEIGHTS)


def test_schema_12_tables_are_append_only(db):
    from database.db import SCHEMA_VERSION
    assert SCHEMA_VERSION >= 12              # 13: sweep_corrections (0.9.8 follow-up)
    db.regime_add_change({"timeframe": "4h", "at": 10, "regime": "TRENDING BULLISH", "previous": None,
                          "confidence": 80, "detail": {}}, now=1)
    db.shadow_add({"close_time": 10, "regime": "TRENDING BULLISH", "regime_confidence": 80, "regime_score": 0.8,
                   "sweep_score": 0.0, "vp_score": 0.1, "poc": 1.0, "vah": 2.0, "val": 0.5, "live_signal": "NO TRADE",
                   "live_setup": "HOLD", "live_score": 0.05, "live_engine": "signal-1.8", "weights": {},
                   "combined_score": 0.05, "detail": {}}, now=1)
    db.sweep_add({"key": "4h:buy_side:1", "timeframe": "4h", "direction": "buy_side", "level": 2.0, "equal_levels": 1,
                  "swing_time": 0, "event_time": 1, "sweep_extreme": 2.1, "reclaim_price": 1.9},
                 [{"status": "POTENTIAL SWEEP", "at": 1, "strength": 50.0, "factors": {}, "note": ""}], now=1)
    for table in ("regime_changes", "shadow_context", "sweep_events", "sweep_updates"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            with db.connect() as conn:
                conn.execute(f"UPDATE {table} SET created_at = 5")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            with db.connect() as conn:
                conn.execute(f"DELETE FROM {table}")


def test_shadow_record_stores_one_row_per_closed_4h_candle_and_never_moves_the_live_signal(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    before = c.get("/api/signal").get_json()
    adv = c.application.config["BTC_ADVANCED"]
    assert adv.record() is True
    assert adv.record() is False                        # same closed 4h candle: nothing new
    rows = db.shadow_rows(10)
    assert len(rows) == 1
    r = rows[0]
    for k in ("regime_score", "sweep_score", "vp_score", "combined_score"):
        assert -1 <= r[k] <= 1
    w = r["weights"]
    expected = (1 - sum(w.values())) * r["live_score"] + w["regime"] * r["regime_score"] \
        + w["liquidity_sweep"] * r["sweep_score"] + w["volume_profile"] * r["vp_score"]
    assert r["combined_score"] == pytest.approx(expected, abs=1e-9)
    assert r["live_engine"] == "signal-1.8"
    after = c.get("/api/signal").get_json()
    assert live(after) == live(before)                  # score, setup, signal (incl. NO TRADE) unchanged
    assert not {"regime_score", "liquidity_sweep_score", "volume_profile_score", "regime"} & names(after)
    assert len(db.regime_changes(10)) == 1              # the first regime seen is recorded once


def test_the_monitor_records_shadow_rows(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    c.application.config["BTC_MONITOR"].run_once()
    assert len(db.shadow_rows(10)) == 1


def test_api_advanced_reports_three_panels_as_context(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    d = c.get("/api/advanced").get_json()
    assert d["note"] == "Context, not part of the score"
    assert d["regime"]["status"] == "ok" and d["regime"]["regime"] in (
        "TRENDING BULLISH", "TRENDING BEARISH", "SIDEWAYS / RANGING", "HIGH VOLATILITY", "LOW VOLATILITY",
        "TRANSITIONAL / UNCERTAIN")
    assert 0 <= d["regime"]["confidence"] <= 100 and "previous" in d["regime"] and "changed_at" in d["regime"]
    assert d["profile"]["timeframe"] == "4h" and d["profile"]["candles_used"] == 200
    assert d["profile"]["val"] <= d["profile"]["poc"] <= d["profile"]["vah"]
    assert "latest" in d["sweeps"] and isinstance(d["sweeps"]["recent"], list)
    assert d["shadow"]["mode"] == "shadow" and d["shadow"]["affects_live_signal"] is False
    one_h = c.get("/api/advanced?profile_tf=1h").get_json()["profile"]
    assert one_h["timeframe"] == "1h" and one_h["candles_used"] == 240
    assert c.get("/api/advanced?profile_tf=15m").status_code == 400


def test_api_advanced_survives_a_data_outage(config, db):
    c = make_client(config, db, [FakeProvider("fake1", fail=True)])
    r = c.get("/api/advanced")
    assert r.status_code == 200
    d = r.get_json()
    assert d["regime"]["status"] == "no_data" and d["profile"]["status"] == "no_data"
    assert c.application.config["BTC_ADVANCED"].record() is False      # nothing stored without closed candles


def test_settings_section_shadow_weights_and_switch(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    s = c.get("/api/settings").get_json()["advanced"]
    assert s["shadow_enabled"]["value"] is True
    assert [s[k]["value"] for k in ("weight_regime", "weight_liquidity_sweep", "weight_volume_profile")] == [0.1] * 3
    assert c.put("/api/settings/advanced", json={"weight_regime": 0.6}).status_code == 400
    assert c.put("/api/settings/advanced", json={"shadow_enabled": False}).status_code == 200
    adv = c.application.config["BTC_ADVANCED"]
    assert adv.record() is False and db.shadow_rows(10) == []
    assert c.get("/api/advanced").get_json()["shadow"]["enabled"] is False
    # the live engine's own config has no such fields
    cfg = c.get("/api/signal/config").get_json()["config"]
    assert not {"shadow_enabled", "weight_regime"} & set(cfg) and "regime" not in cfg["weights"]


def test_shadow_history_endpoint(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    c.application.config["BTC_ADVANCED"].record()
    h = c.get("/api/advanced/shadow?limit=5").get_json()
    assert len(h["rows"]) == 1 and h["note"].startswith("Shadow mode")
