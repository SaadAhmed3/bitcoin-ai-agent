"""Advanced market analysis: regime, liquidity sweeps and volume profile - display + SHADOW mode.

Phase 1 is display-only: the three features are shown on Market & chart and as context next to the signal
("Context, not part of the score"). SHADOW mode computes three components, stores them once per closed 4h candle
next to the live signal and a combined "what if" score, and NEVER feeds them back: the live engine (signal-1.8), its
score, weights, thresholds and NO TRADE gates are untouched. Whether any of this may ever enter the score is a
separate, pre-registered research question.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from typing import Any, Callable

import pandas as pd

from config import TIMEFRAMES
from database import now_ms
from services.chart_overlays import RegimeCache, regime_bars, regime_segments, regime_transitions, sweep_markers
from services.liquidity_sweeps import ACTIVE, SweepConfig, detect_sweeps, status_at, sweep_score
from services.regime import classify, regime_history, regime_score
from services.volume_profile import LOOKBACK, ProfileConfig, volume_profile, vp_score

log = logging.getLogger(__name__)

COMPONENTS = ("regime", "liquidity_sweep", "volume_profile")
NOTE = "Context, not part of the score"
SHADOW_NOTE = ("Shadow mode: the three components are computed, shown and stored next to the live signal. They never "
               "change the live signal, its score, weights, thresholds or NO TRADE gates (signal-1.8).")
PROFILE_TIMEFRAMES = tuple(LOOKBACK)
SWEEP_BARS = 300                 # 4h candles scanned for sweeps (50 days)
H4_MS = TIMEFRAMES["4h"] * 1000


def combined_score(live_score: float | None, comps: dict[str, float], weights: dict[str, float]) -> float | None:
    """(1 - sum(w)) x live score + sum(w_k x component_k). A research number only: nothing acts on it."""
    if live_score is None:
        return None
    w = {k: float(weights.get(k, 0.0)) for k in COMPONENTS}
    return (1 - sum(w.values())) * float(live_score) + sum(w[k] * float(comps.get(k, 0.0)) for k in COMPONENTS)


# Corrections to stored sweep events (append-only, sweep_corrections). The event rows stay as they were written.
SWEEP_CORRECTIONS = [
    ("4h:buy_side:1793289600000", "superseded", "duplicate of the same move (open-event rule, 0.9.8 follow-up)"),
]


def apply_sweep_corrections(db, by_version: str, now: int) -> int:
    """Record each known correction once, for events that are stored here. Returns the number added."""
    added = 0
    for key, action, reason in SWEEP_CORRECTIONS:
        try:
            added += db.sweep_correct(key, action, reason, by_version, now)
        except LookupError:
            pass                                      # that event was never stored in this database
    return added


def persist_sweeps(db, events: list[dict], now: int) -> tuple[int, int]:
    """Append sweep events and their status updates; anything already stored is ignored (no duplicates)."""
    ev = up = 0
    for e in events:
        a, b = db.sweep_add(e, e["updates"], now)
        ev, up = ev + a, up + b
    return ev, up


def _public_event(e: dict) -> dict:
    keep = ("key", "timeframe", "direction", "level", "equal_levels", "swing_time", "event_time", "sweep_extreme",
            "reclaim_price", "volume_ratio", "status", "confirmed_at", "strength", "factors", "updates")
    return {k: e.get(k) for k in keep}


class AdvancedAnalysis:
    def __init__(self, db, market, signal_fn: Callable[[], dict], setting: Callable[[str], Any],
                 clock: Callable[[], int] = now_ms):
        """signal_fn() -> the live signal (only read, to store it next to the shadow row); setting(key) -> value of
        Settings -> Advanced analysis."""
        self.db, self.market, self.signal_fn, self.setting, self.clock = db, market, signal_fn, setting, clock
        self._lock = threading.Lock()
        self._core: dict = {}
        self._profiles: dict = {}
        self._regimes = RegimeCache()

    # ---------------------------------------------------------------- data
    def _frame(self, tf: str) -> pd.DataFrame:
        df = self.market.get_candles(tf).df
        return df if df is not None else pd.DataFrame()

    def weights(self) -> dict[str, float]:
        return {"regime": float(self.setting("weight_regime")),
                "liquidity_sweep": float(self.setting("weight_liquidity_sweep")),
                "volume_profile": float(self.setting("weight_volume_profile"))}

    def _core_state(self) -> dict | None:
        """Regime (+ previous / change time) and sweeps for the latest closed candles; cached per candle close."""
        c4, c1, cd = self._frame("4h"), self._frame("1h"), self._frame("1d")
        if c4.empty:
            return None
        key = tuple(int(f["close_time"].iloc[-1]) if not f.empty else 0 for f in (c4, c1, cd))
        with self._lock:
            if self._core.get("key") == key:
                return self._core
        regime = classify(c4, c1, cd)
        hist = regime_history(c4, c1, cd, bars=60) if regime["status"] == "ok" else {}
        regime.update(previous=hist.get("previous"), changed_at=hist.get("changed_at"))
        sweep_df = c4.iloc[-SWEEP_BARS:]
        superseded = self.db.superseded_sweep_keys()
        events = [e for e in detect_sweeps(sweep_df, c1, cd) if e["key"] not in superseded]
        as_of = int(c4["close_time"].iloc[-1])
        core = {"key": key, "as_of": as_of, "close": float(c4["close"].iloc[-1]), "regime": regime,
                "events": events, "sweep_score": sweep_score(sweep_df, events, as_of), "c4": c4}
        with self._lock:
            self._core = core
        return core

    def profile(self, tf: str = "4h") -> dict:
        if tf not in PROFILE_TIMEFRAMES:
            raise ValueError(f"Unsupported profile timeframe '{tf}'. Use one of: {', '.join(PROFILE_TIMEFRAMES)}")
        df = self._frame(tf)
        if df.empty:
            return {"status": "no_data", "timeframe": tf}
        key = (tf, int(df["close_time"].iloc[-1]))
        with self._lock:
            if key in self._profiles:
                return self._profiles[key]
        p = volume_profile(df, ProfileConfig(lookback=LOOKBACK[tf]), timeframe=tf)
        with self._lock:
            self._profiles = {k: v for k, v in self._profiles.items() if k[0] != tf}
            self._profiles[key] = p
        return p

    def _components(self, core: dict, profile_4h: dict) -> dict[str, float]:
        return {"regime": round(regime_score(core["regime"]), 4), "liquidity_sweep": round(core["sweep_score"], 4),
                "volume_profile": round(vp_score(profile_4h, core["close"]), 4)}

    # ---------------------------------------------------------------- read (GET /api/advanced)
    def snapshot(self, profile_tf: str = "4h") -> dict:
        if profile_tf not in PROFILE_TIMEFRAMES:
            raise ValueError(f"Unsupported profile timeframe '{profile_tf}'. Use one of: {', '.join(PROFILE_TIMEFRAMES)}")
        enabled = bool(self.setting("shadow_enabled"))
        out: dict[str, Any] = {"note": NOTE, "generated_at": self.clock(),
                               "shadow": {"mode": "shadow", "enabled": enabled, "affects_live_signal": False,
                                          "note": SHADOW_NOTE, "weights": self.weights()}}
        try:
            core = self._core_state()
            prof = self.profile(profile_tf)
        except ValueError:
            raise
        except Exception as exc:          # bad data or a bug must not break the dashboard
            log.exception("Advanced analysis failed")
            err = {"status": "error", "error": str(exc)}
            return {**out, "regime": err, "sweeps": {"status": "error", "latest": None, "recent": []},
                    "profile": {**err, "timeframe": profile_tf}}
        if core is None:
            nd = {"status": "no_data"}
            return {**out, "regime": nd, "sweeps": {"status": "no_data", "latest": None, "recent": []},
                    "profile": {**prof, "status": "no_data"} if prof.get("status") != "ok" else prof}
        events = core["events"]
        recent = [_public_event(e) for e in reversed(events[-5:])]
        out.update(as_of=core["as_of"], regime=core["regime"], profile=prof,
                   sweeps={"status": "ok", "timeframe": "4h", "scanned_bars": min(SWEEP_BARS, len(core["c4"])),
                           "latest": recent[0] if recent else None, "recent": recent,
                           "heuristic": "Strength is a heuristic score, not a probability. A sweep is never a "
                                        "BUY or SELL signal."})
        if enabled:
            p4 = prof if profile_tf == "4h" else self.profile("4h")
            comps = self._components(core, p4)
            last = (self.db.signal_history(1) or [None])[0]
            live_score = last["score"] if last else None
            out["shadow"].update(components=comps, live_score=live_score,
                                 combined_score=combined_score(live_score, comps, out["shadow"]["weights"]))
        return out

    # ---------------------------------------------------------------- write (monitor job, once per 4h candle)
    # ---------------------------------------------------------------- chart overlays (read-only)
    def overlays(self, tf: str, limit: int = 300) -> dict:
        """Regime shading and stored sweep markers for the chart's own closed candles (services/chart_overlays.py)."""
        if tf not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe '{tf}'. Use one of: {', '.join(TIMEFRAMES)}")
        notes: list[str] = []
        chart_res = self.market.get_candles(tf, limit)
        chart = chart_res.df if chart_res.df is not None else pd.DataFrame()
        out: dict[str, Any] = {"tf": tf, "analysis_tf": "4h", "regime": {"bars": [], "segments": [], "transitions": []},
                               "sweeps": [], "notes": notes, "as_of": None}
        res4 = self.market.get_candles("4h")
        c4 = res4.df if res4.df is not None else pd.DataFrame()
        if chart.empty:
            notes.append("No chart candles: overlays need the chart's closed candles.")
        if c4.empty:
            notes.append("No 4h candles available: the regime and sweep overlays need them.")
        if chart.empty or c4.empty:
            return out
        start, as_of = int(chart["open_time"].iloc[0]), int(chart["close_time"].iloc[-1])
        out["as_of"] = as_of
        if res4.is_stale:
            notes.append("4h candles are stale: regime and sweep overlays may be out of date.")
        bars = regime_bars(c4, self._frame("1h"), self._frame("1d"), start, as_of, self._regimes)
        if bars and bars[0]["start"] > start + H4_MS:
            notes.append("Regime shading covers only the part of the chart with enough 4h history.")
        out["regime"] = {"bars": bars, "segments": regime_segments(bars), "transitions": regime_transitions(bars)}
        stored = self.db.sweep_events(500, since=start - H4_MS)       # superseded events are excluded here
        out["sweeps"] = sweep_markers(stored, chart, tf, as_of)
        if not out["sweeps"]:
            notes.append("No stored liquidity sweeps in this range (the background monitor stores them).")
        return out

    def chart_profile(self, tf: str, limit: int = 300, mode: str = "last", start: int | None = None,
                      end: int | None = None) -> dict:
        """Volume profile of the chart's closed candles: the last 200, or the visible range [start, end]."""
        if tf not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe '{tf}'. Use one of: {', '.join(TIMEFRAMES)}")
        if mode not in ("last", "visible"):
            raise ValueError("mode must be 'last' or 'visible'")
        df = self.market.get_candles(tf, limit).df
        if df is None or df.empty:
            return {"status": "no_data", "timeframe": tf, "mode": mode}
        if mode == "last":
            return {**volume_profile(df, ProfileConfig(lookback=200), timeframe=tf), "mode": mode}
        if start is None or end is None:
            raise ValueError("A visible-range profile needs 'from' and 'to' (ms)")
        sub = df[(df["open_time"] >= start) & (df["open_time"] <= end)]      # every chart candle here is closed
        if len(sub) < 10:
            return {"status": "too_few_candles", "timeframe": tf, "mode": mode, "candles_used": int(len(sub))}
        return {**volume_profile(sub, ProfileConfig(lookback=len(sub)), timeframe=tf), "mode": mode}

    # ---------------------------------------------------------------- write (monitor job, once per 4h candle)
    def record(self) -> bool:
        """Store new sweep status updates and a regime change (always: the chart overlays read them) and, in shadow
        mode, the shadow row for the newest closed 4h candle (once). Returns True when a shadow row was added.
        Never touches the live signal."""
        try:
            core = self._core_state()
        except Exception:
            log.exception("Advanced analysis (shadow) failed")
            return False
        if core is None or core["regime"].get("status") != "ok":
            return False
        now = self.clock()
        persist_sweeps(self.db, core["events"], now)
        reg = core["regime"]
        last = (self.db.regime_changes(1) or [None])[0]
        if last is None or last["regime"] != reg["regime"]:
            self.db.regime_add_change({"timeframe": "4h", "at": reg.get("changed_at") or core["as_of"],
                                       "regime": reg["regime"], "previous": reg.get("previous") if last is None
                                       else last["regime"], "confidence": reg["confidence"],
                                       "detail": {"evidence": reg["evidence"], "conflicts": reg["conflicts"]}}, now)
        if not self.setting("shadow_enabled"):
            return False
        if (self.db.shadow_last_time() or 0) >= core["as_of"]:
            return False
        p4 = self.profile("4h")
        comps = self._components(core, p4)
        weights = self.weights()
        sig = self.signal_fn() or {}
        live_score = sig.get("score")
        active = [e for e in core["events"] if (status_at(e, core["as_of"]) or {}).get("status") in ACTIVE]
        return self.db.shadow_add({
            "close_time": core["as_of"], "regime": reg["regime"], "regime_confidence": reg["confidence"],
            "regime_score": comps["regime"], "sweep_score": comps["liquidity_sweep"],
            "vp_score": comps["volume_profile"], "poc": p4.get("poc"), "vah": p4.get("vah"), "val": p4.get("val"),
            "live_signal": sig.get("signal"), "live_setup": sig.get("setup"), "live_score": live_score,
            "live_engine": sig.get("engine"), "weights": weights,
            "combined_score": combined_score(live_score, comps, weights),
            "detail": {"close": core["close"], "active_sweeps": [e["key"] for e in active[-3:]],
                       "sweep_config": asdict(SweepConfig())},
        }, now)
