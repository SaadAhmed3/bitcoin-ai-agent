"""Liquidity sweep detection on closed 4h candles: no repainting, no look-ahead, append-only storage."""
from __future__ import annotations

import pandas as pd
import pytest

from services.liquidity_sweeps import STATUSES, SweepConfig, detect_sweeps, levels_before, sweep_score

H4 = 4 * 3_600_000
T0 = 1_700_000_000_000 // H4 * H4
BASE = (100.0, 100.5, 99.5, 100.0, 100.0)          # open, high, low, close, volume: equal highs/lows -> no swings


def build(pattern: list[tuple], base: int = 40, drop: tuple = ()) -> pd.DataFrame:
    rows = [BASE] * base + list(pattern)
    out = []
    for i, (o, h, l, c, v) in enumerate(rows):
        if i in drop:
            continue
        out.append({"open_time": T0 + i * H4, "open": o, "high": h, "low": l, "close": c, "volume": v,
                    "close_time": T0 + (i + 1) * H4 - 1})
    return pd.DataFrame(out)


SWING_HIGH = (100.0, 102.0, 99.5, 100.0, 100.0)     # level 102
SWING_LOW = (100.0, 100.5, 98.0, 100.0, 100.0)      # level 98
BUY_SWEEP = (100.0, 102.5, 99.8, 101.0, 200.0)      # wick above 102, closes back below, 2x volume
SELL_SWEEP = (100.0, 100.2, 97.5, 99.0, 200.0)      # wick below 98, closes back above
J = 40                                               # index of the swing candle


def events(df, **kw):
    return detect_sweeps(df, **kw)


def ct(i: int) -> int:
    return T0 + (i + 1) * H4 - 1


def test_buy_side_sweep_is_potential_at_the_sweep_close_then_confirmed_at_the_next_close():
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, (101.0, 101.5, 100.0, 100.5, 100.0), BASE])
    ev = events(df)
    assert len(ev) == 1
    e = ev[0]
    assert e["direction"] == "buy_side" and e["level"] == 102.0 and e["sweep_extreme"] == 102.5
    assert e["reclaim_price"] == 101.0 and e["timeframe"] == "4h"
    assert [(u["status"], u["at"]) for u in e["updates"]] == [("POTENTIAL SWEEP", ct(J + 4)),
                                                              ("CONFIRMED SWEEP", ct(J + 5))]
    assert e["status"] == "CONFIRMED SWEEP" and e["confirmed_at"] == ct(J + 5) and e["event_time"] == ct(J + 4)
    assert 0 < e["strength"] <= 100 and e["factors"]["volume"] == 20
    assert set(e["factors"]) == {"penetration", "rejection_wick", "close_inside", "volume", "confirmation",
                                 "higher_timeframe"}


def test_sell_side_sweep():
    df = build([SWING_LOW, BASE, BASE, BASE, SELL_SWEEP, (99.0, 100.0, 98.5, 99.5, 100.0), BASE])
    (e,) = events(df)
    assert e["direction"] == "sell_side" and e["level"] == 98.0 and e["sweep_extreme"] == 97.5
    assert e["status"] == "CONFIRMED SWEEP"
    assert sweep_score(df, [e], ct(J + 6)) == pytest.approx(e["strength"] / 100)      # bullish evidence


def test_a_close_beyond_the_level_is_a_breakout_not_a_sweep():
    df = build([SWING_HIGH, BASE, BASE, BASE, (100.0, 102.6, 99.9, 102.3, 200.0), BASE])
    assert events(df) == []


def test_wick_beyond_then_acceptance_above_is_a_failed_sweep():
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, (101.0, 102.4, 100.8, 102.2, 100.0), BASE])
    (e,) = events(df)
    assert [u["status"] for u in e["updates"]] == ["POTENTIAL SWEEP", "FAILED SWEEP"]
    assert e["updates"][-1]["at"] == ct(J + 5) and e["confirmed_at"] is None


def test_wick_alone_is_never_confirmed_on_its_own_candle():
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP])        # the next candle has not closed yet
    (e,) = events(df)
    assert e["status"] == "POTENTIAL SWEEP" and e["confirmed_at"] is None


def test_reclaim_confirmed():
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, (101.0, 101.5, 100.0, 100.5, 100.0),
                (100.5, 100.6, 98.8, 99.0, 100.0)])                 # closes below the sweep candle's low (99.8)
    (e,) = events(df)
    assert [u["status"] for u in e["updates"]] == ["POTENTIAL SWEEP", "CONFIRMED SWEEP", "RECLAIM CONFIRMED"]
    assert e["updates"][-1]["at"] == ct(J + 6)


def test_no_confirmation_after_three_candles():
    higher = [(101.0, 102.5 + 0.1 * k, 100.5, 101.0, 100.0) for k in range(1, 4)]   # each makes a new extreme
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, *higher, BASE])
    (e,) = events(df)
    assert [u["status"] for u in e["updates"]] == ["POTENTIAL SWEEP", "NO CONFIRMATION"]
    assert e["updates"][-1]["at"] == ct(J + 7)


def test_missing_candle_is_no_confirmation_not_a_guess():
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, (101.0, 101.5, 100.0, 100.5, 100.0), BASE],
               drop=(J + 5,))                                       # the candle after the sweep is missing
    (e,) = events(df)
    assert e["status"] == "NO CONFIRMATION" and "missing candle" in e["updates"][-1]["note"]


def test_levels_are_only_swings_confirmed_before_the_sweep_candle():
    # Swing at J needs k=3 later closed candles (J+1..J+3). A candle AT J+3 can't use it: no event.
    early = build([SWING_HIGH, BASE, BASE, BUY_SWEEP, BASE, BASE])
    assert events(early) == []
    ok = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, BASE, BASE])
    (e,) = events(ok)
    k = SweepConfig().k
    assert e["swing_time"] + k * H4 <= e["event_time"] - H4 + 1          # confirmed at or before the previous close
    highs, _ = levels_before(ok, J + 4)
    assert [lv["level"] for lv in highs] == [102.0]
    assert levels_before(ok, J + 3) == ([], [])


def test_detection_never_changes_with_future_candles():
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, (101.0, 101.5, 100.0, 100.5, 100.0), BASE, BASE,
                SWING_LOW, BASE, BASE, BASE, SELL_SWEEP, (99.0, 100.0, 98.5, 99.5, 100.0), BASE])
    full = events(df)
    for t in range(J, len(df)):
        part = events(df.iloc[:t + 1])
        cut = int(df["close_time"].iloc[t])
        seen = [{**e, "updates": [u for u in e["updates"] if u["at"] <= cut]} for e in full if e["event_time"] <= cut]
        assert [(e["key"], [u["status"] for u in e["updates"]]) for e in part] == \
               [(e["key"], [u["status"] for u in e["updates"]]) for e in seen]


def test_equal_highs_merge_into_one_level():
    # 102.03 is within 0.1 x ATR of 102: an equal high, not a sweep of it; the sweep must clear both
    df = build([SWING_HIGH, BASE, BASE, BASE, (100.0, 102.03, 99.5, 100.0, 100.0), BASE, BASE, BASE,
                (100.0, 102.6, 99.8, 101.0, 200.0), (101.0, 101.5, 100.0, 100.5, 100.0)])
    (e,) = events(df)
    assert e["level"] == 102.03 and e["equal_levels"] == 2


def test_duplicates_are_not_stored_twice(db):
    from services.advanced_analysis import persist_sweeps
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, (101.0, 101.5, 100.0, 100.5, 100.0), BASE])
    ev = events(df)
    assert persist_sweeps(db, ev, now=1) == (1, 2)
    assert persist_sweeps(db, ev, now=2) == (0, 0)
    stored = db.sweep_events(10)
    assert len(stored) == 1 and [u["status"] for u in stored[0]["updates"]] == ["POTENTIAL SWEEP", "CONFIRMED SWEEP"]


def test_statuses_and_score_window():
    assert STATUSES == ("POTENTIAL SWEEP", "CONFIRMED SWEEP", "FAILED SWEEP", "RECLAIM CONFIRMED", "NO CONFIRMATION")
    df = build([SWING_HIGH, BASE, BASE, BASE, BUY_SWEEP, (101.0, 101.5, 100.0, 100.5, 100.0)] + [BASE] * 10)
    (e,) = events(df)
    assert sweep_score(df, [e], ct(J + 5)) == pytest.approx(-e["strength"] / 100)    # bearish evidence
    assert sweep_score(df, [e], ct(J + 4)) == 0                                       # only potential at that time
    assert sweep_score(df, [e], ct(J + 12)) == 0                                      # older than 6 bars


# ---------------------------------------------------------------- real-data review (0.9.8): one move, one event
def real_candles() -> pd.DataFrame:
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).parent / "fixtures" / "btc_4h_sweep_2026-10.json").read_text())
    return pd.DataFrame(data["rows"])


def test_real_candles_one_buy_side_sweep_at_81823_not_two():
    # 2026-10-29 12:00 UTC: wick to 81,900 above the 81,823 swing high, close back below -> POTENTIAL.
    # 16:00 UTC: a higher wick (81,971) while that event is still open: the same move, not a second sweep.
    ev = [e for e in detect_sweeps(real_candles()) if e["direction"] == "buy_side" and e["level"] == 81823.0]
    assert len(ev) == 1
    e = ev[0]
    utc = lambda ms: pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%m-%d %H:%M")  # noqa: E731
    assert utc(e["event_time"]) == "10-29 15:59" and e["sweep_extreme"] == 81971.0
    assert [(u["status"], utc(u["at"])) for u in e["updates"]] == [
        ("POTENTIAL SWEEP", "10-29 15:59"), ("CONFIRMED SWEEP", "10-29 23:59"), ("RECLAIM CONFIRMED", "10-30 03:59")]


def test_a_level_with_an_open_event_is_not_swept_again_until_the_event_closes():
    # each poke wicks a little higher (a new extreme of the open event) but stays within 0.1 x ATR of the level, so
    # the level is still "untaken"; it must not start a second event while the first is open
    poke = lambda hi: (100.0, hi, 99.8, 101.0, 150.0)  # noqa: E731
    first = (100.0, 102.06, 99.8, 101.0, 200.0)
    df = build([SWING_HIGH, BASE, BASE, BASE, first, poke(102.07), poke(102.08), poke(102.09), BASE])
    ev = events(df)
    assert len(ev) == 1 and ev[0]["updates"][-1]["status"] == "NO CONFIRMATION"
    assert ev[0]["updates"][-1]["at"] == ct(J + 7)
    # once the first event is closed, a later poke at the same level is a new event
    later = build([SWING_HIGH, BASE, BASE, BASE, first, poke(102.07), poke(102.08), poke(102.09), poke(102.10),
                   (101.0, 101.5, 100.0, 100.5, 100.0)])
    ev = events(later)
    assert [e["event_time"] for e in ev] == [ct(J + 4), ct(J + 8)]
