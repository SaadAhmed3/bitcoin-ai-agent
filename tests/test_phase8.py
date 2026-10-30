"""Phase 8: alerts, exports, settings, watchlist, economic calendar, and the Part 0 assistant setting.

Everything here is deterministic and offline: fake providers, fake HTTP, fixed clocks.
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from pathlib import Path

import pytest
from conftest import FIXED_NOW, FakeOverview, FakeProvider, local_client, make_rows

from services import exports as exports_mod
from services.alerts import PAUSED_TITLE, AlertMonitor, AlertService
from services.calendar_service import CalendarService, parse_bls_ics, parse_fomc
from services.http_client import ProviderError
from services.settings_service import SECTIONS, SettingsService
from services.watchlist import WatchlistService

FIXTURE = Path(__file__).parent / "fixtures" / "fomc_calendar_sample.htm"
MIN = 60_000
HOUR = 3_600_000


# ====================================================================== helpers
class Clock:
    def __init__(self, t=FIXED_NOW):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += ms


def alert_defaults(**over):
    d = {k: f.default for k, f in SECTIONS["alerts"].items()}
    d.update(over)
    return d


def make_alerts(db, clock=None, notifiers=None, **data):
    """AlertService with providers that read from the mutable `data` dict."""
    clock = clock or Clock()
    settings = alert_defaults(**data.pop("settings", {}))
    svc = AlertService(db, settings=lambda k: settings[k], clock=clock, notifiers=notifiers,
                       providers={name: (lambda name=name: data.get(name)) for name in
                                  ("price", "price_1h_ago", "news", "volatility", "signal", "health", "paper")})
    return svc, data, clock, settings


def price(p, stale=False, change=0.5, provider="binance"):
    return {"price": p, "provider": provider, "quote": "USDT", "observed_at": FIXED_NOW - 1000,
            "change_24h_pct": change, "is_stale": stale, "age_seconds": 500 if stale else 1, "status": "stale" if stale else "fresh"}


def rule(svc, kind, **params):
    return svc.create_rule({"kind": kind, "params": params})


def assert_complete(alert):
    assert alert["created_at"] and alert["created_at_iso"].endswith("Z")
    assert alert["reason"].strip() and alert["message"].strip() and alert["title"].strip()
    assert alert["links"] and all(isinstance(x, str) and x for x in alert["links"])
    assert alert["severity"] in ("info", "warning", "critical") and alert["source"]


# ====================================================================== alerts: rules
def test_price_above_fires_once_then_rearms_after_hysteresis(db):
    svc, data, clock, _ = make_alerts(db, price=price(64_000))
    r = rule(svc, "price_above", price=65_000)
    assert svc.evaluate() == []                                   # below the level: nothing
    data["price"] = price(65_100)
    out = svc.evaluate()
    assert len(out) == 1 and out[0]["kind"] == "price_above" and out[0]["value"] == 65_100 and out[0]["threshold"] == 65_000
    assert "crossed above" in out[0]["reason"] and out[0]["links"] == ["https://www.binance.com/en/trade/BTC_USDT"]
    assert_complete(out[0])
    clock.advance(2 * HOUR)                                       # cooldown long gone, but still above: no refire
    data["price"] = price(65_500)
    assert svc.evaluate() == []
    data["price"] = price(64_800)                                 # back only 0.3% - inside the 0.5% band
    assert svc.evaluate() == []
    data["price"] = price(65_100)
    assert svc.evaluate() == []                                   # still not re-armed
    data["price"] = price(64_600)                                 # 0.62% below the level: re-armed
    assert svc.evaluate() == []
    data["price"] = price(65_050)
    again = svc.evaluate()
    assert len(again) == 1 and again[0]["rule_id"] == r["id"]


def test_price_below_mirror_and_starts_armed(db):
    svc, data, clock, _ = make_alerts(db, price=price(64_000))
    rule(svc, "price_below", price=64_500)                        # already below at creation: fires at once
    out = svc.evaluate()
    assert len(out) == 1 and "below" in out[0]["title"]
    assert svc.evaluate() == []
    data["price"] = price(64_700)                                 # +0.31% - not re-armed
    clock.advance(HOUR)
    svc.evaluate()
    data["price"] = price(64_400)
    assert svc.evaluate() == []
    data["price"] = price(64_900)                                 # +0.62%: re-armed
    svc.evaluate()
    data["price"] = price(64_400)
    assert len(svc.evaluate()) == 1


def test_cooldown_blocks_refire_even_when_rearmed(db):
    svc, data, clock, _ = make_alerts(db, price=price(65_100))
    rule(svc, "price_above", price=65_000)
    assert len(svc.evaluate()) == 1
    data["price"] = price(64_000)
    svc.evaluate()                                                # re-armed
    data["price"] = price(65_200)
    clock.advance(10 * MIN)                                       # inside the 30 min default cooldown
    assert svc.evaluate() == []
    clock.advance(25 * MIN)
    assert len(svc.evaluate()) == 1


def test_identical_message_dedupe_within_cooldown(db):
    svc, _, clock, _ = make_alerts(db)
    r = rule(svc, "big_move")
    args = dict(severity="info", title="t", message="same text", reason="why", value=1.0, threshold=1.0, source="s", links=["/x"])
    assert svc.emit(r, cooldown_key="a", **args)
    assert svc.emit(r, cooldown_key="b", **args) is None          # different cooldown key, same message
    assert svc.emit(r, cooldown_key="c", **{**args, "message": "other text"})
    clock.advance(31 * MIN)
    assert svc.emit(r, cooldown_key="d", **args)


def test_big_move_1h_and_24h_on_transition_only(db):
    svc, data, clock, _ = make_alerts(db, price=price(60_000, change=2.0), price_1h_ago=60_000)
    rule(svc, "big_move")
    assert svc.evaluate() == []
    data["price"] = price(62_000, change=2.0)                     # +3.33% in 1h
    out = svc.evaluate()
    assert len(out) == 1 and "1h" in out[0]["title"] and out[0]["threshold"] == 3.0 and "up" in out[0]["title"]
    clock.advance(2 * HOUR)
    assert svc.evaluate() == []                                   # still breached: no repeat
    data["price"] = price(61_000, change=-9.0)                    # 1h back under; 24h -9%
    out = svc.evaluate()
    assert len(out) == 1 and "24h" in out[0]["title"] and "down" in out[0]["title"] and out[0]["value"] == -9.0
    assert_complete(out[0])


def test_big_move_custom_threshold_from_rule(db):
    svc, data, _, _ = make_alerts(db, price=price(60_700, change=0.1), price_1h_ago=60_000)
    rule(svc, "big_move", pct_1h=1)
    out = svc.evaluate()
    assert len(out) == 1 and out[0]["threshold"] == 1.0


def news_event(eid, impact="high", fresh="new", publishers=("CoinDesk",)):
    return {"id": eid, "title": f"Story {eid}", "publisher": publishers[0], "impact": impact, "freshness": fresh,
            "sentiment": "bearish", "published_at": FIXED_NOW - 10 * MIN, "independent_sources": len(publishers),
            "link": f"https://news.test/{eid}/0",
            "sources": [{"publisher": p, "link": f"https://news.test/{eid}/{i}"} for i, p in enumerate(publishers)]}


def test_significant_news_once_per_cluster_never_per_publisher(db):
    cluster = news_event("c1", publishers=("CoinDesk", "Reuters", "The Block"))
    svc, data, clock, _ = make_alerts(db, news={"events": [cluster, news_event("low", impact="medium"),
                                                         news_event("old", fresh="recent")]})
    rule(svc, "significant_news")
    out = svc.evaluate()
    assert len(out) == 1 and out[0]["title"].startswith("High-impact news: Story c1")
    assert out[0]["links"] == ["https://news.test/c1/0", "https://news.test/c1/1", "https://news.test/c1/2"]
    assert "3 publisher(s)" in out[0]["message"]
    assert_complete(out[0])
    clock.advance(7 * HOUR)
    assert svc.evaluate() == []                                   # same cluster never alerts twice
    data["news"]["events"].append(news_event("c2"))
    out = svc.evaluate()
    assert [a["title"] for a in out] == ["High-impact news: Story c2"]


def test_high_volatility_only_on_transition(db):
    svc, data, clock, _ = make_alerts(db, volatility={"4h": "normal", "1h": "high"})
    rule(svc, "high_volatility")
    assert svc.evaluate() == []
    data["volatility"] = {"4h": "extreme", "1h": "high"}
    out = svc.evaluate()
    assert len(out) == 1 and "4h" in out[0]["title"] and "normal to extreme" in out[0]["reason"]
    assert_complete(out[0])
    clock.advance(2 * HOUR)
    assert svc.evaluate() == []                                   # stays extreme: no repeat
    data["volatility"] = {"4h": "high", "1h": "extreme"}
    out = svc.evaluate()
    assert len(out) == 1 and "1h" in out[0]["title"]
    data["volatility"] = {"4h": "high", "1h": "high"}
    svc.evaluate()
    clock.advance(2 * HOUR)
    data["volatility"] = {"4h": "extreme", "1h": "high"}
    assert len(svc.evaluate()) == 1                               # a NEW transition alerts again


def test_signal_change_baseline_then_changes(db):
    svc, data, clock, _ = make_alerts(db, signal={"id": 7, "signal": "NO TRADE", "setup": "none", "score": 0.1, "engine": "signal-1.7"})
    rule(svc, "signal_change")
    assert svc.evaluate() == []                                   # first look stores a baseline
    assert svc.evaluate() == []
    data["signal"] = {"id": 8, "signal": "NO TRADE", "setup": "long_pullback", "score": 0.2, "engine": "signal-1.7"}
    out = svc.evaluate()
    assert len(out) == 1 and out[0]["links"] == ["/api/signal/8"] and "none -> NO TRADE / long_pullback" in out[0]["message"]
    assert_complete(out[0])
    clock.advance(HOUR)
    assert svc.evaluate() == []


def test_source_failure_after_three_and_recovery(db):
    svc, data, clock, _ = make_alerts(db, health=[{"source": "binance", "status": "error", "consecutive_failures": 2}])
    rule(svc, "source_failure")
    assert svc.evaluate() == []
    data["health"] = [{"source": "binance", "status": "error", "consecutive_failures": 3, "last_error": "HTTP 451"}]
    out = svc.evaluate()
    assert len(out) == 1 and out[0]["severity"] == "critical" and "HTTP 451" in out[0]["message"]
    assert_complete(out[0])
    data["health"][0]["consecutive_failures"] = 7
    assert svc.evaluate() == []
    data["health"] = [{"source": "binance", "status": "ok", "consecutive_failures": 0, "last_success": FIXED_NOW}]
    out = svc.evaluate()
    assert len(out) == 1 and out[0]["title"] == "Data source recovered: binance" and out[0]["severity"] == "info"
    assert svc.evaluate() == []


def test_source_failure_stale_price_and_recovery(db):
    svc, data, _, _ = make_alerts(db, price=price(65_000, stale=True), health=[])
    rule(svc, "source_failure")
    out = svc.evaluate()
    assert [a["title"] for a in out] == ["BTC price is stale"]
    assert svc.evaluate() == []
    data["price"] = price(65_000)
    assert [a["title"] for a in svc.evaluate()] == ["BTC price is fresh again"]


def test_db_consecutive_failures_counter(db):
    for _ in range(3):
        db.record_health("x", False, error="boom")
    assert db.get_health()[0]["consecutive_failures"] == 3
    db.record_health("x", True)
    assert db.get_health()[0]["consecutive_failures"] == 0


def test_risk_limit_alerts_are_labelled_simulated(db):
    pa = {"daily_limit_reached": False, "daily_loss_pct": 1.0, "exposure_pct": 30.0,
          "settings": {"max_exposure_pct": 50.0, "daily_loss_limit_pct": 3.0}}
    svc, data, _, _ = make_alerts(db, paper=pa)
    rule(svc, "risk_limit")
    assert svc.evaluate() == []
    data["paper"] = {**pa, "daily_limit_reached": True, "daily_loss_pct": 3.2, "exposure_pct": 55.0}
    out = svc.evaluate()
    assert len(out) == 2
    for a in out:
        assert "SIMULATED" in a["title"] and "SIMULATED" in a["message"] and "SIMULATED" in a["reason"] and "SIMULATED" in a["source"]
        assert_complete(a)
    assert svc.evaluate() == []


def test_global_cap_then_single_paused_alert(db):
    svc, data, clock, _ = make_alerts(db, settings={"global_cap_per_hour": 3},
                                      news={"events": [news_event(f"e{i}") for i in range(8)]})
    rule(svc, "significant_news")
    out = svc.evaluate()
    assert len(out) == 3
    titles = [a["title"] for a in svc.list_alerts()]
    assert titles.count(PAUSED_TITLE) == 1
    data["news"]["events"].append(news_event("later"))
    clock.advance(10 * MIN)
    assert svc.evaluate() == [] and [a["title"] for a in svc.list_alerts()].count(PAUSED_TITLE) == 1
    clock.advance(HOUR)                                           # the hourly window has passed
    assert len(svc.evaluate()) == 3                               # e3..e7 and "later" were held back; 3 fit now


def test_state_survives_restart(db, config):
    svc, data, clock, _ = make_alerts(db, price=price(65_100))
    rule(svc, "price_above", price=65_000)
    assert len(svc.evaluate()) == 1
    from database import Database
    db2 = Database(config.db_path)                                # a new process: same SQLite file
    svc2, data2, _, _ = make_alerts(db2, clock=Clock(clock.t + 2 * HOUR), price=price(65_200))
    assert svc2.evaluate() == []                                  # disarmed state was persisted
    assert len(svc2.list_alerts()) == 1


def test_notifier_failure_never_breaks_alerting(db):
    seen = []

    def bad(alert):
        raise RuntimeError("toast failed")

    svc, _, _, _ = make_alerts(db, notifiers=[bad, seen.append], price=price(65_100))
    rule(svc, "price_above", price=65_000)
    out = svc.evaluate()
    assert len(out) == 1 and seen and seen[0]["id"] == out[0]["id"]


def test_failing_rule_does_not_stop_others(db):
    svc, data, _, _ = make_alerts(db, price=price(65_100))
    svc.providers["volatility"] = lambda: (_ for _ in ()).throw(RuntimeError("analysis broke"))
    rule(svc, "high_volatility")
    rule(svc, "price_above", price=65_000)
    out = svc.evaluate()
    assert [a["kind"] for a in out] == ["price_above"]


def test_monitor_runs_jobs_before_alerts_and_isolates_their_errors():
    order = []

    class Svc:
        def evaluate(self):
            order.append("alerts")

    def bad_job():
        order.append("bad")
        raise RuntimeError("job failed")

    m = AlertMonitor(Svc(), jobs=[bad_job, lambda: order.append("good")])
    m.run_once()
    assert order == ["bad", "good", "alerts"] and m.errors == 1


def test_monitor_survives_exceptions_and_stops_cleanly(db):
    calls = []

    class Boom:
        def evaluate(self):
            calls.append(1)
            raise RuntimeError("cycle failed")

    m = AlertMonitor(Boom(), interval=0.01, first_delay=0)
    m.start()
    deadline = time.time() + 5
    while len(calls) < 3 and time.time() < deadline:
        time.sleep(0.01)
    assert m.running and len(calls) >= 3 and m.errors >= 3
    m.stop()
    assert not m.running


def test_seed_defaults_once(db):
    svc, _, _, _ = make_alerts(db)
    svc.seed_defaults()
    svc.seed_defaults()
    kinds = sorted(r["kind"] for r in svc.list_rules())
    assert kinds == sorted(["big_move", "significant_news", "high_volatility", "signal_change", "source_failure", "risk_limit"])


def test_rule_validation(db):
    svc, _, _, _ = make_alerts(db)
    for bad in [None, [], {"kind": "nope"}, {"kind": "price_above"}, {"kind": "price_above", "params": {"price": "abc"}},
                {"kind": "price_above", "params": {"price": True}}, {"kind": "price_above", "params": {"price": -5}},
                {"kind": "price_above", "params": {"price": 1, "zzz": 1}}, {"kind": "big_move", "params": {"pct_1h": 99}},
                {"kind": "big_move", "cooldown_s": 5}, {"kind": "big_move", "cooldown_s": "x"}, {"kind": "big_move", "enabled": "yes"},
                {"kind": "big_move", "params": "x"}]:
        with pytest.raises(ValueError):
            svc.create_rule(bad)
    r = svc.create_rule({"kind": "price_above", "params": {"price": "70000"}, "cooldown_s": 600})
    assert r["params"] == {"price": 70_000.0} and r["cooldown_s"] == 600 and r["enabled"] is True
    with pytest.raises(ValueError):
        svc.update_rule(r["id"], {"kind": "price_below"})
    with pytest.raises(LookupError):
        svc.update_rule(999, {"enabled": False})


# ====================================================================== app helpers
class FakeHttp:
    """Stands in for HttpClient: scripted responses by URL substring; records every call."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def _find(self, url, params):
        self.calls.append((url, dict(params or {})))
        for key, val in self.routes.items():
            if key in url:
                val = val(url, params) if callable(val) else val
                if isinstance(val, Exception):
                    raise val
                return val
        raise ProviderError(f"{url}: no route")

    def get_json(self, url, params=None, headers=None):
        return self._find(url, params), 5.0

    def get_bytes(self, url, params=None, headers=None, max_bytes=5_000_000):
        v = self._find(url, params)
        self.calls[-1] = (url, dict(params or {}), dict(headers or {}))
        return (v.encode("utf-8") if isinstance(v, str) else v), 5.0


def make_app(config, db, watch_http=None, cal_http=None, cal_email="", fake_ollama=None, wl_symbols=None):
    from app import create_app
    from services.http_client import HttpClient
    from services.market_data import MarketDataService
    from services.news_collector import NewsCollector
    from services.news_service import NewsService
    from test_assistant import FakeOllama, service
    svc = MarketDataService(db, config, http=HttpClient(min_interval=0), providers=[FakeProvider("fake1")],
                            overview_provider=FakeOverview(), clock=lambda: FIXED_NOW)
    collector = NewsCollector(db, http=None, feeds=[], clock=lambda: FIXED_NOW, fetcher=lambda f: (b"", 1.0))
    news = NewsService(db, collector, price_series=lambda: None, clock=lambda: FIXED_NOW)
    settings_for_wl = SettingsService(db)
    wl = WatchlistService(db, watch_http or FakeHttp(), symbols=lambda: wl_symbols or settings_for_wl.get("watchlist", "symbols"),
                          clock=lambda: FIXED_NOW)
    cal = CalendarService(db, cal_http or FakeHttp(), contact_email=lambda: cal_email, clock=lambda: FIXED_NOW)
    app = create_app(config, market=svc, news=news, ollama=service(fake_ollama or FakeOllama(up=False)),
                     watchlist=wl, calendar=cal, notifiers=[])
    app.testing = True
    return local_client(app), app


def test_monitor_not_started_by_create_app(config, db):
    c, app = make_app(config, db)
    assert app.config["BTC_MONITOR"].running is False
    assert c.get("/api/alerts").get_json()["monitor_running"] is False
    assert not any(t.name == "AlertMonitor" for t in threading.enumerate())


def test_alert_routes_and_400s(config, db):
    c, app = make_app(config, db)
    rules = c.get("/api/alerts/rules").get_json()["rules"]
    assert {r["kind"] for r in rules} >= {"big_move", "signal_change", "risk_limit"}        # seeded defaults
    r = c.post("/api/alerts/rules", json={"kind": "price_above", "params": {"price": 70_000}})
    assert r.status_code == 201
    rid = r.get_json()["id"]
    for bad in [{"kind": "x"}, {"kind": "price_above"}, {"kind": "price_below", "params": {"price": "abc"}}, "nope"]:
        assert c.post("/api/alerts/rules", json=bad).status_code == 400
    assert c.post("/api/alerts/rules", data="not json", content_type="text/plain").status_code == 400
    assert c.put(f"/api/alerts/rules/{rid}", json={"enabled": False}).get_json()["enabled"] is False
    assert c.put(f"/api/alerts/rules/{rid}", json={"cooldown_s": 1}).status_code == 400
    assert c.put("/api/alerts/rules/9999", json={"enabled": False}).status_code == 404
    assert c.delete(f"/api/alerts/rules/{rid}").get_json() == {"deleted": rid}
    assert c.delete(f"/api/alerts/rules/{rid}").status_code == 404
    t = c.post("/api/alerts/test")
    assert t.status_code == 201 and t.get_json()["title"].startswith("TEST alert") and t.get_json()["kind"] == "test"
    assert_complete(t.get_json())
    body = c.get("/api/alerts?unread=1&limit=10").get_json()
    assert body["unread"] == 1 and len(body["alerts"]) == 1
    assert c.get("/api/alerts?unread=2").status_code == 400
    assert c.get("/api/alerts?limit=abc").status_code == 400
    assert c.get("/api/alerts?limit=0").status_code == 400
    aid = body["alerts"][0]["id"]
    assert c.post(f"/api/alerts/{aid}/ack").get_json()["unread"] == 0
    assert c.post("/api/alerts/9999/ack").status_code == 404
    c.post("/api/alerts/test")
    c.post("/api/alerts/test")
    assert c.post("/api/alerts/ack_all").get_json()["acknowledged"] == 2
    assert c.get("/api/alerts?unread=1").get_json()["alerts"] == []


# ====================================================================== exports
def seed_export_data(db):
    from services.backtest_service import config_hash  # noqa: F401  (import check only)
    db.upsert_candles("fake1", "BTCUSDT", "1h", make_rows(50), FIXED_NOW)
    sig = {"generated_at": FIXED_NOW, "signal": "NO TRADE", "setup": "none", "score": -0.12,
           "price": {"value": 65_000.0, "provider": "fake1"}}
    db.save_signal(sig, "=cmd|' /C calc'!A0")
    db.save_backtest(FIXED_NOW, "signal-1.7", "abc", False, {"verdict": "Not validated", "strategy": {"net_return_pct": -1.5}},
                     {"criteria": [{"name": "Enough trades", "passed": False, "detail": "+3 trades"}],
                      "trades": [{"entry_time": FIXED_NOW - 2 * HOUR, "exit_time": FIXED_NOW - HOUR, "entry": 60_000, "exit": 61_000,
                                  "stop": 59_000, "target": 61_000, "reason": "target", "ret": 0.016, "r_multiple": 1.0, "bars": 1,
                                  "entry_type": "standard"}],
                      "equity": {"time": [FIXED_NOW - HOUR, FIXED_NOW], "strategy": [1.0, 1.016], "buy_and_hold": [1.0, 0.99]},
                      "verdict": "Not validated"})
    with db.connect() as conn:
        conn.execute("""INSERT INTO paper_orders (created_at, side, qty, price, fee, realized_pnl, reason, simulated)
                        VALUES (?,?,?,?,?,?,?,1)""", (FIXED_NOW, "BUY", 0.01, 65_000, 0.65, None, "@SUM(1+1)"))
    db.insert_news([{"id": "n1", "feed": "t", "publisher": "Pub", "title": "=HYPERLINK(\"http://evil\") bitcoin ETF inflows",
                     "link": "https://a.test/1", "summary": "", "published_at": FIXED_NOW - HOUR, "collected_at": FIXED_NOW}])
    db.add_chat("user", "+SUM(A1)", FIXED_NOW)
    db.add_chat("assistant", "-12.5 is a plain number", FIXED_NOW, mode="rule")


def read_csv(resp):
    raw = resp.data
    assert raw.startswith(b"\xef\xbb\xbf")                       # UTF-8 BOM for Excel
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


@pytest.mark.parametrize("dataset,query", [("candles", "&tf=1h"), ("signals", ""), ("news", "&hours=72"),
                                           ("backtest", "&run=1"), ("paper_orders", ""), ("alerts", ""), ("chat", "")])
def test_every_dataset_csv_and_json(config, db, dataset, query):
    c, _ = make_app(config, db)
    seed_export_data(db)
    c.post("/api/alerts/test")
    r = c.get(f"/api/export/{dataset}?format=csv{query}")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert re.match(rf'attachment; filename="btc-signal-lab_{dataset}_\d{{8}}T\d{{6}}Z\.csv"', r.headers["Content-Disposition"])
    rows = read_csv(r)
    assert len(rows) >= 2 and all(len(x) == len(rows[0]) for x in rows)
    j = c.get(f"/api/export/{dataset}?format=json{query}")
    assert j.status_code == 200 and j.mimetype == "application/json"
    assert j.headers["Content-Disposition"].endswith('.json"')
    meta = j.get_json()["metadata"]
    # batch 2: live engine bumped to signal-1.8 (deliberate). A backtest export reports the engine that made the
    # run (the seeded run is signal-1.7); before the bump the two happened to be the same.
    expected_engine = "signal-1.7" if dataset == "backtest" else "signal-1.8"
    assert meta["dataset"] == dataset and meta["app_version"] == __import__("app").APP_VERSION and meta["engine"] == expected_engine
    assert meta["generated_at"].endswith("Z") and meta["source_notes"]


def test_csv_has_utc_and_local_time_columns(config, db):
    c, _ = make_app(config, db)
    seed_export_data(db)
    rows = read_csv(c.get("/api/export/signals?format=csv"))
    assert rows[0][:2] == ["id", "created_at_utc"] and rows[0][2].startswith("created_at_local_")
    assert rows[1][1].endswith("Z") and re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", rows[1][2])


def test_csv_injection_escaped_but_numbers_kept(config, db):
    c, _ = make_app(config, db)
    seed_export_data(db)
    sig = read_csv(c.get("/api/export/signals?format=csv"))
    assert sig[1][-1].startswith("'=cmd") and sig[1][-2] == "fake1" and sig[1][-4] == "-0.12"
    paper = read_csv(c.get("/api/export/paper_orders?format=csv"))
    assert paper[1][paper[0].index("reason")] == "'@SUM(1+1)" and paper[1][-1] == "SIMULATED"
    news = read_csv(c.get("/api/export/news?format=csv&hours=72"))
    assert news[1][news[0].index("title")].startswith("'=HYPERLINK")
    chat = read_csv(c.get("/api/export/chat?format=csv"))
    content = [r[chat[0].index("content")] for r in chat[1:]]
    assert content[0] == "'+SUM(A1)" and content[1] == "'-12.5 is a plain number"  # text starting with "-" is escaped
    sig_scores = [r[sig[0].index("score")] for r in sig[1:]]
    assert sig_scores == ["-0.12"]                                                   # real numbers are not
    assert exports_mod.safe_cell("-12.5") == "-12.5" and exports_mod.safe_cell("+3") == "+3"
    assert exports_mod.safe_cell("\tx") == "'\tx" and exports_mod.safe_cell("\rx") == "'\rx" and exports_mod.safe_cell(-3) == -3
    assert exports_mod.safe_cell("-1+2") == "'-1+2"


def test_export_metadata_labels(config, db):
    c, _ = make_app(config, db)
    seed_export_data(db)
    bt = c.get("/api/export/backtest?format=json&run=1").get_json()
    assert "HYPOTHETICAL" in bt["metadata"]["label"] and set(bt["data"]) >= {"summary", "criteria", "trades", "equity"}
    assert len(bt["data"]["trades"]) == 1
    pp = c.get("/api/export/paper_orders?format=json").get_json()
    assert "SIMULATED" in pp["metadata"]["label"] and pp["data"][0]["simulated"] == "SIMULATED"
    eq = read_csv(c.get("/api/export/backtest?format=csv&run=1&part=equity"))
    assert eq[0][0] == "time_utc" and "strategy" in eq[0] and len(eq) == 3


def test_export_errors_and_row_limit(config, db, monkeypatch):
    c, _ = make_app(config, db)
    seed_export_data(db)
    assert c.get("/api/export/nope").status_code == 400
    assert c.get("/api/export/signals?format=xml").status_code == 400
    assert c.get("/api/export/candles?tf=7m").status_code == 400
    assert c.get("/api/export/candles?from=yesterday").status_code == 400
    assert c.get("/api/export/candles?from=2026-02-01&to=2026-01-01").status_code == 400
    assert c.get("/api/export/news?hours=999").status_code == 400
    assert c.get("/api/export/backtest").status_code == 400
    assert c.get("/api/export/backtest?run=99").status_code == 404
    assert c.get("/api/export/backtest?run=1&part=bogus").status_code == 400
    monkeypatch.setattr(exports_mod, "MAX_ROWS", 10)
    r = c.get("/api/export/candles?tf=1h")
    assert r.status_code == 400 and "Too many rows" in r.get_json()["error"]
    rows = read_csv(c.get(f"/api/export/candles?tf=1h&from={FIXED_NOW - 5 * HOUR}"))
    assert 2 <= len(rows) <= 11


# ====================================================================== settings
def test_settings_validation_persistence_and_reset(config, db):
    c, _ = make_app(config, db)
    s = c.get("/api/settings").get_json()
    assert s["general"]["display_timezone"]["source"] in ("default", "env")
    assert s["assistant"]["ai_explanations"]["value"] is False and s["assistant"]["ai_explanations"]["source"] == "default"
    for body in [{"display_timezone": "Mars/Base"}, {"refresh_price_s": 5}, {"refresh_news_s": 60}, {"refresh_price_s": "fast"},
                 {"theme": "pink"}, {"nope": 1}, {}]:
        assert c.put("/api/settings/general", json=body).status_code == 400, body
    assert c.put("/api/settings/nosuch", json={"a": 1}).status_code == 400
    assert c.put("/api/settings/general", data="x", content_type="text/plain").status_code == 400
    assert c.put("/api/settings/alerts", json={"global_cap_per_hour": 0}).status_code == 400
    assert c.put("/api/settings/market", json={"provider_order": ["binance", "kraken"]}).status_code == 400
    ok = c.put("/api/settings/general", json={"refresh_price_s": 30, "theme": "light"}).get_json()["values"]
    assert ok["refresh_price_s"]["value"] == 30 and ok["refresh_price_s"]["source"] == "settings"
    m = c.put("/api/settings/market", json={"provider_order": ["coinbase", "binance"]}).get_json()["values"]
    assert m["provider_order"]["restart_required"] is True
    fresh = SettingsService(db)                                   # a restart reads the same SQLite settings
    assert fresh.get("general", "refresh_price_s") == 30 and fresh.get("general", "theme") == "light"
    # Phase 9 (deliberate change, was 'theme: "light"' in an inline script): the page config is now a JSON data
    # block so the CSP can stay script-src 'self'.
    assert '"theme": "light"' in c.get("/").get_data(as_text=True)
    reset = c.post("/api/settings/general/reset").get_json()["values"]
    assert reset["refresh_price_s"]["value"] == 15 and reset["refresh_price_s"]["source"] == "default"
    assert c.post("/api/settings/bogus/reset").status_code == 400


def test_env_override_wins_and_is_read_only(db):
    env = {"DISPLAY_TIMEZONE": "Europe/London", "OLLAMA_TIMEOUT_SECONDS": "200", "OLLAMA_MODEL": ""}
    s = SettingsService(db, env=env.get)
    db.set_setting("settings:general", {"display_timezone": "Asia/Tokyo"})
    assert s.effective("general", "display_timezone") == ("Europe/London", "env")
    assert s.section("general")["display_timezone"]["read_only"] is True
    with pytest.raises(ValueError, match="read-only"):
        s.update("general", {"display_timezone": "UTC"})
    assert s.effective("assistant", "timeout_s") == (200, "env")
    assert s.effective("assistant", "model") == ("qwen2.5:3b", "default")          # empty env value is ignored
    env["DISPLAY_TIMEZONE"] = "Not/AZone"                                         # invalid env falls back
    assert s.effective("general", "display_timezone") == ("Asia/Tokyo", "settings")


def test_assistant_model_validated_against_ollama(config, db):
    from test_assistant import FakeOllama
    c, app = make_app(config, db, fake_ollama=FakeOllama(models=("qwen2.5:3b", "qwen3:4b")))
    r = c.put("/api/settings/assistant", json={"model": "llama9:70b"})
    assert r.status_code == 400 and "not installed" in r.get_json()["error"]
    r = c.put("/api/settings/assistant", json={"model": "qwen3:4b", "timeout_s": 120})
    assert r.status_code == 200
    ollama = app.config["BTC_ASSISTANT"].ollama
    assert ollama.model == "qwen3:4b" and ollama.timeout == 120.0                  # live, no restart


def test_coingecko_key_never_exposed(config, db):
    config.coingecko_demo_key = "CG-SUPERSECRET-123"
    c, _ = make_app(config, db)
    seed_export_data(db)
    body = c.get("/api/settings").get_data(as_text=True)
    assert "CG-SUPERSECRET-123" not in body and json.loads(body)["market"]["coingecko_demo_key"]["value"] == "set"
    for url in ["/", "/settings", "/api/sources", "/api/export/signals?format=json", "/api/export/alerts?format=csv"]:
        assert "CG-SUPERSECRET-123" not in c.get(url).get_data(as_text=True), url
    c.put("/api/settings/general", json={"theme": "dark"})
    with db.connect() as conn:
        dump = json.dumps([dict(r) for r in conn.execute("SELECT * FROM settings")])
    assert "CG-SUPERSECRET-123" not in dump
    config.coingecko_demo_key = ""
    c2, _ = make_app(config, db)
    assert c2.get("/api/settings").get_json()["market"]["coingecko_demo_key"]["value"] == "not set"


def test_stale_validation_warning_when_signal_config_changes(config, db):
    from app import SIGNAL_STALE_WARNING
    from services.backtest_service import config_hash
    c, app = make_app(config, db)
    db.set_setting("strategy_validation", {"validated": False, "config_hash": config_hash(app.config["BTC_SIGNALS"].config()),
                                           "run_id": 1})
    s = c.get("/api/settings").get_json()["signal"]
    assert s["warning"] == SIGNAL_STALE_WARNING and not s["validation"].get("stale")
    assert SIGNAL_STALE_WARNING in c.get("/settings").get_data(as_text=True)
    assert c.put("/api/signal/config", json={"buy_threshold": 0.3}).status_code == 200
    s = c.get("/api/settings").get_json()["signal"]
    assert s["validation"]["stale"] is True and s["validation"]["validated"] is False and s["warning"] == SIGNAL_STALE_WARNING
    assert c.get("/api/signal").get_json()["signal"] == "NO TRADE"


def test_settings_page_renders_sections(config, db):
    c, _ = make_app(config, db)
    html = c.get("/settings").get_data(as_text=True)
    for sec in ("general", "market", "signal", "risk", "alerts", "assistant", "watchlist"):
        assert f'id="{sec}"' in html
    assert "Reset section to defaults" in html and "SIMULATED" in html


# ====================================================================== watchlist
def ticker(sym, p, ch):
    return {"symbol": sym, "lastPrice": str(p), "priceChangePercent": str(ch), "closeTime": FIXED_NOW - 2000}


def test_watchlist_one_batched_request_and_cache(db):
    http = FakeHttp({"/api/v3/ticker/24hr": [ticker("BTCUSDT", 65_000, 1.2), ticker("ETHUSDT", 2500.5, -0.4)]})
    clock = Clock()
    wl = WatchlistService(db, http, symbols=lambda: ["BTCUSDT", "ETHUSDT"], clock=clock)
    out = wl.get()
    assert len(http.calls) == 1 and json.loads(http.calls[0][1]["symbols"]) == ["BTCUSDT", "ETHUSDT"]
    assert [r["symbol"] for r in out["rows"]] == ["BTCUSDT", "ETHUSDT"] and out["rows"][1]["change_24h_pct"] == -0.4
    assert out["status"] == "fresh" and not out["from_cache"] and "Display only" in out["note"]
    clock.advance(59_000)
    assert wl.get()["from_cache"] is True and len(http.calls) == 1          # cached >= 60 s
    clock.advance(2_000)
    wl.get()
    assert len(http.calls) == 2


def test_watchlist_failure_uses_cache_marked_stale_then_coinbase(db):
    http = FakeHttp({"/api/v3/ticker/24hr": [ticker("BTCUSDT", 65_000, 1.2)]})
    clock = Clock()
    wl = WatchlistService(db, http, symbols=lambda: ["BTCUSDT"], clock=clock)
    wl.get()
    http.routes["/api/v3/ticker/24hr"] = ProviderError("api.binance.com: HTTP 451")
    clock.advance(10 * MIN)
    out = wl.get()
    assert out["status"] == "cached_fallback" and out["is_stale"] is True and out["rows"][0]["price"] == 65_000
    assert any("NOT current" in w for w in out["warnings"])
    # No cache at all (new symbol list): Coinbase fallback where a USD product exists
    http.routes["/products/ETH-USD/stats"] = {"open": "2400", "last": "2460", "high": "1", "low": "1"}
    wl2 = WatchlistService(db, http, symbols=lambda: ["ETHUSDT", "BNBBTC"], clock=clock)
    out = wl2.get()
    assert out["rows"][0]["provider"] == "coinbase" and out["rows"][0]["price"] == 2460 and out["rows"][0]["quote"] == "USD"
    assert round(out["rows"][0]["change_24h_pct"], 2) == 2.5 and out["rows"][1]["price"] is None
    assert any("Coinbase fallback" in w for w in out["warnings"])
    # Nothing works and nothing is cached: honest unavailable state
    http.routes.pop("/products/ETH-USD/stats")
    out = WatchlistService(db, http, symbols=lambda: ["SOLUSDT"], clock=clock).get()
    assert out["status"] == "unavailable" and out["rows"] == []


def test_watchlist_symbol_validation_cached_24h(db):
    def info(url, params):
        if "symbols" in (params or {}):
            syms = json.loads(params["symbols"])
            if "FAKEUSDT" in syms:
                return ProviderError("api.binance.com: HTTP 400")
            return {"symbols": [{"symbol": s, "status": "TRADING"} for s in syms]}
        if params.get("symbol") == "FAKEUSDT":
            return ProviderError("api.binance.com: HTTP 400")
        return {"symbols": [{"symbol": params["symbol"], "status": "TRADING"}]}

    http = FakeHttp({"/api/v3/exchangeInfo": info})
    clock = Clock()
    wl = WatchlistService(db, http, symbols=lambda: [], clock=clock)
    assert wl.unknown_symbols(["BTCUSDT", "FAKEUSDT", "ETHUSDT"]) == ["FAKEUSDT"]
    n = len(http.calls)
    assert wl.unknown_symbols(["BTCUSDT", "FAKEUSDT"]) == ["FAKEUSDT"] and len(http.calls) == n     # cached
    clock.advance(25 * HOUR)
    wl.unknown_symbols(["BTCUSDT"])
    assert len(http.calls) == n + 1
    http.routes["/api/v3/exchangeInfo"] = ProviderError("timeout")
    with pytest.raises(ProviderError):
        wl.unknown_symbols(["SOLUSDT"])                           # a network failure is never "unknown"


def test_watchlist_settings_route_validates(config, db):
    http = FakeHttp({"/api/v3/exchangeInfo": lambda u, p: ({"symbols": [{"symbol": s, "status": "TRADING"}
                                                                         for s in json.loads(p["symbols"]) if s != "NOPEUSDT"]}),
                     "/api/v3/ticker/24hr": [ticker("BTCUSDT", 65_000, 1.0), ticker("SOLUSDT", 150, 2.0)]})
    c, _ = make_app(config, db, watch_http=http)
    assert c.put("/api/settings/watchlist", json={"symbols": ["BTCUSDT", "NOPEUSDT"]}).status_code == 400
    assert c.put("/api/settings/watchlist", json={"symbols": ["btc-usdt"]}).status_code == 400
    assert c.put("/api/settings/watchlist", json={"symbols": [f"AAA{i}USDT" for i in range(11)]}).status_code == 400
    assert c.put("/api/settings/watchlist", json={"symbols": ["BTCUSDT", "BTCUSDT"]}).status_code == 400
    assert c.put("/api/settings/watchlist", json={"symbols": "BTCUSDT, SOLUSDT"}).status_code == 200
    out = c.get("/api/watchlist").get_json()
    assert [r["symbol"] for r in out["rows"]] == ["BTCUSDT", "SOLUSDT"]
    http.routes["/api/v3/exchangeInfo"] = ProviderError("timeout")
    r = c.put("/api/settings/watchlist", json={"symbols": ["XRPUSDT"]})
    assert r.status_code == 400 and "Could not reach Binance" in r.get_json()["error"]


# ====================================================================== calendar
def test_fomc_parser_on_fixture():
    events = parse_fomc(FIXTURE.read_text(encoding="utf-8"))
    by = {e["at_utc"]: e for e in events}
    dec = by["2026-12-09T19:00:00Z"]                              # 2:00 p.m. EST = 19:00 UTC
    assert dec["event"] == "FOMC rate decision + projections (SEP)" and dec["meeting"] == "Dec 08 - Dec 09, 2026"
    assert by["2026-10-28T18:00:00Z"]["event"] == "FOMC rate decision"                 # EDT = 18:00 UTC
    assert by["2024-05-01T18:00:00Z"]["meeting"] == "Apr 30 - May 01, 2024"           # cross-month "Apr/May 30-1"
    assert not any(e["at_utc"].startswith("2025-08-22") for e in events)               # notation vote skipped
    assert all(e["link"].startswith("https://www.federalreserve.gov/") and e["source"] == "Federal Reserve Board" for e in events)
    assert events == sorted(events, key=lambda e: e["at"]) and len(events) >= 16


def iso_ms(s):
    import datetime as dt
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def test_calendar_window_next_event_and_daily_cache(db):
    page = FIXTURE.read_text(encoding="utf-8")
    http = FakeHttp({"fomccalendars.htm": page})
    clock = Clock(iso_ms("2026-12-01T00:00:00Z"))
    cal = CalendarService(db, http, clock=clock)
    out = cal.upcoming()
    assert [e["at_utc"] for e in out["events"]] == ["2026-12-09T19:00:00Z"] and out["empty_reason"] is None
    assert out["sources"][0]["status"] == "ok" and out["sources"][1]["status"] == "not_configured"
    assert "CALENDAR_CONTACT_EMAIL" in out["sources"][1]["error"]
    clock.advance(12 * HOUR)
    cal.upcoming()
    assert len(http.calls) == 1                                   # at most one fetch per day
    clock.t = iso_ms("2026-10-29T12:00:00Z")                      # Dec 9 is 41 days away
    cal2 = CalendarService(db, http, clock=clock)
    out = cal2.upcoming()
    assert out["events"] == [] and out["empty_reason"] == "No verified FOMC events in the next 14 days."
    assert out["next_fomc"]["at_utc"] == "2026-12-09T19:00:00Z"


def test_calendar_failure_handling(db):
    http = FakeHttp({"fomccalendars.htm": ProviderError("www.federalreserve.gov: HTTP 503")})
    clock = Clock(iso_ms("2026-12-01T00:00:00Z"))
    cal = CalendarService(db, http, clock=clock)
    out = cal.upcoming()
    assert out["events"] == [] and out["empty_reason"] == "No free verified calendar source could be loaded right now."
    assert out["sources"][0]["status"] == "error" and "HTTP 503" in out["warnings"][0]
    cal.upcoming()
    assert len(http.calls) == 1                                   # no hammering: retry only after 1 h
    http.routes["fomccalendars.htm"] = "<html>layout changed</html>"
    clock.advance(2 * HOUR)
    out = cal.upcoming()
    assert out["sources"][0]["status"] == "error" and "no meeting dates" in out["sources"][0]["error"]
    # A cached copy is kept and labelled when a later refresh fails
    http.routes["fomccalendars.htm"] = FIXTURE.read_text(encoding="utf-8")
    clock.advance(2 * HOUR)
    assert cal.upcoming()["events"]
    http.routes["fomccalendars.htm"] = ProviderError("timeout")
    clock.advance(25 * HOUR)
    out = cal.upcoming()
    assert out["sources"][0]["status"] == "cached" and "cached copy" in out["warnings"][0]


BLS_ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
DTSTART;TZID=America/New_York:20261210T083000\r
SUMMARY:Consumer Price Index for November 2026\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;TZID=America/New_York:20261204T083000\r
SUMMARY:Employment Situation for\r
  November 2026\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;TZID=America/New_York:20261203T100000\r
SUMMARY:Job Openings and Labor Turnover Survey\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_bls_ics_parser_and_contact_header(db):
    ev = parse_bls_ics(BLS_ICS)                                   # synthetic sample in the official ICS layout
    assert [(e["kind"], e["at_utc"]) for e in ev] == [("Jobs report", "2026-12-04T13:30:00Z"), ("CPI", "2026-12-10T13:30:00Z")]
    assert ev[0]["event"] == "Employment Situation for November 2026"
    http = FakeHttp({"fomccalendars.htm": FIXTURE.read_text(encoding="utf-8"), "bls.ics": BLS_ICS})
    cal = CalendarService(db, http, contact_email=lambda: "owner@example.com", clock=Clock(iso_ms("2026-12-01T00:00:00Z")))
    out = cal.upcoming()
    assert [e["kind"] for e in out["events"]] == ["Jobs report", "FOMC", "CPI"]
    bls_call = [c for c in http.calls if "bls.ics" in c[0]][0]
    assert bls_call[2]["From"] == "owner@example.com" and "owner@example.com" in bls_call[2]["User-Agent"]


def test_calendar_route(config, db):
    c, _ = make_app(config, db, cal_http=FakeHttp({"fomccalendars.htm": ProviderError("HTTP 403")}))
    out = c.get("/api/calendar").get_json()
    assert out["events"] == [] and out["empty_reason"] and out["sources"][0]["status"] == "error"


def test_new_sources_documented(config, db):
    c, _ = make_app(config, db)
    docs = {d["source"] for d in c.get("/api/sources").get_json()["docs"]}
    assert {"binance:watchlist", "calendar:fomc", "calendar:bls"} <= docs


# ====================================================================== Part 0: assistant AI setting
def test_ai_explanations_default_off_returns_rule_draft_instantly(config, db):
    from services.assistant import AI_OFF_NOTE
    from test_assistant import FakeOllama
    fake = FakeOllama(reply="AI text [F2].")
    c, _ = make_app(config, db, fake_ollama=fake)
    assert c.get("/api/assistant/status").get_json()["ai_explanations"] is False
    out = c.post("/api/assistant/chat", json={"message": "Explain the signal in simple words"}).get_json()
    assert out["mode"] == "rule" and out["warning"] == AI_OFF_NOTE and out["label"] == "Rule-based (instant)" and out["sources"]
    ex = c.get("/api/assistant/explain?ai=1").get_json()
    assert ex["mode"] == "rule" and ex["warning"] == AI_OFF_NOTE
    assert fake.posts == []                                       # the local model was never called


def test_ai_explanations_on_uses_ai_with_label(config, db):
    from test_assistant import FakeOllama
    fake = FakeOllama(reply="The signal is NO TRADE because it is not validated [F2].")
    c, _ = make_app(config, db, fake_ollama=fake)
    assert c.put("/api/settings/assistant", json={"ai_explanations": True}).status_code == 200
    assert c.get("/api/assistant/status").get_json()["ai_explanations"] is True
    out = c.post("/api/assistant/chat", json={"message": "Explain the signal in simple words"}).get_json()
    assert out["mode"] == "ai" and out["label"] == "AI rewrite (local qwen2.5:3b) - may contain mistakes; check the Sources"
    assert len(fake.posts) == 1
    fact = c.post("/api/assistant/chat", json={"message": "What is the price?"}).get_json()
    assert fact["mode"] == "rule" and len(fake.posts) == 1        # factual questions stay rule-only


def test_busy_fallback_after_cancel_returns_rule_draft(config, db):
    """A cancelled request leaves the model running: the next AI question gets the instant answer, not a 429."""
    from services.assistant import BUSY_FALLBACK
    from test_assistant import FakeOllama
    fake = FakeOllama(reply="Plain [F2].")
    c, app = make_app(config, db, fake_ollama=fake)
    c.put("/api/settings/assistant", json={"ai_explanations": True})
    a = app.config["BTC_ASSISTANT"]
    a._lock.acquire()                                             # the earlier (cancelled) AI call is still running
    try:
        assert c.post("/api/assistant/route", json={"message": "Explain the signal"}).get_json()["uses_ai"] is False
        r = c.post("/api/assistant/chat", json={"message": "Why is momentum bearish or bullish right now?"})
        assert r.status_code == 200 and r.get_json()["warning"] == BUSY_FALLBACK and r.get_json()["mode"] == "rule"
        assert r.get_json()["answer"] and r.get_json()["sources"] and fake.posts == []
    finally:
        a._lock.release()
    assert c.post("/api/assistant/chat", json={"message": "Explain the signal"}).get_json()["mode"] == "ai"


def test_loader_text_and_timeout():
    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "Thinking locally… usually 1-3 min on CPU" in js and "30-90 s" not in js
    from config import Config
    assert Config().ollama_timeout == 150.0 and SECTIONS["assistant"]["timeout_s"].default == 150
