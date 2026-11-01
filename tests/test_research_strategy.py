"""scripts/research_strategy.py: one design backtest per strategy and config, after a committed pre-registration."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from test_strategy_dca import daily

ROOT = Path(__file__).resolve().parents[1]
D = 86_400_000


@pytest.fixture
def rs(db, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("research_strategy", ROOT / "scripts" / "research_strategy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from services.backtesting import HOLDOUT_START
    rows = daily(900, start=HOLDOUT_START - 700 * D, price=20_000, step=15).to_dict("records")
    monkeypatch.setattr(mod, "open_db", lambda: db)
    monkeypatch.setattr(mod, "load_rows", lambda db_, tf: rows)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "prereg_committed", lambda path: True)
    return mod


def test_runs_once_on_design_data_and_stores_the_result(rs, db, tmp_path):
    assert rs.main(["dca"]) == 0
    stored = db.strategy_backtests("dca")
    assert len(stored) == 1 and stored[0]["period"] == "design"
    assert stored[0]["result"]["period"]["kind"] == "design"
    assert (tmp_path / "strategy_dca_design.md").exists() and (tmp_path / "strategy_dca_design.json").exists()
    assert rs.main(["dca"]) == 2                                       # second run refused
    assert len(db.strategy_backtests("dca")) == 1


def test_refuses_without_a_committed_preregistration(rs, db, monkeypatch):
    monkeypatch.setattr(rs, "prereg_committed", lambda path: False)
    assert rs.main(["dca"]) == 2 and db.strategy_backtests("dca") == []


def test_refuses_when_the_defaults_no_longer_match_the_preregistration(rs, db, monkeypatch):
    from services.strategies import dca
    monkeypatch.setitem(dca.DEFAULTS, "amount_usd", 250.0)
    assert rs.main(["dca"]) == 2 and db.strategy_backtests("dca") == []


def test_unimplemented_strategy_is_refused(rs):
    assert rs.main(["range"]) == 2


def test_trend_runs_once_on_its_own_timeframes(rs, db, tmp_path, monkeypatch):
    from functools import partial

    from services.backtesting import HOLDOUT_START
    from services.strategies import trend
    from test_strategy_trend import H4, uptrend
    df4, df1 = uptrend(n=1500)
    shift = (HOLDOUT_START - int(df4["close_time"].iloc[1200]) - 1) // H4 * H4
    for df in (df4, df1):
        df["open_time"] += shift
        df["close_time"] += shift
    seen = []
    monkeypatch.setattr(rs, "load_rows", lambda db_, tf: seen.append(tf) or (df4 if tf == "4h" else df1).to_dict("records"))
    monkeypatch.setattr(trend, "backtest", partial(trend.backtest, random_runs=20))
    assert rs.main(["trend"]) == 0 and seen == ["4h", "1d"]
    stored = db.strategy_backtests("trend")
    assert len(stored) == 1 and stored[0]["result"]["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE")
    assert (tmp_path / "strategy_trend_design.md").exists() and rs.main(["trend"]) == 2
