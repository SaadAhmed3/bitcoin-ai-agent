"""Accuracy tracker: pre-registered rules, immutable rows, closed candles only, baselines, small samples say nothing."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from test_intel_reactions import candle, full_calendar, seed
from test_phase8 import make_app

from services.intel_events import summarize_event
from services.intel_impact import RULES_VERSION
from services.intel_reactions import align_base, align_target
from services.intel_tracker import (MIN_EPISODES, MIN_SAMPLES, PREREG_SHA256, SCORE_HORIZONS, TrackerService, baselines, classify_zone,
                                    score_label)

ROOT = Path(__file__).resolve().parents[1]
HOUR = 3_600_000
T = (FIXED_NOW // HOUR) * HOUR
START = T - 400 * HOUR
FAR = T + 3000 * HOUR


# ---------------------------------------------------------------- pre-registration
def test_preregistration_file_is_pinned():
    text = (ROOT / "PREREGISTRATION_NEWS_IMPACT.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(text).hexdigest() == PREREG_SHA256


def test_code_matches_the_preregistered_numbers():
    text = (ROOT / "PREREGISTRATION_NEWS_IMPACT.md").read_text(encoding="utf-8")
    assert SCORE_HORIZONS == {"0-24h": 24, "1-7d": 168, "1-3m": 2160}
    assert (MIN_SAMPLES, MIN_EPISODES) == (30, 20)
    assert "at least 30 counted scored assessments and at least 20 separate episodes" in text
    assert "X = 0.5 x the normal move" in text and "+90 days" in text and RULES_VERSION == 2
    assert "Amendment 1, 2026-11-02: rules version 2" in text and "0 scores" in text


# ---------------------------------------------------------------- the hit rule
@pytest.mark.parametrize("ret,zone", [(0.6, "up"), (0.5, "band"), (0.0, "band"), (-0.5, "band"), (-0.6, "down")])
def test_zone_is_half_the_normal_move(ret, zone):
    assert classify_zone(ret, 1.0) == zone


@pytest.mark.parametrize("label,ret,outcome", [("Bullish", 0.6, "hit"), ("Bullish", 0.5, "miss"), ("Bullish", -2.0, "miss"), ("Bearish", -0.6, "hit"),
                                               ("Bearish", -0.5, "miss"), ("Bearish", 2.0, "miss"), ("Neutral", 0.5, "hit"), ("Neutral", -0.5, "hit"),
                                               ("Neutral", 0.6, "miss"), ("Neutral", -0.6, "miss"), ("Mixed", 5.0, "not_scored"), ("Uncertain", -5.0, "not_scored")])
def test_hit_rule(label, ret, outcome):
    assert score_label(label, ret, 1.0) == outcome


# ---------------------------------------------------------------- baselines
def row(outcome, zone, label="Bullish"):
    return {"outcome": outcome, "zone": zone, "label": label}


def test_baselines_on_the_same_rows():
    rows = [row("hit", "up", "Bullish"), row("miss", "band", "Bullish"), row("hit", "band", "Neutral"), row("miss", "down", "Bullish")]
    b = baselines(rows)
    assert b["always_neutral"] == 0.5 and b["random_uniform"] == pytest.approx(1 / 3)
    # labels: 3/4 Bullish, 1/4 Neutral; returns: up 1/4, band 2/4, down 1/4
    assert b["label_mix"] == pytest.approx(0.75 * 0.25 + 0.25 * 0.5)


def test_baselines_on_no_rows_are_none():
    b = baselines([])
    assert b["always_neutral"] is None and b["label_mix"] is None


# ---------------------------------------------------------------- scoring on stored rows
def assessment(db, aid="a", assessed_at=T, event_time=None, label="Bullish", eligible=1, macro=0, anchor=None, rules=RULES_VERSION):
    a = {"id": aid, "source_id": "t", "source_type": "news", "publisher": "W", "title": f"Headline {aid}", "link": f"https://x.test/{aid}", "summary": "",
         "published_at": event_time if event_time is not None else assessed_at - HOUR, "collected_at": assessed_at}
    db.intel_add_articles([a])
    payload = {"horizons": {h: {"label": label, "score": 40} for h in SCORE_HORIZONS}, "factors": [], "interpretations": []}
    return db.intel_add_assessment({"article_set_hash": f"hash-{aid}", "article_ids": [aid], "anchor_article_id": anchor or aid, "event_time_at": a["published_at"],
                                    "category": "war_geopolitics", "status": "confirmed", "rules_version": rules, "assessed_at": assessed_at, "eligible": eligible,
                                    "macro_window": macro, "time_flag": None, "labels": {h: label for h in SCORE_HORIZONS}, "payload": payload})


def scores(db):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM intel_assessment_scores ORDER BY assessment_id, horizon")]


def score_of(db, horizon):
    return next(r for r in scores(db) if r["horizon"] == horizon)


def tracker(db, now):
    return TrackerService(db, clock=lambda: now)


def test_scoring_measures_from_the_assessment_not_from_publication(db):
    seed(db, start=START, hours=800)
    assessment(db, assessed_at=T + 30 * 60_000, event_time=T - 20 * HOUR)
    tracker(db, T + 200 * HOUR).score_due()
    s = score_of(db, "0-24h")
    assert s["base_open_time"] == align_base(T + 30 * 60_000) == T - HOUR
    assert s["target_open_time"] == align_target(T + 30 * 60_000, 24) == T + 24 * HOUR
    b, t = 100.0 + (T - HOUR - START) / HOUR, 100.0 + (T + 24 * HOUR - START) / HOUR
    assert s["base_close"] == b and s["target_close"] == t and s["return_pct"] == pytest.approx((t / b - 1) * 100)
    assert s["threshold_pct"] == pytest.approx(s["normal_abs_pct"] * 0.5)


def test_no_look_ahead_candles_after_the_assessment_cannot_change_the_base_or_normal(tmp_path):
    from database import Database
    out = []
    for name, price in (("a", lambda i: 100.0 + i), ("b", lambda i: 100.0 + i if START + i * HOUR < T else 100.0 + i * 40)):
        d = Database(str(tmp_path / f"{name}.sqlite3"))
        d.init()
        seed(d, start=START, hours=800, price=price)
        assessment(d)
        tracker(d, T + 200 * HOUR).score_due()
        s = score_of(d, "0-24h")
        out.append((s["base_close"], s["normal_abs_pct"], s["threshold_pct"]))
    assert out[0] == out[1]


def test_horizon_not_passed_is_not_scored(db):
    seed(db, start=START, hours=800)
    assessment(db)
    tracker(db, T + 23 * HOUR).score_due()
    assert scores(db) == []
    tracker(db, T + 25 * HOUR).score_due()
    assert [s["horizon"] for s in scores(db)] == ["0-24h"]


def test_forming_candle_is_never_scored(db):
    seed(db, start=START, hours=(T + 23 * HOUR - START) // HOUR)
    db.upsert_candles("binance", "BTCUSDT", "1h", [candle(T + 23 * HOUR, 777.0)], T + 23 * HOUR + 60_000)
    assessment(db)
    tracker(db, T + 26 * HOUR).score_due()      # the close time has passed, but it was saved while forming
    assert scores(db) == []


def test_missing_candle_is_never_filled_from_a_neighbour(db):
    seed(db, start=START, hours=800, skip={T + 23 * HOUR})
    assessment(db)
    t = tracker(db, T + 200 * HOUR)
    t.score_due()
    assert "0-24h" not in [s["horizon"] for s in scores(db)]
    assert t.report()["horizons"]["0-24h"]["unscorable"] == 1


def test_missing_base_candle_means_unscorable(db):
    seed(db, start=START, hours=800, skip={T - HOUR})
    assessment(db)
    t = tracker(db, T + 200 * HOUR)
    t.score_due()
    assert scores(db) == [] and t.report()["horizons"]["0-24h"]["unscorable"] == 1


def test_no_normal_move_means_unscorable(db):
    seed(db, start=T - 2 * HOUR, hours=60)
    assessment(db)
    tracker(db, T + 40 * HOUR).score_due()
    assert scores(db) == []


def test_mixed_and_uncertain_store_the_zone_but_are_not_scored(db):
    seed(db, start=START, hours=800)
    assessment(db, label="Uncertain")
    t = tracker(db, T + 200 * HOUR)
    t.score_due()
    assert score_of(db, "0-24h")["outcome"] == "not_scored"
    h = t.report()["horizons"]["0-24h"]
    assert h["counted"]["n"] == 0 and h["not_scored"] == 1


@pytest.mark.parametrize("sql", ["DELETE FROM intel_assessment_scores", "UPDATE intel_assessment_scores SET outcome = 'hit'"])
def test_score_rows_are_immutable(db, sql):
    seed(db, start=START, hours=800)
    assessment(db)
    tracker(db, T + 200 * HOUR).score_due()
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(sql)


def test_a_later_candle_revision_never_rewrites_a_score(db):
    seed(db, start=START, hours=800)
    assessment(db)
    t = tracker(db, T + 200 * HOUR)
    t.score_due()
    first = score_of(db, "0-24h")
    db.upsert_candles("binance", "BTCUSDT", "1h", [candle(T + 23 * HOUR, 1.0)], T + 199 * HOUR)
    assert t.score_due()["scored"] == 0 and score_of(db, "0-24h") == first


# ---------------------------------------------------------------- what counts
def test_only_the_first_assessment_for_an_anchor_counts(db):
    seed(db, start=START, hours=800)
    assessment(db, aid="a", anchor="a", assessed_at=T)
    assessment(db, aid="b", anchor="a", assessed_at=T + HOUR)
    t = tracker(db, T + 200 * HOUR)
    t.score_due()
    assert t.report()["horizons"]["0-24h"]["counted"]["n"] == 1


def test_ineligible_assessments_are_not_counted(db):
    seed(db, start=START, hours=800)
    assessment(db, eligible=0)
    t = tracker(db, T + 200 * HOUR)
    t.score_due()
    r = t.report()
    assert r["horizons"]["0-24h"]["counted"]["n"] == 0 and r["totals"]["not_eligible"] == 1


def test_macro_window_is_shown_apart(db):
    seed(db, start=START, hours=800)
    assessment(db, aid="a", macro=1)
    assessment(db, aid="b", macro=0, assessed_at=T + 5 * HOUR)
    t = tracker(db, T + 200 * HOUR)
    t.score_due()
    h = t.report()["horizons"]["0-24h"]
    assert h["counted"]["n"] == 1 and h["macro_window"]["n"] == 1


def test_other_rules_versions_are_never_pooled(db):
    seed(db, start=START, hours=800)
    assessment(db, aid="old", rules=RULES_VERSION + 1)
    t = tracker(db, T + 200 * HOUR)
    t.score_due()
    assert t.report()["horizons"]["0-24h"]["counted"]["n"] == 0


# ---------------------------------------------------------------- what is shown
def fill(db, n, gap=30, label="Bullish"):
    seed(db, start=T - 500 * HOUR, hours=n * gap + 700, price=lambda i: 100.0 + (i % 40) * 3)
    for i in range(n):
        assessment(db, aid=f"a{i}", assessed_at=T + i * gap * HOUR, label=label)
    return tracker(db, T + (n * gap + 100) * HOUR)


def test_hit_rate_is_hidden_below_the_minimum_and_says_small_samples_prove_nothing(db):
    t = fill(db, 5)
    t.score_due()
    r = t.report()
    c = r["horizons"]["0-24h"]["counted"]
    assert c["n"] == 5 and c["shown"] is False and c["hit_rate"] is None and c["hits"] is None
    assert c["baselines"] is None and c["ci95"] is None
    assert "small samples prove nothing" in c["note"].lower()
    assert "small samples prove nothing" in " ".join(r["notes"]).lower()


def test_hit_rate_is_shown_at_the_minimum_with_baselines_and_interval(db):
    t = fill(db, 30)
    t.score_due()
    c = t.report()["horizons"]["0-24h"]["counted"]
    assert c["n"] == 30 and c["episodes"] == 30 and c["shown"] is True
    assert c["hit_rate"] == pytest.approx(c["hits"] / 30)
    assert set(c["baselines"]) == {"always_neutral", "random_uniform", "label_mix"}
    assert c["ci95"][0] <= c["hit_rate"] <= c["ci95"][1]
    assert c["verdict"] in ("beats_baselines", "not_established")


def test_many_overlapping_assessments_stay_hidden(db):
    t = fill(db, 40, gap=1)      # 40 assessments an hour apart: heavily overlapping 24 h windows
    t.score_due()
    c = t.report()["horizons"]["0-24h"]["counted"]
    assert c["n"] == 40 and c["episodes"] < MIN_EPISODES and c["shown"] is False


def test_verdict_needs_the_interval_above_every_baseline():
    from services.intel_tracker import verdict
    b = {"always_neutral": 0.3, "random_uniform": 1 / 3, "label_mix": 0.35}
    assert verdict([0.36, 0.5], b) == "beats_baselines"
    assert verdict([0.34, 0.5], b) == "not_established"


def test_report_has_coverage_and_the_label(db):
    t = fill(db, 3)
    t.score_due()
    r = t.report()
    assert r["label"] == "Context, not part of the signal"
    assert r["rules_version"] == RULES_VERSION and r["prereg_file"] == "PREREGISTRATION_NEWS_IMPACT.md"
    assert r["totals"]["assessments"] == 3 and "pending" in r["horizons"]["1-3m"]


def test_empty_tracker_report(db):
    r = tracker(db, T).report()
    assert r["horizons"]["0-24h"]["counted"]["n"] == 0 and r["totals"]["assessments"] == 0


def test_route(config, db):
    c, app = make_app(config, db)
    r = c.get("/api/intel/tracker")
    assert r.status_code == 200 and r.get_json()["label"] == "Context, not part of the signal"


def test_refresh_runs_impact_and_tracker_and_a_failure_does_not_stop_it(db, monkeypatch):
    from test_phase8 import FakeHttp

    from services.intel_service import IntelService
    now = T + 200 * HOUR
    seed(db, start=START, hours=800)
    arts = [{"id": "a", "source_id": "t", "source_type": "news", "publisher": "W", "title": "Report", "link": "https://x.test/a", "summary": "",
             "published_at": now - HOUR, "collected_at": now - HOUR}]
    db.intel_add_articles(arts)
    db.intel_save_event(None, summarize_event(arts, now), ["a"], now)
    intel = IntelService(db, http=FakeHttp(), sources=[], clock=lambda: now, calendar=full_calendar())
    out = intel.refresh(force=True)
    assert out["impact"]["assessed"] == 1 and "scored" in out["tracker"]

    def boom():
        raise RuntimeError("no")
    monkeypatch.setattr(intel.impact, "update", boom)
    monkeypatch.setattr(intel.tracker, "score_due", boom)
    out = intel.refresh(force=True)
    assert out["impact"] == {"error": "RuntimeError"} and out["tracker"] == {"error": "RuntimeError"}


def test_json_payload_is_serialisable(db):
    t = fill(db, 3)
    t.score_due()
    json.dumps(t.report())


def test_far_target_uses_the_same_rules(db):
    seed(db, start=T - 2400 * HOUR, hours=4600)
    assessment(db)
    tracker(db, FAR).score_due()
    s = score_of(db, "1-3m")
    assert s["target_open_time"] == T + 2159 * HOUR and s["window_hours"] == 2160
