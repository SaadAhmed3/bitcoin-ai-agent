"""DCA deposit-neutral metrics: REPORTED ONLY, no criterion (addendum 2026-11-01 to PREREGISTRATION_DCA.md).

Why: the pre-registered DCA drawdown is measured on the whole budget from day 1 (cash not yet invested + BTC at
liquidation value). No deposits are added, but the uninvested cash dampens it: early in a window most of the account
is cash. These two views measure the money that IS invested:

  time-weighted return (TWR) index: each day is split at the buy (the day's open):
      before the buy  r_a = BTC_prev x open / (BTC_prev x previous close)
      after the buy   r_b = BTC_now x close / (BTC_prev x open + amount)    (fee and slippage lower r_b)
      index_t = index_(t-1) x r_a x r_b, starting at 1 on the first buy; all values x (1 - fee - slippage).
    Its max drawdown is the drawdown of the invested money, independent of how much has been added.
  return on invested capital over time: (BTC x close x (1 - fee - slippage) - invested) / invested.

Computed from the simulation's own records (simulate() buys and equity rows, or the forward tracker's events); no
new prices and no change to the locked study or its verdict.
"""
from __future__ import annotations

from typing import Any

import numpy as np

NOTE = ("Reported only, no criterion: measured on the invested money (time-weighted and return on invested capital), "
        "added 2026-11-01. The pre-registered verdict is unchanged.")


def _series(days: list[dict], liq: float) -> dict[str, Any]:
    """days: [{day, close, btc, invested, buy: {open, usd} | None}] in order."""
    index, idx, roi = 1.0, [], []
    prev_btc = prev_close = None
    started = False
    for d in days:
        buy = d.get("buy")
        if not started:
            if not buy:
                continue
            started = True
            index *= (d["btc"] * d["close"] * liq) / buy["usd"]
        else:
            if buy:
                before = prev_btc * buy["open"] * liq
                r_a = buy["open"] / prev_close
                r_b = (d["btc"] * d["close"] * liq) / (before + buy["usd"])
                index *= r_a * r_b
            else:
                index *= d["close"] / prev_close
        prev_btc, prev_close = d["btc"], d["close"]
        idx.append({"day": d["day"], "index": index})
        value = d["btc"] * d["close"] * liq
        roi.append({"day": d["day"], "roi_pct": (value / d["invested"] - 1) * 100 if d["invested"] else 0.0})
    if not idx:
        return {"twr_index": [], "twr_return_pct": None, "twr_max_drawdown_pct": None, "roi_series": [],
                "roi_final_pct": None, "roi_worst_pct": None, "roi_worst_day": None, "reported_only": True, "note": NOTE}
    v = np.array([x["index"] for x in idx])
    peak = np.maximum.accumulate(v)
    worst = min(roi, key=lambda r: r["roi_pct"])
    return {"twr_index": idx, "twr_return_pct": (v[-1] - 1) * 100, "twr_max_drawdown_pct": float(np.max((peak - v) / peak)) * 100,
            "roi_series": roi, "roi_final_pct": roi[-1]["roi_pct"], "roi_worst_pct": worst["roi_pct"],
            "roi_worst_day": worst["day"], "reported_only": True, "note": NOTE}


def deposit_neutral(sim: dict, cfg: dict) -> dict[str, Any]:
    """From dca.simulate()'s result (its buys and daily equity rows)."""
    liq = 1 - cfg["fee_pct"] / 100 - cfg["slippage_pct"] / 100
    buys = {b["day"]: b for b in sim["buys"]}
    days = [{"day": e["day"], "close": e["close"], "btc": e["btc"], "invested": e["invested"],
             "buy": {"open": buys[e["day"]]["open"], "usd": buys[e["day"]]["usd"]} if e["day"] in buys else None}
            for e in sim["equity"]]
    return _series(days, liq)


def from_forward_events(events: list[dict], cfg: dict) -> dict[str, Any]:
    """From the strategy forward tracker's DCA events (buy at the open, mark at the close)."""
    liq = 1 - cfg["fee_pct"] / 100 - cfg["slippage_pct"] / 100
    buys = {e["day"]: e["detail"] for e in events if e["kind"] == "buy"}
    days = [{"day": e["day"], "close": e["detail"]["close"], "btc": e["detail"]["btc"], "invested": e["detail"]["invested"],
             "buy": {"open": buys[e["day"]]["open"], "usd": buys[e["day"]]["usd"]} if e["day"] in buys else None}
            for e in events if e["kind"] == "mark"]
    return _series(days, liq)


def thin(series: list[dict], n: int = 300) -> list[dict]:
    if len(series) <= n:
        return series
    idx = np.linspace(0, len(series) - 1, n).astype(int)
    return [series[i] for i in idx]
