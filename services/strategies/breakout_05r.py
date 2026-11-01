"""Breakout-0.5R: a FORWARD-ONLY variant of Breakout (round 4). Research only, simulated only.

CHOSEN AFTER THE DESIGN RESULTS: Breakout FAILED its design criteria on drawdown (23.71% > 15%); halving the risk per
trade is an idea that came from seeing that result. A design backtest of it would be data snooping, so it has none and
never gets a design verdict. It can only earn evidence in its own forward test (PREREGISTRATION_BREAKOUT_05R.md).

Rules: identical to Breakout (its own features() / step() / evaluate_frames(), not a copy); only risk_pct = 0.5.
The forward fingerprint of this strategy is this module + the tracker core; this module pins breakout.py by SHA-256,
so the rules it runs are part of it: if breakout.py changes, the variant gives NO TRADE and its forward test pauses
(flagged "dependency changed") instead of silently running other rules. The original Breakout and its FAIL verdict
are untouched.
"""
from __future__ import annotations

import dataclasses
import hashlib
from typing import Any

from services.strategies import breakout as bo
from services.strategies.base import DataQuality, StrategyResult

STRATEGY_ID = "breakout_05r"
VERSION = "breakout_05r-1"
VARIANT_OF = "breakout"
FORWARD_ONLY = True
LABEL = "chosen after design results: forward test only, no design verdict"
PREREGISTRATION = "PREREGISTRATION_BREAKOUT_05R.md"
EVAL_TIMEFRAMES = bo.EVAL_TIMEFRAMES
FORWARD_TIMEFRAME = bo.FORWARD_TIMEFRAME
RULES_FILE = bo.__file__
RULES_SHA256 = "81a0aaf854af5c4b423875abf96bba18d0bb8488486bb1adc2e248e3d851bb8c"     # services/strategies/breakout.py

DEFAULTS: dict[str, Any] = {**bo.DEFAULTS, "risk_pct": 0.5}
FIELDS = bo.FIELDS
LIMITATIONS = [LABEL[0].upper() + LABEL[1:] + ".", *bo.LIMITATIONS[1:]]


def rules_dependency_ok() -> bool:
    with open(RULES_FILE, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest() == RULES_SHA256


def validate_config(payload: dict | None) -> dict[str, Any]:
    """Breakout's own validation, with this variant's defaults for anything not given."""
    payload = dict(payload or {})
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown breakout setting(s): {', '.join(sorted(unknown))}")
    return bo.validate_config({**DEFAULTS, **payload})


def evaluate_frames(frames: dict, cfg: dict, now: int, data: DataQuality, regime: str | None = None,
                    config_version: int = 1) -> StrategyResult:
    r = bo.evaluate_frames(frames, cfg, now, data, regime, config_version)
    r = dataclasses.replace(r, strategy_id=STRATEGY_ID, strategy_version=VERSION)
    if not rules_dependency_ok():
        problem = "services/strategies/breakout.py changed since this variant was pre-registered"
        return dataclasses.replace(r, signal="NO TRADE", entry_zone=None, stop=None, targets=[], reward_risk=None,
                                   contradictory=r.contradictory + [problem], explanation="Dependency problem: no research signal.",
                                   rejection=problem)
    return r


# --------------------------------------------------------------------------- forward test (Breakout's own per-bar logic)
def forward_step(cfg: dict, ctx: dict) -> tuple[list[dict], dict | None]:
    if not rules_dependency_ok():
        return [], ctx["state"]                       # paused: the tracker retries from the last processed bar
    return bo.forward_step(cfg, ctx)


def forward_state(events: list[dict]) -> dict | None:
    return bo.forward_state(events)


def forward_summary(events: list[dict], cfg: dict, done: bool) -> dict:
    return {**bo.forward_summary(events, cfg, done), "dependency_changed": not rules_dependency_ok(),
            "forward_only": True}
