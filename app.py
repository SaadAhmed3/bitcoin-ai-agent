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
from services.signal_service import RANGES as SIGNAL_RANGES, SignalService
from services.backtest_service import BacktestService, current_validation, record_contaminations
from services.backtesting import DESIGN_BADGE, FULL_BADGE
from services.paper_trading import PaperTrading
from services.forward_tests import ForwardTests
from services.advanced_analysis import AdvancedAnalysis, apply_sweep_corrections
from services.no_trade_text import summary as no_trade_summary
from services.strategy_forward import StrategyForward
from services.strategy_lab import StrategyLab
from services.risk_manager import RISK_RANGES, RiskSettings, position_size
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

APP_VERSION = "0.9.8 (advanced analysis)"
DOC_FILES = ("README.md", "USER_GUIDE.md", "SECURITY.md", "CHANGELOG.md")   # served read-only at /docs/<name>
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

    def _with_summary(sig):
        """Add the one shared plain NO TRADE sentence (Overview, Signal details, alerts, assistant)."""
        if isinstance(sig, dict) and "no_trade_reasons" in sig:
            fwd = app.config.get("BTC_FORWARD")
            sig = {**sig, "no_trade_summary": no_trade_summary(sig.get("no_trade_reasons"), fwd.status() if fwd else None)}
        return sig
    backtests = backtests or BacktestService(
        db, engine_config=signals.config,
        http_factory=lambda: HttpClient(timeout=config.request_timeout, retries=3, backoff=1.0,
                                        min_interval=0.3, user_agent=config.user_agent))
    app.config["BTC_BACKTESTS"] = backtests
    # 0.9.7: a stored full run that reached into a holdout period makes that period contaminated (append-only).
    record_contaminations(db)
    apply_sweep_corrections(db, APP_VERSION, now_ms())     # known duplicates: superseded, never deleted
    def _price_at_day_open(ts: int) -> float | None:
        """BTC price at 00:00 UTC: the stored 1h candle opening then, else a price snapshot within 10 minutes."""
        for p in market.providers:
            rows = db.get_candles_range(p.name, p.symbol, "1h", ts, ts)
            if rows:
                return float(rows[0]["open"])
        snap = db.price_near(ts)
        return float(snap["price"]) if snap else None

    paper = PaperTrading(db, day_open_price=_price_at_day_open)
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
        "signal": lambda: _with_summary(signals.current()),
        "mtf": _mtf_summary,
        "news": lambda: news.report(hours=72),
        "backtest": _latest_backtest,
        "validation_run": _validation_backtest,
        "validation": lambda: current_validation(db, signals.config()),
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
        "signal": lambda: _with_summary(_signal_throttled()),
        "health": db.get_health,
        "paper": lambda: paper.state(market.get_price()),
    }, clock=now_ms, notifiers=alert_notifiers)
    alerts.seed_defaults()
    app.config["BTC_ALERTS"] = alerts
    last_background_exit: dict = {}

    def paper_levels_check():
        """SIMULATED stop/target check every monitor cycle, whether or not the Paper view is open. Same atomic
        close path as a manual SELL. GET /api/paper is read-only (batch 2): this is the only automatic exit."""
        res = paper.check_levels(market.get_price())
        if res:
            last_background_exit.update(res, at=now_ms())

    # Forward paper tests (research-1): simulated H1/H2/buy & hold on new daily candles. No live-signal change.
    forward = ForwardTests(db)
    app.config["BTC_FORWARD"] = forward

    def forward_tests_job():
        """Start once (next UTC midnight), refresh the daily candles (cached), append the newly closed days."""
        forward.ensure_started()
        market.get_candles("1d")
        forward.process()

    # Advanced analysis (regime, sweeps, volume profile): display + SHADOW mode. It reads the live signal to store
    # it next to its own components and never feeds anything back (signal-1.8 unchanged).
    advanced = AdvancedAnalysis(db, market, signal_fn=_signal_throttled,
                                setting=lambda key: settings.get("advanced", key))
    app.config["BTC_ADVANCED"] = advanced

    def advanced_shadow_job():
        """Once per newly closed 4h candle: the shadow row, a regime change and new sweep status updates."""
        advanced.record()

    # Strategy Lab (research only, separate from signal-1.8): versioned configs, daily signal records and its own
    # forward tracker (services/strategy_forward.py, not the H1/H2 tracker).
    strategy_forward = StrategyForward(db)
    lab = StrategyLab(db, market, strategy_forward,
                      regime_fn=lambda: (advanced.snapshot().get("regime") or {}).get("regime"))
    lab.ensure_defaults()
    app.config["BTC_STRATEGY_LAB"] = lab
    app.config["BTC_STRATEGY_FORWARD"] = strategy_forward

    def strategy_lab_job():
        """Refresh the daily candles (cached), store today's strategy signals once, advance the strategy tracker."""
        market.get_candles("1d")
        lab.record_daily()
        lab.forward_job()

    monitor = AlertMonitor(alerts, interval=60.0, jobs=[paper_levels_check, forward_tests_job, advanced_shadow_job,
                                                         strategy_lab_job])
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
        return jsonify(_with_summary(signals.current()))

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
        st["validation"] = current_validation(db, signals.config())    # st["holdout"]: the single-use holdout
        return jsonify(st)

    @app.post("/api/backtest/holdout_start")
    def backtest_holdout_start():
        """Start a NEW holdout period after the current one was spent (forward data only). Body {"start": "YYYY-MM-DD"}."""
        body = request.get_json(silent=True) or {}
        return jsonify({"holdout": backtests.set_holdout_start(body.get("start"))})

    @app.get("/api/backtest/runs")
    def backtest_runs():
        return jsonify({"runs": db.backtest_runs(20)})

    @app.get("/api/advanced")
    def advanced_snapshot():
        """Regime, liquidity sweeps and volume profile from closed candles: context, not part of the score."""
        return jsonify(advanced.snapshot(request.args.get("profile_tf", "4h")))

    @app.get("/api/advanced/overlays")
    def advanced_overlays():
        """Chart overlays: regime shading (closed 4h bars) and stored sweep markers on the chart's candles."""
        limit = min(max(int(request.args.get("limit", 300)), 50), 1000)
        return jsonify(advanced.overlays(tf_arg(), limit))

    @app.get("/api/advanced/profile")
    def advanced_chart_profile():
        """Volume profile of the chart's closed candles: mode=last (200 candles) or mode=visible&from=&to= (ms)."""
        limit = min(max(int(request.args.get("limit", 300)), 50), 1000)
        mode = request.args.get("mode", "last")
        num = lambda k: int(float(request.args[k])) if request.args.get(k) else None  # noqa: E731
        return jsonify(advanced.chart_profile(tf_arg(), limit, mode, num("from"), num("to")))

    @app.get("/api/strategies")
    def strategies():
        """Strategy Lab: five research strategies (implemented ones evaluated now) and the comparison table."""
        cards = lab.cards()
        return jsonify({"strategies": cards, "comparison": lab.comparison(cards)})

    @app.get("/api/strategies/<sid>")
    def strategy_detail(sid: str):
        return jsonify(lab.detail(sid))

    @app.get("/api/strategies/<sid>/config")
    def strategy_config_get(sid: str):
        return jsonify(lab.config(sid))

    @app.put("/api/strategies/<sid>/config")
    def strategy_config_put(sid: str):
        """A changed configuration is a new version: NOT VALIDATED (settings changed), nothing inherited."""
        return jsonify(lab.update_config(sid, request.get_json(silent=True)))

    @app.post("/api/strategies/<sid>/config/reset")
    def strategy_config_reset(sid: str):
        return jsonify(lab.reset_config(sid))

    @app.get("/api/advanced/shadow")
    def advanced_shadow():
        limit = min(max(int(request.args.get("limit", 50)), 1), 500)
        return jsonify({"rows": db.shadow_rows(limit),
                        "note": "Shadow mode: stored next to the live signal for later research; never used by it."})

    @app.get("/api/forward")
    def forward_status():
        """Forward paper tests (read-only; the background monitor starts and advances them)."""
        return jsonify(forward.status())

    @app.get("/api/backtest/holdout")
    def backtest_holdout_registry():
        """Every holdout run (newest first). Only the first run of a hypothesis counts for validation."""
        return jsonify({"runs": db.holdout_runs(50)})

    @app.get("/api/backtest/latest")
    def backtest_latest():
        runs = db.backtest_runs(1)
        if not runs:
            return jsonify({"error": "No backtest has been run yet."}), 404
        return jsonify(_with_period_badge(db.get_backtest(runs[0]["id"])))

    @app.get("/api/backtest/<int:run_id>")
    def backtest_get(run_id: int):
        run = db.get_backtest(run_id)
        if run is None:
            return jsonify({"error": "Backtest not found"}), 404
        return jsonify(_with_period_badge(run))

    def _with_period_badge(run: dict) -> dict:
        """Runs stored before 0.9.2 have no period_badge; derive it (no period_kind = a pre-6.2 full run)."""
        if "period_badge" not in run:
            kind = run.get("period_kind") or (run.get("period") or {}).get("kind") or "full"
            run["period_badge"] = {"design": DESIGN_BADGE, "full": FULL_BADGE}.get(kind)
        return run

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
        """Read-only (batch 2, B16). Simulated stop/target exits happen only in the background monitor."""
        st = paper.state(market.get_price())
        st["last_background_exit"] = dict(last_background_exit) or None
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
                         "validation": current_validation(db, signals.config()),
                         "ranges": SIGNAL_RANGES,
                         # one-time notice (e.g. a stored news weight set to 0 by signal-1.8); the Settings page
                         # consumes it with ?ack_notice=1, other readers (the dashboard's timers) do not
                         "notice": signals.notice(consume=request.args.get("ack_notice") == "1")}
        out["risk"] = {"settings": asdict(paper.settings()), "defaults": asdict(RiskSettings()),
                       "label": "SIMULATED paper account settings", "ranges": RISK_RANGES}
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
