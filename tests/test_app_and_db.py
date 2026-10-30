import pytest
from conftest import FIXED_NOW, FakeOverview, FakeProvider, local_client

from app import create_app
from database import Database
from services.http_client import HttpClient
from services.market_data import MarketDataService
from services.news_collector import NewsCollector
from services.news_service import NewsService

NEWS_XML = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
            b'<item><title>Spot bitcoin ETFs log record $1.2 billion inflows</title><link>https://a.test/1</link>'
            b'<pubDate>Thu, 09 Oct 2025 08:00:00 GMT</pubDate></item>'
            b'<item><title>Major exchange hacked, $200 million drained</title><link>https://a.test/2</link>'
            b'<pubDate>Thu, 09 Oct 2025 07:00:00 GMT</pubDate></item></channel></rss>')


def make_client(config, db, providers, news_fail=False):
    svc = MarketDataService(db, config, http=HttpClient(min_interval=0), providers=providers,
                            overview_provider=FakeOverview(), clock=lambda: FIXED_NOW)

    def fetcher(feed):
        if news_fail:
            from services.http_client import ProviderError
            raise ProviderError("feed down")
        return NEWS_XML, 5.0

    collector = NewsCollector(db, http=None, feeds=[{"id": "t", "name": "Test", "url": "https://t.test"}],
                              clock=lambda: FIXED_NOW, fetcher=fetcher)
    news = NewsService(db, collector, price_series=lambda: None, clock=lambda: FIXED_NOW)
    app = create_app(config, market=svc, news=news)
    app.testing = True
    return local_client(app)


# ----------------------------------------------------------------------- database
def test_upsert_is_idempotent_and_persistent(config):
    db = Database(config.db_path)
    db.init()
    rows = [{"open_time": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3, "close_time": 2}]
    db.upsert_candles("p", "S", "1h", rows, 100)
    rows[0]["close"] = 1.7
    db.upsert_candles("p", "S", "1h", rows, 200)
    reopened = Database(config.db_path)
    got = reopened.get_candles("p", "S", "1h", 10)
    assert len(got) == 1 and got[0]["close"] == 1.7 and reopened.latest_candle_fetch("p", "S", "1h") == 200


def test_health_counters(db):
    db.record_health("x", True, latency_ms=5)
    db.record_health("x", False, error="boom")
    h = db.get_health()[0]
    assert h["status"] == "error" and h["success_count"] == 1 and h["failure_count"] == 1 and h["last_error"] == "boom"


def test_first_health_records_from_two_threads_do_not_collide(db):
    """Regression: two requests recording the FIRST health row of a source at once raised
    'UNIQUE constraint failed: source_health.source' (a 500 on the first dashboard load)."""
    import contextlib
    import threading
    barrier = threading.Barrier(2)
    original = db.connect

    class Synced:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            cur = self.conn.execute(sql, *args)
            if sql.lstrip().upper().startswith("SELECT"):
                try:
                    barrier.wait(timeout=1)   # both threads have looked for the row before either writes
                except threading.BrokenBarrierError:
                    pass
            return cur

    @contextlib.contextmanager
    def synced_connect():
        with original() as conn:
            yield Synced(conn)

    db.connect = synced_connect
    errors = []

    def record():
        try:
            db.record_health("newsource", True, latency_ms=5)
        except Exception as exc:          # noqa: BLE001 - the test reports whatever escapes
            errors.append(repr(exc))

    threads = [threading.Thread(target=record) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    db.connect = original
    assert errors == []
    h = [r for r in db.get_health() if r["source"] == "newsource"][0]
    assert h["success_count"] == 2 and h["status"] == "ok"


def test_settings_roundtrip(db):
    db.set_setting("theme", {"mode": "dark"})
    assert db.get_setting("theme") == {"mode": "dark"} and db.get_setting("missing", 3) == 3


# ------------------------------------------------------------------------- routes
def test_dashboard_renders(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    r = c.get("/")
    assert r.status_code == 200 and b"BTC Signal Lab" in r.data and b"Never places orders" in r.data


def test_api_routes(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    assert c.get("/api/health").get_json()["db"] is True
    p = c.get("/api/price").get_json()
    assert p["price"] == 65_000.0 and p["provider"] == "fake1"
    cand = c.get("/api/candles?tf=1h&limit=200").get_json()
    assert cand["meta"]["count"] == 200 and len(cand["series"]["time"]) == 200
    a = c.get("/api/analysis?tf=1h").get_json()
    assert a["status"] == "ok" and a["data"]["provider"] == "fake1"
    m = c.get("/api/mtf").get_json()
    assert [r["timeframe"] for r in m["timeframes"]] == ["5m", "15m", "1h", "4h", "1d"]
    s = c.get("/api/sources").get_json()
    assert s["docs"] and s["health"]
    assert c.get("/api/overview").get_json()["btc_dominance_pct"] == 57.3


def test_bad_timeframe_is_400(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    r = c.get("/api/analysis?tf=7m")
    assert r.status_code == 400 and "Unsupported timeframe" in r.get_json()["error"]


def test_routes_survive_total_outage(config, db):
    c = make_client(config, db, [FakeProvider("fake1", fail=True)])
    assert c.get("/api/price").get_json()["status"] == "unavailable"
    a = c.get("/api/analysis?tf=1h")
    assert a.status_code == 200 and a.get_json()["status"] == "insufficient_data"
    assert c.get("/api/candles?tf=4h").get_json()["meta"]["count"] == 0


def test_news_routes(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    rep = c.get("/api/news").get_json()
    assert len(rep["events"]) == 2 and rep["summary"]["events_total"] == 2
    ev = rep["events"][0]
    for key in ("title", "link", "publisher", "sentiment", "impact", "verification", "freshness", "btc_impact", "sources"):
        assert key in ev
    bearish = c.get("/api/news?sentiment=bearish").get_json()["events"]
    assert [e["sentiment"] for e in bearish] == ["bearish"]
    assert c.get("/api/news?category=etf_flows").get_json()["events"][0]["categories"][0] == "etf_flows"
    assert c.post("/api/news/refresh").status_code == 200
    docs = c.get("/api/sources").get_json()["docs"]
    assert any(d["source"] == "rss:t" for d in docs) is False  # docs list the real feeds, not test ones
    assert any(d["source"].startswith("rss:") for d in docs)


def test_news_route_with_feeds_down(config, db):
    c = make_client(config, db, [FakeProvider("fake1")], news_fail=True)
    rep = c.get("/api/news").get_json()
    assert rep["events"] == [] and rep["summary"]["bias_label"] == "insufficient_news"
    assert any("failed" in w for w in rep["warnings"])


def test_read_only_database_cannot_write_or_touch_file(config):
    import os
    import sqlite3

    import pytest
    db = Database(config.db_path)
    db.init()
    db.insert_news([{"id": "x", "feed": "f", "publisher": "P", "title": "Bitcoin test headline here",
                     "link": "https://x.test", "summary": "", "published_at": 1, "collected_at": 1}])
    before = os.stat(config.db_path).st_mtime_ns
    ro = Database(config.db_path, read_only=True)
    assert len(ro.get_news(0)) == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.set_setting("k", 1)
    assert os.stat(config.db_path).st_mtime_ns == before


# ------------------------------------------------------------------ signals (Phase 5)
def test_signal_route_and_history(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    s = c.get("/api/signal").get_json()
    assert s["signal"] in ("STRONG BUY SETUP", "BUY SETUP", "HOLD", "SELL / REDUCE EXPOSURE", "NO TRADE")
    # No validation record exists yet, so the final signal must be NO TRADE with that reason.
    assert s["signal"] == "NO TRADE" and any("validated" in r for r in s["no_trade_reasons"])
    assert s["id"]
    # An unchanged signal within the hour is not stored twice.
    c.get("/api/signal")
    hist = c.get("/api/signal/history").get_json()["history"]
    assert len(hist) == 1 and hist[0]["reason"] == "first signal"
    detail = c.get(f"/api/signal/{hist[0]['id']}").get_json()
    assert detail["setup"] == s["setup"]
    assert c.get("/api/signal/999999").status_code == 404


def test_signal_history_records_changes(config, db):
    from services.signal_service import SignalService
    svc = SignalService(db, market=None)
    base = {"generated_at": 1_000, "signal": "NO TRADE", "setup": "HOLD", "score": 0.1,
            "price": {"value": 1.0, "provider": "p"}}
    assert svc._record(dict(base))
    svc._record({**base, "generated_at": 2_000})                       # unchanged -> not stored
    svc._record({**base, "generated_at": 3_000, "setup": "BUY SETUP"})  # setup change -> stored
    svc._record({**base, "generated_at": 3_000 + 3_600_000, "setup": "BUY SETUP"})  # hourly record
    reasons = [h["reason"] for h in db.signal_history()]
    assert reasons == ["hourly record (unchanged)", "setup HOLD -> BUY SETUP", "first signal"]


def test_signal_config_api(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    cfg = c.get("/api/signal/config").get_json()
    assert sum(cfg["config"]["weights"].values()) == pytest.approx(1.0, abs=1e-3) and cfg["rationale"]
    r = c.put("/api/signal/config", json={"weights": {"news": 0}, "min_rr": 2})
    assert r.status_code == 200 and r.get_json()["config"]["weights"]["news"] == 0
    assert c.get("/api/signal/config").get_json()["config"]["min_rr"] == 2
    for bad in ({"weights": {"magic": 1}}, {"weights": {"trend": -1}}, {"engine": "x"},
                {"buy_threshold": -0.5}, {"require_validated_edge": "no"}, {"min_rr": "abc"}):
        assert c.put("/api/signal/config", json=bad).status_code == 400, bad
    assert c.put("/api/signal/config", data="not json", content_type="text/plain").status_code == 400
    r = c.put("/api/signal/config", json={"reset": True})
    assert r.get_json()["config"]["weights"]["news"] == 0.15


@pytest.mark.parametrize("bad, field", [
    ({"fee_pct": -0.1}, "fee_pct"), ({"slippage_pct": -0.01}, "slippage_pct"), ({"fee_pct": 1.5}, "fee_pct"),
    ({"min_rr": 0}, "min_rr"), ({"min_rr": -1}, "min_rr"),
    ({"buy_threshold": 1.5}, "buy_threshold"), ({"strong_threshold": 1.2}, "strong_threshold"),
    ({"sell_threshold": -1.5}, "sell_threshold"),
    ({"strong_threshold": 0.25}, "strong"),                       # strong == buy
    ({"strong_threshold": 0.2, "buy_threshold": 0.3}, "strong"),  # strong < buy
    ({"sell_threshold": 0}, "sell"), ({"sell_threshold": 0.1}, "sell"),
    ({"min_agreement": 5}, "min_agreement"), ({"strong_min_agreement": -0.1}, "strong_min_agreement"),
    ({"counter_trend_limit": 2}, "counter_trend_limit"),
])
def test_signal_config_rejects_out_of_range_values(config, db, bad, field):
    """Regression (B2): negative costs, min R/R <= 0 and unreachable or inverted thresholds were accepted."""
    c = make_client(config, db, [FakeProvider("fake1")])
    r = c.put("/api/signal/config", json=bad)
    assert r.status_code == 400, bad
    assert field in r.get_json()["error"]
    assert c.get("/api/signal/config").get_json()["config"]["fee_pct"] == 0.10     # nothing was stored


def test_signal_config_accepts_edges_and_ignores_stored_out_of_range_values(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    for ok in ({"fee_pct": 0, "slippage_pct": 0}, {"min_rr": 0.5}, {"strong_threshold": 1.0},
               {"sell_threshold": -1.0}, {"min_agreement": 1.0}):
        assert c.put("/api/signal/config", json=ok).status_code == 200, ok
    c.put("/api/signal/config", json={"reset": True})
    db.set_setting("signal_config", {"fee_pct": -1, "min_rr": 2, "buy_threshold": 9})   # written by an older version
    cfg = c.get("/api/signal/config").get_json()["config"]
    assert cfg["fee_pct"] == 0.10 and cfg["buy_threshold"] == 0.25 and cfg["min_rr"] == 2
    ranges = c.get("/api/settings").get_json()["signal"]["ranges"]
    assert ranges["fee_pct"]["min"] == 0 and ranges["min_rr"]["min_exclusive"] is True


def test_risk_pct_above_max_risk_is_rejected_by_the_api(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    r = c.post("/api/paper/settings", json={"risk_pct": 2.5})
    assert r.status_code == 400 and "max_risk_pct" in r.get_json()["error"]
    r = c.post("/api/risk/size", json={"portfolio_value": 10_000, "entry": 100, "stop": 95, "settings": {"risk_pct": 3}})
    assert r.status_code == 400 and "risk_pct" in r.get_json()["error"]
    assert c.post("/api/paper/settings", json={"risk_pct": 2}).status_code == 200
    assert "risk_pct" in c.get("/api/settings").get_json()["risk"]["ranges"]


def test_validation_record_unlocks_signal_only_for_matching_settings(config, db):
    from services.backtesting import config_hash
    from services.signal_engine import EngineConfig
    c = make_client(config, db, [FakeProvider("fake1")])
    db.set_setting("strategy_validation", {"validated": True, "note": "test only",
                                           "config_hash": config_hash(EngineConfig())})
    s = c.get("/api/signal").get_json()
    assert not any("validated" in r for r in s["no_trade_reasons"])
    # Changing the settings invalidates it until the backtest is re-run.
    c.put("/api/signal/config", json={"min_rr": 2})
    s = c.get("/api/signal").get_json()
    assert any("validated" in r for r in s["no_trade_reasons"]) and s["validation"].get("stale")


def test_validation_without_fingerprint_does_not_unlock(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    db.set_setting("strategy_validation", {"validated": True, "note": "no fingerprint"})
    s = c.get("/api/signal").get_json()
    assert any("validated" in r for r in s["no_trade_reasons"])


def test_signal_record_on_engine_change_and_score_move(config, db):
    from services.signal_service import SignalService
    svc = SignalService(db, market=None)
    base = {"generated_at": 1_000, "signal": "NO TRADE", "setup": "HOLD", "score": -0.168, "engine": "signal-1.0",
            "price": {"value": 1.0, "provider": "p"}}
    first = svc._record(dict(base))
    assert first["stored"] and first["reason"] == "first signal"
    same = svc._record({**base, "generated_at": 2_000, "score": -0.17})
    assert same["stored"] is False and same["id"] == first["id"] and "unchanged" in same["reason"]
    eng = svc._record({**base, "generated_at": 3_000, "score": -0.142, "engine": "signal-1.2"})
    assert eng["stored"] and "engine signal-1.0 -> signal-1.2" in eng["reason"]
    mv = svc._record({**base, "generated_at": 4_000, "score": -0.02, "engine": "signal-1.2"})
    assert mv["stored"] and "score" in mv["reason"]


# ------------------------------------------------------------------ Phase 6 routes
def make_client6(config, db, runner=None):
    from services.backtest_service import BacktestService
    svc = MarketDataService(db, config, http=HttpClient(min_interval=0), providers=[FakeProvider("fake1")],
                            overview_provider=FakeOverview(), clock=lambda: FIXED_NOW)
    collector = NewsCollector(db, http=None, feeds=[], clock=lambda: FIXED_NOW, fetcher=lambda f: (b"", 1.0))
    news = NewsService(db, collector, price_series=lambda: None, clock=lambda: FIXED_NOW)
    from services.signal_service import SignalService
    signals = SignalService(db, svc, news_report=lambda: news.report(hours=72))
    bt = BacktestService(db, engine_config=signals.config, http_factory=lambda: None, clock=lambda: FIXED_NOW,
                         runner=runner or (lambda *a, **k: None), downloader=lambda *a, **k: [])
    app = create_app(config, market=svc, news=news, signals=signals, backtests=bt)
    app.testing = True
    return local_client(app), bt


def fake_result(validated, engine_cfg):
    from services.backtesting import config_hash
    from services.signal_engine import ENGINE_VERSION
    return {"engine": ENGINE_VERSION, "config_hash": config_hash(engine_cfg), "validated": validated,
            "verdict": "VALIDATED" if validated else "NOT VALIDATED", "period": {"years": 1},
            "strategy": {"trades": 40, "net_return_pct": 10, "max_drawdown_pct": 5, "profit_factor": 1.5, "win_rate_pct": 55},
            "baselines": {"buy_and_hold": {"net_return_pct": 8}}, "criteria": [{"name": "x", "passed": validated}]}


def test_backtest_service_records_validation_and_unlocks_signal(config, db):
    c, bt = make_client6(config, db, runner=lambda h, f, d, cfg, *a, **k: fake_result(True, cfg))
    bt.start(years=1, download=False, background=False)
    st = c.get("/api/backtest/status").get_json()
    assert st["state"] == "done" and st["run_id"] and st["validation"]["validated"] is True
    assert c.get("/api/backtest/latest").get_json()["validated"] is True
    assert len(c.get("/api/backtest/runs").get_json()["runs"]) == 1
    s = c.get("/api/signal").get_json()
    assert not any("validated" in r for r in s["no_trade_reasons"])


def test_backtest_not_validated_keeps_no_trade(config, db):
    c, bt = make_client6(config, db, runner=lambda h, f, d, cfg, *a, **k: fake_result(False, cfg))
    bt.start(years=1, download=False, background=False)
    s = c.get("/api/signal").get_json()
    assert s["signal"] == "NO TRADE" and any("validated" in r for r in s["no_trade_reasons"])


def test_backtest_errors_are_reported_not_raised(config, db):
    def boom(*a, **k):
        raise ValueError("No historical candles. Download history first.")
    c, bt = make_client6(config, db, runner=boom)
    bt.start(years=1, download=False, background=False)
    st = c.get("/api/backtest/status").get_json()
    assert st["state"] == "error" and "Download history" in st["error"]
    assert c.post("/api/backtest/run", json={"years": 99}).status_code == 400
    assert c.get("/api/backtest/latest").status_code == 404


def test_risk_and_paper_routes(config, db):
    c, _ = make_client6(config, db)
    r = c.post("/api/risk/size", json={"portfolio_value": 10000, "entry": 65000, "stop": 63000})
    assert r.status_code == 200 and r.get_json()["max_loss_at_stop"] > 0
    assert c.post("/api/risk/size", json={"portfolio_value": 10000, "entry": 65000, "stop": 66000}).status_code == 400
    st = c.get("/api/paper").get_json()
    assert st["simulated"] and st["cash"] == 10000
    o = c.post("/api/paper/order", json={"side": "BUY", "usd": 1000, "stop": 60000}).get_json()
    assert o["simulated"] and o["state"]["btc"] > 0
    assert c.post("/api/paper/order", json={"side": "SHORT", "usd": 1}).status_code == 400
    o = c.post("/api/paper/order", json={"side": "SELL"}).get_json()
    assert o["message"].startswith("SIMULATED SELL")
    assert c.post("/api/paper/settings", json={"max_exposure_pct": 500}).status_code == 400
    assert c.post("/api/paper/reset", json={"start_balance": 5000}).get_json()["simulated"]
