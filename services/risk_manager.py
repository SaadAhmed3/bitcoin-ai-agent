"""Position sizing and risk limits for SPOT trading research.

Rules (from the project spec):
  * no leverage, no futures, no short selling, no automatic execution;
  * size from portfolio value and the risk you accept per trade, including fees and slippage;
  * show the maximum estimated loss at the stop and warn that stops are not guaranteed;
  * cap total exposure and enforce a daily loss limit;
  * NEVER increase size after losses: after consecutive losses risk is reduced, not raised.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass
class RiskSettings:
    risk_pct: float = 1.0               # % of portfolio you accept to lose if the stop is hit
    max_exposure_pct: float = 50.0      # max % of portfolio in BTC at once
    daily_loss_limit_pct: float = 3.0   # no new buys once today's loss reaches this
    fee_pct: float = 0.10               # per side
    slippage_pct: float = 0.05          # per side
    loss_streak_reduce: int = 2         # after this many losses in a row, risk is halved
    max_risk_pct: float = 2.0           # hard ceiling regardless of what is entered

    def validate(self) -> "RiskSettings":
        checks = [
            (0 < self.risk_pct <= 5, "risk_pct must be between 0 and 5"),
            (0 < self.max_exposure_pct <= 100, "max_exposure_pct must be between 0 and 100 (no leverage)"),
            (0 < self.daily_loss_limit_pct <= 20, "daily_loss_limit_pct must be between 0 and 20"),
            (0 <= self.fee_pct <= 1 and 0 <= self.slippage_pct <= 1, "fee/slippage must be between 0 and 1%"),
            (1 <= self.loss_streak_reduce <= 10, "loss_streak_reduce must be between 1 and 10"),
            (0 < self.max_risk_pct <= 5, "max_risk_pct must be between 0 and 5"),
        ]
        for ok, msg in checks:
            if not ok:
                raise ValueError(msg)
        return self

    @classmethod
    def from_dict(cls, d: dict | None) -> "RiskSettings":
        base = asdict(cls())
        for k, v in (d or {}).items():
            if k not in base:
                raise ValueError(f"Unknown risk setting '{k}'")
            try:
                base[k] = int(v) if k == "loss_streak_reduce" else float(v)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"'{k}' must be a number") from None
        return cls(**base).validate()


def loss_streak(recent_pnls: list[float]) -> int:
    """Number of consecutive losing trades at the end of the list (most recent last)."""
    n = 0
    for p in reversed(recent_pnls):
        if p < 0:
            n += 1
        else:
            break
    return n


def position_size(portfolio_value: float, entry: float, stop: float, settings: RiskSettings,
                  recent_pnls: list[float] | None = None, atr: float | None = None,
                  current_exposure_value: float = 0.0) -> dict:
    """Spot long position size so that a stop-out loses about risk_pct of the portfolio, after costs."""
    for name, v in (("portfolio_value", portfolio_value), ("entry", entry), ("stop", stop)):
        if v is None or not math.isfinite(v) or v <= 0:
            raise ValueError(f"{name} must be a positive number")
    if stop >= entry:
        raise ValueError("For a long position the stop must be below the entry price")
    settings.validate()
    notes, warnings = [], []

    risk_pct = min(settings.risk_pct, settings.max_risk_pct)
    if settings.risk_pct > settings.max_risk_pct:
        notes.append(f"Risk capped at the {settings.max_risk_pct}% ceiling.")
    streak = loss_streak(recent_pnls or [])
    if streak >= settings.loss_streak_reduce:
        risk_pct /= 2
        notes.append(f"{streak} losses in a row: risk halved to {risk_pct:.2f}%. Size is never increased to win losses back.")

    side_cost = (settings.fee_pct + settings.slippage_pct) / 100
    per_unit_loss = (entry - stop) + entry * side_cost + stop * side_cost   # price risk + costs on both sides
    risk_amount = portfolio_value * risk_pct / 100
    qty = risk_amount / per_unit_loss
    notional = qty * entry

    room = max(0.0, portfolio_value * settings.max_exposure_pct / 100 - current_exposure_value)
    capped = False
    if notional > room:
        qty, notional, capped = room / entry, room, True
        notes.append(f"Size capped by the {settings.max_exposure_pct}% exposure limit.")
    max_loss = qty * per_unit_loss

    stop_pct = (entry - stop) / entry * 100
    if stop_pct < 0.5:
        warnings.append("Stop is within 0.5% of entry: normal price noise is likely to trigger it.")
    if stop_pct > 10:
        warnings.append("Stop is more than 10% away: the position becomes very small, or the loss very large.")
    if atr and entry - stop < atr:
        warnings.append(f"Stop is closer than one 4h ATR ({atr:,.0f}); it may be hit by ordinary volatility.")
    warnings.append("Stops are not guaranteed: gaps, fast markets and exchange outages can fill well below the stop.")

    return {
        "simulated": False,
        "inputs": {"portfolio_value": portfolio_value, "entry": entry, "stop": stop, "atr": atr},
        "risk_pct_used": round(risk_pct, 3),
        "loss_streak": streak,
        "quantity_btc": round(qty, 8),
        "notional": round(notional, 2),
        "exposure_pct": round(notional / portfolio_value * 100, 2),
        "stop_distance_pct": round(stop_pct, 3),
        "max_loss_at_stop": round(max_loss, 2),
        "max_loss_pct": round(max_loss / portfolio_value * 100, 3),
        "costs_included": f"{settings.fee_pct}% fee + {settings.slippage_pct}% slippage on entry and exit",
        "capped_by_exposure": capped,
        "notes": notes,
        "warnings": warnings,
        "disclaimer": "Research calculation only. No leverage, no orders are placed, not financial advice.",
    }
