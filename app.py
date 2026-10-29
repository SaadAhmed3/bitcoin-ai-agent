"""AI Bitcoin Analysis & Signal Agent - Flask application.

Start with:  python app.py
Then open:   http://127.0.0.1:5000

This build covers Phases 1-9 (foundation, market data, technical analysis, news, signals, backtesting,
paper trading, risk management, local assistant, alerts, exports, settings, watchlist, economic calendar).
It never places orders and has no exchange account connection.
"""
from __future__ import annotations

import logging
import math
import os
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from config import TIMEFRAMES, Config, ensure_dirs
from database import Database, now_ms
from services.http_client import HttpClient
from services.market_data import SOURCE_DOCS, MarketDataService
from services.news_collector import FEEDS, NewsCollector
from services.news_service import NewsService
from services.signal_engine import DEFAULT_WEIGHTS, ENGINE_VERSION, WEIGHT_RATIONALE
from services.signal_service import SignalService
from services.backtest_service import BacktestService, effective_validation
from services.paper_trading import PaperTrading
from services.risk_manager import RiskSettings, position_size
from services.technical_analysis import analyze, chart_series
from services.assistant import Assistant, AssistantBusy, load_study
from services.ollama_service import OllamaService, _model_matches
from services.settings_service import SettingsService
from services.alerts import AlertMonitor, AlertService
from services.exports import ExportService
from services.watchlist import WatchlistService
from services.calendar_service import CalendarService
from services.http_client import ProviderError
from services import security
from services.security import MAX_BODY_BYTES
from werkzeug.exceptions import HTTPException

APP_VERSION = "0.9.1 (Phases 1-9, UI polish)"
DOC_FILES = ("README.md", "SECURITY.md", "CHANGELOG.md")      # served read-only at /docs/<name>
SIGNAL_STALE_WARNING = ("Changing these makes the current validation stale; the signal stays NO TRADE until a new "
                        "backtest validates the new settings.")
log = logging.getLogger("btc_agent")


def create_app(config: Config | None = None, market: MarketDataService | None = None,
               news: NewsService | None = None, signals: SignalService | None = None,
               backtests: BacktestService | None = None, ollama: OllamaService | None = None,
               watchlist: WatchlistService | None = None, calendar: CalendarService | None = None,
               start_monitor: bool = False, notifiers: list | None = None) -> Flask:
    """start_monitor=True (only from `python app.py`) starts the background alert monitor. Tests never pass it."""
    config = config or Config()
    ensure_dirs(config)
    db = Database(config.db_path)
    db.init()
    settings = SettingsService(db, secrets=lambda: {"coingecko_demo_key": bool(config.coingecko_demo_key)})
    if market is None:
        config.providers = settings.get("market", "provider_order")      # restart required to change
    market = market or MarketDataService(db, config)
    if news is None:
        rss_http = HttpClient(timeout=config.request_timeout, retries=1, backoff=config.retry_backoff,
                              min_interval=config.min_request_interval, user_agent=config.user_agent)
        collector = NewsCollector(db, rss_http, refresh_seconds=config.news_refresh_seconds)

        def hourly_closes():
            df = market.get_candles("1h").df
            return None if df.empty else df["close"]

        news = NewsService(db, collector, price_series=hourly_closes)

    app = Flask(__name__)
    security.install(app, port_fn=lambda: config.port, extra_hosts=set(config.allowed_hosts))
    app.config["BTC_CONFIG"] = config
    app.config["BTC_DB"] = db
    app.config["BTC_MARKET"] = market
    app.config["BTC_NEWS"] = news
    signals = signals or SignalService(db, market, news_report=lambda: news.report(hours=72))
    app.config["BTC_SIGNALS"] = signals
    backtests = backtests or BacktestService(
        db, engine_config=signals.config,
        http_factory=lambda: HttpClient(timeout=config.request_timeout, retries=3, backoff=1.0,
                                        min_interval=0.3, user_agent=config.user_agent))
    app.config["BTC_BACKTESTS"] = backtests
    paper = PaperTrading(db)
    app.config["BTC_PAPER"] = paper

    # Local AI assistant (Phase 7). Every provider reads the app's own data; Ollama is optional.
    ollama = ollama or OllamaService(config.ollama_url, config.ollama_model, timeout=config.ollama_timeout)

    def _apply_assistant_settings():
        """Settings → Assistant take effect live; env or default values leave the service as built."""
        for key, attr, cast in (("model", "model", str), ("timeout_s", "timeout", float)):
            value, source = settings.effective("assistant", key)
            if source == "settings":
                setattr(ollama, attr, cast(value))
        ollama._cache = None
    _apply_assistant_settings()

    def _model_check(name: str) -> str | None:
        st = ollama.status(force=True)
        if not st.get("available"):
            return None                       # Ollama not running: can't check, accept the name
        if not any(_model_matches(name, m) for m in st.get("models") or []):
            return f"Model '{name}' is not installed in Ollama (installed: {', '.join(st.get('models') or []) or 'none'})"
        return None
    settings.model_check = _model_check

    def _mtf_summary():
        out = []
        for tf in ("1h", "4h", "1d"):
            a = analyze(market.get_candles(tf).df, tf)
            out.append({"timeframe": tf, "trend": (a.get("structure") or {}).get("trend"),
                        "rsi14": (a.get("indicators") or {}).get("rsi14")})
        return out

    def _latest_backtest():
        runs = db.backtest_runs(1)
        return db.get_backtest(runs[0]["id"]) if runs else None

    def _validation_backtest():
        stored = db.get_setting("strategy_validation") or {}
        return db.get_backtest(int(stored["run_id"])) if stored.get("run_id") else None

    assistant = Assistant(db, ollama, providers={
        "price": market.get_price,
        "signal": signals.current,
        "mtf": _mtf_summary,
        "news": lambda: news.report(hours=72),
        "backtest": _latest_backtest,
        "validation_run": _validation_backtest,
        "validation": lambda: effective_validation(db.get_setting("strategy_validation"), signals.config()),
        "paper": lambda: paper.state(market.get_price()),
        "health": db.get_health,
        "study": lambda: load_study(Path(config.db_path).parent / "score_study_design.json"),
    }, clock=now_ms, max_chars=config.assistant_max_chars,
        ai_enabled=lambda: bool(settings.get("assistant", "ai_explanations")))
    app.config["BTC_ASSISTANT"] = assistant
    app.config["BTC_SETTINGS"] = settings

    # Watchlist and calendar (Phase 8) reuse the market HttpClient (timeouts, retries, per-host rate limit).
    watchlist = watchlist or WatchlistService(db, market.http, symbols=lambda: settings.get("watchlist", "symbols"))
    app.config["BTC_WATCHLIST"] = watchlist

    def _symbol_check(symbols: list[str]) -> list[str]:
        try:
            return watchlist.unknown_symbols(symbols)
        except ProviderError as exc:
            raise ValueError(f"Could not reach Binance to validate the symbols ({exc}). Try again later.") from None
    settings.symbol_check = _symbol_check
    calendar = calendar or CalendarService(db, market.http,
                                           contact_email=lambda: os.getenv("CALENDAR_CONTACT_EMAIL", "").strip())
    app.config["BTC_CALENDAR"] = calendar

    def display_tz() -> str:
        return settings.get("general", "display_timezone")

    settings.on_change(lambda section, changes: _apply_assistant_settings() if section == "assistant" else None)

    # Alerts (Phase 8). Every provider reads the existing cached services; nothing polls an endpoint directly.
    def _price_1h_ago():
        snap = db.price_near(now_ms() - 3_600_000)
        if snap:
            return snap["price"]
        df = market.get_candles("1h").df
        return float(df["close"].iloc[-2]) if len(df) >= 2 else None

    def _volatility():
        return {tf: (analyze(market.get_candles(tf).df, tf).get("volatility") or {}).get("regime") for tf in ("4h", "1h")}

    _sig_cache: dict = {}

    def _signal_throttled():
        """The full signal needs every timeframe; the monitor recomputes it at most every 5 minutes."""
        if not _sig_cache or now_ms() - _sig_cache["at"] > 300_000:
            _sig_cache.update(at=now_ms(), sig=signals.current())
        return _sig_cache["sig"]

    alert_notifiers = list(notifiers) if notifiers is not None else \
        [_desktop_notifier(lambda: settings.get("alerts", "channel_desktop"))]
    alerts = AlertService(db, settings=lambda key: settings.get("alerts", key), providers={
        "price": market.get_price,
        "price_1h_ago": _price_1h_ago,
        "news": lambda: news.report(hours=72),
        "volatility": _volatility,
        "signal": _signal_throttled,
        "health": db.get_health,
        "paper": lambda: paper.state(market.get_price()),
    }, clock=now_ms, notifiers=alert_notifiers)
    alerts.seed_defaults()
    app.config["BTC_ALERTS"] = alerts
    monitor = AlertMonitor(alerts, interval=60.0)
    app.config["BTC_MONITOR"] = monitor
    if start_monitor:
        monitor.start()

    def _candle_source() -> tuple[str, str]:
        return (market.providers[0].name, market.providers[0].symbol) if market.providers else ("binance", "BTCUSDT")

    exports = ExportService(db, APP_VERSION, ENGINE_VERSION, timezone=display_tz,
                            news_report=lambda h: news.report(hours=h, refresh=False),
                            candle_provider=_candle_source, clock=now_ms)

    def tf_arg() -> str:
        tf = request.args.get("tf", "1h")
        if tf not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe '{tf}'. Use one of: {', '.join(TIMEFRAMES)}")
        return tf

    @app.errorhandler(ValueError)
    def bad_request(exc):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(LookupError)
    def not_found(exc):
        if type(exc) is not LookupError:          # a KeyError/IndexError is a bug, not "not found"
            return server_error(exc)
        return jsonify({"error": exc.args[0] if exc.args else "Not found"}), 404

    @app.errorhandler(AssistantBusy)
    def assistant_busy(exc):
        return jsonify({"error": str(exc)}), 429

    @app.errorhandler(HTTPException)
    def http_error(exc):
        """404/405/413 etc. as JSON with their real status (they used to fall into the 500 handler)."""
        if exc.code == 413:
            return jsonify({"error": f"Request body too large (max {MAX_BODY_BYTES // 1024} KB)."}), 400
        return jsonify({"error": exc.description or exc.name}), exc.code or 500

    @app.errorhandler(Exception)
    def server_error(exc):
        log.exception("Unhandled error")          # the trace stays in the local console, never in the response
        return jsonify({"error": "Internal error. Check the server console for details."}), 500

    # ------------------------------------------------------------------ pages
    @app.get("/vendor/plotly.min.js")
    def plotly_js():
        """Serve Plotly.js bundled with the `plotly` pip package, so charts work offline."""
        try:
            import plotly
        except ImportError:
            return ("// plotly package not installed", 404, {"Content-Type": "application/javascript"})
        bundle = Path(plotly.__file__).parent / "package_data"
        return send_from_directory(bundle, "plotly.min.js", mimetype="application/javascript", max_age=86400)

    @app.get("/")
    def index():
        return render_template("index.html", version=APP_VERSION, timeframes=list(TIMEFRAMES),
                               display_tz=display_tz(), theme=settings.get("general", "theme"))

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html", version=APP_VERSION, display_tz=display_tz(),
                               theme=settings.get("general", "theme"), signal_warning=SIGNAL_STALE_WARNING)

    @app.get("/docs/<name>")
    def docs(name: str):
        """README / SECURITY / CHANGELOG as plain text for the System view. Fixed allow-list: no paths."""
        if name not in DOC_FILES:
            return jsonify({"error": "Not found"}), 404
        text = (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")
        return Response(text, mimetype="text/plain")

    # -------------------------------------------------------------------- api
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "version": APP_VERSION, "db": db.ping(), "server_time": now_ms()})

    @app.get("/api/price")
    def price():
        return jsonify(market.get_price(force=request.args.get("force") == "1"))

    @app.get("/api/overview")
    def overview():
        return jsonify(market.get_overview())

    @app.get("/api/candles")
    def candles():
        tf = tf_arg()
        limit = min(max(int(request.args.get("limit", config.candle_limit)), 50), 1000)
        res = market.get_candles(tf, limit)
        return jsonify({"meta": res.meta(), "series": chart_series(res.df, tf, tail=min(limit, 400))})

    @app.get("/api/analysis")
    def analysis():
        tf = tf_arg()
        res = market.get_candles(tf)
        result = analyze(res.df, tf)
        result["data"] = res.meta()
        return jsonify(result)

    @app.get("/api/mtf")
    def multi_timeframe():
        rows = []
        for tf in TIMEFRAMES:
            started = time.perf_counter()
            res = market.get_candles(tf)
            a = analyze(res.df, tf)
            rows.append({
                "timeframe": tf, "status": a["status"], "bars": a.get("bars"),
                "price": a.get("price"), "rsi14": (a.get("indicators") or {}).get("rsi14"),
                "adx": (a.get("indicators") or {}).get("adx"),
                "trend": (a.get("structure") or {}).get("trend"),
                "volatility": (a.get("volatility") or {}).get("regime"),
                "breakout": (a.get("breakout") or {}).get("state"),
                "tally": a.get("tally"), "is_stale": res.is_stale, "provider": res.provider,
                "warnings": res.warnings + a.get("warnings", []),
                "ms": round((time.perf_counter() - started) * 1000),
            })
        return jsonify({"timeframes": rows, "generated_at": now_ms(),
                        "note": "Counts of bullish/bearish readings are not probabilities. No signal engine yet (Phase 5)."})

    @app.get("/api/news")
    def news_report():
        hours = min(max(int(request.args.get("hours", 72)), 1), 168)
        report = news.report(hours=hours)
        cat = request.args.get("category")
        sentiment = request.args.get("sentiment")
        events = report["events"]
        if cat:
            events = [e for e in events if cat in e["categories"]]
        if sentiment:
            events = [e for e in events if e["sentiment"] == sentiment]
        report["events"] = events[:200]
        return jsonify(report)

    @app.post("/api/news/refresh")
    def news_refresh():
        return jsonify(news.refresh(force=True))

    # ---------------------------------------------------------------- signals
    @app.get("/api/signal")
    def signal_current():
        return jsonify(signals.current())

    @app.get("/api/signal/history")
    def signal_history():
        limit = min(max(int(request.args.get("limit", 50)), 1), 500)
        return jsonify({"history": db.signal_history(limit)})

    @app.get("/api/signal/<int:signal_id>")
    def signal_detail(signal_id: int):
        sig = db.get_signal(signal_id)
        if sig is None:
            return jsonify({"error": "Signal not found"}), 404
        return jsonify(sig)

    @app.get("/api/signal/config")
    def signal_config_get():
        from dataclasses import asdict
        return jsonify({"config": asdict(signals.config()), "defaults": DEFAULT_WEIGHTS, "rationale": WEIGHT_RATIONALE})

    @app.put("/api/signal/config")
    def signal_config_put():
        from dataclasses import asdict
        body = request.get_json(silent=True)
        if body is None:
            raise ValueError("Send a JSON body, e.g. {\"weights\": {\"news\": 0.1}}")
        if body.get("reset"):
            return jsonify({"config": asdict(signals.reset_config()), "reset": True})
        return jsonify({"config": asdict(signals.update_config(body))})

    # ---------------------------------------------------------------- backtests (Phase 6)
    @app.post("/api/backtest/run")
    def backtest_run():
        body = request.get_json(silent=True) or {}
        return jsonify(backtests.start(years=body.get("years", 4), download=bool(body.get("download", True)),
                                       entry_mode=body.get("entry_mode"), period=body.get("period", "full"))), 202

    @app.post("/api/backtest/cancel")
    def backtest_cancel():
        return jsonify(backtests.cancel())

    @app.get("/api/backtest/status")
    def backtest_status():
        st = backtests.status()
        st["validation"] = effective_validation(db.get_setting("strategy_validation"), signals.config())
        return jsonify(st)

    @app.get("/api/backtest/runs")
    def backtest_runs():
        return jsonify({"runs": db.backtest_runs(20)})

    @app.get("/api/backtest/latest")
    def backtest_latest():
        runs = db.backtest_runs(1)
        if not runs:
            return jsonify({"error": "No backtest has been run yet."}), 404
        return jsonify(db.get_backtest(runs[0]["id"]))

    @app.get("/api/backtest/<int:run_id>")
    def backtest_get(run_id: int):
        run = db.get_backtest(run_id)
        if run is None:
            return jsonify({"error": "Backtest not found"}), 404
        return jsonify(run)

    # ---------------------------------------------------------------- risk & paper trading (Phase 6)
    def _num(body: dict, key: str, required: bool = True):
        v = body.get(key)
        if v in (None, ""):
            if required:
                raise ValueError(f"'{key}' is required")
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' must be a number") from None
        if not math.isfinite(f):                  # "nan"/"inf" parse as floats but are never valid amounts
            raise ValueError(f"'{key}' must be a finite number")
        return f

    @app.post("/api/risk/size")
    def risk_size():
        body = request.get_json(silent=True) or {}
        if body.get("settings") is not None and not isinstance(body["settings"], dict):
            raise ValueError("'settings' must be an object, e.g. {\"risk_pct\": 1}")
        settings = RiskSettings.from_dict(body.get("settings") or paper.state(None)["settings"])
        sells = [o["realized_pnl"] for o in reversed(db_paper_orders()) if o["side"] == "SELL" and o["realized_pnl"] is not None]
        return jsonify(position_size(_num(body, "portfolio_value"), _num(body, "entry"), _num(body, "stop"), settings,
                                     recent_pnls=sells if body.get("use_paper_history", True) else None,
                                     atr=_num(body, "atr", required=False)))

    def db_paper_orders():
        return paper.state(None)["orders"]

    @app.get("/api/paper")
    def paper_state():
        price = market.get_price()
        auto = paper.check_levels(price)
        st = paper.state(price)
        st["auto_exit"] = auto
        return jsonify(st)

    @app.post("/api/paper/reset")
    def paper_reset():
        body = request.get_json(silent=True) or {}
        return jsonify(paper.reset(body.get("start_balance", 10_000)))

    @app.post("/api/paper/settings")
    def paper_settings():
        from dataclasses import asdict
        return jsonify({"settings": asdict(paper.update_settings(request.get_json(silent=True) or {}))})

    @app.post("/api/paper/order")
    def paper_order():
        body = request.get_json(silent=True) or {}
        side = str(body.get("side", "")).upper()
        price = market.get_price(force=True)
        if side == "BUY":
            res = paper.buy(price, _num(body, "usd"), _num(body, "stop", False), _num(body, "target", False),
                            reason=str(body.get("reason") or "manual")[:120])
        elif side == "SELL":
            res = paper.sell(price, _num(body, "fraction", False) or 1.0, reason=str(body.get("reason") or "manual")[:120])
        else:
            raise ValueError("side must be BUY or SELL")
        return jsonify({**res, "state": paper.state(price)})

    # ---------------------------------------------------------------- assistant (Phase 7)
    @app.get("/api/assistant/status")
    def assistant_status():
        return jsonify(assistant.status())

    @app.post("/api/assistant/route")
    def assistant_route():
        body = request.get_json(silent=True) or {}
        return jsonify(assistant.route(body.get("message")))

    @app.post("/api/assistant/chat")
    def assistant_chat():
        body = request.get_json(silent=True) or {}
        return jsonify(assistant.chat(body.get("message")))

    @app.get("/api/assistant/history")
    def assistant_history():
        limit = min(max(int(request.args.get("limit", 50)), 1), 500)
        return jsonify({"messages": db.chat_history(limit)})

    @app.post("/api/assistant/clear")
    def assistant_clear():
        return jsonify({"cleared": db.clear_chat()})

    @app.get("/api/assistant/explain")
    def assistant_explain():
        return jsonify(assistant.explain_signal(use_ai=request.args.get("ai", "0") == "1"))

    # ---------------------------------------------------------------- settings (Phase 8)
    @app.get("/api/settings")
    def settings_get():
        from dataclasses import asdict
        out = settings.all()
        out["signal"] = {"config": asdict(signals.config()), "warning": SIGNAL_STALE_WARNING,
                         "validation": effective_validation(db.get_setting("strategy_validation"), signals.config())}
        out["risk"] = {"settings": asdict(paper.settings()), "defaults": asdict(RiskSettings()),
                       "label": "SIMULATED paper account settings"}
        return jsonify(out)

    @app.put("/api/settings/<section>")
    def settings_put(section: str):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object with the values to change")
        return jsonify({"section": section, "values": settings.update(section, body)})

    @app.post("/api/settings/<section>/reset")
    def settings_reset(section: str):
        return jsonify({"section": section, "values": settings.reset(section), "reset": True})

    # ---------------------------------------------------------------- alerts (Phase 8)
    def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(request.args.get(name, str(default)))
        except ValueError:
            raise ValueError(f"'{name}' must be a whole number") from None
        if not lo <= v <= hi:
            raise ValueError(f"'{name}' must be between {lo} and {hi}")
        return v

    @app.get("/api/alerts/rules")
    def alert_rules():
        return jsonify({"rules": alerts.list_rules()})

    @app.post("/api/alerts/rules")
    def alert_rule_create():
        return jsonify(alerts.create_rule(request.get_json(silent=True))), 201

    @app.put("/api/alerts/rules/<int:rule_id>")
    def alert_rule_update(rule_id: int):
        return jsonify(alerts.update_rule(rule_id, request.get_json(silent=True)))

    @app.delete("/api/alerts/rules/<int:rule_id>")
    def alert_rule_delete(rule_id: int):
        if not alerts.delete_rule(rule_id):
            return jsonify({"error": "Rule not found"}), 404
        return jsonify({"deleted": rule_id})

    @app.get("/api/alerts")
    def alerts_list():
        unread = request.args.get("unread", "0")
        if unread not in ("0", "1"):
            raise ValueError("'unread' must be 0 or 1")
        return jsonify({"alerts": alerts.list_alerts(unread == "1", _int_arg("limit", 50, 1, 500)),
                        "unread": alerts.unread_count(), "monitor_running": monitor.running})

    @app.post("/api/alerts/<int:alert_id>/ack")
    def alert_ack(alert_id: int):
        if not alerts.ack(alert_id):
            return jsonify({"error": "Alert not found"}), 404
        return jsonify({"acknowledged": alert_id, "unread": alerts.unread_count()})

    @app.post("/api/alerts/ack_all")
    def alert_ack_all():
        return jsonify({"acknowledged": alerts.ack_all(), "unread": 0})

    @app.post("/api/alerts/test")
    def alert_test():
        return jsonify(alerts.create_test()), 201

    # ---------------------------------------------------------------- exports (Phase 8)
    @app.get("/api/export/<dataset>")
    def export(dataset: str):
        body, mimetype, filename = exports.export(dataset, request.args.get("format", "csv"), request.args.to_dict())
        return Response(body, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                                          "Cache-Control": "no-store"})

    # ---------------------------------------------------------------- watchlist & calendar (Phase 8)
    @app.get("/api/watchlist")
    def watchlist_get():
        return jsonify(watchlist.get())

    @app.get("/api/calendar")
    def calendar_get():
        return jsonify(calendar.upcoming())

    @app.get("/api/sources")
    def sources():
        feed_docs = [{"source": f"rss:{f['id']}", "provides": f"{f['name']} headlines, links, summaries",
                      "api_key": "No", "limits": "Polite polling: once per 10 min",
                      "refresh": f"Every {config.news_refresh_seconds // 60} min", "delay": "Minutes after publication",
                      "failure": "Stored items still shown; feed marked as error",
                      "notes": "Headline + short snippet only; full article stays on the publisher's site."
                               + (" Aggregator: publisher taken from the source tag." if f.get("aggregator") else "")}
                     for f in FEEDS]
        return jsonify({"health": db.get_health(), "docs": SOURCE_DOCS + feed_docs})

    return app


def _desktop_notifier(enabled):
    """Windows desktop notification via the optional `plyer` package. Failures are logged by the caller."""
    def notify(alert: dict) -> None:
        if not enabled():
            return
        try:
            from plyer import notification
        except ImportError:
            log.warning("Desktop notifications are on but the optional 'plyer' package is not installed")
            return
        notification.notify(title=alert["title"][:63], message=alert["message"][:255],
                            app_name="BTC Signal Lab", timeout=10)
    return notify


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config()
    cfg.host, cfg.debug, bind_warnings = security.resolve_bind(cfg.host, cfg.debug, cfg.allow_remote)
    for w in bind_warnings + [security.ollama_url_warning(cfg.ollama_url) or ""]:
        if w:
            log.warning(w)
    application = create_app(cfg, start_monitor=True)
    print(f"\n  Bitcoin AI Agent {APP_VERSION}\n  Dashboard: http://{cfg.host}:{cfg.port}\n  Press Ctrl+C to stop.\n")
    # use_reloader=False: the reloader would start a second process (and a second alert monitor) in debug mode
    application.run(host=cfg.host, port=cfg.port, debug=cfg.debug, threaded=True, use_reloader=False)
