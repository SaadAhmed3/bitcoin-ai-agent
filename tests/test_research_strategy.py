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


def test_unknown_strategy_is_refused(rs):
    assert rs.main(["nope"]) == 2


def test_a_forward_only_variant_never_gets_a_design_backtest(rs, db):
    assert rs.main(["breakout_05r"]) == 2 and db.strategy_backtests("breakout_05r") == []


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


def test_breakout_runs_once_on_its_own_timeframes(rs, db, tmp_path, monkeypatch):
    from functools import partial

    from services.strategies import breakout
    from test_strategy_breakout import stairs
    df4, df1 = stairs()
    seen = []
    monkeypatch.setattr(rs, "load_rows", lambda db_, tf: seen.append(tf) or (df4 if tf == "4h" else df1).to_dict("records"))
    monkeypatch.setattr(breakout, "backtest", partial(breakout.backtest, random_runs=20))
    assert rs.main(["breakout"]) == 0 and seen == ["4h", "1d"]
    stored = db.strategy_backtests("breakout")
    assert len(stored) == 1 and stored[0]["result"]["period"]["kind"] == "design"
    assert (tmp_path / "strategy_breakout_design.md").exists() and rs.main(["breakout"]) == 2


def test_rotation_runs_once_on_btc_and_the_three_alts(rs, db, tmp_path, monkeypatch):
    from functools import partial

    from services.strategies import rotation
    from test_strategy_rotation import SYMS, shifted
    m = shifted()
    seen = []
    by_key = {"1d": m["BTCUSDT"], **{f"1d:{s}": m[s] for s in SYMS[1:]}}
    monkeypatch.setattr(rs, "load_rows", lambda db_, tf: seen.append(tf) or by_key[tf].to_dict("records"))
    monkeypatch.setattr(rotation, "backtest", partial(rotation.backtest, random_runs=20))
    assert rs.main(["rotation"]) == 0 and seen == ["1d", "1d:ETHUSDT", "1d:SOLUSDT", "1d:BNBUSDT"]
    assert len(db.strategy_backtests("rotation")) == 1 and rs.main(["rotation"]) == 2


def test_load_rows_reads_another_symbol(db):
    import importlib.util
    spec = importlib.util.spec_from_file_location("rs2", ROOT / "scripts" / "research_strategy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db.upsert_candles("binance", "ETHUSDT", "1d", [{"open_time": 0, "open": 1, "high": 1, "low": 1, "close": 1,
                                                    "volume": 1, "close_time": 86_399_999}], 1)
    assert len(mod.load_rows(db, "1d:ETHUSDT")) == 1 and mod.load_rows(db, "1d") == []
