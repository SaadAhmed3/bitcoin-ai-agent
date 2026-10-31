"""Forward paper tests (research-1): H1, H2 and buy & hold on NEW daily candles only.

* Simulated only: no real orders, no change to the live signal.
* Started once, at the next UTC midnight after the background monitor first runs this code. The start day and the
  SHA-256 of this file (the rule code) are stored with each rule set and never change.
* Each closed Binance BTCUSDT daily candle from the start day on is processed once, in order:
    1. fill yesterday's decision at today's OPEN (0.10% fee + 0.05% slippage per side),
    2. mark at today's CLOSE at liquidation value,
    3. decide at today's CLOSE (closes before the start are indicator warm-up only).
  A missing day stops processing until it arrives; nothing is skipped or filled in.
* Every decision, fill and mark is appended to the database (forward_events); rows are never updated or deleted.

Rules (PREREGISTRATION_H1.md, PREREGISTRATION_H2.md):
  H1  in the market when the daily close > SMA 200 of daily closes   (failed on design data: information only)
  H2  in the market when EMA 50 > EMA 200 of daily closes             (the pre-registered forward test)
  BH  always in the market                                            (buy & hold baseline)
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from database import Database, now_ms

DAY_MS = 86_400_000
COST = (0.10 + 0.05) / 100              # per side
TARGET_DAYS = 365                       # PREREGISTRATION_H2.md: at least 365 days before a verdict
CRITERIA = {"max_dd_vs_buy_hold": 0.60, "min_random_percentile": 90.0, "random_runs": 1000, "random_seed": 7}


# --------------------------------------------------------------------------- rules (decided at a close)
def _sma200(closes: np.ndarray) -> dict:
    if len(closes) < 200:
        return {"in_market": False, "detail": {"reason": "SMA 200 warm-up", "close": float(closes[-1])}}
    sma = float(np.mean(closes[-200:]))
    return {"in_market": bool(closes[-1] > sma), "detail": {"close": float(closes[-1]), "sma200": round(sma, 2)}}


def _ema50_200(closes: np.ndarray) -> dict:
    s = pd.Series(closes)
    fast = s.ewm(span=50, adjust=False, min_periods=50).mean().iloc[-1]
    slow = s.ewm(span=200, adjust=False, min_periods=200).mean().iloc[-1]
    if pd.isna(fast) or pd.isna(slow):
        return {"in_market": False, "detail": {"reason": "EMA 200 warm-up", "close": float(closes[-1])}}
    return {"in_market": bool(fast > slow), "detail": {"close": float(closes[-1]), "ema50": round(float(fast), 2),
                                                       "ema200": round(float(slow), 2)}}


def _always(closes: np.ndarray) -> dict:
    return {"in_market": True, "detail": {"close": float(closes[-1])}}


RULES: dict[str, Callable[[np.ndarray], dict]] = {"H1": _sma200, "H2": _ema50_200, "BH": _always}
RULE_TEXT = {
    "H1": {"label": "H1: SMA 200 filter", "role": "failed on design data (C3 84.7%) - information only",
           "rule": "in the market when the daily close > SMA 200 of daily closes, else cash"},
    "H2": {"label": "H2: EMA 50/200", "role": "pre-registered forward test (PREREGISTRATION_H2.md)",
           "rule": "in the market when EMA 50 > EMA 200 of daily closes (pandas ewm, adjust=False), else cash"},
    "BH": {"label": "Buy & hold", "role": "baseline", "rule": "always in the market"},
}
COMMON = ("Binance BTCUSDT daily candles; decide at the close, act at the next open; 0.10% fee + 0.05% slippage per "
          "side; 100% in or out; marked at liquidation value")


def current_fingerprint() -> str:
    """SHA-256 of this file (line endings normalised): the rule code a forward test was started with."""
    return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- tracker
class ForwardTests:
    def __init__(self, db: Database, clock: Callable[[], int] = now_ms,
                 candles: Callable[[], list[dict]] | None = None):
        """candles() -> stored Binance BTCUSDT daily candles (closed only), any order. It must not hit the network:
        status() uses it too and is called from a GET request."""
        self.db = db
        self.clock = clock
        self.candles = candles or (lambda: db.get_candles_range("binance", "BTCUSDT", "1d"))

    def ensure_started(self) -> dict:
        """Create the forward tests once, starting at the next UTC midnight. Never moves an existing start."""
        existing = self.db.forward_tests()
        start = existing[0]["start_day"] if existing else (self.clock() // DAY_MS + 1) * DAY_MS
        missing = [n for n in RULES if n not in {t["name"] for t in existing}]
        if missing:
            fp = current_fingerprint()
            self.db.forward_start([{"name": n, "rules": {**RULE_TEXT[n], "common": COMMON}, "fingerprint": fp,
                                    "start_day": start} for n in missing], self.clock())
        return {"start_day": start, "tests": self.db.forward_tests()}

    def _closed_days(self) -> dict[int, dict]:
        now = self.clock()
        return {int(r["open_time"]): r for r in self.candles() if int(r["close_time"]) < now}

    def process(self) -> int:
        """Process every newly closed day, in order. Returns the number of events appended (0 when up to date)."""
        tests = self.db.forward_tests()
        if not tests:
            return 0
        days = self._closed_days()
        order = sorted(days)
        closes = np.array([float(days[d]["close"]) for d in order])
        index = {d: i for i, d in enumerate(order)}
        added = 0
        for t in tests:
            added += self.db.forward_add_events(self._advance(t, days, closes, index))
        return added

    def _advance(self, t: dict, days: dict, closes: np.ndarray, index: dict) -> list[dict]:
        rule, start, now = RULES[t["name"]], t["start_day"], self.clock()
        cash, units, last_mark, decision = 1.0, 0.0, None, {}
        for e in self.db.forward_events(t["name"]):                  # rebuild the state from the appended rows
            if e["kind"] == "fill":
                cash, units = e["detail"]["cash"], e["detail"]["units"]
            elif e["kind"] == "mark":
                last_mark = e["day"]
            else:
                decision[e["day"]] = e["in_market"]
        new, d = [], start if last_mark is None else last_mark + DAY_MS
        while d in days:
            row = days[d]
            want = decision.get(d - DAY_MS) if d > start else None
            if want is not None and want != (units > 0):
                o = float(row["open"])
                if want:
                    cash, units, side = 0.0, cash / (o * (1 + COST)), "BUY"
                else:
                    cash, units, side = units * o * (1 - COST), 0.0, "SELL"
                new.append(self._ev(t, d, "fill", units > 0, o, None,
                                    {"side": side, "units": units, "cash": cash, "decided_on": _utc_date(d - DAY_MS)}))
            c = float(row["close"])
            equity = units * c * (1 - COST) if units > 0 else cash
            new.append(self._ev(t, d, "mark", units > 0, c, equity, {"units": units, "cash": cash}))
            result = rule(closes[: index[d] + 1])
            decision[d] = result["in_market"]
            new.append(self._ev(t, d, "decision", result["in_market"], c, None, result["detail"]))
            d += DAY_MS
        return new

    def _ev(self, t: dict, day: int, kind: str, in_market: bool, price, equity, detail: dict) -> dict:
        return {"test_id": t["id"], "day": day, "kind": kind, "in_market": in_market, "price": price,
                "equity": equity, "detail": detail, "created_at": self.clock()}

    # ---------------------------------------------------------------- status (read-only)
    def status(self) -> dict:
        tests = self.db.forward_tests()
        base = {"target_days": TARGET_DAYS, "common_rules": COMMON, "fingerprint_now": current_fingerprint()}
        if not tests:
            return {**base, "state": "not_started", "strategies": [],
                    "note": "Starts at the next UTC midnight after the app's background monitor first runs."}
        start, now = tests[0]["start_day"], self.clock()
        days = self._closed_days()
        events = {t["name"]: self.db.forward_events(t["name"]) for t in tests}
        marks = {n: [e for e in ev if e["kind"] == "mark"] for n, ev in events.items()}
        elapsed = max(len(m) for m in marks.values())
        nxt = start + elapsed * DAY_MS
        if nxt in days:
            state, waiting = "waiting", f"processing {_utc_date(nxt)} at the next monitor cycle"
        elif now >= nxt + DAY_MS:
            state, waiting = "waiting", f"waiting for the {_utc_date(nxt)} daily candle (not stored yet)"
        elif elapsed == 0:
            state, waiting = "waiting", f"first daily candle {_utc_date(start)} closes at {_utc_date(start + DAY_MS)} 00:00 UTC"
        else:
            state, waiting = "running", None
        # The verdict window is FIXED: processed days 1-365 only (PREREGISTRATION_H2.md); later days never change it.
        end = start + TARGET_DAYS * DAY_MS
        within = {n: [e for e in ev if e["day"] < end] for n, ev in events.items()}
        bh_window = self._summary([e for e in within.get("BH", []) if e["kind"] == "mark"], within.get("BH", []))
        strategies = []
        for t in tests:
            s = self._summary(marks[t["name"]], events[t["name"]])
            s.update(name=t["name"], label=t["rules"]["label"], role=t["rules"]["role"], rule=t["rules"]["rule"],
                     fingerprint_at_start=t["fingerprint"], code_changed=t["fingerprint"] != current_fingerprint())
            if t["name"] in ("H1", "H2"):
                ev = within[t["name"]]
                s["evaluation"] = self._evaluate(self._summary([e for e in ev if e["kind"] == "mark"], ev), bh_window,
                                                 ev, {d: r for d, r in days.items() if d < end}, start)
            strategies.append(s)
        return {**base, "state": state, "waiting_for": waiting, "start_day": start, "start_date": _utc_date(start),
                "days_elapsed": elapsed, "strategies": strategies}

    @staticmethod
    def _summary(marks: list[dict], events: list[dict]) -> dict:
        eq = np.array([m["equity"] for m in marks], dtype=float)
        path = np.concatenate([[1.0], eq])                      # the start value 1.0 counts as the first peak
        decisions = [e for e in events if e["kind"] == "decision"]
        last = decisions[-1] if decisions else None
        position = bool(marks[-1]["in_market"]) if marks else False
        out = {"days": len(marks), "position": "in" if position else "out",
               "net_return_pct": round(float(eq[-1] - 1) * 100, 2) if len(eq) else None,
               "max_drawdown_pct": round(float(np.max(1 - path / np.maximum.accumulate(path))) * 100, 2) if len(eq) else None,
               "trades": sum(1 for e in events if e["kind"] == "fill" and e["detail"].get("side") == "BUY"),
               "time_in_market_pct": round(float(np.mean([m["in_market"] for m in marks])) * 100, 1) if marks else None,
               "last_decision": ({"date": _utc_date(last["day"]), "in_market": last["in_market"], "detail": last["detail"]}
                                 if last else None)}
        out["pending"] = (("buy at the next open" if last["in_market"] else "sell at the next open")
                          if last and last["in_market"] != position else None)
        return out

    def _evaluate(self, s: dict, bh: dict, events: list[dict], days: dict, start: int) -> dict:
        """The pre-registered criteria (same as research H1) on the first TARGET_DAYS processed days only."""
        n = s["days"]
        if n < TARGET_DAYS:
            return {"verdict": "NOT YET", "days": n, "detail": f"{n} of {TARGET_DAYS} days"}
        window = {"first_day": _utc_date(start), "last_day": _utc_date(start + (TARGET_DAYS - 1) * DAY_MS)}
        return {**self._verdict(s, bh, events, days, start), "days": n, "window": window}

    def _verdict(self, s: dict, bh: dict, events: list[dict], days: dict, start: int) -> dict:
        n = s["days"]
        fills = [e for e in events if e["kind"] == "fill"]
        if not fills or s["max_drawdown_pct"] in (None, 0) or bh["max_drawdown_pct"] in (None, 0):
            return {"verdict": "INCONCLUSIVE", "days": n, "detail": "no position change or no drawdown to compare"}
        pct = self._random_percentile(s["net_return_pct"], fills, days, start)
        if pct is None:
            return {"verdict": "INCONCLUSIVE", "days": n, "detail": "no random-entry comparison possible"}
        limit = CRITERIA["max_dd_vs_buy_hold"] * bh["max_drawdown_pct"]
        ratio, bh_ratio = s["net_return_pct"] / s["max_drawdown_pct"], bh["net_return_pct"] / bh["max_drawdown_pct"]
        rows = [
            {"name": "Drawdown at most 60% of buy & hold", "passed": s["max_drawdown_pct"] <= limit,
             "detail": f"{s['max_drawdown_pct']:.2f}% vs limit {limit:.2f}%"},
            {"name": "Return/drawdown at least buy & hold's", "passed": ratio >= bh_ratio,
             "detail": f"{ratio:.3f} vs {bh_ratio:.3f}"},
            {"name": "Beats >= 90% of random entries", "passed": pct >= CRITERIA["min_random_percentile"],
             "detail": f"better than {pct:.1f}% of runs"},
        ]
        return {"verdict": "PASS" if all(r["passed"] for r in rows) else "FAIL", "days": n, "criteria": rows}

    @staticmethod
    def _random_percentile(net_pct: float, fills: list[dict], days: dict, start: int) -> float | None:
        """Same trade count and holding days placed at random (non-overlapping) within the forward period."""
        period = [d for d in sorted(days) if d >= start]
        if len(period) < 2:
            return None
        o = np.array([float(days[d]["open"]) for d in period])
        c = np.array([float(days[d]["close"]) for d in period])
        holds, entry = [], None
        for f in fills:
            if f["detail"]["side"] == "BUY":
                entry = period.index(f["day"])
            elif entry is not None:
                holds.append(period.index(f["day"]) - entry)
                entry = None
        if entry is not None:
            holds.append(len(period) - entry)
        first_i, last_i = 1, len(period) - 1
        span, total = last_i - first_i + 1, sum(holds)
        if not holds or total >= span:
            return None
        rng, results = random.Random(CRITERIA["random_seed"]), []
        for _ in range(CRITERIA["random_runs"]):
            hs = holds[:]
            rng.shuffle(hs)
            cuts = sorted(rng.randint(0, span - total) for _ in hs)
            eq, pos, prev = 1.0, first_i, 0
            for cut, h in zip(cuts, hs):
                pos += cut - prev
                prev = cut
                x = pos + h
                sell = o[x] * (1 - COST) if x <= last_i else c[last_i] * (1 - COST)
                eq *= sell / (o[pos] * (1 + COST))
                pos = x
            results.append((eq - 1) * 100)
        return round(float(np.mean(np.array(results) < net_pct)) * 100, 1)
