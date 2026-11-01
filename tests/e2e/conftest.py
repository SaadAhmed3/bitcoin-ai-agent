"""Playwright end-to-end fixtures (Phase 9).

The app runs in a background thread on a free 127.0.0.1 port with a temporary SQLite database and fake
providers: no network, no Ollama. Seeded fixture data: candles (synthetic random walk, tests only), news (a fake
RSS feed, including a hostile <script> headline), one stored design-period backtest, signal history, paper
settings and a watchlist/calendar from fakes.

Run:   python -m pytest -m e2e                  (the default `python -m pytest` skips these)
Smoke against an already running app (read-only checks only):
       $env:E2E_BASE_URL="http://127.0.0.1:5050"; python -m pytest -m "e2e and smoke"
"""
from __future__ import annotations

import os
import socket
import threading
import time
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

from conftest import FakeOverview, make_rows
from test_assistant import FakeOllama, service
from test_phase8 import FIXTURE as FOMC_FIXTURE, FakeHttp

from app import create_app
from config import Config
from database import Database, now_ms
from services.calendar_service import CalendarService
from services.market_data import MarketDataService
from services.http_client import HttpClient
from services.news_collector import NewsCollector
from services.news_service import NewsService
from services.watchlist import WatchlistService

E2E_KEY = "CG-e2e-fake-key-7d41c0b9e2"            # must never appear in the browser
XSS_TITLE = "<script>window.__xss=1</script>Bitcoin ETF headline <img src=x onerror=window.__xss=2>"
# Seeded straight into the DB (distinct wording, so it is not merged with the feed story above).
XSS_RAW = "<script>window.__xss=3</script>Bitcoin miners report record hashrate <img src=x onerror=window.__xss=4>"
BASE_URL = os.getenv("E2E_BASE_URL", "").rstrip("/")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveFakeProvider:
    """Like tests' FakeProvider, but 'now' is the real clock so prices are fresh and paper orders can fill."""
    name, symbol, quote = "fake1", "BTCUSDT", "USDT"

    def __init__(self, price: float = 65_000.0):
        self.price = price

    def fetch_price(self):
        return {"price": self.price, "observed_at": now_ms() - 500, "change_24h_pct": 1.5, "latency_ms": 12.0}

    def fetch_candles(self, timeframe, limit):
        return make_rows(min(limit, 1000), timeframe, start_price=60_000.0, end_ms=now_ms()), 8.0


def rss(items: list[tuple[str, str, int]]) -> bytes:
    from xml.sax.saxutils import escape
    body = "".join(f"<item><title>{escape(t)}</title><link>{escape(link)}</link>"
                   f"<pubDate>{format_datetime(datetime.fromtimestamp(ms / 1000, timezone.utc))}</pubDate></item>"
                   for t, link, ms in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Fake</title>{body}</channel></rss>'.encode()


def news_items() -> list[tuple[str, str, int]]:
    n = now_ms()
    return [("Spot bitcoin ETFs log record $1.2 billion inflows", "https://news.test/etf-inflows", n - 2 * 3_600_000),
            ("Major crypto exchange hacked, $200 million in bitcoin drained", "https://news.test/hack", n - 3 * 3_600_000),
            ("SEC delays decision on new bitcoin ETF applications", "https://news.test/sec", n - 5 * 3_600_000),
            (XSS_TITLE, "https://news.test/xss", n - 4 * 3_600_000)]


def seeded_backtest(engine_cfg) -> dict:
    """A stored design-period run shaped exactly like services.backtesting.run_backtest output (few trades, so
    passed criteria are shown greyed as 'not meaningful')."""
    from services.backtesting import DESIGN_BADGE, HOLDOUT_START, config_hash
    from services.signal_engine import ENGINE_VERSION
    t0, t1 = HOLDOUT_START - 3 * 365 * 86_400_000, HOLDOUT_START - 4 * 3_600_000
    times = [t0 + i * (t1 - t0) // 59 for i in range(60)]
    trades = [{"entry_time": t0 + k * 40 * 86_400_000, "exit_time": t0 + k * 40 * 86_400_000 + 3 * 86_400_000,
               "entry": 30_000 + 4_000 * k, "exit": 30_000 + 4_000 * k + (600 if k % 2 else -450), "stop": 29_000 + 4_000 * k,
               "target": 32_000 + 4_000 * k, "reason": "target" if k % 2 else "stop", "ret": 0.018 if k % 2 else -0.016,
               "r_multiple": 1.4 if k % 2 else -1.0, "entry_type": "standard"} for k in range(6)]
    crit = [("Enough trades", False, "6 trades (need 30)"), ("Profit factor", True, "1.31 (need 1.2)"),
            ("Max drawdown", True, "4.1% (limit 25%)"), ("Net return", True, "+1.2% (need > 0)"),
            ("Beats random entries", False, "62nd percentile (need 90th)"), ("Beats EMA 50/200 return/DD", False, "0.29 vs 0.41"),
            ("Consistent windows", True, "4 of 6 windows positive")]
    return {
        "engine": ENGINE_VERSION, "config_hash": config_hash(engine_cfg), "created_at": now_ms(), "entry_mode": "standard",
        "period_kind": "design", "design_badge": DESIGN_BADGE, "holdout_start": HOLDOUT_START,
        "period": {"kind": "design", "start": t0, "end": t1, "bars": 6570, "years": 3.0},
        "strategy": {"trades": 6, "net_return_pct": 1.2, "max_drawdown_pct": 4.1, "return_over_max_dd": 0.29,
                     "profit_factor": 1.31, "win_rate_pct": 50.0, "avg_trade_pct": 0.2, "avg_r_multiple": 0.2,
                     "exposure_pct": 0.8, "costs_paid_pct": 1.8, "exit_reasons": {"target": 3, "stop": 3}},
        "baselines": {"buy_and_hold": {"net_return_pct": 180.4, "max_drawdown_pct": 32.0, "return_over_max_dd": 5.6,
                                       "trades": 1, "exposure_pct": 100.0},
                      "ema_50_200": {"net_return_pct": 60.2, "max_drawdown_pct": 21.0, "return_over_max_dd": 2.9,
                                     "trades": 9, "exposure_pct": 55.0},
                      "random_entries": {"runs": 200, "median_return_pct": 0.4, "p10_return_pct": -3.1, "p90_return_pct": 3.9}},
        "walk_forward": {"windows": [{"start": times[i * 10], "end": times[i * 10 + 9], "default_return_pct": 0.3 * (i - 2),
                                      "default_trades": 1, "chosen_weights": "default", "chosen_reason": "no full training year yet",
                                      "chosen_return_pct": 0.3 * (i - 2), "chosen_trades": 1, "buy_hold_return_pct": 12.0}
                                     for i in range(6)],
                         "positive_window_share": 0.667, "windows_with_trades": 6, "windows_total": 6, "chosen_on_train": None,
                         "note": "Default weights are never fitted, so their results are out-of-sample."},
        "hit_rate": {"precision": 0.51, "base_rate": 0.5, "balanced_accuracy": 0.5},
        "funnel": {"bars": 6570, "score_percentiles": {"p50": 0.02, "p90": 0.19, "p99": 0.31, "max": 0.44},
                   "bars_score_at_or_above_buy": 140, "bars_score_at_or_below_sell": 210, "raw_buy_setups": 140,
                   "downgraded_low_rr": 118, "downgraded_no_plan": 4, "buy_setups_after_rr": 18,
                   "blocked_by_safeguard": {"counter_trend": 7, "volatility": 3}, "tradable_buy_bars": 8, "trades": 6,
                   "low_rr_values": {"median": 0.71, "p75": 1.1, "share_at_or_above_1.0": 0.3, "share_no_room": 0.12},
                   "what_if_min_rr": {"min_rr_1.5": 8, "min_rr_1.2": 15, "min_rr_1.0": 29, "min_rr_0.8": 44},
                   "note": "Most raw setups fail the reward/risk minimum.", "trades_by_entry": {"standard": 6}},
        "criteria": [{"name": n, "passed": p, "detail": d} for n, p, d in crit],
        "validated": False, "verdict": f"{DESIGN_BADGE}. 4 of 7 criteria passed on design data.",
        "limitations": ["News is not replayed (no historical headline archive).",
                        "Past performance does not guarantee future results; market regimes change."],
        "equity": {"time": times, "strategy": [1 + 0.0002 * i for i in range(60)],
                   "buy_and_hold": [1 + 0.03 * i for i in range(60)], "ema_50_200": [1 + 0.01 * i for i in range(60)]},
        "trades": trades,
    }


def ticker(sym, p, ch):
    return {"symbol": sym, "lastPrice": str(p), "priceChangePercent": str(ch), "closeTime": now_ms() - 2000}


class LiveServer:
    def __init__(self, tmp: Path, news_empty: bool = False, fresh: bool = False):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        cfg = Config()
        cfg.db_path = str(tmp / "e2e.sqlite3")
        cfg.port = self.port
        cfg.providers = ["fake1"]
        cfg.price_cache_seconds = 0
        cfg.coingecko_demo_key = E2E_KEY
        db = Database(cfg.db_path)
        db.init()
        self.db = db
        market = MarketDataService(db, cfg, http=HttpClient(min_interval=0), providers=[LiveFakeProvider()],
                                   overview_provider=FakeOverview())
        feed_bytes = rss([] if news_empty else news_items())
        collector = NewsCollector(db, http=None, feeds=[{"id": "fake", "name": "Fake Wire", "url": "https://news.test/rss"}],
                                  fetcher=lambda f: (feed_bytes, 3.0))
        news = NewsService(db, collector, price_series=lambda: None)
        wl = WatchlistService(db, FakeHttp({"/api/v3/ticker/24hr": lambda u, p: [ticker("BTCUSDT", 65_000, 1.2),
                                                                                    ticker("ETHUSDT", 2500.5, -0.4)]}),
                              symbols=lambda: ["BTCUSDT", "ETHUSDT"])
        cal = CalendarService(db, FakeHttp({"federalreserve.gov": FOMC_FIXTURE.read_text(encoding="utf-8")}))
        self.app = create_app(cfg, market=market, news=news, ollama=service(FakeOllama(up=False)),
                              watchlist=wl, calendar=cal, notifiers=[])
        self.seed(news_empty, fresh)
        from werkzeug.serving import make_server
        self.server = make_server("127.0.0.1", self.port, self.app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, name="e2e-server", daemon=True)
        self.thread.start()

    def seed(self, news_empty: bool, fresh: bool = False) -> None:
        a = self.app.config
        if fresh:                                                      # like a first start: nothing stored yet
            return
        a["BTC_PAPER"].update_settings({"risk_pct": 1.0, "max_exposure_pct": 50.0})
        a["BTC_ALERTS"].create_rule({"kind": "price_below", "params": {"price": 62_000}})   # drawn as a chart alert line
        if not news_empty:
            a["BTC_NEWS"].refresh(force=True)                           # the collector strips tags from feed titles
            # Raw hostile text straight into the DB (as if it bypassed ingestion): the browser must escape it.
            self.db.insert_news([{"id": "xss-raw", "feed": "fake", "publisher": "Fake Wire", "title": XSS_RAW,
                                  "link": "https://news.test/xss-raw", "published_at": now_ms() - 3_600_000,
                                  "collected_at": now_ms() - 3_600_000}])
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO alerts (rule_id, kind, severity, title, message, reason, source, links_json,
                            created_at, acknowledged_at) VALUES (NULL, 'test', 'info', ?, ?, 'seeded fixture', 'app',
                            '["javascript:alert(1)"]', ?, ?)""",
                         ("<b>XSS</b> alert title", XSS_TITLE, now_ms() - 7_200_000, now_ms() - 7_000_000))
        sig = a["BTC_SIGNALS"].current()                                # stores the first live record
        for hours, score in ((30, -0.05), (20, 0.08)):                  # two older audit-log records
            self.db.save_signal({**sig, "generated_at": now_ms() - hours * 3_600_000, "score": score},
                                "seeded fixture record")
        res = seeded_backtest(a["BTC_SIGNALS"].config())
        summary = {"verdict": res["verdict"], "period": res["period"], "entry_mode": "standard", "period_kind": "design",
                   "strategy": {k: res["strategy"][k] for k in ("trades", "net_return_pct", "max_drawdown_pct",
                                                                "profit_factor", "win_rate_pct")},
                   "buy_and_hold": 180.4, "criteria_passed": 4, "criteria_total": 7}
        self.db.save_backtest(now_ms(), res["engine"], res["config_hash"], False, summary, res)

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(5)


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    if BASE_URL:
        yield type("External", (), {"url": BASE_URL, "external": True})()
        return
    srv = LiveServer(tmp_path_factory.mktemp("e2e"))
    srv.external = False
    yield srv
    srv.stop()


@pytest.fixture
def fresh_server(tmp_path):
    """A first start: empty news feed, no backtest, no signal history, default paper settings."""
    srv = LiveServer(tmp_path, news_empty=True, fresh=True)
    yield srv
    srv.stop()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


class Page:
    """A fresh browser context per test; collects console errors and page errors."""

    def __init__(self, browser, base_url: str, **ctx):
        self.context = browser.new_context(accept_downloads=True, **ctx)
        # upper bounds only (every wait is for a state, never a fixed time): generous so a busy PC does not fail a run
        self.context.set_default_navigation_timeout(90_000)
        self.context.set_default_timeout(60_000)
        self.page = self.context.new_page()
        self.base = base_url
        self.errors: list[str] = []
        self.page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.external: list[str] = []
        self.pending: set = set()                       # in-flight requests, for idle(): a state, not a time
        self.page.on("request", lambda r: self.pending.add(r))
        self.page.on("requestfinished", lambda r: self.pending.discard(r))
        self.page.on("requestfailed", lambda r: self.pending.discard(r))
        self.context.route("**/*", self._route)

    def idle(self, timeout: float = 60_000, quiet: int = 3) -> None:
        """Wait until no request is in flight (checked `quiet` times in a row, events pumped in between): every answer
        already asked for has arrived and been handled. Fails after `timeout` ms."""
        waited, calm = 0, 0
        while calm < quiet:
            self.page.evaluate("() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)))")
            calm = calm + 1 if not self.pending else 0
            if self.pending:
                self.page.wait_for_timeout(25)
                waited += 25
                assert waited < timeout, f"requests still in flight: {[r.url for r in self.pending]}"

    def _route(self, route):
        """No network from the browser: anything not on this machine gets a stub page and is recorded."""
        host = urlparse(route.request.url).hostname
        if host in ("127.0.0.1", "localhost"):
            route.continue_()
        else:
            self.external.append(route.request.url)
            route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>stub</title>")

    def goto(self, path: str = "/", ready: bool = True):
        """Open a dashboard path. "/" is the Overview; "/#/<view>" opens a view (0.9.1 hash routes)."""
        self.page.goto(self.base + path)
        if ready and path.split("#")[0] in ("/", ""):
            view = path.split("#/")[1] if "#/" in path else None
            wait_dashboard_ready(self.page, view)
        return self.page

    def view(self, name: str):
        """Switch view the way a user does: click it in the sidebar, then wait until its data has loaded."""
        self.page.click(f"#viewNav a[data-view='{name}']")
        wait_view_ready(self.page, name)
        return self.page


VIEWS = ["overview", "market", "news", "signal", "research", "strategies", "paper", "alerts", "assistant", "system"]


def wait_view_ready(page, view: str, timeout: float = 30_000) -> None:
    page.wait_for_selector(f"main[data-view='{view}'][data-view-ready='1']", timeout=timeout)


def wait_dashboard_ready(page, view: str | None = None, timeout: float = 30_000) -> None:
    """The view is shown and loaded. The Overview (default) also waits for its markers and the chart."""
    view = view or page.evaluate("() => document.getElementById('main').dataset.view")
    wait_view_ready(page, view, timeout)
    if view == "overview":
        for sel in ("#sigSummary", "#signal", "#watchlist", "#calendar", "#alerts"):
            page.wait_for_selector(f'{sel}[data-state="ready"]', state="attached", timeout=timeout)
        page.wait_for_selector("#chartBox .main-svg", timeout=timeout)


@pytest.fixture
def ui(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    yield p
    p.context.close()
    assert {urlparse(u).hostname for u in p.external} <= {"news.test"}, p.external   # nothing else left the PC


@pytest.fixture
def mobile(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
             device_scale_factor=2)
    yield p
    p.context.close()
    assert p.external == [], p.external


def pytest_collection_modifyitems(config, items):
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.e2e)
            if BASE_URL and "smoke" not in item.keywords:
                item.add_marker(pytest.mark.skip(reason="E2E_BASE_URL set: only read-only smoke tests run"))
