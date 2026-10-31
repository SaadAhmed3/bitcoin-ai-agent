"""Chart overlays: regime shading, sweep markers and the chart's volume profile, as chart coordinates.

Visualisation only: the detection engines are reused as they are; closed candles only, no look-ahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import FakeProvider
from test_app_and_db import make_client
from test_regime import DECLINING, frame, trend

H1, H4, D1 = 3_600_000, 4 * 3_600_000, 86_400_000
T0 = 1_700_000_000_000 // D1 * D1


def candles(spec, step, start=T0):
    """spec: (high, low) per candle; open/close in the middle, volume 10."""
    return pd.DataFrame([{"open_time": start + i * step, "open": (h + l) / 2, "high": h, "low": l,
                          "close": (h + l) / 2, "volume": 10.0, "close_time": start + (i + 1) * step - 1}
                         for i, (h, l) in enumerate(spec)])


def event(direction="buy_side", sweep_open=T0 + 2 * H4, updates=(("POTENTIAL SWEEP", 0), ("CONFIRMED SWEEP", 1)),
          key="4h:buy_side:x"):
    t = sweep_open + H4 - 1
    return {"event_key": key, "direction": direction, "level": 105.0, "equal_levels": 1, "swing_time": T0,
            "event_time": t, "sweep_extreme": 106.0, "reclaim_price": 104.5, "volume_ratio": 1.8,
            "updates": [{"status": s, "at": t + k * H4, "strength": 60.0 + k, "factors": {}, "note": ""}
                        for s, k in updates]}


# ---------------------------------------------------------------- sweep markers: coordinates and timestamps
def test_on_1h_the_marker_sits_on_the_1h_candle_that_made_the_actual_high():
    from services.chart_overlays import sweep_markers
    # 1h chart around the 4h sweep candle at T0 + 8h .. T0 + 12h: the 2nd hour holds the high (106)
    spec = [(101, 99)] * 8 + [(104, 102), (106, 103), (105, 103), (104.5, 103)] + [(104, 102)] * 8
    chart = candles(spec, H1)
    (m,) = sweep_markers([event()], chart, "1h", as_of=int(chart["close_time"].iloc[-1]))
    assert m["marker_time"] == T0 + 9 * H1 and m["marker_price"] == 106.0
    assert m["status"] == "CONFIRMED SWEEP" and m["confirmed_at"] == T0 + 3 * H4 - 1 + H4
    assert m["line_start"] == T0 and m["line_end"] >= m["marker_time"]
    for k in ("direction", "level", "sweep_extreme", "reclaim_price", "strength", "event_time"):
        assert m[k] is not None


def test_sell_side_marker_at_the_lowest_low_and_on_4h_at_the_sweep_candle():
    from services.chart_overlays import sweep_markers
    spec = [(101, 99)] * 8 + [(100, 97), (99, 94), (100, 95), (100, 96)] + [(101, 99)] * 8
    chart = candles(spec, H1)
    (m,) = sweep_markers([event("sell_side")], chart, "1h", as_of=int(chart["close_time"].iloc[-1]))
    assert (m["marker_time"], m["marker_price"]) == (T0 + 9 * H1, 94.0)
    c4 = candles([(101, 99), (101, 99), (106, 103), (104, 102), (104, 102)], H4)
    (m4,) = sweep_markers([event()], c4, "4h", as_of=int(c4["close_time"].iloc[-1]))
    assert (m4["marker_time"], m4["marker_price"]) == (T0 + 2 * H4, 106.0)


def test_on_1d_the_marker_uses_the_closed_daily_candle_that_contains_the_sweep():
    from services.chart_overlays import sweep_markers
    d = candles([(110, 90), (108, 95)], D1)
    (m,) = sweep_markers([event()], d, "1d", as_of=int(d["close_time"].iloc[-1]))
    assert (m["marker_time"], m["marker_price"]) == (T0, 110.0)
    # the day holding the sweep is not closed on this chart: no marker (no incomplete candles)
    assert sweep_markers([event(sweep_open=T0 + D1 + 2 * H4)], d.iloc[:1], "1d", as_of=int(d["close_time"].iloc[0])) == []


def test_status_is_shown_as_of_the_chart_and_never_confirmed_early():
    from services.chart_overlays import sweep_markers
    spec = [(101, 99)] * 8 + [(104, 102), (106, 103), (105, 103), (104.5, 103)] + [(104, 102)] * 2
    chart = candles(spec, H1)                       # ends at T0 + 14h: before the confirmation at T0 + 16h - 1
    (m,) = sweep_markers([event()], chart, "1h", as_of=int(chart["close_time"].iloc[-1]))
    assert m["status"] == "POTENTIAL SWEEP" and m["confirmed_at"] is None and m["strength"] == 60.0
    later = event(updates=(("POTENTIAL SWEEP", 0), ("FAILED SWEEP", 1)))
    full = candles(spec + [(104, 102)] * 8, H1)
    (f,) = sweep_markers([later], full, "1h", as_of=int(full["close_time"].iloc[-1]))
    assert f["status"] == "FAILED SWEEP" and f["style"] == "failed"
    # an event detected after the chart's last closed candle is not drawn at all
    assert sweep_markers([event(sweep_open=T0 + 5 * H4)], chart, "1h", as_of=int(chart["close_time"].iloc[-1])) == []


def test_no_marker_without_the_candles_that_hold_the_sweep():
    from services.chart_overlays import sweep_markers
    chart = candles([(101, 99)] * 6, H1)            # ends before the sweep candle
    assert sweep_markers([event()], chart, "1h", as_of=int(chart["close_time"].iloc[-1])) == []


# ---------------------------------------------------------------- regime shading: full 4h periods, closed only
def test_regime_bars_cover_full_4h_periods_and_only_closed_ones():
    from services.chart_overlays import RegimeCache, regime_bars
    from services.regime import regime_at
    c4 = frame(trend(), DECLINING)
    n = len(c4)
    chart_start = int(c4["open_time"].iloc[n - 20])
    chart_end = int(c4["close_time"].iloc[n - 2]) - 2 * H1          # a 1h chart: the last 4h bar is half done
    bars = regime_bars(c4, None, None, chart_start, chart_end, RegimeCache())
    assert bars[0]["start"] == chart_start and bars[-1]["end"] == int(c4["close_time"].iloc[n - 3]) + 1
    assert all(b["end"] - b["start"] == H4 for b in bars)
    i = n - 10
    b = next(x for x in bars if x["start"] == int(c4["open_time"].iloc[i]))
    r = regime_at(c4, i)
    assert (b["regime"], b["confidence"], b["checks_used"]) == (r["regime"], r["confidence"], r["checks_used"])


def test_regime_labels_are_cached_per_closed_bar(monkeypatch):
    import services.chart_overlays as co
    calls = []
    real = co.regime_at
    monkeypatch.setattr(co, "regime_at", lambda *a, **k: calls.append(1) or real(*a, **k))
    c4 = frame(trend(), DECLINING)
    cache = co.RegimeCache()
    start, end = int(c4["open_time"].iloc[-12]), int(c4["close_time"].iloc[-1])
    co.regime_bars(c4, None, None, start, end, cache)
    first = len(calls)
    co.regime_bars(c4, None, None, start, end, cache)
    assert first == 12 and len(calls) == first                      # a closed bar's label never changes


def test_segments_and_transitions():
    from services.chart_overlays import regime_segments, regime_transitions
    bars = [{"start": k * H4, "end": (k + 1) * H4, "regime": r, "confidence": 70, "checks_used": 5}
            for k, r in enumerate(["TRENDING BULLISH"] * 3 + ["TRANSITIONAL / UNCERTAIN"] * 2 + ["HIGH VOLATILITY"])]
    seg = regime_segments(bars)
    assert [(s["regime"], s["start"], s["end"], s["shade"]) for s in seg] == [
        ("TRENDING BULLISH", 0, 3 * H4, "bullish"), ("TRANSITIONAL / UNCERTAIN", 3 * H4, 5 * H4, None),
        ("HIGH VOLATILITY", 5 * H4, 6 * H4, "high_vol")]
    tr = regime_transitions(bars)
    assert [(t["time"], t["from"], t["to"]) for t in tr] == [(3 * H4, "TRENDING BULLISH", "TRANSITIONAL / UNCERTAIN"),
                                                             (5 * H4, "TRANSITIONAL / UNCERTAIN", "HIGH VOLATILITY")]


# ---------------------------------------------------------------- API
def test_overlays_api_uses_stored_events_and_excludes_superseded(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    adv = c.application.config["BTC_ADVANCED"]
    c.put("/api/settings/advanced", json={"shadow_enabled": False})
    adv.record()                                                     # sweeps are stored even without shadow rows
    stored = db.sweep_events(500)
    assert stored and db.shadow_rows(5) == []
    d = c.get("/api/advanced/overlays?tf=4h&limit=300").get_json()
    assert d["tf"] == "4h" and d["regime"]["bars"] and d["regime"]["segments"]
    keys = {m["key"] for m in d["sweeps"]}
    assert keys and keys <= {e["event_key"] for e in stored}         # only real stored events
    db.sweep_correct(next(iter(keys)), "superseded", "test", "t", now=1)
    again = c.get("/api/advanced/overlays?tf=4h&limit=300").get_json()
    assert next(iter(keys)) not in {m["key"] for m in again["sweeps"]}
    assert c.get("/api/advanced/overlays?tf=2h").status_code == 400


def test_overlays_api_explains_missing_data(config, db):
    c = make_client(config, db, [FakeProvider("fake1", fail=True)])
    r = c.get("/api/advanced/overlays?tf=1h")
    assert r.status_code == 200
    d = r.get_json()
    assert d["regime"]["bars"] == [] and d["sweeps"] == []
    assert any("No 4h candles" in n for n in d["notes"])


def test_chart_profile_last_200_or_visible_range(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    p = c.get("/api/advanced/profile?tf=1h&limit=300").get_json()
    assert p["status"] == "ok" and p["mode"] == "last" and p["candles_used"] == 200 and p["timeframe"] == "1h"
    rows = c.get("/api/candles?tf=1h&limit=300").get_json()["series"]["time"]
    t = [int(pd.Timestamp(x).value // 10**6) for x in rows]
    v = c.get(f"/api/advanced/profile?tf=1h&limit=300&mode=visible&from={t[100]}&to={t[150] + H1 - 1}").get_json()
    assert v["mode"] == "visible" and v["candles_used"] == 51
    assert v["range"]["from_open_time"] == t[100] and v["range"]["to_close_time"] == t[150] + H1 - 1
    few = c.get(f"/api/advanced/profile?tf=1h&limit=300&mode=visible&from={t[100]}&to={t[103]}").get_json()
    assert few["status"] == "too_few_candles"
    assert c.get("/api/advanced/profile?tf=1h&mode=visible").status_code == 400
