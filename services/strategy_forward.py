"""Strategy forward tracker (Strategy Lab): the only way a strategy's status can become "forward tested".

Separate from services/forward_tests.py (H1/H2): own tables (strategy_forward_tests / strategy_forward_events, both
append-only), own fingerprint (strategy_fingerprint: base.py + the strategy module + this file), own start dates. A
strategy starts the next UTC midnight after the background monitor first registers it, i.e. the day after it is
merged and the app runs, with its PRE-REGISTERED configuration only. The 2025-10-29 holdout is never used.

Processing: closed Binance BTCUSDT daily candles from the start day, in order, each day once; a missing day stops
processing until it is stored. After target_days the pre-registered forward criteria give a fixed verdict.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

import numpy as np

from database import now_ms
from services.strategies import dca
from services.strategies.base import STRATEGY_DIR

DAY = 86_400_000
TARGET_DAYS = 365


def strategy_fingerprint(strategy_id: str) -> str:
    """SHA-256 of the shared interface, the strategy's module and this tracker (line endings normalised)."""
    h = hashlib.sha256()
    for p in (STRATEGY_DIR / "base.py", STRATEGY_DIR / f"{strategy_id}.py", Path(__file__)):
        h.update(p.name.encode() + b"\0" + p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def _max_dd(values: list[float]) -> float:
    if not values:
        return 0.0
    v = np.asarray(values, dtype=float)
    peak = np.maximum.accumulate(v)
    return float(np.max((peak - v) / peak)) * 100


class StrategyForward:
    def __init__(self, db, clock: Callable[[], int] = now_ms, candles: Callable[[], list[dict]] | None = None):
        """candles() -> stored closed Binance BTCUSDT daily candles (no network)."""
        self.db = db
        self.clock = clock
        self.candles = candles or (lambda: db.get_candles_range("binance", "BTCUSDT", "1d"))

    def ensure_started(self, strategy_id: str, config: dict, config_hash: str) -> dict:
        """Register once: start = the next UTC midnight; an existing start is never moved."""
        existing = self.db.strategy_forward_test(strategy_id, config_hash)
        if existing:
            return existing
        start = (self.clock() // DAY + 1) * DAY
        return self.db.strategy_forward_start(strategy_id, config_hash, config, strategy_fingerprint(strategy_id),
                                              start, self.clock())

    # ---------------------------------------------------------------- processing
    def process(self) -> int:
        added = 0
        rows = None
        for t in self.db.strategy_forward_tests():
            if t["strategy_id"] != "dca":
                continue                     # later rounds add their own daily step
            rows = rows if rows is not None else sorted(self.candles(), key=lambda r: r["open_time"])
            added += self._process_dca(t, rows)
        return added

    def _process_dca(self, t: dict, rows: list[dict]) -> int:
        cfg = dca.validate_config(t["config"])
        start, now = t["start_day"], self.clock()
        end = start + (TARGET_DAYS - 1) * DAY
        events = self.db.strategy_forward_events(t["id"])
        marks = [e for e in events if e["kind"] == "mark"]
        by_day = {int(r["open_time"]): r for r in rows if r["close_time"] < now}
        budget = cfg["amount_usd"] * len(dca.scheduled_days(start, end, cfg))
        f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
        btc = sum(e["detail"]["btc"] for e in events if e["kind"] == "buy")
        invested = sum(e["detail"]["usd"] for e in events if e["kind"] == "buy")
        lump_btc = marks[0]["detail"]["lump_btc"] if marks else None
        day = marks[-1]["day"] + DAY if marks else start
        new = []
        while day <= end and day in by_day:
            r = by_day[day]
            if lump_btc is None:
                lump_btc = budget * (1 - f) / (float(r["open"]) * (1 + s))
            if dca.is_buy_day(day, cfg):
                usd = cfg["amount_usd"]
                fill = float(r["open"]) * (1 + s)
                got = (usd - usd * f) / fill
                btc, invested = btc + got, invested + usd
                new.append({"test_id": t["id"], "day": day, "kind": "buy",
                            "detail": {"open": float(r["open"]), "fill_price": fill, "usd": usd, "fee_usd": usd * f, "btc": got}})
            close = float(r["close"])
            new.append({"test_id": t["id"], "day": day, "kind": "mark",
                        "detail": {"close": close, "btc": btc, "invested": invested, "budget": budget, "lump_btc": lump_btc,
                                   "dca_equity": (budget - invested) + btc * close * (1 - f - s),
                                   "lump_equity": lump_btc * close * (1 - f - s)}})
            day += DAY
        return self.db.strategy_forward_add_events(new, now) if new else 0

    # ---------------------------------------------------------------- status (read-only)
    def status(self, strategy_id: str, config_hash: str) -> dict:
        t = self.db.strategy_forward_test(strategy_id, config_hash)
        fp_now = strategy_fingerprint(strategy_id)
        if t is None:
            return {"state": "not_started", "target_days": TARGET_DAYS, "fingerprint_now": fp_now, "code_changed": False,
                    "days_elapsed": 0, "verdict": None}
        events = self.db.strategy_forward_events(t["id"])
        marks = [e for e in events if e["kind"] == "mark"][:TARGET_DAYS]
        n = len(marks)
        out = {"state": "done" if n >= TARGET_DAYS else "running" if n else "waiting", "target_days": TARGET_DAYS,
               "start_day": t["start_day"], "days_elapsed": n, "fingerprint": t["fingerprint"],
               "fingerprint_now": fp_now, "code_changed": t["fingerprint"] != fp_now,
               "buys": sum(1 for e in events if e["kind"] == "buy" and e["day"] < t["start_day"] + TARGET_DAYS * DAY),
               "verdict": None, "criteria": [], "dca_return_pct": None, "lump_return_pct": None,
               "dca_max_dd_pct": None, "lump_max_dd_pct": None, "waiting_for": None}
        next_day = t["start_day"] + n * DAY
        if n < TARGET_DAYS:
            out["waiting_for"] = (f"waiting for the daily candle of {_date(next_day)} (closes {_date(next_day + DAY)} "
                                  "00:00 UTC)")
        if marks:
            d0 = marks[-1]["detail"]
            dca_eq = [m["detail"]["dca_equity"] for m in marks]
            lump_eq = [m["detail"]["lump_equity"] for m in marks]
            out.update(dca_return_pct=(dca_eq[-1] / d0["budget"] - 1) * 100,
                       lump_return_pct=(lump_eq[-1] / d0["budget"] - 1) * 100,
                       dca_max_dd_pct=_max_dd(dca_eq), lump_max_dd_pct=_max_dd(lump_eq))
        if n >= TARGET_DAYS:
            passed = out["dca_max_dd_pct"] < out["lump_max_dd_pct"]
            out["criteria"] = [{"id": "F1", "rule": "max drawdown over the 365 forward days: DCA < lump sum",
                                "passed": passed, "dca": out["dca_max_dd_pct"], "lump_sum": out["lump_max_dd_pct"]}]
            out["verdict"] = "PASS" if passed else "FAIL"
        return out


def _date(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")
