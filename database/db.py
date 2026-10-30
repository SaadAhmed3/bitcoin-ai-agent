"""SQLite persistence layer.

One short-lived connection per operation keeps this safe under Flask's
threaded dev server. All timestamps are stored as UTC epoch milliseconds.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 7  # 2: news_items, 3: signals, 4: backtests + paper trading, 5: assistant chat, 6: alerts,
                    # 7: holdout_runs (0.9.2). Migrations are additive; an upgrade backs the file up first.

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candles (
    provider    TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    open_time   INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    close_time  INTEGER NOT NULL,
    fetched_at  INTEGER NOT NULL,
    PRIMARY KEY (provider, symbol, timeframe, open_time)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles (provider, symbol, timeframe, open_time DESC);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    provider     TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    quote        TEXT    NOT NULL,
    price        REAL    NOT NULL,
    observed_at  INTEGER,
    fetched_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_fetched ON price_snapshots (fetched_at DESC);

CREATE TABLE IF NOT EXISTS source_health (
    source          TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    last_success    INTEGER,
    last_failure    INTEGER,
    last_error      TEXT,
    latency_ms      REAL,
    success_count   INTEGER NOT NULL DEFAULT 0,
    failure_count   INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kv_cache (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    fetched_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS news_items (
    id            TEXT PRIMARY KEY,
    feed          TEXT    NOT NULL,
    publisher     TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    link          TEXT    NOT NULL,
    summary       TEXT    NOT NULL DEFAULT '',
    published_at  INTEGER,
    collected_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_time ON news_items (COALESCE(published_at, collected_at) DESC);

CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    INTEGER NOT NULL,
    signal        TEXT    NOT NULL,
    setup         TEXT    NOT NULL,
    score         REAL    NOT NULL,
    price         REAL,
    provider      TEXT,
    reason        TEXT    NOT NULL,
    payload       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_time ON signals (created_at DESC);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    INTEGER NOT NULL,
    engine        TEXT    NOT NULL,
    config_hash   TEXT    NOT NULL,
    validated     INTEGER NOT NULL,
    summary       TEXT    NOT NULL,
    payload       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_account (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    created_at    INTEGER NOT NULL,
    start_balance REAL    NOT NULL,
    cash          REAL    NOT NULL,
    btc           REAL    NOT NULL,
    avg_entry     REAL,
    stop          REAL,
    target        REAL,
    settings      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    INTEGER NOT NULL,
    side          TEXT    NOT NULL,
    qty           REAL    NOT NULL,
    price         REAL    NOT NULL,
    fee           REAL    NOT NULL,
    realized_pnl  REAL,
    reason        TEXT    NOT NULL,
    simulated     INTEGER NOT NULL DEFAULT 1 CHECK (simulated = 1)
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role          TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    content       TEXT    NOT NULL,
    created_at    INTEGER NOT NULL,
    mode          TEXT,
    model         TEXT,
    sources_json  TEXT    NOT NULL DEFAULT '[]',
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_time ON chat_messages (id DESC);

CREATE TABLE IF NOT EXISTS alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,
    params      TEXT    NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    cooldown_s  INTEGER,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id          INTEGER,
    kind             TEXT    NOT NULL,
    severity         TEXT    NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    title            TEXT    NOT NULL,
    message          TEXT    NOT NULL,
    reason           TEXT    NOT NULL,
    value            REAL,
    threshold        REAL,
    source           TEXT    NOT NULL,
    links_json       TEXT    NOT NULL DEFAULT '[]',
    created_at       INTEGER NOT NULL,
    acknowledged_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts (created_at DESC);

CREATE TABLE IF NOT EXISTS alert_state (
    key         TEXT PRIMARY KEY,
    value       TEXT    NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- Every holdout run, kept forever. Only the FIRST run of a hypothesis (same trading rules) may change the
-- validation; later runs are stored with counts_for_validation = 0. The partial unique index enforces it.
CREATE TABLE IF NOT EXISTS holdout_runs (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at             INTEGER NOT NULL,
    hypothesis_hash        TEXT    NOT NULL,
    config_hash            TEXT    NOT NULL,
    engine                 TEXT    NOT NULL,
    settings               TEXT    NOT NULL,
    result                 TEXT    NOT NULL,
    backtest_run_id        INTEGER,
    counts_for_validation  INTEGER NOT NULL CHECK (counts_for_validation IN (0, 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_holdout_first ON holdout_runs (hypothesis_hash) WHERE counts_for_validation = 1;
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Database:
    def __init__(self, path: str, read_only: bool = False):
        """read_only=True opens the file with SQLite's mode=ro: no writes, no schema or journal changes."""
        self.path = path
        self.read_only = read_only

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            conn = sqlite3.connect(Path(self.path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        else:
            conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        if not self.read_only:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    MIGRATIONS = [("paper_account", "avg_fill", "REAL"),  # columns added after a table first shipped
                  ("source_health", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")]

    def _stored_schema_version(self) -> int | None:
        """Schema version of an existing database file, or None for a new or empty file."""
        p = Path(self.path)
        if not p.exists() or p.stat().st_size == 0:
            return None
        conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            return int(row[0]) if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def backup(self, suffix: str) -> Path:
        """Consistent copy (SQLite online backup API, safe while the app runs) next to the database file."""
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = Path(f"{self.path}.backup-{stamp}-{suffix}")
        src = sqlite3.connect(Path(self.path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")   # one self-contained file, no -wal/-shm sidecars
        finally:
            dst.close()
            src.close()
        return dest

    def init(self) -> None:
        old = self._stored_schema_version()
        if old is not None and old < SCHEMA_VERSION:
            self.backup(f"schema-{old}")      # before any schema change; existing data is never dropped
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            for table, column, ctype in self.MIGRATIONS:
                cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def ping(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    # ---------------------------------------------------------------- candles
    def upsert_candles(self, provider: str, symbol: str, timeframe: str, rows: Iterable[dict], fetched_at: int) -> int:
        payload = [
            (
                provider, symbol, timeframe,
                int(r["open_time"]), float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), float(r["volume"]), int(r["close_time"]), fetched_at,
            )
            for r in rows
        ]
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO candles (provider, symbol, timeframe, open_time, open, high, low, close,
                                        volume, close_time, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(provider, symbol, timeframe, open_time) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                     volume=excluded.volume, close_time=excluded.close_time, fetched_at=excluded.fetched_at""",
                payload,
            )
        return len(payload)

    def get_candles(self, provider: str, symbol: str, timeframe: str, limit: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT open_time, open, high, low, close, volume, close_time, fetched_at
                   FROM candles WHERE provider=? AND symbol=? AND timeframe=?
                   ORDER BY open_time DESC LIMIT ?""",
                (provider, symbol, timeframe, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def latest_candle_fetch(self, provider: str, symbol: str, timeframe: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(fetched_at) AS f FROM candles WHERE provider=? AND symbol=? AND timeframe=?",
                (provider, symbol, timeframe),
            ).fetchone()
        return row["f"] if row and row["f"] is not None else None

    def get_candles_range(self, provider: str, symbol: str, timeframe: str,
                          start_ms: int = 0, end_ms: int = 2**62) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT open_time, open, high, low, close, volume, close_time FROM candles
                   WHERE provider=? AND symbol=? AND timeframe=? AND open_time>=? AND open_time<=?
                   ORDER BY open_time""",
                (provider, symbol, timeframe, start_ms, end_ms),
            ).fetchall()
        return [dict(r) for r in rows]

    def candle_span(self, provider: str, symbol: str, timeframe: str) -> dict:
        with self.connect() as conn:
            r = conn.execute(
                """SELECT COUNT(*) n, MIN(open_time) first, MAX(close_time) last FROM candles
                   WHERE provider=? AND symbol=? AND timeframe=?""", (provider, symbol, timeframe)).fetchone()
        return {"count": r["n"], "first": r["first"], "last": r["last"]}

    # ---------------------------------------------------------------- backtests
    def save_backtest(self, created_at: int, engine: str, config_hash: str, validated: bool,
                      summary: dict, payload: dict) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO backtest_runs (created_at, engine, config_hash, validated, summary, payload)
                   VALUES (?,?,?,?,?,?)""",
                (created_at, engine, config_hash, int(validated), json.dumps(summary), json.dumps(payload)))
            return int(cur.lastrowid)

    def register_holdout_run(self, created_at: int, hypothesis: str, config_hash: str, engine: str,
                             settings: dict, result: dict, backtest_run_id: int | None) -> dict:
        """Record a holdout run. One statement decides atomically whether it is the first run of this hypothesis
        (the only one that counts for validation); the partial unique index backs that up.
        Returns {"id", "counts_for_validation", "first_run_id"}."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """INSERT INTO holdout_runs (created_at, hypothesis_hash, config_hash, engine, settings, result,
                                             backtest_run_id, counts_for_validation)
                   VALUES (?,?,?,?,?,?,?, NOT EXISTS (SELECT 1 FROM holdout_runs
                                                      WHERE hypothesis_hash=? AND counts_for_validation=1))""",
                (created_at, hypothesis, config_hash, engine, json.dumps(settings), json.dumps(result),
                 backtest_run_id, hypothesis))
            rid = int(cur.lastrowid)
            row = conn.execute("SELECT counts_for_validation FROM holdout_runs WHERE id=?", (rid,)).fetchone()
            first = conn.execute("SELECT id FROM holdout_runs WHERE hypothesis_hash=? AND counts_for_validation=1",
                                 (hypothesis,)).fetchone()
        return {"id": rid, "counts_for_validation": bool(row[0]), "first_run_id": int(first[0])}

    def link_holdout_run(self, holdout_id: int, backtest_run_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE holdout_runs SET backtest_run_id=? WHERE id=? AND backtest_run_id IS NULL",
                         (backtest_run_id, holdout_id))

    def holdout_counts(self) -> dict:
        """{"made": every holdout run ever recorded, "counted": first runs (the only ones that count)}."""
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) made, COALESCE(SUM(counts_for_validation), 0) counted "
                               "FROM holdout_runs").fetchone()
        return {"made": int(row["made"]), "counted": int(row["counted"])}

    def holdout_runs(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM holdout_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "settings": json.loads(r["settings"]), "result": json.loads(r["result"]),
                 "counts_for_validation": bool(r["counts_for_validation"])} for r in rows]

    def backtest_runs(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT id, created_at, engine, config_hash, validated, summary FROM backtest_runs
                                   ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
        return [{**dict(r), "summary": json.loads(r["summary"]), "validated": bool(r["validated"])} for r in rows]

    def get_backtest(self, run_id: int) -> dict | None:
        with self.connect() as conn:
            r = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        return {"id": r["id"], "created_at": r["created_at"], "engine": r["engine"], "config_hash": r["config_hash"],
                "validated": bool(r["validated"]), "summary": json.loads(r["summary"]), **json.loads(r["payload"])}

    # ----------------------------------------------------------------- prices
    def save_price(self, provider: str, symbol: str, quote: str, price: float,
                   observed_at: int | None, fetched_at: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO price_snapshots (provider, symbol, quote, price, observed_at, fetched_at)
                   VALUES (?,?,?,?,?,?)""",
                (provider, symbol, quote, price, observed_at, fetched_at),
            )

    def price_near(self, ts: int, tolerance_ms: int = 600_000) -> dict | None:
        """The stored price snapshot closest to `ts`, if one exists within the tolerance."""
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM price_snapshots WHERE fetched_at BETWEEN ? AND ?
                   ORDER BY ABS(fetched_at - ?) LIMIT 1""", (ts - tolerance_ms, ts + tolerance_ms, ts)).fetchone()
        return dict(row) if row else None

    def latest_price(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM price_snapshots ORDER BY fetched_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # ----------------------------------------------------------------- health
    def record_health(self, source: str, ok: bool, latency_ms: float | None = None, error: str | None = None) -> None:
        ts = now_ms()
        # One UPSERT statement: a SELECT-then-INSERT let two requests both insert the first row for a source.
        # On conflict, success keeps last_failure/last_error; failure keeps last_success/latency (as before).
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO source_health (source, status, last_success, last_failure, last_error,
                                              latency_ms, success_count, failure_count, updated_at, consecutive_failures)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source) DO UPDATE SET
                       status = excluded.status,
                       last_success = COALESCE(excluded.last_success, last_success),
                       last_failure = COALESCE(excluded.last_failure, last_failure),
                       last_error = CASE WHEN excluded.status = 'ok' THEN last_error ELSE excluded.last_error END,
                       latency_ms = CASE WHEN excluded.status = 'ok' THEN excluded.latency_ms ELSE latency_ms END,
                       success_count = success_count + excluded.success_count,
                       failure_count = failure_count + excluded.failure_count,
                       consecutive_failures = CASE WHEN excluded.status = 'ok' THEN 0 ELSE consecutive_failures + 1 END,
                       updated_at = excluded.updated_at""",
                (source, "ok" if ok else "error", ts if ok else None, None if ok else ts,
                 None if ok else (error or "unknown error")[:500], latency_ms,
                 1 if ok else 0, 0 if ok else 1, ts, 0 if ok else 1),
            )

    def get_health(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM source_health ORDER BY source").fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- kv cache
    def cache_set(self, key: str, value: Any, fetched_at: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_cache (key, value, fetched_at) VALUES (?,?,?)",
                (key, json.dumps(value), fetched_at or now_ms()),
            )

    def cache_get(self, key: str) -> tuple[Any, int] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value, fetched_at FROM kv_cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return json.loads(row["value"]), row["fetched_at"]

    # ------------------------------------------------------------------- news
    def insert_news(self, items: Iterable[dict]) -> int:
        """Insert new items; existing ids keep their original collected_at. Returns count inserted."""
        rows = [(i["id"], i["feed"], i["publisher"], i["title"], i["link"], i.get("summary", ""),
                 i.get("published_at"), i["collected_at"]) for i in items]
        if not rows:
            return 0
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT OR IGNORE INTO news_items
                   (id, feed, publisher, title, link, summary, published_at, collected_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                rows,
            )
            return conn.total_changes - before

    def get_news(self, since_ms: int, limit: int = 1000) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM news_items WHERE COALESCE(published_at, collected_at) >= ?
                   ORDER BY COALESCE(published_at, collected_at) DESC LIMIT ?""",
                (since_ms, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- signals
    def save_signal(self, sig: dict, reason: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO signals (created_at, signal, setup, score, price, provider, reason, payload)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (sig["generated_at"], sig["signal"], sig["setup"], sig["score"], sig["price"]["value"],
                 sig["price"]["provider"], reason, json.dumps(sig)),
            )
            return int(cur.lastrowid)

    def latest_signal(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM signals ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def signal_history(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, created_at, signal, setup, score, price, provider, reason FROM signals
                   ORDER BY created_at DESC, id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_signal(self, signal_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM signals WHERE id=?", (signal_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    # --------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, json.dumps(value)))

    # --------------------------------------------------------------- assistant chat (Phase 7)
    def add_chat(self, role: str, content: str, created_at: int, mode: str | None = None, model: str | None = None,
                 sources: list | None = None, error: str | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO chat_messages (role, content, created_at, mode, model, sources_json, error)
                   VALUES (?,?,?,?,?,?,?)""",
                (role, content, created_at, mode, model, json.dumps(sources or []), error))
            return int(cur.lastrowid)

    def chat_history(self, limit: int = 50) -> list[dict]:
        """Most recent `limit` messages, oldest first."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in reversed(rows):
            d = dict(r)
            d["sources"] = json.loads(d.pop("sources_json") or "[]")
            out.append(d)
        return out

    def clear_chat(self) -> int:
        with self.connect() as conn:
            return conn.execute("DELETE FROM chat_messages").rowcount
