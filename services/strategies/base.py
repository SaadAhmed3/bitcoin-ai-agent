"""Strategy Lab: the interface every strategy engine shares. Research only, spot only, simulated only.

Five independent engines (round 1: DCA; rounds 2-5: trend, range, breakout, rotation). They are separate from the
live signal engine (signal-1.8): they never read or change its settings and it never reads them.

Result schema ("strategy-result-1"), one per evaluation:
  strategy_id, strategy_version   e.g. "dca", "dca-1" (the code version)
  evaluated_at                    ms UTC
  asset, timeframe                e.g. "BTCUSDT", "1d"
  signal                          one of SIGNALS[strategy_id]
  strength                        0-100 HEURISTIC or None (not a probability of profit; see strength_note)
  supporting, contradictory       evidence lines
  entry_zone {low, high}, stop, targets [..], reward_risk      None / [] when not applicable (DCA)
  fees_pct, slippage_pct          per side, as used by the strategy
  data                            DataQuality: source, retrieved_at, last_candle_close, is_stale, missing_candles,
                                  warnings
  regime                          the 4h market regime as context (or None)
  explanation                     why this signal
  rejection                       why no trade / no buy (or None)
  config_version, config_hash     the versioned configuration it used
  labels                          LIVE DATA / CALCULATED / MODEL ESTIMATE / HYPOTHETICAL / SIMULATED / NOT VALIDATED
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "strategy-result-1"
STRENGTH_NOTE = "Signal strength is a heuristic 0-100 score, not a probability of profit (not calibrated)."

STRATEGIES: list[dict[str, Any]] = [
    {"id": "dca", "name": "DCA (dollar-cost averaging)", "round": 1,
     "purpose": "Long-term accumulation on a fixed schedule. Not a short-term buy/sell prediction."},
    {"id": "trend", "name": "Trend following", "round": 2,
     "purpose": "Trade only a confirmed directional trend. Defined differently from H2 (EMA 50/200)."},
    {"id": "range", "name": "Range trading", "round": 3, "purpose": "Trade confirmed reactions inside a valid range."},
    {"id": "breakout", "name": "Breakout trading", "round": 4,
     "purpose": "Trade candle-close breakouts from established structures; never a wick alone."},
    {"id": "breakout_05r", "name": "Breakout-0.5R (variant)", "round": 4, "variant_of": "breakout", "forward_only": True,
     "purpose": "Breakout's identical rules at 0.5% risk per trade. Chosen after design results: forward test only, "
                "no design verdict."},
    {"id": "rotation", "name": "Narrative / sector rotation", "round": 5,
     "purpose": "Relative strength of BTC vs ETH, SOL and BNB. Research only; never rotates funds."},
]
_TRADE = ("BUY SETUP", "HOLD", "NO TRADE", "EXIT / REDUCE EXPOSURE")
SIGNALS: dict[str, tuple[str, ...]] = {"dca": ("ACCUMULATE", "WAIT", "PAUSE"), "trend": _TRADE, "range": _TRADE,
                                       "breakout": _TRADE, "breakout_05r": _TRADE,
                                       "rotation": ("BTC STRONGER", "ALTCOIN STRENGTH", "NEUTRAL", "NO TRADE")}
LABELS = ("LIVE DATA", "CALCULATED", "MODEL ESTIMATE", "HYPOTHETICAL", "SIMULATED", "NOT VALIDATED")

# Validation statuses (the 2025-10-29 holdout is contaminated: out-of-sample evidence can only come from the
# separate strategy forward tracker).
NOT_VALIDATED = "NOT VALIDATED"
SETTINGS_CHANGED = "NOT VALIDATED (settings changed)"
CODE_CHANGED = "NOT VALIDATED (code changed)"
BACKTESTED = "BACKTESTED (design data)"
FORWARD_TESTING = "FORWARD TESTING"
FORWARD_PASS = "FORWARD TESTED: PASS"
FORWARD_FAIL = "FORWARD TESTED: FAIL"

STRATEGY_DIR = Path(__file__).resolve().parent


@dataclass
class DataQuality:
    source: str | None
    retrieved_at: int | None
    last_candle_close: int | None
    is_stale: bool
    missing_candles: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class StrategyResult:
    strategy_id: str
    strategy_version: str
    evaluated_at: int
    asset: str
    timeframe: str
    signal: str
    strength: int | None
    supporting: list[str]
    contradictory: list[str]
    entry_zone: dict | None
    stop: float | None
    targets: list[float]
    reward_risk: float | None
    fees_pct: float
    slippage_pct: float
    data: DataQuality
    regime: str | None
    explanation: str
    rejection: str | None
    config_version: int
    config_hash: str
    labels: list[str]

    def __post_init__(self):
        if self.signal not in SIGNALS.get(self.strategy_id, ()):
            raise ValueError(f"'{self.signal}' is not a {self.strategy_id} signal ({', '.join(SIGNALS.get(self.strategy_id, ()))})")
        if self.strength is not None and not 0 <= self.strength <= 100:
            raise ValueError("strength must be 0-100 or None")
        bad = [x for x in self.labels if x not in LABELS]
        if bad:
            raise ValueError(f"unknown label(s): {bad}")

    def to_dict(self) -> dict:
        return {**asdict(self), "schema": SCHEMA_VERSION, "strength_note": STRENGTH_NOTE}


def config_hash(cfg: dict) -> str:
    """Identifies a configuration (key order does not matter)."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def code_fingerprint(strategy_id: str) -> str:
    """SHA-256 of the shared interface + the strategy's module (line endings normalised)."""
    h = hashlib.sha256()
    for p in (STRATEGY_DIR / "base.py", STRATEGY_DIR / f"{strategy_id}.py"):
        h.update(p.name.encode() + b"\0" + p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


DESIGN_WORD = {"PASS": "PASSED", "FAIL": "FAILED", "INCONCLUSIVE": "INCONCLUSIVE"}


def validation_status(prereg_hash: str | None, current_hash: str, backtest: dict | None,
                      forward: dict | None) -> dict[str, str]:
    """The strategy's validation status for its CURRENT configuration (never inherited across configurations), with
    a display label that always shows the design verdict, e.g. "FAILED on design data · not validated"."""
    out = _status(prereg_hash, current_hash, backtest, forward)
    verdict = (backtest or {}).get("verdict") if out["status"] != SETTINGS_CHANGED else None
    design = f"{DESIGN_WORD[verdict]} on design data" if verdict in DESIGN_WORD else None
    if out["status"] == SETTINGS_CHANGED:
        label = "Settings changed · not validated"
    elif out["status"] == CODE_CHANGED:
        label = " · ".join(x for x in (design, "code changed", "not validated") if x)
    elif out["status"] == FORWARD_TESTING:
        label = " · ".join(x for x in (design, f"forward testing, day {forward.get('days_elapsed', 0)} of "
                                               f"{forward.get('target_days', 365)}", "not validated") if x)
    elif out["status"] == FORWARD_PASS:
        label = "Forward tested: PASS"
    elif out["status"] == FORWARD_FAIL:
        label = "Forward tested: FAIL · not validated"
    elif design:
        label = f"{design} · not validated"
    else:
        label = "Not validated"
    return {**out, "label": label, "design_verdict": verdict}


def _status(prereg_hash: str | None, current_hash: str, backtest: dict | None, forward: dict | None) -> dict[str, str]:
    if prereg_hash is None or current_hash != prereg_hash:
        return {"status": SETTINGS_CHANGED, "detail": "The settings differ from the pre-registered ones: no earlier "
                                                      "backtest or forward result applies to them."}
    if forward and forward.get("code_changed"):
        return {"status": CODE_CHANGED, "detail": "The strategy code changed after its forward test started."}
    if forward and forward.get("state") == "done" and forward.get("verdict") in ("PASS", "FAIL"):
        return {"status": FORWARD_PASS if forward["verdict"] == "PASS" else FORWARD_FAIL,
                "detail": f"Pre-registered forward criteria after {forward.get('target_days', 365)} days."}
    if forward and forward.get("state") == "running":
        return {"status": FORWARD_TESTING, "detail": f"Forward test running, day {forward.get('days_elapsed', 0)} of "
                                                     f"{forward.get('target_days', 365)}; no verdict yet."}
    if backtest:
        return {"status": BACKTESTED, "detail": f"Design data only (before 2025-10-29): {backtest.get('verdict', 'n/a')} "
                                                "on the pre-registered criteria. Design results never validate."}
    return {"status": NOT_VALIDATED, "detail": "No design backtest and no forward data yet."}


def data_quality(res, now: int) -> DataQuality:
    """From a market_data.CandleResult."""
    df = res.df
    last = int(df["close_time"].iloc[-1]) if df is not None and not df.empty else None
    return DataQuality(source=res.provider, retrieved_at=res.fetched_at or now, last_candle_close=last,
                       is_stale=bool(res.is_stale) or last is None, missing_candles=int(res.gaps or 0),
                       warnings=list(res.warnings or []))
