"""Sources, back-off, markets, BEA + curated BLS calendar, migration, routes. No network: fake HTTP, fixed clocks.
Formats copied from the real responses checked on 2026-11-02; values in the CSV/XLS samples are synthetic."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from test_phase8 import FakeHttp, make_app

from database import Database
from services.calendar_service import CalendarService, parse_bea_ics
from services.http_client import ProviderError
from services.intel_markets import (MARKET_SERIES, UNAVAILABLE, IntelMarkets, parse_cboe_vix, parse_eia_xls, parse_fred_api,
                                    parse_h10_csv, parse_treasury_xml)
from services.intel_service import IntelService, IntelWorker
from services.intel_sources import BACKOFF_CAP_MS, SOURCES, fetch_source, parse_gdelt, parse_treasury_press

FIX = Path(__file__).parent / "fixtures"
HOUR = 3_600_000
DAY = 24 * HOUR
ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, t=FIXED_NOW):
        self.t = t

    def __call__(self):
        return self.t


def rss(*items):
    body = "".join(f"<item><title>{t}</title><link>{l}</link><pubDate>{d}</pubDate></item>" for t, l, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{body}</channel></rss>'


NOW_RFC = "Thu, 09 Oct 2025 08:00:00 GMT"       # one hour before FIXED_NOW's day part is irrelevant: within 14 days
GOOD = rss(("Federal Reserve issues FOMC statement", "https://www.federalreserve.gov/a.htm", NOW_RFC))


# ---------------------------------------------------------------- source list
def test_source_list_is_the_approved_one():
    ids = {s["id"] for s in SOURCES}
    assert ids == {"bbc_world", "bbc_business", "fed_press", "sec_press", "cftc_general", "cftc_enforcement", "treasury_press", "gdelt"}
    assert all(s["url"].startswith("https://") for s in SOURCES)
    assert not any("bls.gov" in s["url"] or "news.google" in s["url"] for s in SOURCES)
    assert {s["id"] for s in SOURCES if s["type"] == "official"} == {"fed_press", "sec_press", "cftc_general", "cftc_enforcement", "treasury_press"}


def test_no_from_header_is_ever_sent():
    src = (ROOT / "services" / "intel_sources.py").read_text(encoding="utf-8")
    assert '"From"' not in src                       # the SEC contact goes in its User-Agent only: test_intel_contact_freshness.py


# ---------------------------------------------------------------- parsers
def test_treasury_press_parser_on_the_real_layout():
    html = ('<div><span class=date-format><time datetime=2026-10-30T20:00:00Z class=datetime>October 30, 2026</time></span><span></span>'
            '<h3 class=featured-stories__headline><a href=/news/press-releases/sb0672/ hreflang=en>Report on U.S. Portfolio &amp; Holdings</a></h3></div>')
    items = parse_treasury_press(html, {"id": "treasury_press", "name": "U.S. Treasury"}, FIXED_NOW + 400 * DAY)
    assert len(items) == 1 and items[0]["title"] == "Report on U.S. Portfolio & Holdings"
    assert items[0]["link"] == "https://home.treasury.gov/news/press-releases/sb0672/" and items[0]["published_at"] == 1793390400000


def test_treasury_press_layout_change_is_an_error():
    with pytest.raises(ProviderError, match="layout"):
        parse_treasury_press("<html>new design</html>", {"id": "treasury_press", "name": "T"}, FIXED_NOW)


def test_gdelt_parser_and_bad_links():
    items = parse_gdelt({"articles": [
        {"url": "https://a.test/1", "title": "Bitcoin rises", "domain": "a.test", "seendate": "20251009T080000Z", "sourcecountry": "United States", "language": "English"},
        {"url": "javascript:alert(1)", "title": "Bad", "domain": "b.test", "seendate": "20251009T080000Z"},
        {"url": "https://c.test/2", "title": "", "domain": "c.test", "seendate": "20251009T080000Z"},
        {"url": "https://d.test/3", "title": "No date", "domain": "d.test"}]}, {"id": "gdelt", "name": "GDELT"}, FIXED_NOW)
    assert [i["link"] for i in items] == ["https://a.test/1", "https://d.test/3"]
    assert items[0]["publisher"] == "a.test" and items[0]["published_at"] == 1759996800000 and items[1]["published_at"] is None
    assert parse_gdelt({}, {"id": "gdelt", "name": "GDELT"}, FIXED_NOW) == []


def test_rss_source_uses_the_shared_parser_and_blocks_bad_links():
    http = FakeHttp({"federalreserve": rss(("OK", "https://x.test/a", NOW_RFC), ("Bad", "javascript:alert(1)", NOW_RFC))})
    src = next(s for s in SOURCES if s["id"] == "fed_press")
    items = fetch_source(src, http, FIXED_NOW)
    assert [i["title"] for i in items] == ["OK"] and items[0]["source_type"] == "official" and items[0]["publisher"] == "Federal Reserve"


# ---------------------------------------------------------------- refresh, failures, back-off
def routes(**over):
    r = {"federalreserve": GOOD, "feeds.bbci": rss(("Russia launches missile strikes on Kyiv", "https://bbc.test/1", NOW_RFC)),
         "sec.gov": rss(("SEC sues crypto firm", "https://sec.test/1", NOW_RFC)), "cftc.gov": rss(("CFTC to host roundtable", "https://cftc.test/1", NOW_RFC)),
         "home.treasury": '<time datetime=2025-10-09T08:00:00Z class=datetime>x</time></span><h3><a href=/news/press-releases/sb1/ hreflang=en>Treasury sanctions network</a></h3>',
         "gdeltproject": {"articles": []}}
    r.update(over)
    return r


def test_refresh_stores_articles_events_and_health(db):
    intel = IntelService(db, http=FakeHttp(routes()), clock=Clock())
    out = intel.refresh(force=True)
    assert out["articles_added"] >= 4 and out["events_touched"] >= 4
    health = {h["source"]: h for h in intel.health()}
    assert health["fed_press"]["status"] == "ok" and health["fed_press"]["consecutive_failures"] == 0
    evs = intel.events()["events"]
    fed = next(e for e in evs if e["title"] == "Federal Reserve issues FOMC statement")
    assert fed["status"] == "official_statement" and fed["articles"][0]["link"] == "https://www.federalreserve.gov/a.htm"
    assert intel.events()["label"] == "Context, not part of the signal"


def test_one_failing_source_does_not_stop_the_others(db):
    intel = IntelService(db, http=FakeHttp(routes(**{"sec.gov": ProviderError("www.sec.gov: HTTP 403")})), clock=Clock())
    out = intel.refresh(force=True)
    health = {h["source"]: h for h in intel.health()}
    assert health["sec_press"]["status"] == "error" and "403" in health["sec_press"]["last_error"]
    assert health["fed_press"]["status"] == "ok" and out["articles_added"] >= 3


def test_failures_never_reach_source_health_or_alerts(db):
    IntelService(db, http=FakeHttp(routes(gdeltproject=ProviderError("api.gdeltproject.org: HTTP 429"))), clock=Clock()).refresh(force=True)
    assert not any("gdelt" in h["source"] for h in db.get_health())    # the source_failure alert rule reads this table


def test_gdelt_exponential_backoff_with_cap_and_clear_status(db):
    http, clock = FakeHttp(routes(gdeltproject=ProviderError("api.gdeltproject.org: HTTP 429"))), Clock()
    intel = IntelService(db, http=http, clock=clock)
    gdelt_calls = lambda: sum("gdeltproject" in c[0] for c in http.calls)
    gaps = []
    for _ in range(9):
        intel.refresh()
        h = {x["source"]: x for x in intel.health()}["gdelt"]
        gaps.append(h["next_attempt_at"] - clock.t)
        before = gdelt_calls()
        intel.refresh()
        assert gdelt_calls() == before                               # no request before the back-off ends
        clock.t = h["next_attempt_at"]
    assert gaps[:4] == [15 * 60_000, 30 * 60_000, 60 * 60_000, 120 * 60_000]
    assert max(gaps) == BACKOFF_CAP_MS == 24 * HOUR
    assert h["status"] == "backing_off" and h["consecutive_failures"] == 9 and "429" in h["last_error"]
    assert "retry" in h["status_text"].lower()
    http.routes["gdeltproject"] = {"articles": []}
    intel.refresh()
    h = {x["source"]: x for x in intel.health()}["gdelt"]
    assert h["status"] == "ok" and h["consecutive_failures"] == 0


def test_backoff_survives_restart(db, config):
    clock = Clock()
    http = FakeHttp(routes(gdeltproject=ProviderError("HTTP 429")))
    IntelService(db, http=http, clock=clock).refresh(force=True)
    n = sum("gdeltproject" in c[0] for c in http.calls)
    IntelService(Database(config.db_path), http=http, clock=clock).refresh()
    assert sum("gdeltproject" in c[0] for c in http.calls) == n


def test_sources_not_refetched_before_their_interval(db):
    http, clock = FakeHttp(routes()), Clock()
    intel = IntelService(db, http=http, clock=clock)
    intel.refresh()
    n = len(http.calls)
    intel.refresh()
    assert len(http.calls) == n
    clock.t += 3 * HOUR
    intel.refresh()
    assert len(http.calls) > n


def test_existing_news_is_read_only_input(db):
    db.insert_news([{"id": "n1", "feed": "coindesk", "publisher": "CoinDesk", "title": "Binance hacked, $1.5 billion stolen", "link": "https://cd.test/1",
                     "summary": "", "published_at": FIXED_NOW - HOUR, "collected_at": FIXED_NOW}])
    intel = IntelService(db, http=FakeHttp(routes()), clock=Clock())
    intel.refresh(force=True)
    ev = next(e for e in intel.events()["events"] if "Binance" in e["title"])
    assert ev["articles"][0]["publisher"] == "CoinDesk" and ev["articles"][0]["source_type"] == "news" and ev["category"] == "crypto_incident"
    n = db.get_news(0)
    assert len(n) == 1


def test_old_articles_are_not_shown_as_live(db):
    intel = IntelService(db, http=FakeHttp(routes()), clock=Clock())
    old = {"id": "o1", "source_id": "bbc_world", "source_type": "news", "publisher": "BBC", "title": "Old war story", "link": "https://bbc.test/o",
           "summary": "", "published_at": FIXED_NOW - 30 * DAY, "collected_at": FIXED_NOW}
    assert intel.add_articles([old]) == 0
    assert intel.events(hours=24 * 60)["events"] == []


def test_same_url_from_two_sources_is_one_article(db):
    intel = IntelService(db, http=FakeHttp(), clock=Clock())
    a = {"id": "same", "source_id": "gdelt", "source_type": "gdelt", "publisher": "x.test", "title": "Fed holds rates steady", "link": "https://x.test/1",
         "summary": "", "published_at": FIXED_NOW - HOUR, "collected_at": FIXED_NOW}
    assert intel.add_articles([a, dict(a), {**a, "source_id": "bbc_world"}]) == 1


def test_conflicting_reports_show_as_disputed_through_the_store(db):
    intel = IntelService(db, http=FakeHttp(), clock=Clock())
    base = {"source_type": "news", "summary": "", "published_at": FIXED_NOW - HOUR, "collected_at": FIXED_NOW}
    intel.add_articles([{**base, "id": "1", "source_id": "a", "publisher": "A", "title": "Binance hacked for $1.5 billion", "link": "https://a.test/1"},
                        {**base, "id": "2", "source_id": "b", "publisher": "B", "title": "Binance denies hack for $1.5 billion", "link": "https://b.test/2"}])
    intel.regroup()
    evs = intel.events()["events"]
    assert len(evs) == 1 and evs[0]["status"] == "disputed" and evs[0]["sources_count"] == 2


def test_event_filters_and_overview(db):
    intel = IntelService(db, http=FakeHttp(routes()), clock=Clock())
    intel.refresh(force=True)
    war = intel.events(group="war")["events"]
    assert war and all(e["category"] == "war_geopolitics" for e in war)
    assert all(e["category"] in ("regulation", "crypto_industry", "crypto_incident") for e in intel.events(group="crypto")["events"])
    assert all(e["status"] == "official_statement" for e in intel.events(status="official_statement")["events"])
    assert all(e["country"] == "Russia" for e in intel.events(country="Russia")["events"])
    with pytest.raises(ValueError):
        intel.events(group="nope")
    with pytest.raises(ValueError):
        intel.events(category="nope")
    with pytest.raises(ValueError):
        intel.events(status="nope")
    o = intel.overview()
    assert o["total"] == sum(o["by_category"].values()) and o["by_group"]["war"] == len(war)
    assert o["label"] == "Context, not part of the signal"
    assert {"ETF flows", "Exchange flows", "Whale movements"} <= {u["name"] for u in o["unavailable"]}


def test_worker_survives_errors_and_stops(db):
    calls = []

    class Boom:
        def refresh(self):
            calls.append(1)
            raise RuntimeError("boom")

    import time
    w = IntelWorker(Boom(), interval=0.01, first_delay=0)
    w.start()
    deadline = time.time() + 5
    while len(calls) < 3 and time.time() < deadline:
        time.sleep(0.01)
    assert w.running and len(calls) >= 3 and w.name != "AlertMonitor"
    w.stop()
    assert not w.running


# ---------------------------------------------------------------- append-only + migration
def test_articles_and_market_points_are_append_only(db):
    intel = IntelService(db, http=FakeHttp(), clock=Clock())
    intel.add_articles([{"id": "1", "source_id": "a", "source_type": "news", "publisher": "A", "title": "T", "link": "https://a.test/1", "summary": "",
                         "published_at": FIXED_NOW, "collected_at": FIXED_NOW}])
    db.intel_add_market_points("vix", [("2025-10-08", 17.0)], "Cboe", FIXED_NOW)
    intel.regroup()
    # Deliberate change after the first live run: event membership is derived and rebuildable, so it is no longer append-only.
    for sql in ("UPDATE intel_articles SET title='x'", "DELETE FROM intel_articles", "UPDATE intel_market_points SET value=1",
                "DELETE FROM intel_market_points"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"), db.connect() as conn:
            conn.execute(sql)


def test_better_rules_rebuild_the_groups_and_keep_every_article(db, monkeypatch):
    from services import intel_service
    intel = IntelService(db, http=FakeHttp(), clock=Clock())
    base = {"source_type": "news", "summary": "", "published_at": FIXED_NOW - HOUR, "collected_at": FIXED_NOW}
    intel.add_articles([{**base, "id": "1", "source_id": "a", "publisher": "A", "title": "Binance hacked for $1.5 billion", "link": "https://a.test/1"},
                        {**base, "id": "2", "source_id": "b", "publisher": "B", "title": "Federal Reserve holds rates", "link": "https://b.test/2"}])
    intel.regroup()
    assert len(intel.events()["events"]) == 2
    with db.connect() as conn:                                   # as a wrong older rule would have stored them
        conn.execute("UPDATE intel_event_articles SET event_id = (SELECT MIN(id) FROM intel_events)")
    assert intel.regroup() == 0                                  # same rules: nothing is redone
    monkeypatch.setattr(intel_service, "GROUPING_RULES_VERSION", intel_service.GROUPING_RULES_VERSION + 1)
    assert intel.regroup() == 2
    evs = intel.events()["events"]
    assert sorted(e["articles_count"] for e in evs) == [1, 1] and len(evs) == 2
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM intel_articles").fetchone()[0] == 2


def test_market_point_keeps_the_first_value(db):
    db.intel_add_market_points("vix", [("2025-10-08", 17.0)], "Cboe", FIXED_NOW)
    db.intel_add_market_points("vix", [("2025-10-08", 99.0), ("2025-10-09", 18.0)], "Cboe", FIXED_NOW + 1)
    assert [(p["data_date"], p["value"]) for p in db.intel_market_points("vix", 5)] == [("2025-10-09", 18.0), ("2025-10-08", 17.0)]


def test_migration_from_schema_15_is_additive_with_a_backup(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    d = Database(path)
    d.init()
    d.set_setting("keep", {"x": 1})
    d.insert_news([{"id": "n", "feed": "f", "publisher": "P", "title": "T", "link": "https://a.test/", "summary": "", "published_at": 1, "collected_at": 1}])
    with sqlite3.connect(path) as conn:
        for t in ("intel_event_articles", "intel_events", "intel_articles", "intel_market_points", "intel_source_health"):
            conn.execute(f"DROP TABLE {t}")
        conn.execute("UPDATE schema_meta SET value='15' WHERE key='schema_version'")
        before = {r[0]: conn.execute(f'SELECT COUNT(*) FROM "{r[0]}"').fetchone()[0]
                  for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    d2 = Database(path)
    d2.init()
    assert list(tmp_path.glob("old.sqlite3.backup-*-schema-15"))
    with sqlite3.connect(path) as conn:
        for t, n in before.items():
            if t != "schema_meta":
                assert conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == n, t
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "18"
    assert {"intel_articles", "intel_events", "intel_event_articles", "intel_market_points", "intel_source_health"} <= tables
    assert d2.get_setting("keep") == {"x": 1}


# ---------------------------------------------------------------- markets
TREASURY = ('<feed><entry><m:properties><d:NEW_DATE m:type="Edm.DateTime">2025-10-07T00:00:00</d:NEW_DATE><d:BC_10YEAR m:type="Edm.Double">4.10</d:BC_10YEAR></m:properties></entry>'
            '<entry><m:properties><d:NEW_DATE m:type="Edm.DateTime">2025-10-08T00:00:00</d:NEW_DATE><d:BC_10YEAR m:type="Edm.Double">4.20</d:BC_10YEAR></m:properties></entry></feed>')
H10 = ('"Series Description","Nominal Broad Dollar Index","Nominal Advanced Foreign Economies Dollar Index"\n"Unit:","Index:_1997_Jan_100","Index"\n'
       '"Time Period","JRXWTFB_N.B","JRXWTFN_N.B"\n2025-10-02,120.0000,114.0\n2025-10-03,ND,114.1\n2025-10-06,121.0000,114.2\n')
VIX = "DATE,OPEN,HIGH,LOW,CLOSE\n10/07/2025,17.0,18.0,16.0,16.50\n10/08/2025,17.0,18.0,16.0,17.50\n"


def test_market_parsers():
    assert parse_treasury_xml(TREASURY) == [("2025-10-07", 4.10), ("2025-10-08", 4.20)]
    assert parse_h10_csv(H10) == [("2025-10-02", 120.0), ("2025-10-06", 121.0)]       # column 1 = broad; ND skipped
    assert parse_cboe_vix(VIX)[-1] == ("2025-10-08", 17.5)
    assert parse_eia_xls((FIX / "eia_synthetic.xls").read_bytes()) == [("2026-10-23", 100.0), ("2026-10-27", 102.0)]    # blank skipped
    assert parse_fred_api({"observations": [{"date": "2025-10-08", "value": "6000.5"}, {"date": "2025-10-07", "value": "."}]}) == [("2025-10-08", 6000.5)]


@pytest.mark.parametrize("fn,bad", [(parse_treasury_xml, "<html/>"), (parse_h10_csv, "nope"), (parse_cboe_vix, "nope"), (parse_eia_xls, b"not xls"), (parse_fred_api, {})])
def test_market_parsers_reject_changed_layouts(fn, bad):
    with pytest.raises(ProviderError):
        fn(bad)


def market_routes(**over):
    r = {"home.treasury.gov": TREASURY, "federalreserve.gov/datadownload": H10, "cdn.cboe.com": VIX,
         "RBRTEd": (FIX / "eia_synthetic.xls").read_bytes(), "RWTCd": (FIX / "eia_synthetic.xls").read_bytes()}
    r.update(over)
    return r


def by_id(out):
    return {s["id"]: s for s in out["series"]}


def test_markets_latest_change_date_delay_source(db):
    clock = Clock()
    m = IntelMarkets(db, FakeHttp(market_routes()), clock=clock)
    m.refresh(force=True)
    out = m.latest()
    s = by_id(out)
    assert s["us10y"]["value"] == 4.20 and s["us10y"]["change"] == pytest.approx(0.10) and s["us10y"]["data_date"] == "2025-10-08"
    assert s["vix"]["change_pct"] == pytest.approx(100 * (17.5 / 16.5 - 1)) and s["vix"]["source"] == "Cboe"
    assert "personal" in s["vix"]["notes"].lower()
    assert s["usd_broad"]["value"] == 121.0
    for x in out["series"]:
        if x["status"] == "ok":
            assert x["delay_label"] and x["source_url"].startswith("https://") and x["age_days"] is not None
    assert out["label"] == "Context, not part of the signal"


def test_gold_and_sp500_unavailable_without_estimates(db):
    m = IntelMarkets(db, FakeHttp(market_routes()), clock=Clock())
    m.refresh(force=True)
    s = by_id(m.latest())
    for k in ("gold", "sp500"):
        assert s[k]["status"] == "unavailable" and s[k]["value"] is None and s[k]["reason"]
    assert "FRED_API_KEY" in s["sp500"]["reason"] and "gold" in UNAVAILABLE
    assert {x["id"] for x in MARKET_SERIES} >= {"us10y", "usd_broad", "vix", "brent", "wti", "sp500"}


def test_sp500_enabled_only_with_a_free_key_and_the_key_never_leaks(db):
    http = FakeHttp(market_routes(**{"api.stlouisfed.org": {"observations": [{"date": "2025-10-08", "value": "6000.5"}, {"date": "2025-10-07", "value": "5950.5"}]}}))
    m = IntelMarkets(db, http, fred_key=lambda: "SECRET-FRED-123", clock=Clock())
    m.refresh(force=True)
    out = m.latest()
    s = by_id(out)["sp500"]
    assert s["status"] == "ok" and s["value"] == 6000.5
    assert "SECRET-FRED-123" not in json.dumps(out) and "SECRET-FRED-123" not in json.dumps(m.health())
    with db.connect() as conn:
        dump = json.dumps([[tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in ("intel_market_points", "intel_source_health", "settings", "kv_cache")])
    assert "SECRET-FRED-123" not in dump


def test_fred_key_not_in_the_error_text(db):
    http = FakeHttp(market_routes(**{"api.stlouisfed.org": ProviderError("api.stlouisfed.org: HTTP 500")}))
    m = IntelMarkets(db, http, fred_key=lambda: "SECRET-FRED-123", clock=Clock())
    m.refresh(force=True)
    assert "SECRET-FRED-123" not in json.dumps(m.health())


def test_market_failure_keeps_last_value_labelled_stale_and_others_work(db):
    clock, http = Clock(), FakeHttp(market_routes())
    m = IntelMarkets(db, http, clock=clock)
    m.refresh(force=True)
    http.routes["cdn.cboe.com"] = ProviderError("cdn.cboe.com: HTTP 503")
    clock.t += 3 * DAY
    m.refresh(force=True)
    s = by_id(m.latest())
    assert s["vix"]["value"] == 17.5 and s["vix"]["fetch_error"] and s["us10y"]["fetch_error"] is None


def test_market_with_no_data_is_an_honest_status(db):
    m = IntelMarkets(db, FakeHttp(), clock=Clock())
    m.refresh(force=True)
    s = by_id(m.latest())
    assert s["vix"]["status"] == "no_data" and s["vix"]["value"] is None and s["vix"]["fetch_error"]


def test_markets_cached_between_refreshes(db):
    clock, http = Clock(), FakeHttp(market_routes())
    m = IntelMarkets(db, http, clock=clock)
    m.refresh()
    n = len(http.calls)
    clock.t += HOUR
    m.refresh()
    assert len(http.calls) == n
    clock.t += 7 * HOUR
    m.refresh()
    assert len(http.calls) > n


# ---------------------------------------------------------------- calendar: BEA + curated BLS, extends the existing service
BEA = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:GDP (Second Estimate) and Corporate Profits\\, 3rd Quarter 2026\r\nDTSTART:20261125T133000Z\r\nDESCRIPTION:www.bea.gov\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nSUMMARY:Personal Income and Outlays\\, October 2026\r\nDTSTART:20261125T133000Z\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nSUMMARY:GDP by County and Personal Income by County\\, 2025\r\nDTSTART:20261202T133000Z\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nSUMMARY:U.S. International Trade in Goods and Services\\, September 2026\r\nDTSTART:20261104T133000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
CURATED = ROOT / "data" / "calendars" / "bls_2026_2027.json"


def ms(s):
    import datetime as dt
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def cal(db, clock, http=None, path=CURATED):
    from test_phase8 import FIXTURE
    http = http or FakeHttp({"fomccalendars": FIXTURE.read_text(encoding="utf-8"), "bea.gov": BEA})
    return CalendarService(db, http, clock=clock, bea=True, bls_curated_path=path), http


def test_bea_parser_keeps_only_gdp_and_pce():
    ev = parse_bea_ics(BEA)
    assert [(e["kind"], e["at_utc"]) for e in ev] == [("GDP", "2026-11-25T13:30:00Z"), ("PCE", "2026-11-25T13:30:00Z")]
    assert ev[0]["event"] == "GDP (Second Estimate) and Corporate Profits, 3rd Quarter 2026" and ev[0]["source"].startswith("U.S. Bureau of Economic")
    assert "bea.gov" in ev[0]["link"]


def test_real_curated_file_is_valid():
    j = json.loads(CURATED.read_text(encoding="utf-8"))
    assert j["source_url"].startswith("https://www.bls.gov/") and j["last_verified"] == "2026-11-02"
    import datetime as dt
    for key in ("CPI", "NFP"):
        dates = [dt.date.fromisoformat(d["date"]) for d in j["releases"][key]["dates"]]
        assert len(dates) == 24 and dates == sorted(dates) and all(d.weekday() < 5 for d in dates)


def test_calendar_has_all_five_kinds_with_utc_and_status(db):
    clock = Clock(ms("2026-11-03T00:00:00Z"))
    out = cal(db, clock)[0].upcoming(days=45, past_days=30)
    kinds = {e["kind"] for e in out["events"]}
    assert {"FOMC", "CPI", "NFP", "GDP", "PCE"} <= kinds
    cpi = next(e for e in out["events"] if e["kind"] == "CPI" and e["status"] == "upcoming")
    assert cpi["at_utc"] == "2026-11-10T13:30:00Z" and cpi["source"].startswith("U.S. Bureau of Labor") and "bls.gov" in cpi["link"]    # 08:30 EST
    fomc = next(e for e in out["events"] if e["kind"] == "FOMC" and e["status"] == "released")
    assert fomc["at_utc"] == "2026-10-28T18:00:00Z"
    assert all(e["status"] in ("released", "upcoming") for e in out["events"])
    assert [e["at"] for e in out["events"]] == sorted(e["at"] for e in out["events"])


def test_curated_dst_conversion():
    from services.calendar_service import load_bls_curated
    ev, meta = load_bls_curated(CURATED, ms("2026-11-03T00:00:00Z"))
    at = {(e["kind"], e["period"]): e["at_utc"] for e in ev}
    assert at[("CPI", "September 2026")] == "2026-10-14T12:30:00Z"          # EDT
    assert at[("CPI", "October 2026")] == "2026-11-10T13:30:00Z"            # EST
    assert meta["stale"] is False and meta["last_verified"] == "2026-11-02"


def test_released_flips_at_release_time(db):
    t = ms("2026-11-10T13:30:00Z")
    before = cal(db, Clock(t - 60_000))[0].upcoming(days=1, past_days=1)
    assert next(e for e in before["events"] if e["kind"] == "CPI" and e["at"] == t)["status"] == "upcoming"
    after = cal(db, Clock(t + 60_000))[0].upcoming(days=1, past_days=1)
    assert next(e for e in after["events"] if e["kind"] == "CPI" and e["at"] == t)["status"] == "released"


def test_curated_warning_when_older_than_12_months(db):
    fresh = cal(db, Clock(ms("2027-10-30T00:00:00Z")))[0].upcoming(days=30)
    assert not any("last verified" in w.lower() for w in fresh["warnings"])
    stale = cal(db, Clock(ms("2027-11-03T00:00:00Z")))[0].upcoming(days=30)
    assert any("last verified" in w.lower() and "2026-11-02" in w for w in stale["warnings"])
    row = next(s for s in stale["sources"] if "Labor" in s["source"])
    assert row["status"] == "stale" and row["last_verified"] == "2026-11-02"


@pytest.mark.parametrize("content", ["not json", "{}", '{"releases": {"CPI": {"dates": [{"date": "bad"}]}}, "last_verified": "2026-11-02", "source_url": "https://www.bls.gov/"}'])
def test_broken_curated_file_is_an_error_not_a_crash(db, tmp_path, content):
    p = tmp_path / "b.json"
    p.write_text(content, encoding="utf-8")
    out = cal(db, Clock(ms("2026-11-03T00:00:00Z")), path=p)[0].upcoming(days=45)
    assert next(s for s in out["sources"] if "Labor" in s["source"])["status"] == "error"
    assert any(e["kind"] == "PCE" for e in out["events"])                  # the other sources still work


def test_missing_curated_file_is_an_error_not_a_crash(db, tmp_path):
    out = cal(db, Clock(ms("2026-11-03T00:00:00Z")), path=tmp_path / "none.json")[0].upcoming(days=45)
    assert next(s for s in out["sources"] if "Labor" in s["source"])["status"] == "error"


def test_never_fetches_bls_even_with_an_old_env_setting(db, monkeypatch):
    monkeypatch.setenv("CALENDAR_CONTACT_EMAIL", "owner@example.com")
    c, http = cal(db, Clock(ms("2026-11-03T00:00:00Z")))
    c.upcoming(days=45)
    assert http.calls and not any("bls.gov" in x[0] for x in http.calls)
    src = (ROOT / "services" / "calendar_service.py").read_text(encoding="utf-8")
    assert "bls.ics" not in src and "CALENDAR_CONTACT_EMAIL" not in src


def test_bea_failure_keeps_the_rest_and_records_intel_health_only(db):
    from test_phase8 import FIXTURE
    http = FakeHttp({"fomccalendars": FIXTURE.read_text(encoding="utf-8"), "bea.gov": ProviderError("www.bea.gov: HTTP 503")})
    recorded = []
    c = CalendarService(db, http, clock=Clock(ms("2026-11-03T00:00:00Z")), bea=True, bls_curated_path=CURATED,
                        health=lambda source, ok, error=None: recorded.append((source, ok)))
    out = c.upcoming(days=45)
    assert any("BEA" in w for w in out["warnings"]) and any(e["kind"] == "CPI" for e in out["events"])
    assert recorded == [("calendar:bea", False)] and not any("bea" in h["source"] for h in db.get_health())


def test_wrong_bea_note_is_gone():
    for f in ("services/calendar_service.py", "services/market_data.py", "README.md", "USER_GUIDE.md"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert "no machine-readable schedule" not in text, f


def test_default_calendar_stays_fomc_only_for_existing_callers(db):
    from test_phase8 import FIXTURE
    http = FakeHttp({"fomccalendars": FIXTURE.read_text(encoding="utf-8")})
    out = CalendarService(db, http, clock=Clock(ms("2026-12-01T00:00:00Z"))).upcoming()
    assert [e["kind"] for e in out["events"]] == ["FOMC"] and len(http.calls) == 1 and out["events"][0]["status"] == "upcoming"


# ---------------------------------------------------------------- routes
def intel_app(config, db):
    c, app = make_app(config, db)
    return c, app


def test_routes_validation_and_label(config, db):
    c, app = intel_app(config, db)
    app.config["BTC_INTEL"].http = app.config["BTC_INTEL"].markets.http = FakeHttp(routes())
    app.config["BTC_INTEL"].refresh(force=True)
    for url in ("/api/intel/overview", "/api/intel/events", "/api/intel/markets"):
        r = c.get(url)
        assert r.status_code == 200 and r.get_json()["label"] == "Context, not part of the signal", url
    assert c.get("/api/intel/events?group=war").status_code == 200
    for bad in ("group=x", "category=x", "status=x", "hours=abc", "hours=0", "hours=100000", "limit=0", "limit=99999", "country=" + "x" * 90):
        assert c.get(f"/api/intel/events?{bad}").status_code == 400, bad
    assert c.get("/api/calendar?days=45&past=14").status_code == 200
    for bad in ("days=x", "days=0", "days=999", "past=-1", "past=999"):
        assert c.get(f"/api/calendar?{bad}").status_code == 400, bad


def test_no_state_changing_intel_routes(config, db):
    c, app = intel_app(config, db)
    rules = [r for r in app.url_map.iter_rules() if r.rule.startswith("/api/intel")]
    assert rules and all(r.methods <= {"GET", "HEAD", "OPTIONS"} for r in rules)


def test_get_requests_never_fetch(config, db):
    c, app = intel_app(config, db)
    http = FakeHttp(routes())
    app.config["BTC_INTEL"].http = http
    app.config["BTC_INTEL"].markets.http = http
    for url in ("/api/intel/overview", "/api/intel/events", "/api/intel/markets"):
        c.get(url)
    assert http.calls == []


def test_worker_not_started_by_create_app(config, db):
    import threading
    _, app = intel_app(config, db)
    assert app.config["BTC_INTEL_WORKER"].running is False
    assert not any(t.name == "IntelWorker" for t in threading.enumerate())


def test_hostile_text_is_data_and_stored_links_are_http_only(config, db):
    c, app = intel_app(config, db)
    intel = app.config["BTC_INTEL"]
    t = intel.clock()                                            # this app runs on real time
    base = {"source_id": "a", "source_type": "news", "publisher": "A", "summary": "", "published_at": t - HOUR, "collected_at": t}
    assert intel.add_articles([{**base, "id": "1", "title": "<script>alert(1)</script> war", "link": "https://a.test/1"},
                               {**base, "id": "2", "title": "Bad link story", "link": "javascript:alert(1)"}]) == 1
    intel.regroup()
    assert c.get("/api/intel/events").mimetype == "application/json"
    events = intel.events()["events"]
    assert [e["title"] for e in events] == ["<script>alert(1)</script> war"]
    assert all(a["link"].startswith("https://") for e in events for a in e["articles"])


def test_intel_health_in_sources_route_and_docs(config, db):
    c, app = intel_app(config, db)
    app.config["BTC_INTEL"].http = app.config["BTC_INTEL"].markets.http = FakeHttp(routes(gdeltproject=ProviderError("HTTP 429")))
    app.config["BTC_INTEL"].refresh(force=True)
    body = c.get("/api/sources").get_json()
    assert any(h["source"] == "gdelt" and h["status"] == "backing_off" for h in body["intel_health"])
    docs = {d["source"] for d in body["docs"]}
    assert {"intel:gdelt", "intel:bbc", "intel:official", "market:treasury", "market:h10", "market:eia", "market:cboe", "calendar:bea", "calendar:bls"} <= docs


def test_fred_key_status_only_and_never_exposed(config, db):
    config.fred_api_key = "SECRET-FRED-123"
    c, _ = intel_app(config, db)
    body = c.get("/api/settings").get_data(as_text=True)
    assert "SECRET-FRED-123" not in body and json.loads(body)["market"]["fred_api_key"]["value"] == "set"
    for url in ("/", "/settings", "/api/sources", "/api/intel/markets", "/api/intel/overview"):
        assert "SECRET-FRED-123" not in c.get(url).get_data(as_text=True), url
    config.fred_api_key = ""
    c2, _ = intel_app(config, db)
    assert c2.get("/api/settings").get_json()["market"]["fred_api_key"]["value"] == "not set"


def test_requirements_and_env_example():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "xlrd==" in req and "FRED_API_KEY=" in env and "CALENDAR_CONTACT_EMAIL=" in env


def test_page_has_the_view_and_context_label(config, db):
    c, _ = intel_app(config, db)
    html = c.get("/").get_data(as_text=True)
    assert 'data-view="intel"' in html and 'data-views="intel"' in html and "Context, not part of the signal" in html
