"""Strategy forward tracker (Strategy Lab): the only way a strategy's status can become "forward tested".

Separate from services/forward_tests.py (H1/H2): own tables (strategy_forward_tests / strategy_forward_events /
strategy_forward_rebases, all append-only), own per-strategy fingerprint (the strategy's module + this generic core),
own start dates. A strategy starts the next UTC midnight after the background monitor first registers it, i.e. the day
after it is merged and the app runs, with its PRE-REGISTERED configuration only. The 2025-10-29 holdout is never used.

This file is the generic core (since round 2): it walks each test's closed candles of the strategy's
FORWARD_TIMEFRAME in order from the start, each bar once, and stops at a missing bar until it is stored. What happens
on a bar is the strategy's own forward_step() (the same per-bar logic as its backtest); its forward_summary() gives the
numbers and, after 365 days, the fixed pre-registered verdict.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config import TIMEFRAMES
from database import now_ms
from services.strategies import ENGINES
from services.strategies.base import STRATEGY_DIR

DAY = 86_400_000
TARGET_DAYS = 365


def fingerprint_files(strategy_id: str) -> list[Path]:
    """What a strategy's forward numbers depend on: its own module and this generic core."""
    return [STRATEGY_DIR / f"{strategy_id}.py", Path(__file__)]


def strategy_fingerprint(strategy_id: str) -> str:
    h = hashlib.sha256()
    for p in fingerprint_files(strategy_id):
        h.update(p.name.encode() + b"\0" + p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def _utc(ms: int, fmt: str = "%Y-%m-%d") -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime(fmt)


class StrategyForward:
    def __init__(self, db, clock: Callable[[], int] = now_ms, candles: Callable[[str], list[dict]] | None = None):
        """candles(timeframe) -> stored closed Binance BTCUSDT candles (no network)."""
        self.db = db
        self.clock = clock
        self.candles = candles or (lambda tf: db.get_candles_range("binance", "BTCUSDT", tf))

    def ensure_started(self, strategy_id: str, config: dict, config_hash: str) -> dict:
        """Register once: start = the next UTC midnight; an existing start is never moved."""
        existing = self.db.strategy_forward_test(strategy_id, config_hash)
        if existing:
            return existing
        start = (self.clock() // DAY + 1) * DAY
        return self.db.strategy_forward_start(strategy_id, config_hash, config, strategy_fingerprint(strategy_id),
                                              start, self.clock())

    def effective_fingerprint(self, t: dict) -> tuple[str, dict | None]:
        rebases = self.db.strategy_forward_rebases(t["id"])
        return (rebases[-1]["new_fingerprint"], rebases[-1]) if rebases else (t["fingerprint"], None)

    # ---------------------------------------------------------------- processing
    def process(self) -> int:
        now = self.clock()
        cache: dict[str, list[dict]] = {}

        def rows_for(tf: str) -> list[dict]:
            if tf not in cache:
                cache[tf] = sorted((r for r in self.candles(tf) if r["close_time"] < now), key=lambda r: r["open_time"])
            return cache[tf]

        added = 0
        for t in self.db.strategy_forward_tests():
            eng = ENGINES.get(t["strategy_id"])
            if eng is None or not hasattr(eng, "forward_step"):
                continue
            step = TIMEFRAMES[eng.FORWARD_TIMEFRAME] * 1000
            rows = rows_for(eng.FORWARD_TIMEFRAME)
            index = {int(r["open_time"]): k for k, r in enumerate(rows)}
            cfg = eng.validate_config(t["config"])
            events = self.db.strategy_forward_events(t["id"])
            marks = [e for e in events if e["kind"] == "mark"]
            state = eng.forward_state(events)
            bar = marks[-1]["day"] + step if marks else t["start_day"]
            end = t["start_day"] + TARGET_DAYS * DAY - step
            new = []
            while bar <= end and bar in index:
                evs, state = eng.forward_step(cfg, {"rows": rows, "k": index[bar], "state": state, "test": t,
                                                    "candles": rows_for})
                new += [{"test_id": t["id"], "day": bar, "kind": e["kind"], "detail": e["detail"]} for e in evs]
                bar += step
            added += self.db.strategy_forward_add_events(new, now) if new else 0
        return added

    # ---------------------------------------------------------------- status (read-only)
    def status(self, strategy_id: str, config_hash: str) -> dict:
        eng = ENGINES.get(strategy_id)
        tf = getattr(eng, "FORWARD_TIMEFRAME", "1d")
        step = TIMEFRAMES[tf] * 1000
        target_bars = TARGET_DAYS * DAY // step
        t = self.db.strategy_forward_test(strategy_id, config_hash)
        fp_now = strategy_fingerprint(strategy_id)
        if t is None:
            return {"state": "not_started", "target_days": TARGET_DAYS, "timeframe": tf, "fingerprint_now": fp_now,
                    "code_changed": False, "days_elapsed": 0, "verdict": None, "rebased": None}
        fp, rebase = self.effective_fingerprint(t)
        events = self.db.strategy_forward_events(t["id"])
        marks = [e for e in events if e["kind"] == "mark"]
        n = min(len(marks), target_bars)
        done = n >= target_bars
        window = [e for e in events if e["day"] < t["start_day"] + TARGET_DAYS * DAY]
        out = {"state": "done" if done else "running" if n else "waiting", "target_days": TARGET_DAYS, "timeframe": tf,
               "start_day": t["start_day"], "days_elapsed": n * step // DAY, "bars_processed": n,
               "fingerprint": fp, "fingerprint_registered": t["fingerprint"], "fingerprint_now": fp_now,
               "code_changed": fp != fp_now,
               "rebased": None if rebase is None else {k: rebase[k] for k in ("old_fingerprint", "new_fingerprint",
                                                                              "reason", "created_at")},
               "waiting_for": None}
        if not done:
            nxt = t["start_day"] + n * step
            out["waiting_for"] = (f"waiting for the daily candle of {_utc(nxt)} (closes {_utc(nxt + DAY)} 00:00 UTC)"
                                  if tf == "1d" else f"waiting for the {tf} candle opening {_utc(nxt, '%Y-%m-%d %H:%M')} UTC")
        out.update(eng.forward_summary(window, eng.validate_config(t["config"]), done) if eng else {})
        return out
