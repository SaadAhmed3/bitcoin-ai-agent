"""H1 research script (scripts/research_h1.py): timing, costs and the design-data guard. Synthetic data only."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location("research_h1", Path(__file__).resolve().parents[1] / "scripts" / "research_h1.py")
h1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h1)
DAY = 86_400_000


def frame(closes, opens=None, end=h1.HOLDOUT_START):
    n = len(closes)
    ot = [end - (n - i) * DAY for i in range(n)]
    return pd.DataFrame({"open_time": ot, "open": opens or closes, "high": closes, "low": closes, "close": closes,
                         "volume": [1.0] * n, "close_time": [t + DAY - 1 for t in ot]})


def test_holdout_candles_are_refused():
    ok = frame([100.0] * 5)
    assert h1.check_design(ok) is not None
    bad = frame([100.0] * 5, end=h1.HOLDOUT_START + DAY)                 # last candle closes inside the holdout
    with pytest.raises(ValueError, match="holdout"):
        h1.check_design(bad)
    gap = ok.drop(index=2)
    with pytest.raises(ValueError, match="gap"):
        h1.check_design(gap)


def test_decided_at_the_close_and_traded_at_the_next_open_with_costs():
    closes = [100.0, 100.0, 120.0, 120.0, 90.0, 90.0]
    opens = [100.0, 100.0, 110.0, 121.0, 100.0, 95.0]
    df = frame(closes, opens)
    sig = np.array([False, False, True, True, False, False])       # in after close 2 > SMA, out after close 4
    r = h1.simulate(df, sig, start=1)
    c = h1.COST
    assert r["trades"] == [{"entry": 3, "exit": 5, "days": 2}]     # bought at open 3, sold at open 5
    units = 1 / (121.0 * (1 + c))
    assert r["equity"][-1] == pytest.approx(units * 95.0 * (1 - c))
    assert r["equity"][2] == pytest.approx(units * 120.0 * (1 - c))   # marked at liquidation value
    assert list(r["in_market"]) == [False, False, True, True, False]


def test_sma_filter_uses_only_past_closes_and_band_is_hysteresis():
    closes = np.array([10.0, 10, 10, 11, 10.2, 9.8, 10.5])   # day 4: below SMA 10.40, above the 2% band 10.19
    plain = h1.sma_filter(closes, 3, 0.0)
    assert list(plain) == [False, False, False, True, False, False, True]
    banded = h1.sma_filter(closes, 3, 0.02)
    assert list(banded) == [False, False, False, True, True, False, True]
    extended = h1.sma_filter(np.append(closes, [1.0, 50.0]), 3, 0.0)
    assert list(extended[:7]) == list(plain)                        # future closes never change past decisions


def test_regimes_use_the_previous_close_only():
    closes = list(np.linspace(100, 100, 95)) + [200.0]
    lab = h1.regimes(frame(closes))
    assert lab[95] == "sideways"            # the jump on day 95 is not visible on day 95 itself


# ------------------------------------------------------------------ pre-registered evaluation and the holdout test
def m(net, dd):
    return {"net_return_pct": net, "max_drawdown_pct": dd}


def test_evaluate_applies_the_preregistered_criteria():
    ok = h1.evaluate(m(20, 10), m(15, 30), 95.0, position_changes=4, days=400)
    assert ok["verdict"] == "PASS" and len(ok["criteria"]) == 3
    assert h1.evaluate(m(20, 19), m(15, 30), 95.0, 4, 400)["verdict"] == "FAIL"       # DD 19 > 0.6 x 30 = 18
    assert h1.evaluate(m(4, 10), m(15, 30), 95.0, 4, 400)["verdict"] == "FAIL"        # 0.4 < 0.5 return/DD
    assert h1.evaluate(m(20, 10), m(15, 30), 89.9, 4, 400)["verdict"] == "FAIL"       # random percentile
    assert h1.evaluate(m(20, 10), m(15, 30), 95.0, 4, 364)["verdict"] == "INCONCLUSIVE"
    assert h1.evaluate(m(0, 0), m(15, 30), 95.0, 0, 400)["verdict"] == "INCONCLUSIVE"  # never changed position


def test_holdout_test_trades_only_from_the_holdout_start_on_synthetic_data():
    rng = np.random.default_rng(3)
    n = 800                                                          # 400 days before and 400 inside the holdout
    closes = list(60_000 * np.exp(np.cumsum(rng.normal(0, 0.03, n))))
    df = frame(closes, end=h1.HOLDOUT_START + 400 * DAY)             # SYNTHETIC candles, not the real holdout
    r = h1.holdout_test(df)
    assert r["window"]["first_trade_open"].startswith("2025-10-29") and r["window"]["days"] == 400
    assert r["evaluation"]["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE")
    assert r["metrics"]["h1_sma200"]["days"] == 400 and len(r["script_sha256"]) == 64


def test_the_holdout_mode_refuses_without_explicit_confirmation(monkeypatch):
    called = []
    monkeypatch.setattr(h1, "load_all_daily", lambda *a: called.append(1))
    with pytest.raises(SystemExit, match="single-use"):
        h1.main(["--holdout"])
    assert called == []                                              # the holdout data was never read
