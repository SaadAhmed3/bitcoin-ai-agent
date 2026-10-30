"""Batch 2 fix 4 (signal-1.8): news leaves the weighted score - exactly as every backtest replayed it - and can only
block. No existing block was loosened.

Block tiers: high-impact news AGAINST the setup (bearish vs BUY / STRONG BUY, bullish vs SELL / REDUCE; HOLD is
never blocked, as before):
  confirmed            new (< 2 h) or recent (< 24 h, not priced in)  -> NO TRADE     (signal-1.7: < 2 h only)
  unverified / rumour  new (< 2 h)                                     -> NO TRADE "unverified, short block"
  speculative          new (< 2 h)                                     -> NO TRADE "speculative, short block"
  speculative          older                                           -> warning only
"""
import pytest
from conftest import FakeProvider
from test_app_and_db import make_client
from test_signal_engine import bearish, run

from services.signal_engine import BUY, HOLD, NO_TRADE, SELL, STRONG_BUY, EngineConfig

BULL_NEWS = {"bias_label": "bullish", "bias_score": 0.8, "weight_total": 5.0, "directional_active": 6}
BEAR_NEWS = {"bias_label": "bearish", "bias_score": -0.8, "weight_total": 5.0, "directional_active": 6}


def ev(sentiment="bearish", verification="confirmed", freshness="new", impact="high", age=1.0):
    return {"id": "e1", "title": "Exchange X halts withdrawals after a hack", "link": "https://news.example/x",
            "publisher": "Example Wire", "sentiment": sentiment, "verification": verification,
            "freshness": freshness, "impact": impact, "age_hours": age}


def gated(s) -> bool:
    return "fresh_news" in s["diagnostics"]["gate_keys"]


# ------------------------------------------------------------------ the score: news out, backtest weights in
def test_news_no_longer_moves_the_score():
    base = run()
    for news in (BULL_NEWS, BEAR_NEWS):
        s = run(news=news)
        assert s["score"] == base["score"]
        comp = next(c for c in s["components"] if c["name"] == "news")
        assert comp["weight"] == 0 and comp["contribution"] in (None, 0)
        assert any("can only block" in r for r in comp["reasons"])


def test_live_weights_are_exactly_the_backtest_weights():
    from services.signal_engine import DEFAULT_WEIGHTS
    assert DEFAULT_WEIGHTS == {"trend": 0.30, "momentum": 0.25, "volume": 0.10, "structure": 0.10,
                               "overextension": 0.10, "news": 0.0}
    with_news = {c["name"]: c["weight"] for c in run(news=BULL_NEWS)["components"]}
    as_backtest = {c["name"]: c["weight"] for c in run(news=None)["components"]}   # the backtest passes no news
    assert with_news == as_backtest == {"trend": 0.3529, "momentum": 0.2941, "volume": 0.1176, "structure": 0.1176,
                                        "overextension": 0.1176, "news": 0.0}


# ------------------------------------------------------------------ block tiers (news against a BUY setup)
@pytest.mark.parametrize("verification, freshness, age, blocks, label", [
    ("confirmed", "new", 1.0, True, "confirmed"),
    ("confirmed", "recent", 8.0, True, "confirmed"),                 # stricter than signal-1.7 (< 2 h only)
    ("confirmed", "likely_priced_in", 8.0, False, None),
    ("confirmed", "too_old", 80.0, False, None),
    ("unverified", "new", 1.0, True, "unverified, short block"),     # kept from signal-1.7
    ("unverified", "recent", 8.0, False, None),
    ("speculative", "new", 1.0, True, "speculative, short block"),   # kept from signal-1.7 (option A)
    ("speculative", "recent", 8.0, False, None),
])
def test_bearish_news_tiers_against_a_buy_setup(verification, freshness, age, blocks, label):
    s = run(events=[ev(verification=verification, freshness=freshness, age=age)])
    assert s["setup"] in (BUY, STRONG_BUY)
    assert gated(s) is blocks
    if blocks:
        assert s["signal"] == NO_TRADE
        r = next(x for x in s["no_trade_reasons"] if "high-impact bearish news" in x)
        assert f"({label}," in r and "Example Wire" in r and "https://news.example/x" in r
        assert '"Exchange X halts withdrawals after a hack"' in r


def test_older_speculative_news_is_a_warning_only():
    s = run(events=[ev(verification="speculative", freshness="recent", age=8.0)])
    assert not gated(s)
    assert any(w.startswith("Speculative high-impact bearish news") and "https://news.example/x" in w
               for w in s["data_warnings"])


def test_medium_impact_or_same_direction_news_never_blocks():
    assert not gated(run(events=[ev(impact="medium")]))
    assert not gated(run(events=[ev(sentiment="bullish")]))          # bullish news does not block a BUY


def test_mirror_rule_bullish_news_blocks_a_sell_setup():
    s = run(bearish(), events=[ev(sentiment="bullish")])
    assert s["setup"] == SELL and gated(s) and s["signal"] == NO_TRADE
    assert any("high-impact bullish news (confirmed," in r for r in s["no_trade_reasons"])
    assert not gated(run(bearish(), events=[ev(sentiment="bearish")]))   # bearish news does not block a SELL
    s = run(bearish(), events=[ev(sentiment="bullish", verification="unverified", freshness="recent", age=8.0)])
    assert not gated(s)                                                   # same tiers as for BUY


def test_hold_is_never_blocked_by_news_as_before():
    s = run(events=[ev()], cfg=EngineConfig(buy_threshold=0.9, strong_threshold=0.95))
    assert s["setup"] == HOLD and not gated(s)


# ------------------------------------------------------------------ settings: news weight must be 0
def test_settings_reject_a_news_weight_above_zero(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    r = c.put("/api/signal/config", json={"weights": {"news": 0.1}})
    assert r.status_code == 400 and "news" in r.get_json()["error"] and "only block" in r.get_json()["error"]
    assert c.put("/api/signal/config", json={"weights": {"news": 0}}).status_code == 200


def test_stored_news_weight_is_set_to_zero_keeping_the_other_weights_with_a_one_time_notice(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    db.set_setting("signal_config", {"weights": {"trend": 0.4, "momentum": 0.2, "volume": 0.1, "structure": 0.1,
                                                 "overextension": 0.1, "news": 0.1}, "min_rr": 2})
    cfg = c.get("/api/signal/config").get_json()["config"]
    w = cfg["weights"]
    assert w["news"] == 0 and cfg["min_rr"] == 2                          # not reset to defaults
    assert w["trend"] == pytest.approx(0.4 / 0.9, abs=1e-4) and w["momentum"] == pytest.approx(0.2 / 0.9, abs=1e-4)
    assert db.get_setting("signal_config")["weights"]["news"] == 0         # migrated once, stored
    assert c.get("/api/settings").get_json()["signal"]["notice"]           # the dashboard's read does not consume it
    notice = c.get("/api/settings?ack_notice=1").get_json()["signal"]["notice"]   # the Settings page shows it once
    assert notice and "news" in notice and "0.1" in notice and "other weights" in notice
    assert c.get("/api/settings?ack_notice=1").get_json()["signal"]["notice"] is None   # one-time
