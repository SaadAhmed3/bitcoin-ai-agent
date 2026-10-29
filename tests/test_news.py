"""News collection + analysis tests with fixed RSS fixtures (no network)."""
import json

import pandas as pd
import pytest
from conftest import FIXED_NOW

from services.http_client import ProviderError
from services.news_analyzer import (HOUR, analyse, analyse_item, build_event, cluster_events, score_sentiment,
                                    summarise)
from services.news_collector import NewsCollector, canonical_url, clean_text, parse_feed, split_publisher_suffix
from services.news_service import NewsService


def rss(items: list[tuple[str, str, str, str]], source_tags: bool = False) -> bytes:
    """items: (title, link, pubDate RFC822, description)."""
    body = ""
    for title, link, date, desc in items:
        src = '<source url="https://x.test">Reuters</source>' if source_tags else ""
        body += f"<item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate><description>{desc}</description>{src}</item>"
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{body}</channel></rss>'.encode()


# FIXED_NOW = 2025-10-09 08:53:20 UTC
D_RECENT = "Thu, 09 Oct 2025 08:00:00 GMT"   # ~0.9h before FIXED_NOW
D_DAY = "Wed, 08 Oct 2025 06:00:00 GMT"      # ~27h before
D_OLD = "Sat, 04 Oct 2025 06:00:00 GMT"      # ~5 days before
FEED = {"id": "testfeed", "name": "Test Feed", "url": "https://feed.test/rss"}
GNEWS = {"id": "googlenews", "name": "Google News", "url": "https://news.test", "aggregator": True}


def item(title, minutes_ago=30, publisher="CoinDesk", summary="", link=None):
    t = FIXED_NOW - minutes_ago * 60_000
    return {"id": title[:20], "feed": "f", "publisher": publisher, "title": title,
            "link": link or f"https://{publisher.lower().replace(' ', '')}.test/{abs(hash(title))}",
            "summary": summary, "published_at": t, "collected_at": t + 60_000}


# ------------------------------------------------------------------- collector
def test_clean_text_strips_html_and_truncates():
    assert clean_text("<p>Fed &amp; <b>BTC</b></p>") == "Fed & BTC"
    long = clean_text("word " * 200, 50)
    assert len(long) <= 50 and long.endswith("…")


def test_canonical_url_drops_tracking():
    assert canonical_url("https://Site.test/a/?utm_source=x&id=5") == "https://site.test/a?id=5"


def test_split_google_news_publisher():
    assert split_publisher_suffix("Bitcoin ETF sees record inflows this week - Reuters") == (
        "Bitcoin ETF sees record inflows this week", "Reuters")


def test_parse_feed_fields_and_times():
    content = rss([("Bitcoin <b>jumps</b> after CPI", "https://a.test/1?utm_source=rss", D_RECENT, "<p>Summary</p>")])
    items, warns = parse_feed(content, FEED, FIXED_NOW)
    assert len(items) == 1 and not warns
    it = items[0]
    assert it["title"] == "Bitcoin jumps after CPI" and it["publisher"] == "Test Feed"
    assert it["published_at"] == FIXED_NOW - (53 * 60 + 20) * 1000
    assert it["collected_at"] == FIXED_NOW and it["summary"] == "Summary"


def test_parse_google_news_uses_source_tag():
    content = rss([("Fed cuts rates by 25 basis points - Reuters", "https://news.test/r1", D_RECENT, "")], source_tags=True)
    items, _ = parse_feed(content, GNEWS, FIXED_NOW)
    assert items[0]["publisher"] == "Reuters" and items[0]["title"] == "Fed cuts rates by 25 basis points"


def test_future_publish_time_is_not_trusted():
    content = rss([("Bitcoin news item here", "https://a.test/2", "Thu, 09 Oct 2030 08:00:00 GMT", "")])
    items, _ = parse_feed(content, FEED, FIXED_NOW)
    assert items[0]["published_at"] is None


def test_empty_feed_is_ok():
    items, _ = parse_feed(rss([]), FEED, FIXED_NOW)
    assert items == []


def test_garbage_feed_raises():
    with pytest.raises(ProviderError):
        parse_feed(b"\x00\x01 not xml at all <<<", FEED, FIXED_NOW)


def test_collector_dedupes_by_link_and_records_health(db):
    content = rss([("Bitcoin rallies above key level", "https://a.test/x?utm_medium=y", D_RECENT, ""),
                   ("Bitcoin rallies above key level", "https://a.test/x", D_RECENT, "")])
    calls = {"n": 0}

    def fetcher(feed):
        calls["n"] += 1
        if feed["id"] == "bad":
            raise ProviderError("bad: HTTP 503")
        return content, 12.0

    feeds = [FEED, {"id": "bad", "name": "Bad", "url": "https://bad.test"}]
    c = NewsCollector(db, http=None, feeds=feeds, clock=lambda: FIXED_NOW, fetcher=fetcher)
    res = c.refresh()
    ok = [r for r in res["refreshed"] if r["feed"] == "testfeed"][0]
    assert ok["new"] == 1  # same story after URL canonicalisation
    health = {h["source"]: h["status"] for h in db.get_health()}
    assert health == {"rss:testfeed": "ok", "rss:bad": "error"}
    # Within the refresh interval nothing is re-fetched - including the failing feed (no hammering).
    c.refresh()
    assert calls["n"] == 2
    # ...unless forced. Re-inserting the same story adds nothing.
    assert [r["new"] for r in c.refresh(force=True)["refreshed"] if r["ok"]] == [0]


# -------------------------------------------------------------------- sentiment
@pytest.mark.parametrize("title,label", [
    ("Fed cuts interest rates by 50 basis points", "bullish"),
    ("Fed signals more rate hikes as inflation stays hot", "bearish"),
    ("Spot bitcoin ETFs log $1.2 billion in inflows", "bullish"),
    ("Bitcoin ETFs see heavy outflows for third day", "bearish"),
    ("Major exchange hacked, $200 million drained", "bearish"),
    ("CPI cools more than expected in September", "bullish"),
    ("Bitcoin price plunges below $100,000", "bearish"),
    ("Bitcoin conference schedule announced for next year", "neutral"),
])
def test_sentiment_labels(title, label):
    assert score_sentiment(title)["label"] == label


def test_negation_flips_direction():
    s = score_sentiment("Powell rules out rate cut this year")
    assert s["label"] == "bearish" and s["hits"][0]["negated"]


def test_mixed_evidence_is_uncertain():
    assert score_sentiment("ETF inflows hit record but exchange hacked overnight")["label"] == "uncertain"


def test_inflation_falls_is_not_read_as_price_drop():
    # 'falls' must not trigger the BTC price-drop rule when the subject is inflation.
    s = score_sentiment("US inflation falls to 2.1%")
    assert s["label"] == "bullish"


# --------------------------------------------------------------- categorisation
def test_categories():
    a = analyse_item(item("SEC sues exchange over unregistered securities"))
    assert "regulation" in a["categories"]
    a = analyse_item(item("Fed holds rates steady, Powell cautious on inflation"))
    assert {"fed_rates", "inflation_macro"} <= set(a["categories"])
    a = analyse_item(item("Bitcoin hashrate hits record as miners expand"))
    assert "network_mining" in a["categories"]
    a = analyse_item(item("Something generic happened in crypto land"))
    assert a["categories"] == ["crypto_market"]


# ------------------------------------------------------------------- clustering
def test_syndicated_story_counts_once():
    items = [analyse_item(item("Spot Bitcoin ETFs record $1.2 billion inflows on Monday", 50, "CoinDesk")),
             analyse_item(item("Spot bitcoin ETFs see record $1.2 billion inflows Monday", 40, "Reuters")),
             analyse_item(item("Spot Bitcoin ETFs record $1.2B inflows on Monday - analysis", 30, "Decrypt")),
             analyse_item(item("Ethereum developers schedule next network upgrade", 20, "The Block"))]
    events = cluster_events(items)
    assert sorted(len(e) for e in events) == [1, 3]
    ev = build_event(max(events, key=len), FIXED_NOW)
    assert ev["independent_sources"] == 3 and ev["verification"] == "confirmed"
    assert ev["publisher"] == "CoinDesk"  # the earliest report is the original
    assert ev["impact"] == "high" and ev["sentiment"] == "bullish"


def test_same_publisher_twice_is_not_independent_confirmation():
    items = [analyse_item(item("Bitcoin miners sell holdings as hashprice slumps", 50, "CoinDesk")),
             analyse_item(item("Bitcoin miners sell holdings as hashprice slumps again", 40, "CoinDesk"))]
    ev = build_event(cluster_events(items)[0], FIXED_NOW)
    assert ev["independent_sources"] == 1 and ev["verification"] == "unverified"


def test_conflicting_reports_in_one_event():
    items = [analyse_item(item("Bitcoin price plunges after exchange hack", 50, "A")),
             analyse_item(item("Bitcoin rallies as exchange restores withdrawals", 40, "B"))]
    assert [i["sentiment"]["label"] for i in items] == ["bearish", "bullish"]
    # Force them into one event to test the conflict rule directly.
    ev = build_event(items, FIXED_NOW)
    assert ev["conflicting_reports"] is True and ev["sentiment"] == "uncertain"


# ------------------------------------------------------------------ verification
def test_rumour_and_speculation_flags():
    rumour = build_event([analyse_item(item("Exchange reportedly preparing to buy bitcoin for treasury"))], FIXED_NOW)
    assert rumour["verification"] == "unverified"
    spec = build_event([analyse_item(item("Could bitcoin hit $200K by year end? Analysts weigh in"))], FIXED_NOW)
    assert spec["verification"] == "speculative" and spec["impact"] == "low"
    conf = build_event([analyse_item(item("SEC approves new spot bitcoin ETF listings"))], FIXED_NOW)
    assert conf["verification"] == "confirmed"


# -------------------------------------------------------------------- freshness
def test_freshness_by_age():
    new = build_event([analyse_item(item("Fed cuts rates by 25 basis points", 30))], FIXED_NOW)
    day = build_event([analyse_item(item("Fed cuts rates by 25 basis points", 27 * 60))], FIXED_NOW)
    old = build_event([analyse_item(item("Fed cuts rates by 25 basis points", 5 * 24 * 60))], FIXED_NOW)
    assert (new["freshness"], day["freshness"], old["freshness"]) == ("new", "likely_priced_in", "too_old")


def test_priced_in_when_price_already_moved():
    idx = pd.date_range(end=pd.Timestamp(FIXED_NOW, unit="ms", tz="UTC"), periods=10, freq="1h")
    prices = pd.Series([100.0] * 7 + [101.0, 102.0, 103.0], index=idx)
    ev = build_event([analyse_item(item("Fed cuts interest rates by 50 basis points", 6 * 60))], FIXED_NOW, prices)
    assert ev["freshness"] == "likely_priced_in" and ev["price_move_since_pct"] == pytest.approx(3.0)


# ---------------------------------------------------------------------- summary
def test_summary_counts_event_once_and_excludes_old():
    items = [item("Spot Bitcoin ETFs record $1.2 billion inflows on Monday", 50, "CoinDesk"),
             item("Spot bitcoin ETFs see record $1.2 billion inflows Monday", 40, "Reuters"),
             item("Major exchange hacked, $200 million drained", 6 * 24 * 60, "Decrypt")]
    events, summary = analyse(items, FIXED_NOW)
    assert summary["events_total"] == 2 and summary["events_active"] == 1
    assert summary["bias_label"] == "bullish"  # the old bearish hack is weighted 0
    json.dumps(summary, allow_nan=False)
    json.dumps(events, allow_nan=False)


def test_summary_without_news():
    s = summarise([])
    assert s["bias_label"] == "insufficient_news" and s["bias_score"] == 0


def test_cross_event_conflict_detected():
    items = [item("Fed signals rate hikes ahead after hot jobs report", 60, "CNBC"),
             item("Fed officials open door to rate cuts in December", 30, "Reuters")]
    _, summary = analyse(items, FIXED_NOW)
    assert summary["conflicts"] and summary["conflicts"][0]["category"] == "fed_rates"


# ----------------------------------------------------------------- news service
def test_service_survives_feed_and_price_failure(db):
    def fetcher(feed):
        raise ProviderError("down")

    c = NewsCollector(db, http=None, feeds=[FEED], clock=lambda: FIXED_NOW, fetcher=fetcher)

    def broken_prices():
        raise RuntimeError("market down")

    svc = NewsService(db, c, price_series=broken_prices, clock=lambda: FIXED_NOW)
    rep = svc.report()
    assert rep["events"] == [] and rep["summary"]["bias_label"] == "insufficient_news"
    joined = " ".join(rep["warnings"])
    assert "No news collected" in joined and "testfeed" in joined and "Price data unavailable" in joined


def test_corporate_treasury_is_not_bond_market_and_buy_is_bullish():
    a = analyse_item(item("Exchange reportedly preparing to buy bitcoin for treasury"))
    assert "dollar_yields" not in a["categories"] and "institutional" in a["categories"]
    assert a["sentiment"]["label"] == "bullish"
    b = analyse_item(item("10-year Treasury yields climb as dollar index jumps"))
    assert "dollar_yields" in b["categories"] and b["sentiment"]["label"] == "bearish"


def test_weak_signals():
    # A single weak rule (hashrate record, weight 0.5) is a mild, low-impact bullish signal.
    ev = build_event([analyse_item(item("Bitcoin hashrate hits record as miners expand"))], FIXED_NOW)
    assert ev["sentiment"] == "bullish" and 0 < ev["score"] < 0.25 and ev["impact"] == "low"
    # A damped altcoin story with a matched rule is neutral, and the explanation says why.
    ev = build_event([analyse_item(item("Ripple Wins Final OCC Approval for national trust charter"))], FIXED_NOW)
    assert ev["sentiment"] == "neutral" and ev["btc_impact"][0].startswith("Weak")
