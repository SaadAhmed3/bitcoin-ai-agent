"""Collects inputs for the signal engine, stores signal history and manages engine settings."""
from __future__ import annotations

import threading
from dataclasses import fields
from typing import Callable

from database import Database, now_ms
from services.market_data import MarketDataService
from services.backtest_service import effective_validation
from services.signal_engine import ENTRY_MODES, EngineConfig, generate_signal, validate_weights
from services.technical_analysis import analyze

SIGNAL_TIMEFRAMES = ("1h", "4h", "1d")
RECORD_EVERY_MS = 60 * 60_000  # also store an unchanged signal at most once per hour, for the audit trail
SCORE_STEP = 0.10              # a score move this large is stored even if signal/setup did not change
EDITABLE = {"strong_threshold", "buy_threshold", "sell_threshold", "min_agreement", "strong_min_agreement",
            "counter_trend_limit", "min_rr", "fee_pct", "slippage_pct", "require_validated_edge", "entry_mode"}


class SignalService:
    def __init__(self, db: Database, market: MarketDataService, news_report: Callable[[], dict] | None = None,
                 clock: Callable[[], int] = now_ms):
        self.db = db
        self.market = market
        self.news_report = news_report or (lambda: {"summary": None, "events": []})
        self.clock = clock
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ config
    def config(self) -> EngineConfig:
        cfg = EngineConfig()
        stored = self.db.get_setting("signal_config", {}) or {}
        if "weights" in stored:
            try:
                cfg.weights = validate_weights(stored["weights"])
            except ValueError:
                pass  # ignore a corrupted stored value; defaults stay
        for k, v in stored.items():
            if k in EDITABLE:
                if k == "entry_mode" and v not in ENTRY_MODES:
                    continue  # ignore a corrupted stored value
                setattr(cfg, k, v)
        return cfg

    def update_config(self, payload: dict) -> EngineConfig:
        if not isinstance(payload, dict):
            raise ValueError("Body must be a JSON object")
        stored = self.db.get_setting("signal_config", {}) or {}
        if "weights" in payload:
            stored["weights"] = validate_weights(payload["weights"])
        types = {f.name: f.type for f in fields(EngineConfig)}
        for k, v in payload.items():
            if k == "weights":
                continue
            if k not in EDITABLE:
                raise ValueError(f"'{k}' cannot be changed")
            if k == "entry_mode":
                if v not in ENTRY_MODES:
                    raise ValueError(f"'entry_mode' must be one of: {', '.join(ENTRY_MODES)}")
            elif types[k] in ("bool", bool):
                if not isinstance(v, bool):
                    raise ValueError(f"'{k}' must be true or false")
            else:
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"'{k}' must be a number") from None
                if not (-1 <= v <= 10):
                    raise ValueError(f"'{k}' is out of range")
            stored[k] = v
        merged = EngineConfig()
        for k, v in stored.items():
            if k == "weights":
                merged.weights = v
            else:
                setattr(merged, k, v)
        if not (merged.sell_threshold < 0 < merged.buy_threshold <= merged.strong_threshold):
            raise ValueError("Thresholds must satisfy sell < 0 < buy <= strong")
        self.db.set_setting("signal_config", stored)
        return merged

    def reset_config(self) -> EngineConfig:
        self.db.set_setting("signal_config", {})
        return EngineConfig()

    # ------------------------------------------------------------------ signal
    def current(self) -> dict:
        with self._lock:
            analyses, meta = {}, {}
            for tf in SIGNAL_TIMEFRAMES:
                res = self.market.get_candles(tf)
                analyses[tf] = analyze(res.df, tf)
                meta[tf] = res.meta()
            price = self.market.get_price()
            try:
                news = self.news_report() or {}
            except Exception:
                news = {}
            cfg = self.config()
            validation = effective_validation(self.db.get_setting("strategy_validation"), cfg)
            sig = generate_signal(analyses, meta, price, news.get("summary"), news.get("events"),
                                  cfg, validation, self.clock())
            rec = self._record(sig)
            sig["id"] = rec["id"]
            sig["record"] = rec
            return sig

    def _record(self, sig: dict) -> dict:
        """Store the signal when anything material changed; otherwise point at the last stored record.

        Returns {"id", "stored", "reason", "stored_at"}. When stored is False the live numbers may differ
        slightly from the stored record (same signal/setup, score within SCORE_STEP, same engine, <1h).
        """
        last = self.db.latest_signal()
        if last is None:
            return self._save(sig, "first signal")
        reasons = []
        if last["signal"] != sig["signal"]:
            reasons.append(f"signal {last['signal']} -> {sig['signal']}")
        if last["setup"] != sig["setup"]:
            reasons.append(f"setup {last['setup']} -> {sig['setup']}")
        last_engine = (self.db.get_signal(last["id"]) or {}).get("engine")
        if sig.get("engine") and last_engine != sig.get("engine"):
            reasons.append(f"engine {last_engine} -> {sig['engine']}")
        if abs(float(sig["score"]) - float(last["score"])) >= SCORE_STEP:
            reasons.append(f"score {last['score']:+.2f} -> {sig['score']:+.2f}")
        if reasons:
            return self._save(sig, "; ".join(reasons))
        if sig["generated_at"] - last["created_at"] >= RECORD_EVERY_MS:
            return self._save(sig, "hourly record (unchanged)")
        return {"id": last["id"], "stored": False, "stored_at": last["created_at"],
                "reason": "unchanged since this record (same signal, setup and engine; score within "
                          f"{SCORE_STEP:.2f}); live numbers may differ slightly"}

    def _save(self, sig: dict, reason: str) -> dict:
        return {"id": self.db.save_signal(sig, reason), "stored": True, "stored_at": sig["generated_at"], "reason": reason}
