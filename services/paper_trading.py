"""Paper trading: a separate SIMULATED account. No real orders are ever placed.

* Spot only: buy BTC with simulated USDT cash, sell BTC you hold. No leverage, no shorting.
* Fills use the latest price plus slippage, and pay the configured fee.
* Every order is stored with simulated = 1 (enforced by a database constraint) and
  every response is labelled SIMULATED.
* Optional stop / target per position; they are checked every 60 s by the background monitor (while
  `python app.py` runs, whatever view is open) and whenever the account is refreshed. Fills use the price at
  that check, so they can be later and worse than the level - like real stops.
* Limits: exposure cap and daily loss limit from the risk settings; buys are blocked
  when the price is stale or the daily loss limit is reached.
* Every order is one SQLite transaction (BEGIN IMMEDIATE): the account row is re-read inside it and
  written with a compare-and-set on the cash and BTC that were read, so two simultaneous requests
  (two tabs, a poll and the background check) can never fill the same position twice.
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
NO_POSITION = "No simulated BTC position to sell (spot only: no short selling)."


def _day_start(ts_ms: int) -> int:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class PaperTrading:
    def __init__(self, db: Database, clock: Callable[[], int] = now_ms,
                 day_open_price: Callable[[int], float | None] | None = None):
        """day_open_price(ms) -> BTC price at that 00:00 UTC, used to measure today's loss on a position
        carried over from a previous day (see _daily)."""
        self.db = db
        self.clock = clock
        self.day_open_price = day_open_price

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

    @staticmethod
    def _read(conn) -> dict | None:
        """The account row, read inside the caller's transaction."""
        row = conn.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
        if row is None:
            return None
        acc = dict(row)
        acc["settings"] = json.loads(acc["settings"])
        return acc

    @staticmethod
    def _swap(conn, acc: dict, cash: float, btc: float, avg_entry, avg_fill, stop, target) -> None:
        """Compare-and-set: write only if the row still holds the cash and BTC this transaction read."""
        cur = conn.execute("UPDATE paper_account SET cash=?, btc=?, avg_entry=?, avg_fill=?, stop=?, target=? "
                           "WHERE id=1 AND cash=? AND btc=?",
                           (cash, btc, avg_entry, avg_fill, stop, target, acc["cash"], acc["btc"]))
        if cur.rowcount != 1:
            raise ValueError("The SIMULATED account changed while this order was processed; nothing was filled. "
                             "Refresh and try again.")

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
    def _daily(self, acc: dict, px: float | None) -> dict:
        """Today's P/L in USDT = equity now - equity at 00:00 UTC.

        Cash and BTC at 00:00 are rebuilt exactly by reversing today's orders. BTC held at 00:00 is marked at the
        00:00 price (`day_open_price`). Without that price, the old conservative rule applies: today's loss
        includes the whole unrealised loss since entry. The basis is reported either way."""
        start = _day_start(self.clock())
        with self.db.connect() as conn:
            rows = conn.execute("SELECT side, qty, price, fee, realized_pnl FROM paper_orders WHERE created_at >= ?",
                                (start,)).fetchall()
        cash0, btc0 = acc["cash"], acc["btc"]
        for o in rows:
            gross = o["qty"] * o["price"]
            if o["side"] == "BUY":
                cash0, btc0 = cash0 + gross + o["fee"], btc0 - o["qty"]
            else:
                cash0, btc0 = cash0 - (gross - o["fee"]), btc0 + o["qty"]
        btc0 = 0.0 if btc0 < 1e-12 else btc0
        realized = sum(o["realized_pnl"] or 0.0 for o in rows)
        unreal = (px - acc["avg_entry"]) * acc["btc"] if px and acc["btc"] > 0 and acc["avg_entry"] else 0.0
        if px is None and acc["btc"] > 0:
            pnl, basis = realized, "realised only (no current price)"
            start_equity = cash0 + btc0 * (acc["avg_entry"] or 0.0)
        else:
            equity = acc["cash"] + acc["btc"] * (px or 0.0)
            p0 = self.day_open_price(start) if btc0 > 0 and self.day_open_price else None
            if btc0 == 0 or p0:
                start_equity = cash0 + btc0 * (p0 or 0.0)
                pnl, basis = equity - start_equity, "since 00:00 UTC"
            else:
                pnl, basis = realized + unreal, "since entry (no 00:00 UTC price)"
                start_equity = equity - pnl
        return {"pnl": pnl, "start_equity": start_equity, "basis": basis, "realized": realized, "unrealized": unreal}

    def state(self, price: dict | None) -> dict:
        acc = self._account()
        px = (price or {}).get("price")
        settings = RiskSettings.from_dict(acc["settings"])
        btc_value = acc["btc"] * px if px else None
        equity = acc["cash"] + (btc_value or 0.0)
        day = self._daily(acc, px)
        realized_today, unreal = day["realized"], day["unrealized"]
        day_loss_pct = max(0.0, -day["pnl"]) / max(1e-9, day["start_equity"]) * 100
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
            "daily_pnl": round(day["pnl"], 2), "daily_loss_basis": day["basis"],
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
        st = self.state(price)
        if st["daily_limit_reached"]:
            raise ValueError(f"Daily loss limit ({st['settings']['daily_loss_limit_pct']}%) reached: "
                             "no new simulated buys today.")
        if stop is not None and stop >= px:
            raise ValueError("Stop must be below the current price for a long position.")
        if target is not None and target <= px:
            raise ValueError("Target must be above the current price.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            acc = self._read(conn)
            if acc is None:
                raise ValueError("The SIMULATED account does not exist yet. Reset it first.")
            s = RiskSettings.from_dict(acc["settings"])
            fill = px * (1 + s.slippage_pct / 100)
            fee = usd * s.fee_pct / 100
            if usd + fee > acc["cash"] + 1e-9:
                raise ValueError(f"Not enough simulated cash ({acc['cash']:,.2f} USDT incl. fee).")
            new_exposure = (acc["btc"] * px + usd) / (acc["cash"] + acc["btc"] * px) * 100
            if new_exposure > s.max_exposure_pct + 1e-9:
                raise ValueError(f"Order would take exposure to {new_exposure:.1f}%, above the {s.max_exposure_pct}% limit.")
            qty = usd / fill
            new_btc = acc["btc"] + qty
            # avg_entry is the cost basis per BTC INCLUDING the buy fee, so realised P/L is net of both fees.
            avg = ((acc["avg_entry"] or 0) * acc["btc"] + usd + fee) / new_btc
            avg_fill = ((acc.get("avg_fill") or 0) * acc["btc"] + fill * qty) / new_btc
            self._swap(conn, acc, acc["cash"] - usd - fee, new_btc, avg, avg_fill,
                       stop if stop is not None else acc["stop"], target if target is not None else acc["target"])
            oid = self._record(conn, "BUY", qty, fill, fee, None, f"SIMULATED {reason}")
        return {"simulated": True, "order_id": oid,
                "message": f"SIMULATED BUY {qty:.6f} BTC at {fill:,.2f} (fee {fee:,.2f} USDT)."}

    def sell(self, price: dict | None, fraction: float = 1.0, reason: str = "manual") -> dict:
        if not price or price.get("price") is None:
            raise ValueError("No current price: cannot fill a simulated order.")
        if price.get("is_stale"):
            raise ValueError("Price is stale: simulated orders are blocked until fresh data arrives.")
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            raise ValueError("fraction must be a number") from None
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be between 0 and 1")
        if self._account()["btc"] <= 0:
            raise ValueError(NO_POSITION)

        def decide(acc: dict) -> str:
            if acc["btc"] <= 0:          # sold by another request since the check above
                raise ValueError(NO_POSITION)
            return reason
        return self._close(float(price["price"]), fraction, decide)

    def _close(self, px: float, fraction: float, decide: Callable[[dict], str | None]) -> dict | None:
        """The one sell path (manual SELL and stop/target exits). `decide(account)` runs inside the transaction on
        the freshly read row and returns the order reason, or None to sell nothing."""
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            acc = self._read(conn)
            reason = decide(acc) if acc is not None else None
            if reason is None:
                return None
            s = RiskSettings.from_dict(acc["settings"])
            qty = acc["btc"] * fraction
            fill = px * (1 - s.slippage_pct / 100)
            gross = qty * fill
            fee = gross * s.fee_pct / 100
            pnl = (fill - acc["avg_entry"]) * qty - fee   # avg_entry already includes the buy fee
            left = acc["btc"] - qty
            open_ = left > 1e-12
            self._swap(conn, acc, acc["cash"] + gross - fee, left if open_ else 0.0,
                       acc["avg_entry"] if open_ else None, acc.get("avg_fill") if open_ else None,
                       acc["stop"] if open_ else None, acc["target"] if open_ else None)
            oid = self._record(conn, "SELL", qty, fill, fee, pnl, f"SIMULATED {reason}")
        return {"simulated": True, "order_id": oid, "realized_pnl": round(pnl, 2),
                "message": f"SIMULATED SELL {qty:.6f} BTC at {fill:,.2f} (P/L {pnl:+,.2f} USDT)."}

    def check_levels(self, price: dict | None) -> dict | None:
        """Simulated stop/target: if the current price is beyond a level, sell the whole position."""
        if not price or price.get("price") is None or price.get("is_stale"):
            return None
        if self._account()["btc"] <= 0:    # cheap pre-check; the decision itself is made inside the transaction
            return None
        px = float(price["price"])

        def decide(acc: dict) -> str | None:
            if acc["btc"] <= 0:
                return None
            if acc["stop"] is not None and px <= acc["stop"]:
                return f"stop hit (level {acc['stop']:,.2f}, filled at the current price)"
            if acc["target"] is not None and px >= acc["target"]:
                return f"target hit (level {acc['target']:,.2f})"
            return None
        return self._close(px, 1.0, decide)
