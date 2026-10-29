"""Exports (Phase 8): CSV and JSON downloads of the app's own stored data.

CSV: UTF-8 with BOM (Excel-friendly), ISO UTC times plus a local-time column, CSV-injection protection
(cells starting with = + - @ TAB or CR get a leading ' unless they are plain numbers).
JSON: the rows plus metadata (generated_at, app version, engine, source notes, hypothetical/SIMULATED labels).
At most MAX_ROWS rows per export; larger requests get a clear 400 (ValueError).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo

MAX_ROWS = 100_000
DATASETS = ("candles", "signals", "news", "backtest", "paper_orders", "alerts", "chat")
BACKTEST_PARTS = ("trades", "equity", "criteria", "summary")
_NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v: Any) -> Any:
    """Neutralise spreadsheet formulas. Real numbers stay numbers."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, (list, dict)):
        v = json.dumps(v, ensure_ascii=False)
    s = str(v)
    if s.startswith(_DANGEROUS) and not _NUMBER_RE.match(s.strip()):
        return "'" + s
    return s


def _parse_time(raw: str | None, name: str) -> int | None:
    if raw in (None, ""):
        return None
    raw = str(raw).strip()
    if raw.isdigit():
        return int(raw)
    try:
        t = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"'{name}' must be an ISO date/time (e.g. 2026-01-31 or 2026-01-31T12:00Z) or epoch ms") from None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp() * 1000)


class ExportService:
    def __init__(self, db, app_version: str, engine: str, timezone: Callable[[], str],
                 news_report: Callable[[int], dict], candle_provider: Callable[[], tuple[str, str]],
                 clock: Callable[[], int]):
        self.db = db
        self.app_version = app_version
        self.engine = engine
        self.timezone = timezone
        self.news_report = news_report
        self.candle_provider = candle_provider          # () -> (provider, symbol) of the stored candles
        self.clock = clock

    # ---------------------------------------------------------------- datasets
    # Each returns (columns, rows, time_columns, meta)
    @staticmethod
    def _check(rows: list) -> None:
        if len(rows) > MAX_ROWS:
            raise ValueError(f"Too many rows (more than {MAX_ROWS:,}). Narrow the range (from/to or hours).")

    def _candles(self, args: dict):
        from config import TIMEFRAMES
        tf = args.get("tf") or "1h"
        if tf not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe '{tf}'. Use one of: {', '.join(TIMEFRAMES)}")
        start = _parse_time(args.get("from"), "from") or 0
        end = _parse_time(args.get("to"), "to") or 2 ** 62
        if start > end:
            raise ValueError("'from' must be before 'to'")
        provider, symbol = self.candle_provider()
        rows = self.db.get_candles_range(provider, symbol, tf, start, end)
        self._check(rows)
        cols = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
        return cols, rows, ["open_time", "close_time"], {
            "timeframe": tf, "provider": provider, "symbol": symbol,
            "source_notes": f"Stored {provider} {symbol} candles (exchange market data). Closed and partial bars as stored."}

    def _signals(self, args: dict):
        rows = self.db.signal_history(MAX_ROWS + 1)
        self._check(rows)
        return ["id", "created_at", "signal", "setup", "score", "price", "provider", "reason"], rows, ["created_at"], {
            "source_notes": "Signal history from the rule-based engine. Research signals, not financial advice.",
            "label": "Research signal history - not financial advice"}

    def _news(self, args: dict):
        try:
            hours = int(args.get("hours") or 72)
        except ValueError:
            raise ValueError("'hours' must be a whole number") from None
        if not 1 <= hours <= 168:
            raise ValueError("'hours' must be between 1 and 168")
        events = self.news_report(hours).get("events") or []
        self._check(events)
        cols = ["id", "published_at", "publisher", "title", "link", "sentiment", "score", "impact", "verification",
                "freshness", "categories", "independent_sources"]
        return cols, events, ["published_at"], {
            "hours": hours, "source_notes": "Headlines and links from public RSS feeds; rule-based headline sentiment. "
                                            "Full articles stay on the publishers' sites."}

    def _backtest(self, args: dict):
        try:
            run_id = int(args.get("run") or args.get("id") or 0)
        except ValueError:
            raise ValueError("'run' must be a backtest run id") from None
        if run_id <= 0:
            raise ValueError("'run' (backtest run id) is required, e.g. ?run=3")
        run = self.db.get_backtest(run_id)
        if run is None:
            raise LookupError(f"Backtest run {run_id} not found")
        part = args.get("part") or "trades"
        if part not in BACKTEST_PARTS:
            raise ValueError(f"'part' must be one of: {', '.join(BACKTEST_PARTS)}")
        meta = {"run_id": run_id, "engine": run.get("engine"), "config_hash": run.get("config_hash"),
                "validated": run.get("validated"), "label": "HYPOTHETICAL backtest results - not real trading",
                "source_notes": "Simulated on stored historical candles with fees/slippage as configured in the run."}
        if part == "trades":
            rows = run.get("trades") or []
            cols = ["entry_time", "exit_time", "entry", "exit", "stop", "target", "reason", "ret", "r_multiple", "bars",
                    "entry_type"]
            return cols, rows, ["entry_time", "exit_time"], meta
        if part == "equity":
            eq = run.get("equity") or {}
            names = [k for k in eq if k != "time"]
            rows = [{"time": t, **{n: (eq[n][i] if i < len(eq[n]) else None) for n in names}}
                    for i, t in enumerate(eq.get("time") or [])]
            self._check(rows)
            return ["time"] + names, rows, ["time"], meta
        if part == "criteria":
            rows = run.get("criteria") or []
            cols = sorted({k for r in rows for k in r}, key=lambda k: (k != "name", k))
            return cols, rows, [], meta
        summary = run.get("summary") or {}
        rows = [{"key": k, "value": v} for k, v in summary.items()]
        return ["key", "value"], rows, [], meta

    def _paper_orders(self, args: dict):
        with self.db.connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM paper_orders ORDER BY id LIMIT ?", (MAX_ROWS + 1,))]
        self._check(rows)
        for r in rows:
            r["simulated"] = "SIMULATED"
        return ["id", "created_at", "side", "qty", "price", "fee", "realized_pnl", "reason", "simulated"], rows, \
            ["created_at"], {"label": "SIMULATED paper orders - no real money, no real orders",
                             "source_notes": "Paper-trading ledger stored locally."}

    def _alerts(self, args: dict):
        with self.db.connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY id LIMIT ?", (MAX_ROWS + 1,))]
        self._check(rows)
        for r in rows:
            r["links"] = " ".join(json.loads(r.pop("links_json") or "[]"))
        cols = ["id", "created_at", "rule_id", "kind", "severity", "title", "message", "reason", "value", "threshold",
                "source", "links", "acknowledged_at"]
        return cols, rows, ["created_at", "acknowledged_at"], {
            "source_notes": "Alerts created by the local alert monitor. Risk-limit alerts refer to the SIMULATED paper account."}

    def _chat(self, args: dict):
        rows = self.db.chat_history(MAX_ROWS + 1)
        self._check(rows)
        for r in rows:
            r["sources"] = " | ".join(str(s) for s in r.get("sources") or [])
        return ["id", "created_at", "role", "mode", "model", "content", "sources", "error"], rows, ["created_at"], {
            "source_notes": "Local assistant chat. AI answers may contain mistakes; rule answers come from the app's data."}

    # ---------------------------------------------------------------- output
    def export(self, dataset: str, fmt: str, args: dict) -> tuple[bytes, str, str]:
        """Returns (body, mimetype, filename)."""
        if dataset not in DATASETS:
            raise ValueError(f"Unknown dataset '{dataset}'. Use one of: {', '.join(DATASETS)}")
        fmt = (fmt or "csv").lower()
        if fmt not in ("csv", "json"):
            raise ValueError("format must be csv or json")
        cols, rows, time_cols, meta = getattr(self, f"_{dataset}")(args)
        now = self.clock()
        tzname = self.timezone()
        tz = ZoneInfo(tzname)
        stamp = dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"btc-signal-lab_{dataset}_{stamp}.{fmt}"

        def times(ms):
            if ms in (None, ""):
                return "", ""
            t = dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc)
            return t.strftime("%Y-%m-%dT%H:%M:%SZ"), t.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")

        if fmt == "json":
            if dataset == "backtest" and not args.get("part"):
                run = self.db.get_backtest(meta["run_id"])
                data: Any = {"summary": run.get("summary"), "criteria": run.get("criteria"), "trades": run.get("trades"),
                             "equity": run.get("equity"), "verdict": run.get("verdict")}
                count = len(run.get("trades") or [])
            else:
                data = [{c: r.get(c) for c in cols} for r in rows]
                for d in data:
                    for c in time_cols:
                        d[f"{c}_utc"], d[f"{c}_local"] = times(d.get(c))
                count = len(data)
            body = {"metadata": {"dataset": dataset, "generated_at": times(now)[0], "generated_at_ms": now,
                                 "app_version": self.app_version, "engine": self.engine, "display_timezone": tzname,
                                 "rows": count, **meta}, "data": data}
            return json.dumps(body, ensure_ascii=False, indent=1, default=str).encode("utf-8"), "application/json", filename

        header: list[str] = []
        for c in cols:
            header += [f"{c}_utc", f"{c}_local_{tzname.replace('/', '_')}"] if c in time_cols else [c]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(header)
        for r in rows:
            line = []
            for c in cols:
                if c in time_cols:
                    line += list(times(r.get(c)))
                else:
                    line.append(safe_cell(r.get(c)))
            w.writerow(line)
        return ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv", filename   # Flask adds charset=utf-8
