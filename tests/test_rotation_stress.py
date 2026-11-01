"""Rotation survivorship stress test (reported only): the same rule over a wider, point-in-time universe that includes
assets which later collapsed or were delisted. The strategy and its verdict are untouched."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from test_strategy_rotation import D, MON, SYMS, daily, frames_of, market, shifted

ROOT = Path(__file__).resolve().parents[1]


def test_preregistration_fixes_the_universe_and_the_data_rules():
    from services import rotation_stress as rs
    text = (ROOT / "PREREGISTRATION_ROTATION_STRESS.md").read_text(encoding="utf-8")
    assert rs.UNIVERSE[:4] == SYMS and len(rs.UNIVERSE) == 18 and {"LUNAUSDT", "FTTUSDT", "MATICUSDT"} <= set(rs.UNIVERSE)
    for sym in rs.UNIVERSE:
        assert f"`{sym}`" in text, sym
    for must in ("2021-06-27", "CoinMarketCap", "USDT, USDC, BUSD", "WBTC", "gap of more than 7 days", "a different asset",
                 "sold at its last close", "LUNA 2.0", "reported only", "verdict", "unchanged", "f3de8ea9c08bc780",
                 "available", "2022-05-13", "2022-11-15", "2024-09-10"):
        assert must in text, must


def test_with_the_original_four_assets_it_is_the_strategy_exactly():
    from services import rotation_stress as rs
    from services.strategies import rotation as rot
    m = market(n=400)
    cfg = rot.validate_config({})
    a = rot.simulate(rot.panel(frames_of(m)), cfg, MON + 70 * D, MON + 390 * D)
    b = rs.simulate(rs.panel({s: m[s] for s in SYMS}, SYMS), SYMS, cfg, MON + 70 * D, MON + 390 * D)
    assert [e["equity"] for e in a["equity"]] == [e["equity"] for e in b["equity"]]
    assert [(w["monday"], w["holding"], w["signal"]) for w in a["weeks"]] == [(w["monday"], w["holding"], w["signal"]) for w in b["weeks"]]


def test_history_ends_at_the_first_gap_longer_than_seven_days():
    from services.rotation_stress import truncate
    df = daily(np.full(60, 10.0))
    gappy = df[(df.index < 20) | (df.index >= 35)].reset_index(drop=True)      # 15 days missing: a re-used ticker
    out, ended = truncate(gappy)
    assert len(out) == 20 and ended == int(df["open_time"].iloc[19])
    short = df[(df.index < 20) | (df.index >= 25)].reset_index(drop=True)      # 5 days missing: kept
    assert len(truncate(short)[0]) == 55 and truncate(short)[1] is None


def test_a_held_asset_whose_data_ends_is_sold_at_its_last_close():
    from services import rotation_stress as rs
    from services.strategies import rotation as rot
    n = 120
    up = 100 * np.exp(np.arange(n) * 0.02)
    crash = up.copy()
    crash[50:] = up[49] * 0.001                                                 # collapses, then delisted
    m = {"BTCUSDT": daily(np.full(n, 100.0)), "ETHUSDT": daily(np.full(n, 100.0) * 0.999 ** np.arange(n)),
         "LUNAUSDT": daily(crash).iloc[:53]}
    syms = ("BTCUSDT", "ETHUSDT", "LUNAUSDT")
    sim = rs.simulate(rs.panel(m, syms), syms, rot.validate_config({}), MON + 35 * D, MON + 70 * D)
    assert sim["weeks"][0]["holding"] == "LUNAUSDT"
    out = [s for s in sim["switches"] if s["from"] == "LUNAUSDT"]
    assert out and out[0]["time"] == MON + 56 * D                               # the next Monday: no data any more
    assert sim["equity"][-1]["equity"] < 0.01                                   # the collapse is in the account


def test_study_reports_next_to_the_original_and_never_writes_a_strategy_result(db, tmp_path):
    from services import rotation_stress as rs
    from services.strategies import rotation as rot
    m = shifted()
    extra = market(n=1400, seed=11, drifts=(0.001, -0.002, 0.0005, 0.0))
    for s, src in zip(("ADAUSDT", "LUNAUSDT"), ("ETHUSDT", "SOLUSDT")):
        df = extra[src].copy()
        df["open_time"], df["close_time"] = m["BTCUSDT"]["open_time"], m["BTCUSDT"]["close_time"]
        m[s] = df.iloc[:700] if s == "LUNAUSDT" else df
    original = rot.backtest(*[m[s].to_dict("records") for s in SYMS], rot.validate_config({}), random_runs=20)
    r = rs.study({s: m[s].to_dict("records") for s in m}, ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT",
                                                           "LUNAUSDT", "XRPUSDT"), original, random_runs=20)
    assert r["universe"]["available"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "LUNAUSDT"]
    assert r["universe"]["not_available"] == ["XRPUSDT"]
    assert r["original"]["net_return_pct"] == original["metrics"]["net_return_pct"]
    assert r["original"]["verdict"] == original["verdict"] and "unchanged" in r["note"]
    assert r["period"]["first_monday"] == original["period"]["first_monday"]
    assert set(r["criteria_reported"][0]) >= {"id", "rule", "passed"} and r["reported_only"] is True
    assert "weeks_held" in r and r["config_hash"] == original["config_hash"]
    text = rs.markdown(r)
    assert "Original" in text and "Wider universe" in text and "XRPUSDT" in text
    assert db.strategy_backtests("rotation") == []


def test_the_lab_shows_the_stored_stress_result_next_to_rotation(db, tmp_path, monkeypatch):
    from services import rotation_stress as rs
    from services.strategy_forward import StrategyForward
    from services.strategy_lab import StrategyLab
    f = tmp_path / "rotation_stress_design.json"
    f.write_text(json.dumps({"reported_only": True, "note": "verdict unchanged", "stress": {"net_return_pct": 12.0}}),
                 encoding="utf-8")
    monkeypatch.setattr(rs, "RESULT_FILE", f)
    lab = StrategyLab(db, None, StrategyForward(db))
    assert lab.stress_test("rotation")["stress"]["net_return_pct"] == 12.0
    assert lab.stress_test("trend") is None
    monkeypatch.setattr(rs, "RESULT_FILE", tmp_path / "missing.json")
    assert lab.stress_test("rotation") is None


def test_the_script_runs_once_after_a_committed_preregistration_and_never_touches_the_strategy(db, tmp_path, monkeypatch):
    import importlib.util
    from functools import partial

    from services import rotation_stress as rs
    from services.strategies import rotation as rot
    spec = importlib.util.spec_from_file_location("rrs", ROOT / "scripts" / "research_rotation_stress.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "open_db", lambda: db)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "prereg_committed", lambda path: False)
    assert mod.main([], http=object()) == 2                                        # not committed: nothing
    monkeypatch.setattr(mod, "prereg_committed", lambda path: True)
    assert mod.main([], http=object()) == 2                                        # no stored Rotation result
    m = shifted()
    for s in SYMS:
        db.upsert_candles("binance", s, "1d", m[s].to_dict("records"), 1)
    res = rot.backtest(*[m[s].to_dict("records") for s in SYMS], rot.validate_config({}), random_runs=20)
    db.strategy_backtest_add("rotation", 1, res["config_hash"], "design", "sha", res, now=1)
    before = db.strategy_backtests("rotation")
    monkeypatch.setattr(mod, "fetch_missing", lambda db_, http: [])
    monkeypatch.setattr(rs, "study", partial(rs.study, random_runs=20))
    assert mod.main([], http=object()) == 0 and (tmp_path / "rotation_stress_design.json").exists()
    assert mod.main([], http=object()) == 2                                        # runs once
    assert db.strategy_backtests("rotation") == before                             # strategy result untouched
    out = json.loads((tmp_path / "rotation_stress_design.json").read_text(encoding="utf-8"))
    assert set(out["universe"]["not_available"]) == set(rs.UNIVERSE) - set(SYMS)  # nothing else stored in this test
