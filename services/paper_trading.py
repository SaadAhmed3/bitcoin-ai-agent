"""Paper trading: a separate SIMULATED account. No real orders are ever placed.

* Spot only: buy BTC with simulated USDT cash, sell BTC you hold. No leverage, no shorting.
* Fills use the latest price plus slippage, and pay the configured fee.
* Every order is stored with simulated = 1 (enforced by a database constraint) and
  every response is labelled SIMULATED.
* Optional stop / target per position; they are checked whenever the account is refreshed
  (the dashboard polls), so fills can be later and worse than the level - like real stops.
* Limits: exposure cap and daily loss limit from the risk settings; buys are blocked
  when the price is stale or the daily loss limit is reached.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable

from database import Database, now_ms
from services.risk_manager import RiskSettings, loss_streak

DEFAULT_BALANCE = 10_000.0


def _day_start(ts_ms: int) -> int:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class PaperTrading:
    def __init__(self, db: Database, clock: Callable[[], int] = now_ms):
        self.db = db
        self.clock = clock

    # ------------------------------------------------------------------ storage
    def _account(self) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
        if row is None:
            self.reset(DEFAULT_BALANCE)
            return self._account()
        acc = dict(row)
        acc["settings"] = json.loads(acc["settings"])
        return acc

    def _orders(self, limit: int = 100) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM paper_orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def settings(self) -> RiskSettings:
        return RiskSettings.from_dict(self._account()["settings"])

    def update_settings(self, payload: dict) -> RiskSettings:
        current = asdict(self.settings())
        current.update(payload or {})
        new = RiskSettings.from_dict(current)
        with self.db.connect() as conn:
            conn.execute("UPDATE paper_account SET settings=? WHERE id=1", (json.dumps(asdict(new)),))
        return new

    def reset(self, start_balance: float = DEFAULT_BALANCE) -> dict:
        try:
            start_balance = float(start_balance)
        except (TypeError, ValueError):
            raise ValueError("start_balance must be a number") from None
        if not 100 <= start_balance <= 100_000_000:
            raise ValueError("start_balance must be between 100 and 100,000,000")
        with self.db.connect() as conn:
            row = conn.execute("SELECT settings FROM paper_account WHERE id=1").fetchone()
            settings = row["settings"] if row else json.dumps(asdict(RiskSettings()))
            conn.execute("DELETE FROM paper_orders")
            conn.execute("DELETE FROM paper_account")
            conn.execute("""INSERT INTO paper_account (id, created_at, start_balance, cash, btc, avg_entry, stop, target, settings)
                            VALUES (1, ?, ?, ?, 0, NULL, NULL, NULL, ?)""", (self.clock(), start_balance, start_balance, settings))
        return {"simulated": True, "message": f"SIMULATED account reset to {start_balance:,.2f} USDT."}

    # ------------------------------------------------------------------ views
    def _today_pnl(self, acc: dict, price: float | None) -> tuple[float, float]:
        """(realized today, unrealized now) in USDT."""
        start = _day_start(self.clock())
        with self.db.connect() as conn:
            r = conn.execute("SELECT COALESCE(SUM(realized_pnl), 0) s FROM paper_orders WHERE created_at >= ?",
                             (start,)).fetchone()
        realized = float(r["s"])
        unreal = (price - acc["avg_entry"]) * acc["btc"] if price and acc["btc"] > 0 and acc["avg_entry"] else 0.0
        return realized, unreal

    def state(self, price: dict | None) -> dict:
        acc = self._account()
        px = (price or {}).get("price")
        settings = RiskSettings.from_dict(acc["settings"])
        btc_value = acc["btc"] * px if px else None
        equity = acc["cash"] + (btc_value or 0.0)
        realized_today, unreal = self._today_pnl(acc, px)
        day_loss_pct = max(0.0, -(realized_today + unreal)) / max(1e-9, equity - realized_today - unreal) * 100
        sells = [o["realized_pnl"] for o in reversed(self._orders(50)) if o["side"] == "SELL" and o["realized_pnl"] is not None]
        return {
            "simulated": True,
            "label": "SIMULATED PAPER ACCOUNT - no real money, no real orders",
            "price": px, "price_status": (price or {}).get("status"),
            "start_balance": acc["start_balance"], "cash": round(acc["cash"], 2), "btc": round(acc["btc"], 8),
            "avg_fill": acc.get("avg_fill"), "cost_basis": acc["avg_entry"],
            "avg_entry": acc["avg_entry"],  # kept for compatibility; = cost_basis (includes the buy fee)
            "stop": acc["stop"], "target": acc["target"],
            "btc_value": None if btc_value is None else round(btc_value, 2),
            "equity": round(equity, 2) if px or acc["btc"] == 0 else None,
            "total_pnl": round(equity - acc["start_balance"], 2) if px or acc["btc"] == 0 else None,
            "total_pnl_pct": round((equity / acc["start_balance"] - 1) * 100, 3) if px or acc["btc"] == 0 else None,
            "unrealized_pnl": round(unreal, 2), "realized_today": round(realized_today, 2),
            "exposure_pct": round(btc_value / equity * 100, 2) if btc_value and equity else 0.0,
            "daily_loss_pct": round(day_loss_pct, 3),
            "daily_limit_reached": day_loss_pct >= settings.daily_loss_limit_pct,
            "loss_streak": loss_streak(sells),
            "settings": asdict(settings),
            "orders": self._orders(50),
            "created_at": acc["created_at"],
        }

    # ------------------------------------------------------------------ orders
    def _record(self, conn, side: str, qty: float, price: float, fee: float, pnl: float | None, reason: str) -> int:
        cur = conn.execute("""INSERT INTO paper_orders (created_at, side, qty, price, fee, realized_pnl, reason, simulated)
                              VALUES (?,?,?,?,?,?,?,1)""", (self.clock(), side, qty, price, fee, pnl, reason))
        return int(cur.lastrowid)

    def buy(self, price: dict | None, usd: float, stop: float | None = None, target: float | None = None,
            reason: str = "manual") -> dict:
        if not price or price.get("price") is None:
            raise ValueError("No current price: cannot fill a simulated order.")
        if price.get("is_stale"):
            raise ValueError("Price is stale: simulated orders are blocked until fresh data arrives.")
        try:
            usd = float(usd)
        except (TypeError, ValueError):
            raise ValueError("Amount must be a number") from None
        if not math.isfinite(usd) or usd <= 0:
            raise ValueError("Amount must be a positive number")
        for name, v in (("Stop", stop), ("Target", target)):
            if v is not None and (not math.isfinite(v) or v <= 0):
                raise ValueError(f"{name} must be a positive number")
        px = float(price["price"])
        acc = self._account()
        st = self.state(price)
        s = RiskSettings.from_dict(acc["settings"])
        if st["daily_limit_reached"]:
            raise ValueError(f"Daily loss limit ({s.daily_loss_limit_pct}%) reached: no new simulated buys today.")
        if stop is not None and stop >= px:
            raise ValueError("Stop must be below the current price for a long position.")
        if target is not None and target <= px:
            raise ValueError("Target must be above the current price.")
        fill = px * (1 + s.slippage_pct / 100)
        fee = usd * s.fee_pct / 100
        if usd + fee > acc["cash"] + 1e-9:
            raise ValueError(f"Not enough simulated cash ({acc['cash']:,.2f} USDT incl. fee).")
        new_exposure = (acc["btc"] * px + usd) / st["equity"] * 100
        if new_exposure > s.max_exposure_pct + 1e-9:
            raise ValueError(f"Order would take exposure to {new_exposure:.1f}%, above the {s.max_exposure_pct}% limit.")
        qty = usd / fill
        new_btc = acc["btc"] + qty
        # avg_entry is the cost basis per BTC INCLUDING the buy fee, so realised P/L is net of both fees.
        avg = ((acc["avg_entry"] or 0) * acc["btc"] + usd + fee) / new_btc
        avg_fill = ((acc.get("avg_fill") or 0) * acc["btc"] + fill * qty) / new_btc
        with self.db.connect() as conn:
            oid = self._record(conn, "BUY", qty, fill, fee, None, f"SIMULATED {reason}")
            conn.execute("UPDATE paper_account SET cash=?, btc=?, avg_entry=?, avg_fill=?, stop=?, target=? WHERE id=1",
                         (acc["cash"] - usd - fee, new_btc, avg, avg_fill, stop if stop is not None else acc["stop"],
                          target if target is not None else acc["target"]))
        return {"simulated": True, "order_id": oid,
                "message": f"SIMULATED BUY {qty:.6f} BTC at {fill:,.2f} (fee {fee:,.2f} USDT)."}

    def sell(self, price: dict | None, fraction: float = 1.0, reason: str = "manual") -> dict:
        if not price or price.get("price") is None:
            raise ValueError("No current price: cannot fill a simulated order.")
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            raise ValueError("fraction must be a number") from None
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be between 0 and 1")
        acc = self._account()
        if acc["btc"] <= 0:
            raise ValueError("No simulated BTC position to sell (spot only: no short selling).")
        s = RiskSettings.from_dict(acc["settings"])
        qty = acc["btc"] * fraction
        fill = float(price["price"]) * (1 - s.slippage_pct / 100)
        gross = qty * fill
        fee = gross * s.fee_pct / 100
        pnl = (fill - acc["avg_entry"]) * qty - fee   # avg_entry already includes the buy fee
        left = acc["btc"] - qty
        with self.db.connect() as conn:
            oid = self._record(conn, "SELL", qty, fill, fee, pnl, f"SIMULATED {reason}")
            conn.execute("UPDATE paper_account SET cash=?, btc=?, avg_entry=?, avg_fill=?, stop=?, target=? WHERE id=1",
                         (acc["cash"] + gross - fee, left if left > 1e-12 else 0.0,
                          acc["avg_entry"] if left > 1e-12 else None,
                          acc.get("avg_fill") if left > 1e-12 else None,
                          acc["stop"] if left > 1e-12 else None, acc["target"] if left > 1e-12 else None))
        return {"simulated": True, "order_id": oid, "realized_pnl": round(pnl, 2),
                "message": f"SIMULATED SELL {qty:.6f} BTC at {fill:,.2f} (P/L {pnl:+,.2f} USDT)."}

    def check_levels(self, price: dict | None) -> dict | None:
        """Simulated stop/target: if the current price is beyond a level, sell the whole position."""
        if not price or price.get("price") is None or price.get("is_stale"):
            return None
        acc = self._account()
        if acc["btc"] <= 0:
            return None
        px = float(price["price"])
        if acc["stop"] is not None and px <= acc["stop"]:
            return self.sell(price, 1.0, f"stop hit (level {acc['stop']:,.2f}, filled at the current price)")
        if acc["target"] is not None and px >= acc["target"]:
            return self.sell(price, 1.0, f"target hit (level {acc['target']:,.2f})")
        return None
