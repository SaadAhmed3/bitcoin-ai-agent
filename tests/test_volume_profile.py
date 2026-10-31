"""Volume profile (VPVR) from closed OHLCV candles: deterministic, rolling, no look-ahead."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.volume_profile import ProfileConfig, profile_at, volume_profile, vp_score

H4 = 4 * 3_600_000
T0 = 1_700_000_000_000 // H4 * H4


def candles(spec: list[tuple[float, float, float]], start: int = T0) -> pd.DataFrame:
    """spec: (low, high, volume) per candle; open/close at the range middle."""
    rows = [{"open_time": start + i * H4, "open": (lo + hi) / 2, "high": hi, "low": lo, "close": (lo + hi) / 2,
             "volume": v, "close_time": start + (i + 1) * H4 - 1} for i, (lo, hi, v) in enumerate(spec)]
    return pd.DataFrame(rows)


def walk(n=400, seed=3):
    rng = np.random.default_rng(seed)
    c = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return candles([(x * 0.995, x * 1.005, float(abs(rng.normal(100, 30)) + 1)) for x in c])


def test_poc_value_area_and_uniform_allocation():
    # 10 bins over 100..110. A spreads 10 over 100-102, B puts 50 into 104-105, C spreads 10 evenly over all bins.
    df = candles([(100, 102, 10), (104, 105, 50), (100, 110, 10)])
    p = volume_profile(df, ProfileConfig(lookback=200, bins=10))
    vols = [b["volume"] for b in p["bins"]]
    assert vols == pytest.approx([6, 6, 1, 1, 51, 1, 1, 1, 1, 1])
    assert sum(vols) == pytest.approx(70) and p["total_volume"] == pytest.approx(70)
    assert p["poc"] == pytest.approx(104.5)
    # 70% of 70 = 49 <= 51 already in the POC bin: the value area is that bin alone
    assert (p["val"], p["vah"]) == (pytest.approx(104), pytest.approx(105))


def test_value_area_expands_to_the_larger_neighbour():
    df = candles([(100, 101, 10), (101, 102, 30), (102, 103, 40), (103, 104, 20), (104, 105, 5)])
    p = volume_profile(df, ProfileConfig(bins=5, value_area=0.70))
    # POC 102-103 (40); next: 101-102 (30) beats 103-104 (20) -> 70/105 = 67%; then 103-104 -> 90/105 >= 70%
    assert p["poc"] == pytest.approx(102.5)
    assert (p["val"], p["vah"]) == (pytest.approx(101), pytest.approx(104))


def test_hvn_and_lvn():
    spec = [(100 + i, 101 + i, v) for i, v in enumerate([5, 10, 60, 10, 5, 1, 1, 5, 10, 55, 10, 5])]
    p = volume_profile(candles(spec), ProfileConfig(bins=12, value_area=0.95))
    hvn = sorted(round(h["price"], 1) for h in p["hvn"])
    assert hvn == [102.5, 109.5]
    lvn = [round(x["price"], 1) for x in p["lvn"]]
    assert lvn and all(105 <= x <= 107 for x in lvn)      # the gap between the two peaks


def test_missing_volume_is_skipped_and_counted():
    df = candles([(100, 102, 10), (104, 105, 0), (100, 110, float("nan")), (100, 110, 10)])
    p = volume_profile(df, ProfileConfig(bins=10))
    assert p["candles_used"] == 2 and p["skipped_missing_volume"] == 2
    allz = volume_profile(candles([(100, 102, 0), (101, 103, 0)]), ProfileConfig(bins=10))
    assert allz["status"] == "no_volume" and allz["poc"] is None


def test_flat_candle_puts_all_volume_in_one_bin():
    p = volume_profile(candles([(100, 110, 10), (103.3, 103.3, 7)]), ProfileConfig(bins=10))
    assert p["bins"][3]["volume"] == pytest.approx(1 + 7)


def test_visible_range_uses_the_last_n_closed_candles():
    df = walk()
    a = volume_profile(df, ProfileConfig(lookback=200))
    b = volume_profile(df, ProfileConfig(lookback=50))
    assert a["candles_used"] == 200 and b["candles_used"] == 50
    assert a["range"]["from_open_time"] == int(df["open_time"].iloc[-200])
    assert b["range"]["from_open_time"] == int(df["open_time"].iloc[-50])
    assert a["range"]["to_close_time"] == b["range"]["to_close_time"] == int(df["close_time"].iloc[-1])
    assert a["poc"] != b["poc"] or a["vah"] != b["vah"]


def test_deterministic():
    df = walk()
    assert volume_profile(df) == volume_profile(df.copy())


def test_rolling_profile_uses_only_the_previous_200_closed_candles():
    df = walk()
    i = 320
    p = profile_at(df, i)
    assert p == volume_profile(df.iloc[i - 199:i + 1])
    changed = df.copy()
    changed.iloc[i + 1:, changed.columns.get_loc("volume")] *= 50       # the future
    changed.iloc[:i - 199, changed.columns.get_loc("volume")] *= 50     # older than the 200-candle window
    assert profile_at(changed, i) == p
    assert p["range"]["to_close_time"] == int(df["close_time"].iloc[i]) and p["candles_used"] == 200


def test_incomplete_candle_is_excluded():
    df = walk()
    now = int(df["close_time"].iloc[-1]) - 1
    assert volume_profile(df, now=now) == volume_profile(df.iloc[:-1])


def test_score():
    p = {"status": "ok", "poc": 100.0, "vah": 110.0, "val": 90.0}
    assert vp_score(p, 115) == 0.5 and vp_score(p, 85) == -0.5
    assert vp_score(p, 105) == pytest.approx(0.25) and vp_score(p, 100) == 0
    assert vp_score({"status": "no_volume"}, 100) == 0
