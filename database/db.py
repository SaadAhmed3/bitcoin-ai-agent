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

SCHEMA_VERSION = 15  # 2: news_items, 3: signals, 4: backtests + paper trading, 5: assistant chat, 6: alerts,
                     # 7: holdout_runs (0.9.2), 8: holdout_runs.holdout_start (0.9.3),
                     # 9: holdout_runs.verdict + criteria, write-once triggers (0.9.4),
                     # 10: forward_tests + forward_events, append-only (research-1),
                     # 11: holdout_contaminations, append-only (0.9.7),
                     # 12: regime_changes, sweep_events, sweep_updates, shadow_context, append-only (advanced analysis),
                     # 13: sweep_corrections, append-only (0.9.8 follow-up),
                     # 14: strategy_configs/signals/backtests/forward_tests/forward_events, append-only (Strategy Lab),
                     # 15: strategy_forward_rebases, append-only, only before a test's first processed day.
                    # Migrations are additive; an upgrade backs the file up first.

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

-- Every holdout run, kept forever. The holdout is SINGLE-USE (0.9.3): the first run in a holdout period, whatever
-- its rule set, counts and spends the period; later runs are stored with counts_for_validation = 0.
-- (The per-period unique index is created in init(), after the holdout_start column migration.)
CREATE TABLE IF NOT EXISTS holdout_runs (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at             INTEGER NOT NULL,
    hypothesis_hash        TEXT    NOT NULL,
    config_hash            TEXT    NOT NULL,
    engine                 TEXT    NOT NULL,
    settings               TEXT    NOT NULL,
    result                 TEXT    NOT NULL,
    backtest_run_id        INTEGER,
    counts_for_validation  INTEGER NOT NULL CHECK (counts_for_validation IN (0, 1)),
    holdout_start          INTEGER NOT NULL DEFAULT 1761696000000,
    verdict                TEXT CHECK (verdict IN ('PASS', 'FAIL')),   -- decided at run time, never recalculated
    criteria               TEXT                                         -- JSON: thresholds + each criterion's value
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_holdout_first ON holdout_runs (hypothesis_hash) WHERE counts_for_validation = 1;

-- Forward paper tests (research-1): each rule set is registered once with its start day and the SHA-256 of the rule
-- code; every daily decision, simulated fill and mark is appended. Nothing is ever updated or deleted.
CREATE TABLE IF NOT EXISTS forward_tests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    rules        TEXT    NOT NULL,
    fingerprint  TEXT    NOT NULL,
    start_day    INTEGER NOT NULL,
    created_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS forward_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id      INTEGER NOT NULL REFERENCES forward_tests (id),
    day          INTEGER NOT NULL,
    kind         TEXT    NOT NULL CHECK (kind IN ('fill', 'mark', 'decision')),
    in_market    INTEGER NOT NULL CHECK (in_market IN (0, 1)),
    price        REAL,
    equity       REAL,
    detail       TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    UNIQUE (test_id, day, kind)
);
CREATE TRIGGER IF NOT EXISTS forward_tests_no_update BEFORE UPDATE ON forward_tests
    BEGIN SELECT RAISE(ABORT, 'forward test rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_tests_no_delete BEFORE DELETE ON forward_tests
    BEGIN SELECT RAISE(ABORT, 'forward test rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_events_no_update BEFORE UPDATE ON forward_events
    BEGIN SELECT RAISE(ABORT, 'forward test rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_events_no_delete BEFORE DELETE ON forward_events
    BEGIN SELECT RAISE(ABORT, 'forward test rows are append-only'); END;

-- A holdout period whose results were displayed before any holdout run (e.g. by a full-period backtest) is
-- contaminated: no run in it may count for validation (0.9.7). Recorded once per period, never changed.
CREATE TABLE IF NOT EXISTS holdout_contaminations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    holdout_start    INTEGER NOT NULL UNIQUE,
    reason           TEXT    NOT NULL,
    seen_via_run_id  INTEGER,
    seen_at          INTEGER NOT NULL,
    created_at       INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS holdout_contaminations_no_update BEFORE UPDATE ON holdout_contaminations
    BEGIN SELECT RAISE(ABORT, 'holdout contamination rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS holdout_contaminations_no_delete BEFORE DELETE ON holdout_contaminations
    BEGIN SELECT RAISE(ABORT, 'holdout contamination rows are append-only'); END;

-- Advanced analysis (display + shadow mode, never part of the live score). Every row is decided at the close of a
-- candle and stored with that close time; nothing is ever changed or deleted (no repainting).
CREATE TABLE IF NOT EXISTS regime_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timeframe    TEXT    NOT NULL,
    at           INTEGER NOT NULL,
    regime       TEXT    NOT NULL,
    previous     TEXT,
    confidence   INTEGER,
    detail       TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    UNIQUE (timeframe, at)
);
CREATE TABLE IF NOT EXISTS sweep_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key      TEXT    NOT NULL UNIQUE,
    timeframe      TEXT    NOT NULL,
    direction      TEXT    NOT NULL CHECK (direction IN ('buy_side', 'sell_side')),
    level          REAL    NOT NULL,
    equal_levels   INTEGER NOT NULL,
    swing_time     INTEGER NOT NULL,
    event_time     INTEGER NOT NULL,
    sweep_extreme  REAL    NOT NULL,
    reclaim_price  REAL    NOT NULL,
    volume_ratio   REAL,
    created_at     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sweep_updates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key    TEXT    NOT NULL REFERENCES sweep_events (event_key),
    status       TEXT    NOT NULL,
    at           INTEGER NOT NULL,
    strength     REAL,
    factors      TEXT    NOT NULL,
    note         TEXT,
    created_at   INTEGER NOT NULL,
    UNIQUE (event_key, status)
);
CREATE TABLE IF NOT EXISTS shadow_context (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    close_time         INTEGER NOT NULL UNIQUE,
    regime             TEXT,
    regime_confidence  INTEGER,
    regime_score       REAL    NOT NULL,
    sweep_score        REAL    NOT NULL,
    vp_score           REAL    NOT NULL,
    poc                REAL,
    vah                REAL,
    val                REAL,
    live_signal        TEXT,
    live_setup         TEXT,
    live_score         REAL,
    live_engine        TEXT,
    weights            TEXT    NOT NULL,
    combined_score     REAL,
    detail             TEXT    NOT NULL,
    created_at         INTEGER NOT NULL
);
-- Strategy Lab (research only, separate from the live signal engine). Versioned configurations, one stored signal per
-- strategy and day, one design backtest per strategy and configuration, and a separate forward tracker. Append-only.
CREATE TABLE IF NOT EXISTS strategy_configs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id  TEXT    NOT NULL,
    version      INTEGER NOT NULL,
    config       TEXT    NOT NULL,
    config_hash  TEXT    NOT NULL,
    reason       TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    UNIQUE (strategy_id, version)
);
CREATE TABLE IF NOT EXISTS strategy_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT    NOT NULL,
    config_version  INTEGER NOT NULL,
    config_hash     TEXT    NOT NULL,
    day             INTEGER NOT NULL,
    signal          TEXT    NOT NULL,
    result          TEXT    NOT NULL,
    created_at      INTEGER NOT NULL,
    UNIQUE (strategy_id, day)
);
CREATE TABLE IF NOT EXISTS strategy_backtests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT    NOT NULL,
    config_version  INTEGER NOT NULL,
    config_hash     TEXT    NOT NULL,
    period          TEXT    NOT NULL CHECK (period IN ('design')),
    prereg_sha      TEXT    NOT NULL,
    result          TEXT    NOT NULL,
    created_at      INTEGER NOT NULL,
    UNIQUE (strategy_id, config_hash, period)
);
CREATE TABLE IF NOT EXISTS strategy_forward_tests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id  TEXT    NOT NULL,
    config_hash  TEXT    NOT NULL,
    config       TEXT    NOT NULL,
    fingerprint  TEXT    NOT NULL,
    start_day    INTEGER NOT NULL,
    created_at   INTEGER NOT NULL,
    UNIQUE (strategy_id, config_hash)
);
CREATE TABLE IF NOT EXISTS strategy_forward_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id     INTEGER NOT NULL REFERENCES strategy_forward_tests (id),
    day         INTEGER NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('buy', 'mark')),
    detail      TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    UNIQUE (test_id, day, kind)
);
CREATE TRIGGER IF NOT EXISTS strategy_configs_no_update BEFORE UPDATE ON strategy_configs
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_configs_no_delete BEFORE DELETE ON strategy_configs
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_signals_no_update BEFORE UPDATE ON strategy_signals
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_signals_no_delete BEFORE DELETE ON strategy_signals
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_backtests_no_update BEFORE UPDATE ON strategy_backtests
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_backtests_no_delete BEFORE DELETE ON strategy_backtests
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_forward_tests_no_update BEFORE UPDATE ON strategy_forward_tests
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_forward_tests_no_delete BEFORE DELETE ON strategy_forward_tests
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_forward_events_no_update BEFORE UPDATE ON strategy_forward_events
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_forward_events_no_delete BEFORE DELETE ON strategy_forward_events
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;

-- A forward test's fingerprint may be re-based ONCE the tracking code is restructured, and only while the test has not
-- processed any day yet (the trigger refuses it afterwards). The test row itself is never changed.
CREATE TABLE IF NOT EXISTS strategy_forward_rebases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id          INTEGER NOT NULL REFERENCES strategy_forward_tests (id),
    old_fingerprint  TEXT    NOT NULL,
    new_fingerprint  TEXT    NOT NULL,
    reason           TEXT    NOT NULL,
    created_at       INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS strategy_forward_rebases_only_before_start BEFORE INSERT ON strategy_forward_rebases
    WHEN EXISTS (SELECT 1 FROM strategy_forward_events WHERE test_id = NEW.test_id)
    BEGIN SELECT RAISE(ABORT, 'a forward test can only be re-based before its first processed day'); END;
CREATE TRIGGER IF NOT EXISTS strategy_forward_rebases_no_update BEFORE UPDATE ON strategy_forward_rebases
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS strategy_forward_rebases_no_delete BEFORE DELETE ON strategy_forward_rebases
    BEGIN SELECT RAISE(ABORT, 'strategy lab rows are append-only'); END;

-- A stored sweep event is never changed or deleted; a later finding (e.g. a duplicate of the same move) is recorded
-- here, once per event, and every reader excludes superseded events.
CREATE TABLE IF NOT EXISTS sweep_corrections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL UNIQUE REFERENCES sweep_events (id),
    action        TEXT    NOT NULL CHECK (action IN ('superseded')),
    reason        TEXT    NOT NULL,
    corrected_at  INTEGER NOT NULL,
    by_version    TEXT    NOT NULL
);
CREATE TRIGGER IF NOT EXISTS sweep_corrections_no_update BEFORE UPDATE ON sweep_corrections
    BEGIN SELECT RAISE(ABORT, 'sweep corrections are append-only'); END;
CREATE TRIGGER IF NOT EXISTS sweep_corrections_no_delete BEFORE DELETE ON sweep_corrections
    BEGIN SELECT RAISE(ABORT, 'sweep corrections are append-only'); END;
CREATE TRIGGER IF NOT EXISTS regime_changes_no_update BEFORE UPDATE ON regime_changes
    BEGIN SELECT RAISE(ABORT, 'advanced analysis rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS regime_changes_no_delete BEFORE DELETE ON regime_changes
    BEGIN SELECT RAISE(ABORT, 'advanced analysis rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS sweep_events_no_update BEFORE UPDATE ON sweep_events
    BEGIN SELECT RAISE(ABORT, 'advanced analysis rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS sweep_events_no_delete BEFORE DELETE ON sweep_events
    BEGIN SELECT RAISE(ABORT, 'advanced analysis rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS sweep_updates_no_update BEFORE UPDATE ON sweep_updates
    BEGIN SELECT RAISE(ABORT, 'advanced analysis rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS sweep_updates_no_delete BEFORE DELETE ON sweep_updates
    BEGIN SELECT RAISE(ABORT, 'advanced analysis rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_context_no_update BEFORE UPDATE ON shadow_context
    BEGIN SELECT RAISE(ABORT, 'advanced analysis rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_context_no_delete BEFORE DELETE ON shadow_context
    BEGIN SELECT RAISE(ABORT, 'advanced analysis rows are append-only'); END;
"""
DEFAULT_HOLDOUT_START = 1_761_696_000_000   # 2025-10-29 00:00 UTC = services.backtesting.HOLDOUT_START (asserted in tests)


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
                  ("source_health", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
                  ("holdout_runs", "holdout_start", f"INTEGER NOT NULL DEFAULT {DEFAULT_HOLDOUT_START}"),
                  ("holdout_runs", "verdict", "TEXT CHECK (verdict IN ('PASS', 'FAIL'))"),
                  ("holdout_runs", "criteria", "TEXT")]
    # Holdout runs are write-once (0.9.4): a stored run - above all its verdict - can never be changed or deleted, so
    # nothing done later (new pass criteria, a re-evaluation) can turn a spent FAIL into a PASS. The only allowed
    # update is linking the stored backtest run once (backtest_run_id NULL -> id).
    WRITE_ONCE_TRIGGERS = [
        """CREATE TRIGGER IF NOT EXISTS holdout_runs_no_update BEFORE UPDATE OF created_at, hypothesis_hash, config_hash,
               engine, settings, result, counts_for_validation, holdout_start, verdict, criteria ON holdout_runs
           BEGIN SELECT RAISE(ABORT, 'holdout runs are write-once'); END""",
        """CREATE TRIGGER IF NOT EXISTS holdout_runs_link_once BEFORE UPDATE OF backtest_run_id ON holdout_runs
           WHEN OLD.backtest_run_id IS NOT NULL
           BEGIN SELECT RAISE(ABORT, 'holdout runs are write-once'); END""",
        """CREATE TRIGGER IF NOT EXISTS holdout_runs_no_delete BEFORE DELETE ON holdout_runs
           BEGIN SELECT RAISE(ABORT, 'holdout runs are write-once'); END""",
    ]

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
            # One counting holdout run per holdout period. A 0.9.2 database may already hold two counting runs in one
            # period (it allowed one per rule set); the index is then skipped - data is never rewritten - and the
            # EARLIEST counting run is the one that spent the period (holdout_state).
            dup = conn.execute("SELECT 1 FROM holdout_runs WHERE counts_for_validation = 1 "
                               "GROUP BY holdout_start HAVING COUNT(*) > 1 LIMIT 1").fetchone()
            if dup is None:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_holdout_spent ON holdout_runs (holdout_start) "
                             "WHERE counts_for_validation = 1")
            for trigger in self.WRITE_ONCE_TRIGGERS:
                conn.execute(trigger)
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
                             settings: dict, result: dict, backtest_run_id: int | None,
                             holdout_start: int = DEFAULT_HOLDOUT_START, verdict: str | None = None,
                             criteria: dict | None = None) -> dict:
        """Record a holdout run. One statement decides atomically whether it is the first run in this holdout
        PERIOD, whatever its rule set: only that run counts for validation, and it spends the period (0.9.3).
        `verdict` (PASS / FAIL) and `criteria` (thresholds + each criterion's value) are the run-time outcome; the
        row is write-once, so they are never recalculated (0.9.4).
        Returns {"id", "counts_for_validation", "first_run_id", "spender"} - spender is the run that spent it."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """INSERT INTO holdout_runs (created_at, hypothesis_hash, config_hash, engine, settings, result,
                                             backtest_run_id, holdout_start, verdict, criteria, counts_for_validation)
                   VALUES (?,?,?,?,?,?,?,?,?,?, NOT EXISTS (SELECT 1 FROM holdout_runs
                                                            WHERE holdout_start=? AND counts_for_validation=1)
                                                AND NOT EXISTS (SELECT 1 FROM holdout_contaminations
                                                                WHERE holdout_start=?))""",
                (created_at, hypothesis, config_hash, engine, json.dumps(settings), json.dumps(result),
                 backtest_run_id, holdout_start, verdict, None if criteria is None else json.dumps(criteria),
                 holdout_start, holdout_start))
            rid = int(cur.lastrowid)
            row = conn.execute("SELECT counts_for_validation FROM holdout_runs WHERE id=?", (rid,)).fetchone()
            spender = self._spender(conn, holdout_start)
            contamination = self._contamination(conn, holdout_start)
        return {"id": rid, "counts_for_validation": bool(row[0]), "first_run_id": spender["id"] if spender else None,
                "spender": spender, "contamination": contamination}

    @staticmethod
    def _contamination(conn, holdout_start: int) -> dict | None:
        row = conn.execute("SELECT * FROM holdout_contaminations WHERE holdout_start=?", (holdout_start,)).fetchone()
        return dict(row) if row else None

    def holdout_contamination(self, holdout_start: int) -> dict | None:
        with self.connect() as conn:
            return self._contamination(conn, holdout_start)

    def mark_holdout_contaminated(self, holdout_start: int, reason: str, seen_via_run_id: int | None, seen_at: int,
                                  created_at: int) -> bool:
        """Record once that a holdout period's results were seen before a holdout run. Returns True if new."""
        with self.connect() as conn:
            cur = conn.execute("INSERT OR IGNORE INTO holdout_contaminations (holdout_start, reason, seen_via_run_id, "
                               "seen_at, created_at) VALUES (?,?,?,?,?)",
                               (holdout_start, reason, seen_via_run_id, seen_at, created_at))
            return cur.rowcount == 1

    @staticmethod
    def _spender(conn, holdout_start: int) -> dict | None:
        """The run that spent a holdout period: its EARLIEST counting run (a 0.9.2 database may hold several)."""
        row = conn.execute("""SELECT id, created_at, backtest_run_id, hypothesis_hash, verdict, result FROM holdout_runs
                              WHERE holdout_start=? AND counts_for_validation=1 ORDER BY id LIMIT 1""",
                           (holdout_start,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["verdict"] = Database._verdict(out.pop("verdict"), out.pop("result"))
        return out

    @staticmethod
    def _verdict(verdict: str | None, result_json: str | None) -> str | None:
        """The verdict stored at run time. Rows from 0.9.2/0.9.3 have no verdict column value: their result JSON,
        written at run time, holds all_criteria_passed. Nothing is recalculated from criteria."""
        if verdict:
            return verdict
        passed = json.loads(result_json or "{}").get("all_criteria_passed")
        return None if passed is None else ("PASS" if passed else "FAIL")

    def holdout_state(self, holdout_start: int) -> dict:
        """Status of one holdout period: untouched, or spent on <date> by run #N; plus the runs made in it."""
        with self.connect() as conn:
            spender = self._spender(conn, holdout_start)
            contamination = self._contamination(conn, holdout_start)
            row = conn.execute("""SELECT COUNT(*) made, COALESCE(SUM(counts_for_validation), 0) counted
                                  FROM holdout_runs WHERE holdout_start=?""", (holdout_start,)).fetchone()
        out = {"start": holdout_start, "status": "spent" if spender else "untouched",
               "made": int(row["made"]), "counted": int(row["counted"]),
               "spent_at": None, "spent_by_run": None, "spent_by_holdout_run": None, "verdict": None,
               "contamination": contamination}
        if spender:
            out.update(spent_at=spender["created_at"], spent_by_holdout_run=spender["id"],
                       spent_by_run=spender["backtest_run_id"], verdict=spender["verdict"])
        return out

    def link_holdout_run(self, holdout_id: int, backtest_run_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE holdout_runs SET backtest_run_id=? WHERE id=? AND backtest_run_id IS NULL",
                         (backtest_run_id, holdout_id))

    # ---------------------------------------------------------------- forward paper tests (append-only)
    def forward_start(self, tests: list[dict], created_at: int) -> list[dict]:
        """Register rule sets once (name is unique; an existing one is never changed)."""
        with self.connect() as conn:
            for t in tests:
                conn.execute("INSERT OR IGNORE INTO forward_tests (name, rules, fingerprint, start_day, created_at) "
                             "VALUES (?,?,?,?,?)", (t["name"], json.dumps(t["rules"]), t["fingerprint"],
                                                    t["start_day"], created_at))
        return self.forward_tests()

    def forward_tests(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM forward_tests ORDER BY id").fetchall()
        return [{**dict(r), "rules": json.loads(r["rules"])} for r in rows]

    def forward_add_events(self, rows: list[dict]) -> int:
        """Append events; one per (test, day, kind) - reprocessing a day adds nothing. Returns the number added."""
        added = 0
        with self.connect() as conn:
            for r in rows:
                cur = conn.execute("INSERT OR IGNORE INTO forward_events (test_id, day, kind, in_market, price, "
                                   "equity, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                                   (r["test_id"], r["day"], r["kind"], int(r["in_market"]), r.get("price"),
                                    r.get("equity"), json.dumps(r.get("detail") or {}), r["created_at"]))
                added += cur.rowcount
        return added

    def forward_events(self, name: str) -> list[dict]:
        """Events of one rule set in processing order: per day fill, then mark, then decision."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT e.* FROM forward_events e JOIN forward_tests t ON t.id = e.test_id
                                    WHERE t.name = ? ORDER BY e.day, CASE e.kind WHEN 'fill' THEN 0
                                    WHEN 'mark' THEN 1 ELSE 2 END""", (name,)).fetchall()
        return [{**dict(r), "in_market": bool(r["in_market"]), "detail": json.loads(r["detail"])} for r in rows]

    # ---------------------------------------------------------------- advanced analysis (append-only, shadow mode)
    def regime_add_change(self, row: dict, now: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("INSERT OR IGNORE INTO regime_changes (timeframe, at, regime, previous, confidence, "
                               "detail, created_at) VALUES (?,?,?,?,?,?,?)",
                               (row["timeframe"], row["at"], row["regime"], row.get("previous"), row.get("confidence"),
                                json.dumps(row.get("detail") or {}), now))
        return cur.rowcount == 1

    def regime_changes(self, limit: int = 20, timeframe: str = "4h") -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM regime_changes WHERE timeframe=? ORDER BY at DESC LIMIT ?",
                                (timeframe, limit)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def sweep_add(self, event: dict, updates: list[dict], now: int) -> tuple[int, int]:
        """Store a sweep event once and each of its status updates once. Returns (events added, updates added)."""
        with self.connect() as conn:
            ev = conn.execute("INSERT OR IGNORE INTO sweep_events (event_key, timeframe, direction, level, equal_levels, "
                              "swing_time, event_time, sweep_extreme, reclaim_price, volume_ratio, created_at) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (event["key"], event["timeframe"], event["direction"], event["level"],
                               event["equal_levels"], event["swing_time"], event["event_time"], event["sweep_extreme"],
                               event["reclaim_price"], event.get("volume_ratio"), now)).rowcount
            up = 0
            for u in updates:
                up += conn.execute("INSERT OR IGNORE INTO sweep_updates (event_key, status, at, strength, factors, "
                                   "note, created_at) VALUES (?,?,?,?,?,?,?)",
                                   (event["key"], u["status"], u["at"], u.get("strength"),
                                    json.dumps(u.get("factors") or {}), u.get("note"), now)).rowcount
        return ev, up

    def sweep_events(self, limit: int = 20, include_superseded: bool = False, since: int | None = None) -> list[dict]:
        """Newest events first, each with its stored status updates in order. Superseded events (sweep_corrections)
        are left out; include_superseded=True is for audits only. since: event_time >= since (ms)."""
        where = "" if include_superseded else \
            " WHERE NOT EXISTS (SELECT 1 FROM sweep_corrections c WHERE c.event_id = e.id AND c.action = 'superseded')"
        if since is not None:
            where += (" AND" if where else " WHERE") + " e.event_time >= ?"
        with self.connect() as conn:
            evs = conn.execute(f"SELECT e.* FROM sweep_events e{where} ORDER BY e.event_time DESC, e.id DESC LIMIT ?",
                               (*(() if since is None else (since,)), limit)).fetchall()
            out = []
            for e in evs:
                ups = conn.execute("SELECT status, at, strength, factors, note FROM sweep_updates WHERE event_key=? "
                                   "ORDER BY at, id", (e["event_key"],)).fetchall()
                out.append({**dict(e), "updates": [{**dict(u), "factors": json.loads(u["factors"])} for u in ups]})
        return out

    def sweep_correct(self, event_key: str, action: str, reason: str, by_version: str, now: int) -> bool:
        """Append a correction for a stored event (once per event). The event row itself is never changed."""
        with self.connect() as conn:
            ev = conn.execute("SELECT id FROM sweep_events WHERE event_key = ?", (event_key,)).fetchone()
            if ev is None:
                raise LookupError(f"No stored sweep event {event_key}")
            cur = conn.execute("INSERT OR IGNORE INTO sweep_corrections (event_id, action, reason, corrected_at, "
                               "by_version) VALUES (?,?,?,?,?)", (ev["id"], action, reason, now, by_version))
        return cur.rowcount == 1

    def superseded_sweep_keys(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT e.event_key FROM sweep_corrections c JOIN sweep_events e ON e.id = c.event_id "
                                "WHERE c.action = 'superseded'").fetchall()
        return {r["event_key"] for r in rows}

    def shadow_add(self, row: dict, now: int) -> bool:
        cols = ("close_time", "regime", "regime_confidence", "regime_score", "sweep_score", "vp_score", "poc", "vah",
                "val", "live_signal", "live_setup", "live_score", "live_engine", "weights", "combined_score", "detail")
        vals = [json.dumps(row.get(c) or {}) if c in ("weights", "detail") else row.get(c) for c in cols]
        with self.connect() as conn:
            cur = conn.execute(f"INSERT OR IGNORE INTO shadow_context ({', '.join(cols)}, created_at) "
                               f"VALUES ({', '.join('?' * (len(cols) + 1))})", (*vals, now))
        return cur.rowcount == 1

    def shadow_rows(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM shadow_context ORDER BY close_time DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "weights": json.loads(r["weights"]), "detail": json.loads(r["detail"])} for r in rows]

    def shadow_last_time(self) -> int | None:
        with self.connect() as conn:
            r = conn.execute("SELECT MAX(close_time) AS t FROM shadow_context").fetchone()
        return r["t"] if r else None

    # ---------------------------------------------------------------- Strategy Lab (append-only)
    def strategy_config_add(self, strategy_id: str, config: dict, config_hash: str, reason: str, now: int) -> int:
        """Append a new configuration version; returns the version number."""
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM strategy_configs WHERE strategy_id=?",
                               (strategy_id,)).fetchone()
            version = int(row["v"]) + 1
            conn.execute("INSERT INTO strategy_configs (strategy_id, version, config, config_hash, reason, created_at) "
                         "VALUES (?,?,?,?,?,?)", (strategy_id, version, json.dumps(config), config_hash, reason, now))
        return version

    def strategy_configs(self, strategy_id: str) -> list[dict]:
        """All configuration versions, newest first."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM strategy_configs WHERE strategy_id=? ORDER BY version DESC",
                                (strategy_id,)).fetchall()
        return [{**dict(r), "config": json.loads(r["config"])} for r in rows]

    def strategy_signal_add(self, strategy_id: str, config_version: int, config_hash: str, day: int, signal: str,
                            result: dict, now: int) -> bool:
        """One stored signal per strategy and UTC day (the first evaluation of the day)."""
        with self.connect() as conn:
            cur = conn.execute("INSERT OR IGNORE INTO strategy_signals (strategy_id, config_version, config_hash, day, "
                               "signal, result, created_at) VALUES (?,?,?,?,?,?,?)",
                               (strategy_id, config_version, config_hash, day, signal, json.dumps(result), now))
        return cur.rowcount == 1

    def strategy_signals(self, strategy_id: str, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM strategy_signals WHERE strategy_id=? ORDER BY day DESC LIMIT ?",
                                (strategy_id, limit)).fetchall()
        return [{**dict(r), "result": json.loads(r["result"])} for r in rows]

    def strategy_backtest_add(self, strategy_id: str, config_version: int, config_hash: str, period: str,
                              prereg_sha: str, result: dict, now: int) -> int:
        """One design backtest per strategy and configuration: a second one raises IntegrityError."""
        with self.connect() as conn:
            cur = conn.execute("INSERT INTO strategy_backtests (strategy_id, config_version, config_hash, period, "
                               "prereg_sha, result, created_at) VALUES (?,?,?,?,?,?,?)",
                               (strategy_id, config_version, config_hash, period, prereg_sha,
                                json.dumps(result, default=float), now))
        return int(cur.lastrowid)

    def strategy_backtests(self, strategy_id: str, config_hash: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM strategy_backtests WHERE strategy_id=?", [strategy_id]
        if config_hash is not None:
            sql, args = sql + " AND config_hash=?", args + [config_hash]
        with self.connect() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC", args).fetchall()
        return [{**dict(r), "result": json.loads(r["result"])} for r in rows]

    def strategy_forward_start(self, strategy_id: str, config_hash: str, config: dict, fingerprint: str,
                               start_day: int, now: int) -> dict:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO strategy_forward_tests (strategy_id, config_hash, config, fingerprint, "
                         "start_day, created_at) VALUES (?,?,?,?,?,?)",
                         (strategy_id, config_hash, json.dumps(config), fingerprint, start_day, now))
        return self.strategy_forward_test(strategy_id, config_hash)

    def strategy_forward_test(self, strategy_id: str, config_hash: str) -> dict | None:
        with self.connect() as conn:
            r = conn.execute("SELECT * FROM strategy_forward_tests WHERE strategy_id=? AND config_hash=?",
                             (strategy_id, config_hash)).fetchone()
        return {**dict(r), "config": json.loads(r["config"])} if r else None

    def strategy_forward_tests(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM strategy_forward_tests ORDER BY id").fetchall()
        return [{**dict(r), "config": json.loads(r["config"])} for r in rows]

    def strategy_forward_rebase(self, test_id: int, old_fingerprint: str, new_fingerprint: str, reason: str,
                                now: int) -> int:
        """Record a fingerprint re-base (refused by the database once the test processed a day)."""
        with self.connect() as conn:
            cur = conn.execute("INSERT INTO strategy_forward_rebases (test_id, old_fingerprint, new_fingerprint, reason, "
                               "created_at) VALUES (?,?,?,?,?)", (test_id, old_fingerprint, new_fingerprint, reason, now))
        return int(cur.lastrowid)

    def strategy_forward_rebases(self, test_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM strategy_forward_rebases WHERE test_id=? ORDER BY id", (test_id,)).fetchall()
        return [dict(r) for r in rows]

    def strategy_forward_add_events(self, rows: list[dict], now: int) -> int:
        added = 0
        with self.connect() as conn:
            for r in rows:
                added += conn.execute("INSERT OR IGNORE INTO strategy_forward_events (test_id, day, kind, detail, "
                                      "created_at) VALUES (?,?,?,?,?)",
                                      (r["test_id"], r["day"], r["kind"], json.dumps(r["detail"]), now)).rowcount
        return added

    def strategy_forward_events(self, test_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM strategy_forward_events WHERE test_id=? ORDER BY day, "
                                "CASE kind WHEN 'buy' THEN 0 ELSE 1 END", (test_id,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def holdout_runs(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM holdout_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "settings": json.loads(r["settings"]), "result": json.loads(r["result"]),
                 "criteria": json.loads(r["criteria"]) if r["criteria"] else None,
                 "verdict": self._verdict(r["verdict"], r["result"]),
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
