"""Rule-based impact assessment: labels from evidence, factors shown, no fixed event-to-direction assumptions."""
from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import FIXED_NOW
from test_intel_reactions import candle, full_calendar, release, seed
from test_phase8 import make_app

from services.intel_events import article_set_hash, summarize_event
from services.intel_impact import IMPACT_HORIZONS, LABELS, RULES_VERSION, ImpactService, assess, interpretations
from services.intel_reactions import ReactionService

HOUR = 3_600_000
NOW = (FIXED_NOW // HOUR) * HOUR
FACTORS = {"severity", "source_credibility", "verification", "novelty", "market_relevance", "already_priced_in", "historical_reaction"}


def art(aid, title="A report about something", publisher="Wire", source_type="news", hours_ago=1, summary=""):
    return {"id": aid, "source_id": "t", "source_type": source_type, "publisher": publisher, "title": title, "link": f"https://news.test/{aid}",
            "summary": summary, "published_at": NOW - hours_ago * HOUR, "collected_at": NOW - hours_ago * HOUR + 60_000}


def event(arts, category="war_geopolitics", status="confirmed"):
    return {**summarize_event(arts, NOW), "category": category, "status": status}


def stat(median=0.0, normal=1.0, ci=None, flag="ok", n=40, episodes=30):
    return {"n": n, "episodes": episodes, "median_pct": median, "normal_abs_pct": normal, "ci95": ci, "sample_flag": flag, "abs_vs_normal": 1.0,
            "sample_note": "note"}


NO_HISTORY = {h: stat(flag="too_few", n=2, episodes=1) for h in IMPACT_HORIZONS}
PRICED_NONE = {"move_pct": 0.0, "normal_abs_pct": 1.0, "priced": 0.0}


def run(arts, category="war_geopolitics", status="confirmed", history=None, priced=None):
    return assess(event(arts, category, status), arts, history or NO_HISTORY, priced or PRICED_NONE, NOW)


# ---------------------------------------------------------------- shape
def test_labels_horizons_and_score_range():
    assert LABELS == ("Bullish", "Bearish", "Neutral", "Mixed", "Uncertain")
    assert list(IMPACT_HORIZONS) == ["0-24h", "1-7d", "1-3m"]
    a = run([art("a")])
    assert set(a["horizons"]) == set(IMPACT_HORIZONS)
    for h in a["horizons"].values():
        assert h["label"] in LABELS and -100 <= h["score"] <= 100
    assert "not a probability" in a["score_note"].lower()


def test_all_seven_factors_are_shown_with_a_value_and_a_note():
    f = {x["key"]: x for x in run([art("a")])["factors"]}
    assert set(f) == FACTORS
    for x in f.values():
        assert x["label"] and x["note"]
    for k in FACTORS - {"historical_reaction"}:
        assert 0 <= f[k]["value"] <= 1


def test_historical_factor_shows_the_sample_size_for_each_horizon():
    h = {k: stat(median=-0.5, ci=[-1.0, -0.2], n=44 + i, episodes=30) for i, k in enumerate(IMPACT_HORIZONS)}
    f = next(x for x in run([art("a")], history=h)["factors"] if x["key"] == "historical_reaction")
    assert f["by_horizon"]["0-24h"]["n"] == 44 and f["by_horizon"]["1-3m"]["n"] == 46
    assert f["by_horizon"]["0-24h"]["sample_flag"] == "ok"


# ---------------------------------------------------------------- no fixed assumptions
@pytest.mark.parametrize("title,category", [("Russia launches missile strikes on Kyiv", "war_geopolitics"), ("War breaks out as troops cross the border", "war_geopolitics"),
                                            ("Federal Reserve cuts rates by half a point", "central_banks"), ("Fed raises rates sharply", "central_banks"),
                                            ("Inflation rises above forecasts", "macro"), ("Exchange hacked, $900 million stolen", "crypto_incident"),
                                            ("SEC approves new spot ETF", "regulation"), ("Oil jumps after pipeline attack", "energy_commodities")])
def test_event_type_alone_never_gives_a_direction(title, category):
    a = run([art("a", title=title, source_type="official", publisher="Federal Reserve")], category=category, status="official_statement")
    assert {h["label"] for h in a["horizons"].values()} == {"Uncertain"}
    assert {h["score"] for h in a["horizons"].values()} == {0}


def test_same_evidence_gives_the_same_direction_in_any_category():
    h = {k: stat(median=-0.8, ci=[-1.2, -0.4]) for k in IMPACT_HORIZONS}
    labels = {c: run([art("a")], category=c, history=h)["horizons"]["0-24h"]["label"]
              for c in ("war_geopolitics", "central_banks", "crypto_industry", "elections_politics")}
    assert set(labels.values()) == {"Bearish"}


def test_positive_history_for_war_gives_bullish_never_assumed_bearish():
    h = {k: stat(median=0.8, ci=[0.4, 1.2]) for k in IMPACT_HORIZONS}
    assert run([art("a", title="War breaks out")], history=h)["horizons"]["0-24h"]["label"] == "Bullish"


@pytest.mark.parametrize("category", ["war_geopolitics", "elections_politics", "macro", "central_banks", "energy_commodities", "regulation",
                                      "crypto_industry", "crypto_incident", "other"])
def test_every_category_shows_competing_interpretations(category):
    items = interpretations(category)
    assert len(items) >= 2
    stances = {i["stance"] for i in items}
    assert {"could_support", "could_weigh"} <= stances
    for i in items:
        assert i["text"] and "will" not in i["text"].lower().split()


def test_assessment_carries_the_interpretations():
    assert len(run([art("a")])["interpretations"]) >= 2


# ---------------------------------------------------------------- the rules
def test_no_evidence_is_uncertain():
    assert run([art("a")])["horizons"]["0-24h"]["label"] == "Uncertain"


def test_too_few_history_is_not_evidence():
    h = {k: stat(median=-3.0, ci=[-4.0, -2.0], flag="too_few", n=3, episodes=2) for k in IMPACT_HORIZONS}
    assert run([art("a")], history=h)["horizons"]["0-24h"]["label"] == "Uncertain"


def test_history_interval_including_zero_is_neutral_evidence():
    h = {k: stat(median=0.9, ci=[-0.2, 1.5]) for k in IMPACT_HORIZONS}
    a = run([art("a")], history=h)
    assert a["horizons"]["0-24h"]["label"] == "Neutral" and a["horizons"]["0-24h"]["score"] == 0


def test_small_sample_counts_half_of_ok():
    ok = {k: stat(median=-0.6, ci=[-1.0, -0.2]) for k in IMPACT_HORIZONS}
    small = {k: stat(median=-0.6, ci=[-1.0, -0.2], flag="small", n=10, episodes=10) for k in IMPACT_HORIZONS}
    a, b = run([art("a")], history=ok), run([art("a")], history=small)
    assert b["horizons"]["0-24h"]["score"] == pytest.approx(a["horizons"]["0-24h"]["score"] / 2, abs=1)


def test_reported_btc_move_is_direction_evidence_short_term_only():
    a = run([art("a", title="Bitcoin slides as traders retreat"), art("b", title="BTC falls below support", publisher="Other")])
    assert a["horizons"]["0-24h"]["label"] == "Bearish"
    assert a["horizons"]["1-3m"]["label"] == "Uncertain"
    assert abs(a["horizons"]["1-7d"]["score"]) < abs(a["horizons"]["0-24h"]["score"])


@pytest.mark.parametrize("title", ["Binance Hack: Why Bitcoin Didn't Crash Like It Did in 2018",        # real headlines misread on 2026-11-02
                                   "AI found a way to crash bitcoin nodes on Windows and Macs. It's now public",
                                   "Bitcoin ETFs sink into October outflows with $643M exit after Binance",
                                   "Bitcoin risks worst October in 12 years as tech sell-off spreads to gold",
                                   "Bitcoin could fall further, analysts say", "Will bitcoin rise after the vote?"])
def test_misleading_wording_about_btc_is_not_a_reported_move(title):
    a = run([art("a", title=title)])
    assert a["wording"] == {"reports_up": 0, "reports_down": 0}
    assert a["horizons"]["0-24h"]["label"] == "Uncertain"


@pytest.mark.parametrize("title,up,down", [("Bitcoin drops to $80,000 on Fed dissent", 0, 1), ("Bitcoin slides below $77,000, BNB drops amid Binance hack", 0, 1),
                                           ("Bitcoin rebounds above $81,000 as Asian chip rally spills into crypto", 1, 0), ("BTC price surges past resistance", 1, 0),
                                           ("Bitcoin's price falls sharply", 0, 1), ("Bitcoin ETF inflows climb to $176M as BTC rebounds above $80K", 1, 0),
                                           ("Crypto markets tumble on the news", 0, 1)])
def test_real_reports_of_btc_moving_still_count(title, up, down):
    assert run([art("a", title=title)])["wording"] == {"reports_up": up, "reports_down": down}


def test_reports_of_btc_moving_alone_are_weak_evidence():
    best = run([art("a", title="Bitcoin plunges 10% after a $5 billion hack and an emergency halt", source_type="official", publisher="Federal Reserve")], status="official_statement")
    assert -50 <= best["horizons"]["0-24h"]["score"] < 0


def test_a_move_of_something_else_is_not_btc_evidence():
    assert run([art("a", title="Oil slides as traders retreat")])["horizons"]["0-24h"]["label"] == "Uncertain"


def test_opposite_reports_are_mixed():
    a = run([art("a", title="Bitcoin surges to a new high"), art("b", title="Bitcoin plunges after the rally", publisher="Other")])
    assert a["horizons"]["0-24h"]["label"] == "Mixed"


def test_reports_against_history_are_mixed():
    h = {k: stat(median=0.8, ci=[0.4, 1.2]) for k in IMPACT_HORIZONS}
    a = run([art("a", title="Bitcoin slides hard")], history=h)
    assert a["horizons"]["0-24h"]["label"] == "Mixed"


@pytest.mark.parametrize("status,label", [("unverified", "Uncertain"), ("speculative", "Uncertain"), ("disputed", "Mixed")])
def test_weak_status_never_gets_a_direction(status, label):
    h = {k: stat(median=-0.8, ci=[-1.2, -0.4]) for k in IMPACT_HORIZONS}
    assert run([art("a", title="Bitcoin slides")], status=status, history=h)["horizons"]["0-24h"]["label"] == label


def test_priced_in_shrinks_the_score():
    h = {k: stat(median=-0.8, ci=[-1.2, -0.4]) for k in IMPACT_HORIZONS}
    a = run([art("a")], history=h)["horizons"]["0-24h"]["score"]
    b = run([art("a")], history=h, priced={"move_pct": -5.0, "normal_abs_pct": 1.0, "priced": 1.0})["horizons"]["0-24h"]["score"]
    assert abs(b) == pytest.approx(abs(a) / 2, abs=1)


def test_unknown_priced_in_says_so():
    f = next(x for x in run([art("a")], priced={"move_pct": None, "normal_abs_pct": None, "priced": 0.0})["factors"] if x["key"] == "already_priced_in")
    assert "no" in f["note"].lower()


def test_official_source_scores_higher_credibility_than_unknown():
    high = next(x for x in run([art("a", source_type="official", publisher="Federal Reserve")])["factors"] if x["key"] == "source_credibility")
    low = next(x for x in run([art("a", publisher="randomblog.example")])["factors"] if x["key"] == "source_credibility")
    assert high["value"] > low["value"]


def test_fresh_event_is_more_novel_than_old():
    novelty = lambda hours: next(x for x in run([art("a", hours_ago=hours)])["factors"] if x["key"] == "novelty")["value"]  # noqa: E731
    assert novelty(1) > novelty(30) > novelty(200)


def test_market_relevance_comes_from_the_words_and_category():
    rel = lambda t, c: next(x for x in run([art("a", title=t)], category=c)["factors"] if x["key"] == "market_relevance")["value"]  # noqa: E731
    assert rel("Bitcoin ETF news", "crypto_industry") > rel("Parliament votes", "elections_politics")


def test_payload_never_gives_trading_advice():
    blob = json.dumps(run([art("a")])).lower()
    for w in (" buy ", " sell ", "go long", "go short", "probability of"):
        assert w not in blob, w


# ---------------------------------------------------------------- the service: stored at the moment it is made
def add_event(db, arts, category="war_geopolitics", now=NOW):
    db.intel_add_articles(arts)
    return db.intel_save_event(None, {**summarize_event(arts, now), "category": category}, [a["id"] for a in arts], now)


def service(db, now=NOW, calendar=None):
    r = ReactionService(db, calendar=calendar or full_calendar(), clock=lambda: now)
    r.update()
    return ImpactService(db, r, clock=lambda: now)


def rows(db):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM intel_assessments ORDER BY id")]


def test_update_stores_one_row_keyed_to_the_article_set_and_publication_time(db):
    arts = [art("b"), art("a", hours_ago=3, publisher="Other")]
    add_event(db, arts)
    assert service(db).update()["assessed"] == 1
    r = rows(db)[0]
    assert r["article_set_hash"] == article_set_hash(["a", "b"]) and json.loads(r["article_ids_json"]) == ["a", "b"]
    assert r["event_time_at"] == NOW - 3 * HOUR and r["anchor_article_id"] == "a" and r["assessed_at"] == NOW
    assert r["rules_version"] == RULES_VERSION
    assert "event_id" not in r
    assert json.loads(r["payload_json"])["horizons"]["0-24h"]["label"] == r["label_0_24h"]


def test_same_article_set_is_not_reassessed(db):
    add_event(db, [art("a")])
    s = service(db)
    s.update()
    assert s.update()["assessed"] == 0 and len(rows(db)) == 1


def test_grown_article_set_is_a_new_row_and_the_old_stays(db):
    eid = add_event(db, [art("a")])
    s = service(db)
    s.update()
    a2 = art("z", publisher="Other")
    db.intel_add_articles([a2])
    db.intel_save_event(eid, summarize_event([art("a"), a2], NOW), ["z"], NOW)
    s.update()
    assert [json.loads(r["article_ids_json"]) for r in rows(db)] == [["a"], ["a", "z"]]


def test_rebuilding_events_does_not_touch_or_duplicate_assessments(db):
    a = art("a")
    add_event(db, [a])
    s = service(db)
    s.update()
    before = rows(db)
    db.intel_reset_events()
    db.intel_save_event(None, summarize_event([a], NOW), ["a"], NOW)
    assert s.update()["assessed"] == 0 and rows(db) == before


@pytest.mark.parametrize("sql", ["DELETE FROM intel_assessments", "UPDATE intel_assessments SET label_0_24h = 'Bullish'"])
def test_assessments_are_immutable(db, sql):
    add_event(db, [art("a")])
    service(db).update()
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(sql)


@pytest.mark.parametrize("hours,eligible", [(1, 1), (24, 1), (25, 0), (100, 0)])
def test_old_events_are_stored_but_never_counted(db, hours, eligible):
    add_event(db, [art("a", hours_ago=hours)])
    service(db).update()
    assert rows(db)[0]["eligible"] == eligible


def test_flagged_time_is_not_eligible(db):
    a = {**art("a"), "published_at": NOW - HOUR, "collected_at": NOW - 2 * HOUR}
    add_event(db, [a])
    service(db).update()
    r = rows(db)[0]
    assert r["eligible"] == 0 and r["time_flag"] == "published_after_first_seen"


def test_no_publication_time_is_stored_as_uncertain_and_not_eligible(db):
    a = {**art("a"), "published_at": None, "collected_at": NOW - HOUR}
    add_event(db, [a])
    service(db).update()
    r = rows(db)[0]
    assert r["event_time_at"] is None and r["eligible"] == 0 and r["label_0_24h"] == "Uncertain"


@pytest.mark.parametrize("hours_from_release,marked", [(1, 1), (5, 0)])
def test_macro_window_is_marked_at_assessment(db, hours_from_release, marked):
    r = NOW - 100 * HOUR
    add_event(db, [art("a", hours_ago=100 - hours_from_release)])
    service(db, calendar=full_calendar(release("CPI", r))).update()
    assert rows(db)[0]["macro_window"] == marked


def test_assessment_time_near_a_release_is_marked_too(db):
    add_event(db, [art("a", hours_ago=10)])
    service(db, calendar=full_calendar(release("CPI", NOW + HOUR))).update()
    assert rows(db)[0]["macro_window"] == 1


def test_unknown_calendar_is_treated_as_marked(db):
    add_event(db, [art("a")])
    r = ReactionService(db, calendar=None, clock=lambda: NOW)
    ImpactService(db, r, clock=lambda: NOW).update()
    assert rows(db)[0]["macro_window"] == 1


def test_priced_in_uses_only_candles_closed_by_the_assessment(db):
    start = NOW - 500 * HOUR
    seed(db, start=start, hours=500)
    db.upsert_candles("binance", "BTCUSDT", "1h", [candle(NOW + i * HOUR, 9_999.0) for i in range(50)], NOW + 60 * HOUR)    # the future
    add_event(db, [art("a", hours_ago=5)])
    service(db).update()
    first = json.loads(rows(db)[0]["payload_json"])
    p = next(f for f in first["factors"] if f["key"] == "already_priced_in")
    assert p["move_pct"] == pytest.approx((close(start, NOW - HOUR) / close(start, NOW - 6 * HOUR) - 1) * 100)


def close(start, open_time):
    return 100.0 + (open_time - start) // HOUR


def test_event_impact_returns_the_stored_row_and_says_when_none_exists(db):
    eid = add_event(db, [art("a")])
    s = service(db)
    d = s.event_impact(eid)
    assert d["assessment"] is None and "not assessed" in d["note"].lower()
    s.update()
    d = s.event_impact(eid)
    assert d["assessment"]["horizons"]["0-24h"]["label"] == "Uncertain" and d["label"] == "Context, not part of the signal"
    assert d["assessed_at"] == NOW
    assert s.event_impact(999_999) is None


def test_routes(config, db):
    c, app = make_app(config, db)
    now = app.config["BTC_INTEL"].clock()
    eid = add_event(db, [{**art("a"), "published_at": now - HOUR, "collected_at": now - HOUR}], now=now)
    app.config["BTC_INTEL"].impact.update()
    r = c.get(f"/api/intel/events/{eid}/impact")
    assert r.status_code == 200 and r.get_json()["assessment"]["factors"]
    assert c.get("/api/intel/events/999999/impact").status_code == 404
