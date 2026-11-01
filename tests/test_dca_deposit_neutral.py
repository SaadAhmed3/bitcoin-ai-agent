"""DCA: deposit-neutral metrics, REPORTED ONLY (no criterion). The locked design study and its verdict are unchanged."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from conftest import FakeProvider
from test_app_and_db import make_client
from test_strategy_dca import MON, daily

ROOT = Path(__file__).resolve().parents[1]
D = 86_400_000
ORIGINAL_PREREG_SHA = "90dad57623dc0ab53e7f9e5db8275146b739e551b02b0a9b9021be025996519e"   # stored with the study


def path_df(prices):
    """One candle per day; open = close = price (so the arithmetic is easy to follow)."""
    import pandas as pd
    return pd.DataFrame([{"open_time": MON + i * D, "open": p, "high": p, "low": p, "close": p, "volume": 1.0,
                          "close_time": MON + (i + 1) * D - 1} for i, p in enumerate(prices)])


def test_twr_index_follows_the_invested_btc_not_the_cash():
    from services.strategies.dca import simulate, validate_config
    from services.strategies.dca_metrics import deposit_neutral
    cfg = validate_config({"interval": "daily", "fee_pct": 0, "slippage_pct": 0})
    prices = [100, 50, 50, 100]
    sim = simulate(path_df(prices), cfg, MON, MON + 3 * D)
    m = deposit_neutral(sim, cfg)
    # no costs: the time-weighted index is the BTC price path from the first buy -> a 50% drawdown,
    # while the budget-basis drawdown (mostly cash on day 2) is much smaller
    assert m["twr_max_drawdown_pct"] == pytest.approx(50.0)
    assert sim["max_drawdown_pct"] < 20
    assert m["twr_return_pct"] == pytest.approx(0.0)                     # 100 -> 50 -> 50 -> 100
    # (value - invested) / invested: day 1 holds 3 BTC at 50 = 150 for 200 invested -> -25%, the worst point
    roi = [r["roi_pct"] for r in m["roi_series"]]
    assert roi == pytest.approx([0.0, -25.0, (250 - 300) / 300 * 100, (600 - 400) / 400 * 100])
    assert m["roi_worst_pct"] == pytest.approx(-25.0) and m["roi_worst_day"] == MON + D
    assert m["reported_only"] is True and "no criterion" in m["note"]


def test_costs_lower_the_twr_index_on_buy_days():
    from services.strategies.dca import simulate, validate_config
    from services.strategies.dca_metrics import deposit_neutral
    cfg = validate_config({"interval": "daily"})                         # 0.10% fee + 0.05% slippage
    sim = simulate(path_df([100, 100, 100]), cfg, MON, MON + 2 * D)
    m = deposit_neutral(sim, cfg)
    assert m["twr_return_pct"] < 0                                       # flat price, costs only


def test_lab_shows_them_next_to_the_locked_study_and_for_the_forward_test(config, db):
    from services.backtesting import HOLDOUT_START
    from services.strategies import dca
    c = make_client(config, db, [FakeProvider("fake1")])
    rows = daily(900, start=HOLDOUT_START - 700 * D, price=20_000, step=15).to_dict("records")
    db.upsert_candles("binance", "BTCUSDT", "1d", rows, 1)
    locked = dca.backtest(rows, dca.validate_config({}))
    h = c.get("/api/strategies/dca/config").get_json()["hash"]
    db.strategy_backtest_add("dca", 1, h, "design", ORIGINAL_PREREG_SHA, locked, now=1)
    d = c.get("/api/strategies/dca").get_json()
    assert d["backtest"]["verdict"] == locked["verdict"]                 # the locked study is shown unchanged
    assert d["backtest"]["metrics"] == pytest.approx(locked["metrics"])
    sup = d["backtest_supplement"]
    assert sup["reported_only"] is True and sup["twr_max_drawdown_pct"] is not None
    assert sup["period"] == {"first_day": locked["period"]["first_day"], "last_day": locked["period"]["last_day"]}
    assert "forward_deposit_neutral" in d                                # computed from forward events once they exist


def test_preregistration_addendum_is_dated_and_the_original_text_is_unchanged():
    text = (ROOT / "PREREGISTRATION_DCA.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    head, sep, addendum = text.partition("\n## Addendum (2026-11-01)")
    assert sep, "no dated addendum"
    assert hashlib.sha256(head.encode()).hexdigest() == ORIGINAL_PREREG_SHA            # byte-identical original
    for words in ("reported only", "no criterion", "time-weighted", "invested", "verdict"):
        assert words in addendum
