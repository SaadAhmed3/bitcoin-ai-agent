"""Event reaction study: closed candles only, no look-ahead, keyed to articles, macro windows, honest statistics.

The candles and events are synthetic, for tests only. Nothing here touches the network.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import FIXED_NOW, make_rows
from test_phase8 import FakeHttp, make_app

from services.intel_events import summarize_event
from services.intel_reactions import (HORIZONS, MACRO_KINDS, MACRO_WINDOW_MS, ReactionService, align_base, align_target, episodes,
                                      summarize)
from services.intel_service import IntelService
from services.regime import REGIMES

HOUR = 3_600_000
DAY = 24 * HOUR
T = (FIXED_NOW // HOUR) * HOUR
START = T - 500 * HOUR


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeCalendar:
    def __init__(self, releases):
        self.releases = releases

    def known_releases(self):
        return {"releases": self.releases, "warnings": []}


def release(kind, at, rid=None):
    return {"id": rid or f"{kind}-{at}", "kind": kind, "at": at, "event": f"{kind} test", "link": "https://example.test/c"}


def full_calendar(*extra):
    """Every macro kind known over a wide span, far from the test window, so the coverage is complete."""
    far = [release(k, T + s * 400 * DAY, f"{k}-{s}") for k in MACRO_KINDS for s in (-1, 1)]
    return FakeCalendar(far + list(extra))


def candle(open_time, close, volume=10.0):
    return {"open_time": open_time, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": volume,
            "close_time": open_time + HOUR - 1}


def seed(db, start=START, hours=1000, price=lambda i: 100.0 + i, volume=lambda i: 10.0, skip=(), fetched_at=None, tf="1h"):
    rows = [candle(start + i * HOUR, price(i), volume(i)) for i in range(hours) if start + i * HOUR not in skip]
    db.upsert_candles("binance", "BTCUSDT", tf, rows, fetched_at if fetched_at is not None else start + (hours + 1) * HOUR)
    return rows


def close_at(open_time, start=START):
    return 100.0 + (open_time - start) // HOUR


def art(aid, published, collected=None, publisher="Wire", title=None):
    return {"id": aid, "source_id": "t", "source_type": "news", "publisher": publisher, "title": title or f"Headline {aid}",
            "link": f"https://news.test/{aid}", "summary": "", "published_at": published,
            "collected_at": collected if collected is not None else (published or T) + 60_000}


def add_event(db, aid, published, category="war_geopolitics", collected=None, now=None):
    a = art(aid, published, collected)
    db.intel_add_articles([a])
    return db.intel_save_event(None, {**summarize_event([a], now or T), "category": category}, [aid], now or T)


def service(db, now, calendar=None):
    return ReactionService(db, calendar=calendar or full_calendar(), clock=Clock(now))


def table(db, name):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY 1,2,3")]


def outcome(db, aid, horizon):
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM intel_reaction_outcomes WHERE subject_type='article' AND subject_id=? AND horizon=?",
                         (aid, horizon)).fetchone()
    return dict(r) if r else None


def baseline(db, aid):
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM intel_reaction_baselines WHERE subject_type='article' AND subject_id=?", (aid,)).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------- candle alignment
def test_horizons_are_the_agreed_ones():
    assert HORIZONS == {"1h": 1, "4h": 4, "24h": 24, "7d": 168, "30d": 720}
    assert MACRO_WINDOW_MS == 2 * HOUR and set(MACRO_KINDS) == {"FOMC", "CPI", "NFP", "GDP", "PCE"}


def test_base_is_the_last_candle_closed_before_the_event():
    assert align_base(T + 30 * 60_000) == T - HOUR                  # the candle containing the event has not closed
    assert align_base(T + HOUR - 1) == T - HOUR
    assert align_base(T) == T - HOUR                                # on the boundary: the candle that just closed


def test_target_is_the_first_candle_closing_at_least_the_horizon_after_the_event():
    assert align_target(T, 1) == T                                  # closes at T+1h, exactly one hour after
    assert align_target(T + 30 * 60_000, 1) == T + HOUR             # closes at T+2h; T+1h would be only 30 min after
    assert align_target(T + 1, 24) == T + 24 * HOUR
    assert align_target(T, 720) == T + 719 * HOUR


def test_mid_candle_event_uses_the_right_candles(db):
    seed(db)
    event = T + 30 * 60_000
    add_event(db, "a1", event)
    service(db, T + 300 * HOUR).update()
    b = baseline(db, "a1")
    assert b["event_ms"] == event and b["base_open_time"] == T - HOUR and b["base_close"] == close_at(T - HOUR)
    o = outcome(db, "a1", "1h")
    assert o["target_open_time"] == T + HOUR and o["target_close"] == close_at(T + HOUR)
    assert o["window_hours"] == 2
    assert o["return_pct"] == pytest.approx((close_at(T + HOUR) / close_at(T - HOUR) - 1) * 100)


def test_baseline_has_volume_atr_and_regime_from_closed_candles(db):
    seed(db, volume=lambda i: 30.0 if START + i * HOUR == T - HOUR else 10.0)
    add_event(db, "a1", T + 10)
    service(db, T + 300 * HOUR).update()
    b = baseline(db, "a1")
    assert b["volume_ratio"] == pytest.approx(3.0)                  # the base candle against the 24 before it
    assert b["atr_pct"] > 0
    assert b["regime"] is None                                      # no 4h history in this test: never guessed


def test_regime_is_labelled_from_4h_history_when_it_exists(db):
    seed(db)
    db.upsert_candles("binance", "BTCUSDT", "4h", make_rows(400, "4h", end_ms=T), T)
    add_event(db, "a1", T + 10)
    service(db, T + 300 * HOUR).update()
    assert baseline(db, "a1")["regime"] in REGIMES


# ---------------------------------------------------------------- no look-ahead
def test_candles_after_the_event_never_change_the_baseline(tmp_path):
    from database import Database
    out = []
    for name, later in (("a", lambda i: 100.0 + i), ("b", lambda i: 100.0 + i if START + i * HOUR < T else 9_000.0 - i)):
        d = Database(str(tmp_path / f"{name}.sqlite3"))
        d.init()
        seed(d, price=later, volume=lambda i: 10.0 if START + i * HOUR < T else 99_999.0)
        db_rows = make_rows(400, "4h", end_ms=T)
        d.upsert_candles("binance", "BTCUSDT", "4h", db_rows, T)
        if name == "b":                                             # a wild 4h future too
            d.upsert_candles("binance", "BTCUSDT", "4h", [candle(T + i * 4 * HOUR, 5.0) for i in range(50)], T + 300 * HOUR)
        add_event(d, "a1", T + 10)
        service(d, T + 300 * HOUR).update()
        b = baseline(d, "a1")
        out.append({k: b[k] for k in ("base_open_time", "base_close", "volume_ratio", "atr_pct", "regime")})
    assert out[0] == out[1]


def test_normal_move_comes_only_from_before_the_event(tmp_path):
    from database import Database
    out = []
    for name, later in (("a", lambda i: 100.0 + i), ("b", lambda i: 100.0 + i if START + i * HOUR < T else 100.0 + i * 50)):
        d = Database(str(tmp_path / f"{name}.sqlite3"))
        d.init()
        seed(d, price=later)
        add_event(d, "a1", T + 10)
        service(d, T + 300 * HOUR).update()
        o = outcome(d, "a1", "1h")
        out.append((o["normal_abs_pct"], o["normal_median_pct"], o["normal_samples"]))
    assert out[0] == out[1] and out[0][2] >= 30


def test_forming_candle_is_never_used(db):
    seed(db, hours=(T + HOUR - START) // HOUR)                      # closed candles up to T
    db.upsert_candles("binance", "BTCUSDT", "1h", [candle(T, 777.0)], T + 10 * 60_000)    # still forming
    add_event(db, "a1", T)
    service(db, T + 20 * 60_000).update()
    assert baseline(db, "a1") and outcome(db, "a1", "1h") is None


def test_candle_saved_while_forming_and_never_refetched_is_not_treated_as_closed(db):
    """Its close_time has passed, but the stored values are from part-way through the hour."""
    seed(db, hours=(T - START) // HOUR)
    db.upsert_candles("binance", "BTCUSDT", "1h", [candle(T, 777.0)], T + 10 * 60_000)
    add_event(db, "a1", T)
    s = service(db, T + 5 * HOUR)
    s.update()
    assert outcome(db, "a1", "1h") is None
    db.upsert_candles("binance", "BTCUSDT", "1h", [candle(T, 123.0)], T + HOUR + 5)       # refetched after it closed
    s.update()
    assert outcome(db, "a1", "1h")["target_close"] == 123.0


def test_event_before_any_candle_closed_has_no_future_reaction(db):
    seed(db)
    add_event(db, "future", T + 200 * HOUR)
    service(db, T).update()                                         # the clock is before the article's time
    assert baseline(db, "future") is None


# ---------------------------------------------------------------- missing data
def test_missing_base_candle_means_no_reaction_not_a_neighbour(db):
    seed(db, skip={T - HOUR})
    eid = add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    s.update()
    assert baseline(db, "a1") is None and outcome(db, "a1", "1h") is None
    d = s.event_detail(eid)
    assert d["baseline"] is None and "candle" in d["baseline_note"].lower()
    assert {h["status"] for h in d["horizons"]} == {"unavailable"}


def test_missing_target_candle_is_shown_missing_and_never_filled_from_a_neighbour(db):
    seed(db, skip={T + 3 * HOUR})
    eid = add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    s.update()
    assert outcome(db, "a1", "4h") is None and outcome(db, "a1", "1h")
    st = {h["horizon"]: h["status"] for h in s.event_detail(eid)["horizons"]}
    assert st["4h"] == "missing" and st["1h"] == "measured"


def test_horizon_not_reached_yet_is_pending(db):
    seed(db)
    eid = add_event(db, "a1", T)
    s = service(db, T + 30 * HOUR)
    s.update()
    st = {h["horizon"]: h for h in s.event_detail(eid)["horizons"]}
    assert st["24h"]["status"] == "measured"
    assert st["7d"]["status"] == "pending" and st["30d"]["status"] == "pending"
    assert st["7d"]["return_pct"] is None


def test_no_candles_at_all_works(db):
    eid = add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    s.update()
    assert s.event_detail(eid)["baseline"] is None
    assert s.study()["categories"]


def test_short_history_gives_no_normal_move_rather_than_a_guess(db):
    seed(db, start=T - 3 * HOUR, hours=10)
    add_event(db, "a1", T)
    service(db, T + 20 * HOUR).update()
    o = outcome(db, "a1", "1h")
    assert o["normal_abs_pct"] is None and o["return_pct"] is not None


# ---------------------------------------------------------------- event time and first seen
def test_event_time_is_the_earliest_publication_and_first_seen_is_stored():
    ev = summarize_event([art("a1", T + 5 * HOUR, T + 6 * HOUR), art("a2", T + 2 * HOUR, T + 7 * HOUR), art("a3", T + 4 * HOUR, T + 3 * HOUR)], T)
    assert ev["event_time_at"] == T + 2 * HOUR and ev["anchor_article_id"] == "a2"
    assert ev["first_seen_at"] == T + 3 * HOUR
    assert ev["time_flag"] is None


def test_flag_when_no_publication_time():
    ev = summarize_event([art("a1", None, T)], T)
    assert ev["event_time_at"] is None and ev["anchor_article_id"] is None and ev["time_flag"] == "publication_missing"
    assert ev["first_seen_at"] == T


def test_flag_when_publication_is_later_than_first_seen():
    ev = summarize_event([art("a1", T + 2 * HOUR, T)], T)
    assert ev["time_flag"] == "published_after_first_seen" and ev["event_time_at"] == T + 2 * HOUR


def test_flag_when_only_some_articles_have_a_publication_time():
    ev = summarize_event([art("a1", T, T + 60_000), art("a2", None, T + HOUR)], T)
    assert ev["time_flag"] == "publication_partial" and ev["event_time_at"] == T


def test_event_fields_are_stored_and_returned(db):
    eid = add_event(db, "a1", T, collected=T + 60_000)
    row = next(e for e in db.intel_events(0) if e["id"] == eid)
    assert (row["event_time_at"], row["first_seen_at"], row["anchor_article_id"], row["time_flag"]) == (T, T + 60_000, "a1", None)


def test_flagged_events_are_reported_and_left_out_of_the_statistics(db):
    seed(db)
    add_event(db, "ok", T)
    add_event(db, "late", T + 5 * HOUR, collected=T + 4 * HOUR)
    add_event(db, "none", None, collected=T)
    s = service(db, T + 300 * HOUR)
    s.update()
    st = s.study()
    war = next(c for c in st["categories"] if c["category"] == "war_geopolitics")
    assert war["clean"]["horizons"]["1h"]["n"] == 1
    assert st["excluded"]["publication_missing"] == 1 and st["excluded"]["published_after_first_seen"] == 1
    assert baseline(db, "none") is None                              # no publication time: never measured from first-seen


def test_reaction_uses_publication_time_not_first_seen(db):
    seed(db)
    add_event(db, "a1", T - 50 * HOUR, collected=T)
    service(db, T + 300 * HOUR).update()
    assert baseline(db, "a1")["event_ms"] == T - 50 * HOUR


# ---------------------------------------------------------------- keyed to articles
def test_reactions_survive_a_rebuild_of_event_membership(db):
    seed(db)
    add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    s.update()
    before = table(db, "intel_reaction_baselines"), table(db, "intel_reaction_outcomes")
    assert before[0] and before[1]
    db.intel_reset_events()
    assert (table(db, "intel_reaction_baselines"), table(db, "intel_reaction_outcomes")) == before
    assert all(r["subject_type"] == "article" and r["subject_id"] == "a1" for r in before[0])


def test_rebuilt_event_finds_the_reaction_of_its_new_earliest_article(db):
    seed(db)
    db.intel_add_articles([art("old", T - 5 * HOUR), art("new", T)])
    first = db.intel_save_event(None, summarize_event([art("new", T)], T), ["new"], T)
    s = service(db, T + 300 * HOUR)
    s.update()
    assert s.event_detail(first)["baseline"]["event_ms"] == T
    db.intel_reset_events()
    both = db.intel_save_event(None, summarize_event([art("new", T), art("old", T - 5 * HOUR)], T), ["new", "old"], T)
    d = s.event_detail(both)
    assert d["anchor_article"]["id"] == "old" and d["baseline"]["event_ms"] == T - 5 * HOUR


def test_tables_have_no_event_id_column(db):
    for t in ("intel_reaction_baselines", "intel_reaction_outcomes"):
        with db.connect() as conn:
            cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({t})")]
        assert "event_id" not in cols and {"subject_type", "subject_id"} <= set(cols)


# ---------------------------------------------------------------- append-only
@pytest.mark.parametrize("name", ["intel_reaction_baselines", "intel_reaction_outcomes"])
def test_reaction_tables_are_append_only(db, name):
    seed(db)
    add_event(db, "a1", T)
    service(db, T + 300 * HOUR).update()
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(f"DELETE FROM {name}")
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(f"UPDATE {name} SET computed_at = 1")


def test_a_later_candle_revision_never_rewrites_a_stored_reaction(db):
    seed(db)
    add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    s.update()
    first = outcome(db, "a1", "1h")
    db.upsert_candles("binance", "BTCUSDT", "1h", [candle(T, 1.0)], T + 299 * HOUR)
    s.update()
    assert outcome(db, "a1", "1h") == first


def test_update_is_idempotent(db):
    seed(db)
    add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    first = s.update()
    assert first["outcomes_added"] >= 3
    assert s.update()["outcomes_added"] == 0


# ---------------------------------------------------------------- statistics
def row(ret, base, hours=1, normal=1.0):
    return {"return_pct": ret, "normal_abs_pct": normal, "normal_median_pct": 0.1, "base_time": base, "target_time": base + hours * HOUR}


def spread(n, gap=3):
    return [row((i % 7) - 3.0, T + i * gap * HOUR) for i in range(n)]


def test_median_range_and_normal_comparison():
    s = summarize([row(-2, T), row(1, T + 5 * HOUR), row(4, T + 10 * HOUR)], "k")
    assert (s["n"], s["median_pct"], s["min_pct"], s["max_pct"]) == (3, 1.0, -2, 4)
    assert s["median_abs_pct"] == 2.0 and s["normal_abs_pct"] == 1.0 and s["abs_vs_normal"] == 2.0
    assert s["normal_median_pct"] == 0.1


def test_empty_group_has_no_numbers():
    s = summarize([], "k")
    assert s["n"] == 0 and s["median_pct"] is None and s["ci95"] is None and s["sample_flag"] == "too_few"


def test_overlapping_windows_are_one_episode_and_get_no_interval():
    rows = [row(i, T + i * HOUR, hours=168) for i in range(40)]
    assert len(episodes(rows)) == 1
    s = summarize(rows, "k")
    assert s["n"] == 40 and s["episodes"] == 1 and s["ci95"] is None and s["sample_flag"] == "too_few"


def test_touching_windows_are_separate_episodes():
    assert len(episodes([row(1, T, hours=1), row(1, T + HOUR, hours=1)])) == 2


@pytest.mark.parametrize("n,flag", [(1, "too_few"), (4, "too_few"), (5, "small"), (29, "small"), (30, "ok"), (60, "ok")])
def test_small_sample_flags(n, flag):
    s = summarize(spread(n), "k")
    assert s["sample_flag"] == flag and s["sample_note"]


def test_few_episodes_lower_the_flag_even_with_many_events():
    rows = [row(i % 5, T + (i // 10) * 100 * HOUR + (i % 10) * HOUR, hours=48) for i in range(60)]
    s = summarize(rows, "k")
    assert s["n"] == 60 and s["episodes"] == 6 and s["sample_flag"] == "small"


def test_bootstrap_interval_is_reproducible_and_contains_the_median():
    a, b = summarize(spread(40), "CPI|24h"), summarize(spread(40), "CPI|24h")
    assert a["ci95"] == b["ci95"]
    assert a["ci95"][0] <= a["median_pct"] <= a["ci95"][1] and a["ci95"][0] < a["ci95"][1]


def test_no_interval_below_five_episodes():
    assert summarize(spread(4), "k")["ci95"] is None
    assert summarize(spread(5), "k")["ci95"] is not None


def test_normal_move_missing_gives_no_ratio():
    s = summarize([{**row(1, T), "normal_abs_pct": None, "normal_median_pct": None}], "k")
    assert s["normal_abs_pct"] is None and s["abs_vs_normal"] is None


# ---------------------------------------------------------------- macro windows
def test_events_within_two_hours_of_a_release_are_shown_separately(db):
    seed(db)
    r = T + 100 * HOUR
    add_event(db, "before-edge", r - 2 * HOUR)
    add_event(db, "after-edge", r + 2 * HOUR)
    add_event(db, "inside", r + 30 * 60_000)
    add_event(db, "just-out", r + 2 * HOUR + 1)
    add_event(db, "far", r - 50 * HOUR)
    s = service(db, T + 400 * HOUR, full_calendar(release("CPI", r)))
    s.update()
    st = s.study()
    war = next(c for c in st["categories"] if c["category"] == "war_geopolitics")
    assert war["clean"]["horizons"]["1h"]["n"] == 2
    assert war["macro_window"]["horizons"]["1h"]["n"] == 3
    assert st["totals"]["clean"] == 2 and st["totals"]["macro_window"] == 3


def test_macro_window_is_named_on_the_event(db):
    seed(db)
    r = T + 100 * HOUR
    eid = add_event(db, "a1", r + HOUR)
    other = add_event(db, "a2", r - 50 * HOUR)
    s = service(db, T + 400 * HOUR, full_calendar(release("FOMC", r)))
    s.update()
    assert [x["kind"] for x in s.event_detail(eid)["macro_window"]["releases"]] == ["FOMC"]
    assert s.event_detail(eid)["macro_window"]["overlaps"] is True
    assert s.event_detail(other)["macro_window"]["overlaps"] is False


@pytest.mark.parametrize("cal", [None, FakeCalendar([]), FakeCalendar([release("FOMC", T)])])
def test_incomplete_calendar_gives_no_clean_statistics(db, cal):
    """Without every release date we cannot say an event was clear of macro news, so it is not called clean."""
    seed(db)
    add_event(db, "a1", T - 50 * HOUR)
    s = ReactionService(db, calendar=cal, clock=Clock(T + 300 * HOUR))
    s.update()
    st = s.study()
    war = next(c for c in st["categories"] if c["category"] == "war_geopolitics")
    assert war["clean"]["horizons"]["1h"]["n"] == 0
    assert st["excluded"]["macro_unknown"] == 1
    assert st["calendar"]["complete"] is False and st["calendar"]["warning"]


def test_event_outside_the_calendar_span_is_unknown_not_clean(db):
    seed(db)
    cal = FakeCalendar([release(k, T + d * HOUR, f"{k}-{d}") for k in MACRO_KINDS for d in (100, 2000)])
    add_event(db, "before-span", T)
    add_event(db, "inside-span", T + 300 * HOUR)
    s = ReactionService(db, calendar=cal, clock=Clock(T + 500 * HOUR))
    s.update()
    st = s.study()
    assert st["excluded"]["macro_unknown"] == 1 and st["totals"]["clean"] == 1


def test_calendar_failure_keeps_the_last_snapshot(db):
    seed(db)
    add_event(db, "a1", T - 50 * HOUR)
    cal = full_calendar()
    s = ReactionService(db, calendar=cal, clock=Clock(T + 300 * HOUR))
    s.update()

    def broken():
        raise RuntimeError("down")
    cal.known_releases = broken
    s.update()
    assert s.study()["calendar"]["complete"] is True


# ---------------------------------------------------------------- macro releases
def test_macro_release_reactions_are_by_type_and_never_above_or_below_expectations(db):
    seed(db)
    r1, r2 = T - 100 * HOUR, T + 50 * HOUR
    cal = full_calendar(release("CPI", r1, "cpi-1"), release("CPI", r2, "cpi-2"), release("NFP", r2, "nfp-2"))
    s = service(db, T + 300 * HOUR, cal)
    s.update()
    st = s.study()
    cpi = next(m for m in st["macro_releases"] if m["kind"] == "CPI")
    assert cpi["label"] == "CPI release" and cpi["horizons"]["1h"]["n"] == 2
    assert cpi["shares_window"] == 1                                # cpi-2 lands with nfp-2
    blob = json.dumps(st["macro_releases"]).lower()
    assert "above" not in blob and "below" not in blob and "beat" not in blob
    assert "consensus" in st["consensus_note"].lower()


def test_future_release_is_not_measured(db):
    seed(db)
    s = service(db, T, full_calendar(release("CPI", T + 10 * HOUR, "soon")))
    s.update()
    assert not [r for r in table(db, "intel_reaction_baselines") if r["subject_id"].startswith("soon")]


def test_rescheduled_release_is_measured_at_its_new_time_and_the_old_row_is_not_counted(db):
    seed(db)
    s = service(db, T + 300 * HOUR, full_calendar(release("CPI", T, "cpi-x")))
    s.update()
    assert next(m for m in s.study()["macro_releases"] if m["kind"] == "CPI")["horizons"]["1h"]["n"] == 1
    s2 = service(db, T + 300 * HOUR, full_calendar(release("CPI", T + 2 * HOUR, "cpi-x")))
    s2.update()
    assert next(m for m in s2.study()["macro_releases"] if m["kind"] == "CPI")["horizons"]["1h"]["n"] == 1
    times = sorted(r["event_ms"] for r in table(db, "intel_reaction_baselines") if r["subject_id"].startswith("cpi-x"))
    assert times == [T, T + 2 * HOUR]                                # both stored: append-only


# ---------------------------------------------------------------- other markets, daily and labelled
def test_other_markets_are_daily_labelled_and_use_values_from_before_the_event_day(db):
    day = "2025-10-09"
    assert __import__("datetime").datetime.fromtimestamp(T / 1000, __import__("datetime").timezone.utc).strftime("%Y-%m-%d") == day
    db.intel_add_market_points("us10y", [("2025-10-07", 4.0), ("2025-10-08", 4.1), ("2025-10-09", 9.9), ("2025-10-10", 4.3),
                                         ("2025-10-16", 4.7)], "U.S. Treasury", T)
    eid = add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    s.update()
    d = s.event_detail(eid)
    m = next(x for x in d["other_markets"] if x["id"] == "us10y")
    assert m["before"] == {"date": "2025-10-08", "value": 4.1}       # not the event day's own close
    assert m["after"]["1d"]["date"] == "2025-10-10" and m["after"]["1d"]["change"] == pytest.approx(0.2)
    assert m["after"]["7d"]["date"] == "2025-10-16"
    assert m["after"]["30d"] is None
    assert "daily" in d["other_markets_note"].lower()
    assert m["source"] == "U.S. Treasury" and m["delay_label"]


def test_other_markets_missing_are_absent_not_estimated(db):
    eid = add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    m = next(x for x in s.event_detail(eid)["other_markets"] if x["id"] == "brent")
    assert m["before"] is None and all(v is None for v in m["after"].values())


# ---------------------------------------------------------------- honesty and wording
def test_study_says_what_it_is_not(db):
    st = service(db, T).study()
    assert st["label"] == "Context, not part of the signal"
    text = " ".join(st["notes"]).lower()
    assert "correlation" in text and "causation" in text and "overlap" in text and "descriptive" in text
    assert "by chance" in text                                       # many groups and horizons are compared at once


def test_study_payload_has_no_predictive_language(db):
    seed(db)
    add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    s.update()
    blob = json.dumps(s.study()).lower()
    for word in ("predict", "signal strength", "buy", "sell", "bullish", "bearish", "will rise", "will fall"):
        assert word not in blob, word


def test_study_is_served_from_a_short_cache_and_refreshes_after_update(db):
    seed(db)
    add_event(db, "a1", T)
    s = service(db, T + 300 * HOUR)
    assert next(c for c in s.study()["categories"] if c["category"] == "war_geopolitics")["clean"]["horizons"]["1h"]["n"] == 0
    s.update()
    assert next(c for c in s.study()["categories"] if c["category"] == "war_geopolitics")["clean"]["horizons"]["1h"]["n"] == 1


# ---------------------------------------------------------------- wiring and routes
def test_service_refresh_runs_the_study(db):
    seed(db)
    now = T + 300 * HOUR
    db.intel_add_articles([art("a1", T)])
    intel = IntelService(db, http=FakeHttp(), sources=[], clock=Clock(now), calendar=full_calendar())
    intel.refresh(force=True)
    assert baseline(db, "a1") is not None


def test_a_failing_study_does_not_break_the_refresh(db, monkeypatch):
    intel = IntelService(db, http=FakeHttp(), sources=[], clock=Clock(T), calendar=full_calendar())

    def boom():
        raise RuntimeError("no")
    monkeypatch.setattr(intel.reactions, "update", boom)
    assert intel.refresh(force=True)["reactions"] == {"error": "RuntimeError"}


def test_routes(config, db):
    c, app = make_app(config, db)
    seed(db)
    intel = app.config["BTC_INTEL"]
    eid = add_event(db, "a1", T - 50 * HOUR)
    intel.reactions.update()
    r = c.get("/api/intel/reactions")
    assert r.status_code == 200 and r.get_json()["label"] == "Context, not part of the signal"
    r = c.get(f"/api/intel/events/{eid}/reaction")
    d = r.get_json()
    assert r.status_code == 200 and d["label"] == "Context, not part of the signal" and d["anchor_article"]["id"] == "a1"
    assert d["anchor_article"]["link"].startswith("https://")
    assert c.get("/api/intel/events/999999/reaction").status_code == 404


def test_event_list_carries_the_time_fields(config, db):
    c, app = make_app(config, db)
    now = app.config["BTC_INTEL"].clock()
    add_event(db, "a1", now - HOUR, collected=now - 30 * 60_000, now=now)
    e = c.get("/api/intel/events").get_json()["events"][0]
    assert e["event_time_at"] == now - HOUR and e["first_seen_at"] == now - 30 * 60_000 and e["time_flag"] is None


def test_known_releases_from_the_real_calendar_service(db):
    from test_intel_sources import Clock as C, cal, ms
    service_, _ = cal(db, C(ms("2026-11-03T00:00:00Z")))
    out = service_.known_releases()
    kinds = {r["kind"] for r in out["releases"]}
    assert {"FOMC", "CPI", "NFP"} <= kinds
    assert all(isinstance(r["at"], int) and r["id"] for r in out["releases"])


def test_migration_from_schema_16_adds_only_and_keeps_data(tmp_path):
    from database import Database
    from database.db import SCHEMA_VERSION
    path = tmp_path / "old.sqlite3"
    d = Database(str(path))
    d.init()
    add_event(d, "a1", T)
    with d.connect() as conn:
        for t in ("intel_reaction_baselines", "intel_reaction_outcomes"):
            conn.execute(f"DROP TABLE {t}")
        for col in ("event_time_at", "first_seen_at", "anchor_article_id", "time_flag"):
            conn.execute(f"ALTER TABLE intel_events DROP COLUMN {col}")
        conn.execute("UPDATE schema_meta SET value='16' WHERE key='schema_version'")
    with d.connect() as conn:
        before = [dict(r) for r in conn.execute("SELECT id, title, category FROM intel_events")]
    Database(str(path)).init()
    assert SCHEMA_VERSION >= 17
    assert list(tmp_path.glob("old.sqlite3.backup-*-schema-16"))
    with d.connect() as conn:
        assert [dict(r) for r in conn.execute("SELECT id, title, category FROM intel_events")] == before
        assert conn.execute("SELECT id FROM intel_articles").fetchone()["id"] == "a1"
        assert {r["name"] for r in conn.execute("PRAGMA table_info(intel_events)")} >= {"event_time_at", "first_seen_at", "anchor_article_id", "time_flag"}
        assert {"intel_reaction_baselines", "intel_reaction_outcomes"} <= {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_grouping_rebuilds_so_old_events_get_the_new_fields(db):
    from services.intel_service import GROUPING_RULES_VERSION
    assert GROUPING_RULES_VERSION >= 3
    db.intel_add_articles([art("a1", T)])
    db.cache_set("intel:grouping_version", 2, T)
    IntelService(db, http=FakeHttp(), sources=[], clock=Clock(T)).regroup()
    e = db.intel_events(0)[0]
    assert e["event_time_at"] == T and e["anchor_article_id"] == "a1"
