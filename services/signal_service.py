"""Collects inputs for the signal engine, stores signal history and manages engine settings."""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, fields
from typing import Callable

from database import Database, now_ms
from services.market_data import MarketDataService
from services.backtest_service import current_validation
from services.signal_engine import (ENGINE_VERSION, ENTRY_MODES, HOLD, NO_TRADE, EngineConfig, generate_signal,
                                    validate_weights)
from services.technical_analysis import analyze

log = logging.getLogger("btc_agent.signal")

SIGNAL_TIMEFRAMES = ("1h", "4h", "1d")
RECORD_EVERY_MS = 60 * 60_000  # also store an unchanged signal at most once per hour, for the audit trail
SCORE_STEP = 0.10              # a score move this large is stored even if signal/setup did not change
EDITABLE = {"strong_threshold", "buy_threshold", "sell_threshold", "min_agreement", "strong_min_agreement",
            "counter_trend_limit", "min_rr", "fee_pct", "slippage_pct", "require_validated_edge", "entry_mode",
            "max_source_divergence_pct"}
# Allowed numeric ranges (inclusive unless min_exclusive). The score lives in [-1, +1], so a threshold outside it
# could never fire; costs below zero would make trades look cheaper than free.
RANGES: dict[str, dict] = {
    "strong_threshold": {"min": -1.0, "max": 1.0, "help": "score scale -1..+1; must be above the buy threshold"},
    "buy_threshold": {"min": -1.0, "max": 1.0, "help": "score scale -1..+1; must be above 0"},
    "sell_threshold": {"min": -1.0, "max": 1.0, "help": "score scale -1..+1; must be below 0"},
    "min_agreement": {"min": 0.0, "max": 1.0, "help": "share of weighted evidence, 0..1"},
    "strong_min_agreement": {"min": 0.0, "max": 1.0, "help": "share of weighted evidence, 0..1"},
    "counter_trend_limit": {"min": 0.0, "max": 1.0, "help": "daily trend score, 0..1"},
    "min_rr": {"min": 0.0, "max": 10.0, "min_exclusive": True, "help": "reward/risk after costs, above 0"},
    "fee_pct": {"min": 0.0, "max": 1.0, "help": "% per side, 0..1"},
    "max_source_divergence_pct": {"min": 0.1, "max": 5.0,
                                  "help": "% two exchanges may differ before NO TRADE (signal-1.8), 0.1..5"},
    "slippage_pct": {"min": 0.0, "max": 1.0, "help": "% per side, 0..1"},
}
for _r in RANGES.values():
    _r.setdefault("min_exclusive", False)


def check_range(key: str, v: float) -> None:
    r = RANGES[key]
    low_ok = v > r["min"] if r["min_exclusive"] else v >= r["min"]
    if not (low_ok and v <= r["max"]) or v != v:
        low = f"greater than {r['min']:g}" if r["min_exclusive"] else f"at least {r['min']:g}"
        raise ValueError(f"'{key}' must be {low} and at most {r['max']:g} ({r['help']}); got {v:g}")


def check_thresholds(cfg: EngineConfig) -> None:
    s, b, st = cfg.sell_threshold, cfg.buy_threshold, cfg.strong_threshold
    if not (-1 <= s < 0 < b < st <= 1):
        raise ValueError(f"Thresholds must satisfy -1 <= sell < 0 < buy < strong <= 1 "
                         f"(got sell {s:g}, buy {b:g}, strong {st:g})")


class SignalService:
    def __init__(self, db: Database, market: MarketDataService, news_report: Callable[[], dict] | None = None,
                 clock: Callable[[], int] = now_ms):
        self.db = db
        self.market = market
        self.news_report = news_report or (lambda: {"summary": None, "events": []})
        self.clock = clock
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ config
    @staticmethod
    def _valid_stored(stored: dict) -> dict:
        """Stored values that are still allowed. Values written by an older version without range checks
        (e.g. a negative fee) are dropped, so their defaults apply."""
        out = {}
        for k, v in stored.items():
            if k == "weights":
                try:
                    out[k] = validate_weights(v)
                except ValueError:
                    pass
            elif k == "entry_mode":
                if v in ENTRY_MODES:
                    out[k] = v
            elif k == "require_validated_edge":
                if isinstance(v, bool):
                    out[k] = v
            elif k in RANGES:
                try:
                    check_range(k, float(v))
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
        return out

    @staticmethod
    def _build(values: dict) -> EngineConfig:
        cfg = EngineConfig()
        for k, v in values.items():
            setattr(cfg, k, v)
        return cfg

    def config(self) -> EngineConfig:
        values = self._valid_stored(self.db.get_setting("signal_config", {}) or {})
        cfg = self._build(values)
        try:
            check_thresholds(cfg)
        except ValueError:          # an inconsistent stored combination: use the default thresholds
            cfg = self._build({k: v for k, v in values.items()
                               if k not in ("strong_threshold", "buy_threshold", "sell_threshold")})
        return cfg

    def update_config(self, payload: dict) -> EngineConfig:
        if not isinstance(payload, dict):
            raise ValueError("Body must be a JSON object")
        stored = self._valid_stored(self.db.get_setting("signal_config", {}) or {})
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
                if isinstance(v, bool):
                    raise ValueError(f"'{k}' must be a number")
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"'{k}' must be a number") from None
                check_range(k, v)
            stored[k] = v
        merged = self._build(stored)
        check_thresholds(merged)
        self.db.set_setting("signal_config", stored)
        return merged

    def reset_config(self) -> EngineConfig:
        self.db.set_setting("signal_config", {})
        return EngineConfig()

    # ------------------------------------------------------------------ signal
    def current(self) -> dict:
        with self._lock:
            stage, price, cfg, validation = "market data", None, EngineConfig(), None
            try:
                analyses, meta = {}, {}
                for tf in SIGNAL_TIMEFRAMES:
                    stage = f"market data ({tf})"
                    res = self.market.get_candles(tf)
                    stage = f"indicator analysis ({tf})"
                    analyses[tf] = analyze(res.df, tf)
                    meta[tf] = res.meta()
                stage = "price"
                price = self.market.get_price()
                stage = "price cross-check"
                price = {**price, "cross_check": self.market.cross_check(price)}
                try:
                    news = self.news_report() or {}
                except Exception:
                    news = {}
                stage = "settings"
                cfg = self.config()
                validation = current_validation(self.db, cfg)
                stage = "signal engine"
                sig = generate_signal(analyses, meta, price, news.get("summary"), news.get("events"),
                                      cfg, validation, self.clock())
            except Exception as exc:   # a bug or bad data must never produce a signal, nor an HTTP 500
                log.exception("Signal calculation failed in %s", stage)
                sig = self._error_signal(stage, exc, price, cfg, validation)
            rec = self._record(sig)
            sig["id"] = rec["id"]
            sig["record"] = rec
            return sig

    def _error_signal(self, stage: str, exc: Exception, price: dict | None, cfg: EngineConfig,
                      validation: dict | None) -> dict:
        """NO TRADE with the calculation error as the only reason. The shape comes from the engine's own no-data
        path (so the dashboard renders it as usual); if the engine itself is what failed, a minimal copy is built."""
        reason = (f"Calculation error in {stage}: {type(exc).__name__}: {str(exc)[:200]}. The signal is withheld; "
                  "details are in the server console.")
        now = self.clock()
        try:
            sig = generate_signal({}, {}, price, None, [], cfg, validation, now)
        except Exception:
            sig = {"setup": HOLD, "score": 0.0, "agreement": 0.0, "directional_weight": 0.0, "direction": "neutral",
                   "strength": "weak", "generated_at": now, "horizon": cfg.horizon,
                   "price": {"value": (price or {}).get("price"), "provider": (price or {}).get("provider"),
                             "quote": (price or {}).get("quote"), "observed_at": (price or {}).get("observed_at"),
                             "status": (price or {}).get("status")},
                   "price_basis": {}, "components": [], "evidence_bullish": [], "evidence_bearish": [],
                   "evidence_for": [], "evidence_against": [], "neutral_components": [], "setup_notes": [],
                   "key_levels": {"resistance": [], "support": [], "current_zone": None, "basis": "n/a", "note": ""},
                   "invalidation": [], "would_change": [], "risk": {},
                   "news": {"bias_label": None, "bias_score": None, "top_events": []},
                   "validation": validation or {"validated": False},
                   "confidence": "Not calibrated. The score counts weighted evidence; it is not a probability of profit.",
                   "data_warnings": [], "config": asdict(cfg), "engine": ENGINE_VERSION}
        sig.update({"signal": NO_TRADE, "setup": HOLD, "actionable": False, "score": 0.0, "plan": None,
                    "pending_order": None, "plan_status": None, "no_trade_reasons": [reason],
                    "diagnostics": {"gate_keys": ["calculation_error"], "error_stage": stage,
                                    "error_type": type(exc).__name__}})
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
