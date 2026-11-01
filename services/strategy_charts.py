"""Strategy Lab charts: what each strategy sees, where it would enter and why. Visualisation only.

* Live chart: the strategy's own timeframe and closed candles, only the indicators its rules use, computed by the
  strategy's own functions (trend.indicators, range.features / regime_labels, dca.scheduled_days / simulate). The
  plan (entry, stop, targets, R:R) comes from the strategy's own result; the rule checklist from the same feature row.
* History chart: the STORED design result (trades, metrics). Series that are not stored at full resolution (equity,
  drawdown, buy & hold, per-trade fees) are recomputed IN MEMORY with the locked configuration and shown only if every
  stored metric is reproduced exactly; otherwise the mismatch is reported and only stored data is drawn. Nothing is
  ever written.
* Forward chart: the strategy forward tracker's stored events, day by day.
* Comparison: the stored equity samples normalised to 100 and net return vs max drawdown. Not ranked.

The strategy modules, the four protected files and services/forward_tests.py are only imported, never changed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.backtesting import HOLDOUT_START
from services.strategies import breakout as bo, dca, dca_metrics, range as rng, trend
from services.technical_analysis import ema

log = logging.getLogger(__name__)
# Derived cache of VERIFIED history series (never a result): data/cache/strategy_charts/<sid>-<key>.json. The key
# covers what the series depend on; any change gives a new key and a recomputation.
CACHE_VERSION = "strategy-charts-1"
LIVE_BARS = 200
DCA_LIVE_DAYS = 120
FAILED_BANNER = "Failed on design data: research only"
HOW_TO = {
    "live": "Closed candles of the strategy's own timeframe with only the indicators its rules use. A setup draws its "
            "entry band, stop (red) and target (green); the list under the chart shows each rule as met (✓) or not (✗).",
    "live_breakout": "Closed 4h candles with only what the breakout rule uses: the 20-bar high (the level), the 10-bar "
                     "low (the trailing exit) and the volume threshold. A setup draws its entry and stop (red); there is "
                     "no fixed target. The list under the chart shows each rule as met (✓) or not (✗).",
    "history": "Every design-backtest trade: ▲ entry, ▼ exit, the line green for a win and red for a loss; click a "
               "trade in the list to zoom to it. Below: the account (equity) against buy & hold, and the drawdown.",
    "history_dca": "Design period: money invested so far (steps) against what the BTC bought is worth, the BTC price, "
                   "and the worst point for the invested money. DCA never sells.",
    "forward": "The separate forward test, filled one closed bar at a time from its start day. Nothing here is a real "
               "trade.",
    "compare": "Each strategy's design-backtest account, started at 100, and net return against max drawdown. "
               "Hypothetical, design data only, not ranked: a higher return with a deeper drawdown is not better by default.",
}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _num(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def _drawdown(values: list[float]) -> list[float]:
    v = np.asarray(values, float)
    if not len(v):
        return []
    peak = np.maximum.accumulate(v)
    return list((peak - v) / peak * 100)


def _ohlc(df: pd.DataFrame) -> dict[str, list]:
    return {"time": [int(t) for t in df["open_time"]], "open": [float(x) for x in df["open"]],
            "high": [float(x) for x in df["high"]], "low": [float(x) for x in df["low"]],
            "close": [float(x) for x in df["close"]], "volume": [float(x) for x in df.get("volume", [0] * len(df))]}


def _data_check(result: dict) -> dict | None:
    d = result.get("data") or {}
    problems = (["data stale"] if d.get("is_stale") else []) + (
        [f"{d.get('missing_candles')} missing candle(s)"] if d.get("missing_candles") else [])
    return {"rule": "data fresh and complete" + (f" ({', '.join(problems)})" if problems else ""), "met": not problems}


def _close_checklist(checks: list[dict], result: dict, setup_word: str) -> list[dict]:
    """Every rule from the feature row, then the data check; if every rule holds but the strategy still did not give
    a setup (e.g. an exit takes priority), say why, so the box always explains the strategy's own result."""
    out = checks + [_data_check(result)]
    if all(c["met"] for c in out) and result["signal"] != setup_word:
        out.append({"rule": f"{result['signal']}: {result.get('explanation', '')}", "met": False})
    return out


def _plan(result: dict, setup_word: str) -> dict | None:
    if result["signal"] != setup_word:
        return None
    return {"entry": result["entry_zone"], "stop": result["stop"], "targets": result["targets"], "rr": result["reward_risk"]}


# --------------------------------------------------------------------------- live charts (pure: frames in, chart out)
def trend_live(frames: dict, cfg: dict, result: dict) -> dict[str, Any]:
    df4 = frames["4h"].sort_values("open_time").reset_index(drop=True)
    df1 = frames.get("1d")
    ind = trend.indicators(df4, df1, cfg)
    tail = ind.iloc[-LIVE_BARS:]
    series = _ohlc(tail)
    series.update({k: [_num(x) for x in tail[k]] for k in ("ema_f", "ema_m", "ema_s", "adx")})
    daily = [None] * len(tail)
    if df1 is not None and len(df1):
        d1 = df1.sort_values("open_time")
        dema = ema(d1["close"].astype(float).reset_index(drop=True), cfg["htf_ema"]).to_numpy()
        j = np.searchsorted(d1["close_time"].to_numpy(), tail["close_time"].to_numpy(), side="right") - 1
        daily = [_num(dema[k]) if k >= 0 else None for k in j]
    series["daily_ema"] = daily
    r = ind.iloc[-1]
    checks = [{"rule": f"EMA {cfg['ema_fast']} > {cfg['ema_mid']} > {cfg['ema_slow']}", "met": bool(r["align"])},
              {"rule": f"EMA {cfg['ema_mid']} rising over {cfg['slope_bars']} bars", "met": bool(r["slope_ok"])},
              {"rule": f"ADX(14) >= {cfg['adx_min']:g}", "met": bool(r["adx"] >= cfg["adx_min"])},
              {"rule": f"daily close > daily EMA {cfg['htf_ema']}", "met": bool(r["htf_ok"])},
              {"rule": f"pullback to EMA {cfg['ema_fast']}, then a close above the previous high", "met": bool(r["trigger"])},
              {"rule": "stop below the close", "met": bool(r["stop_plan"] < r["close"]) if not pd.isna(r["stop_plan"]) else False}]
    return {"timeframe": "4h", "series": series, "checklist": _close_checklist(checks, result, "BUY SETUP"),
            "plan": _plan(result, "BUY SETUP"), "adx_line": cfg["adx_min"],
            "names": {"ema_f": f"EMA {cfg['ema_fast']}", "ema_m": f"EMA {cfg['ema_mid']}", "ema_s": f"EMA {cfg['ema_slow']}",
                      "daily_ema": f"Daily EMA {cfg['htf_ema']}"},
            "indicators": [f"EMA {cfg['ema_fast']} / {cfg['ema_mid']} / {cfg['ema_slow']}", "ADX(14) (panel)",
                           f"daily EMA {cfg['htf_ema']} level"]}


def range_live(frames: dict, cfg: dict, result: dict) -> dict[str, Any]:
    df = frames["4h"].sort_values("open_time").reset_index(drop=True)
    first = max(0, len(df) - LIVE_BARS)
    labels = rng.regime_labels(df, start=first)
    ft = rng.features(df, labels, cfg)
    tail = ft.iloc[first:]
    series = _ohlc(tail)
    series["volume"] = [float(x) for x in df["volume"].iloc[first:]] if "volume" in df else series["volume"]
    series["adx"] = [_num(x) for x in tail["adx"]]
    r = ft.iloc[-1]
    lb, n = cfg["lookback"], len(ft)
    tol = cfg["touch_atr"] * r["atr"] if not pd.isna(r["atr"]) else 0.0
    win = ft.iloc[max(0, n - 1 - lb):n - 1]
    touches = {"top": [{"time": int(x.open_time), "price": float(x.high)} for x in win.itertuples() if x.high >= r["rh"] - tol],
               "bottom": [{"time": int(x.open_time), "price": float(x.low)} for x in win.itertuples() if x.low <= r["rl"] + tol]}
    checks = [{"rule": f"regime SIDEWAYS (now {r['regime']})", "met": bool(r["regime_ok"])},
              {"rule": f"ADX(14) < {cfg['adx_max']:g}", "met": bool(r["adx_ok"])},
              {"rule": f"range width >= {cfg['min_width_atr']:g} × ATR(14)", "met": bool(r["width_ok"])},
              {"rule": f">= {cfg['min_touches']} touches of the range high", "met": bool(r["touches_top"] >= cfg["min_touches"])},
              {"rule": f">= {cfg['min_touches']} touches of the range low", "met": bool(r["touches_bottom"] >= cfg["min_touches"])},
              {"rule": "reclaim of the range low, or a bullish close after the bottom 20%", "met": bool(r["trigger"])},
              {"rule": "close below the range midpoint", "met": bool(r["close"] < r["mid"]) if not pd.isna(r["mid"]) else False},
              {"rule": "stop below the close", "met": bool(r["stop_plan"] < r["close"]) if not pd.isna(r["stop_plan"]) else False}]
    return {"timeframe": "4h", "series": series,
            "box": {"high": _num(r["rh"]), "low": _num(r["rl"]), "mid": _num(r["mid"])},
            "box_from": int(win["open_time"].iloc[0]) if len(win) else None, "touches": touches,
            "regime": {"time": series["time"], "label": list(tail["regime"])},
            "checklist": _close_checklist(checks, result, "BUY SETUP"), "plan": _plan(result, "BUY SETUP"),
            "adx_line": cfg["adx_max"],
            "indicators": ["range high / low and midpoint", "touches", "ADX(14) (panel)", "regime (sideways shaded)"]}


def dca_live(frames: dict, cfg: dict, result: dict) -> dict[str, Any]:
    df = frames["1d"].sort_values("open_time").reset_index(drop=True).iloc[-DCA_LIVE_DAYS:]
    first, last = int(df["open_time"].iloc[0]), int(df["open_time"].iloc[-1])
    sim = dca.simulate(df, cfg, first, last)
    next_open = int(df["close_time"].iloc[-1]) + 1
    checks = [{"rule": f"next open {_day(next_open)} is a scheduled buy day ({dca.describe(cfg)})",
               "met": dca.is_buy_day(next_open, cfg)}]
    data = _data_check(result)
    return {"timeframe": "1d", "series": _ohlc(df), "buy_days": dca.scheduled_days(first, last, cfg),
            "avg_price": sim["avg_price"], "checklist": [data] + checks, "plan": None,
            "indicators": ["scheduled buy days", "average price of the simulated buys in view"]}


def breakout_live(frames: dict, cfg: dict, result: dict) -> dict[str, Any]:
    df = frames["4h"].sort_values("open_time").reset_index(drop=True)
    ft = bo.features(df, cfg)
    tail = ft.iloc[-LIVE_BARS:]
    series = _ohlc(tail)
    series.update(level=[_num(x) for x in tail["level"]], trail_low=[_num(x) for x in tail["trail_low"]],
                  vol_line=[_num(cfg["volume_mult"] * x) for x in tail["vol_avg"]])
    r = ft.iloc[-1]
    lb, vm, tb = cfg["lookback"], cfg["volume_mult"], cfg["trail_bars"]
    checks = [{"rule": f"4h close above the {lb}-bar high (a wick alone never counts)", "met": bool(r["close_above"])},
              {"rule": f"volume >= {vm:g} × its {cfg['volume_window']}-bar average", "met": bool(r["volume_ok"])},
              {"rule": "stop below the close", "met": bool(r["stop_plan"] < r["close"]) if not pd.isna(r["stop_plan"]) else False}]
    return {"timeframe": "4h", "series": series, "checklist": _close_checklist(checks, result, "BUY SETUP"),
            "plan": _plan(result, "BUY SETUP"), "how_to": HOW_TO["live_breakout"],
            "names": {"level": f"{lb}-bar high (level)", "trail_low": f"{tb}-bar low (trailing exit)",
                      "vol_line": f"Volume {vm:g}× average"},
            "indicators": [f"{lb}-bar high (level)", f"{tb}-bar low (trailing exit)", f"volume {vm:g}× its average"]}


LIVE = {"trend": trend_live, "range": range_live, "dca": dca_live, "breakout": breakout_live}


# --------------------------------------------------------------------------- derived cache
def result_hash(res: dict) -> str:
    """SHA-256 of the stored result itself (without the database row id / time added when it is read)."""
    body = {k: v for k, v in res.items() if k not in ("id", "created_at")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=float).encode()).hexdigest()


def cache_key(sid: str, res: dict) -> str:
    """The strategy's forward fingerprint (its module + the tracker core), the stored result, this cache's version,
    and for range the pinned regime module."""
    from services.strategy_forward import strategy_fingerprint
    extra = rng.REGIME_SHA256 if sid == "range" else ""
    return hashlib.sha256(json.dumps([CACHE_VERSION, sid, strategy_fingerprint(sid), result_hash(res), extra]).encode()).hexdigest()


# --------------------------------------------------------------------------- service
class StrategyCharts:
    def __init__(self, db, lab, cache_dir: str | Path | None = None):
        self.db, self.lab = db, lab
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._history: dict = {}
        self._busy: set = set()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- live
    def live(self, sid: str) -> dict:
        if sid not in LIVE:
            raise LookupError(f"No chart for strategy '{sid}' yet")
        eng = self.lab._engine(sid)
        cfg = self.lab.config(sid)["config"]
        result = self.lab.evaluate(sid)
        frames = {tf: self.lab.market.get_candles(tf).df for tf in eng.EVAL_TIMEFRAMES}
        if any(f is None or f.empty for f in frames.values()):
            return {"status": "no_data", "note": "No closed candles for this strategy's timeframe yet.", "how_to": HOW_TO["live"]}
        out = LIVE[sid](frames, cfg, result)
        how_to = out.pop("how_to", HOW_TO["live"])
        status = self.lab.validation(sid, self.lab.config(sid))
        return {"status": "ok", **out, "signal": result["signal"], "explanation": result["explanation"],
                "banner": FAILED_BANNER if status.get("design_verdict") == "FAIL" else None, "how_to": how_to}

    # ---------------------------------------------------------------- history
    def history(self, sid: str, wait: bool = False) -> dict:
        cfg = self.lab.config(sid)
        res = self.lab.backtest_for(sid, cfg["hash"])
        if res is None:
            return {"status": "none", "note": "No design backtest for these settings."}
        key = cache_key(sid, res)
        with self._lock:
            if key in self._history:
                return self._history[key]
            cached = self._load_cache(sid, key)
            if cached is not None:
                self._history[key] = cached
                return cached
            if not wait:
                if key not in self._busy:
                    self._busy.add(key)
                    threading.Thread(target=self._fill, args=(key, sid, res), daemon=True).start()
                return {"status": "preparing", "note": "Preparing the history chart (recomputing the series in "
                                                       "memory and checking them against the stored result)."}
        self._fill(key, sid, res)
        return self._history[key]

    # ---------------------------------------------------------------- derived cache (never a result)
    def _cache_file(self, sid: str, key: str) -> Path | None:
        return self.cache_dir / f"{sid}-{key[:24]}.json" if self.cache_dir else None

    def _load_cache(self, sid: str, key: str) -> dict | None:
        f = self._cache_file(sid, key)
        if f is None or not f.exists():
            return None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("key") != key or not data.get("payload", {}).get("verified"):
                return None
            return {**data["payload"], "from_cache": True}
        except (OSError, ValueError, AttributeError):
            log.warning("Ignoring an unreadable chart cache file %s", f)
            return None

    def _compute_and_cache(self, sid: str, res: dict) -> dict:
        out = self.compute_history(sid, res)
        f = self._cache_file(sid, cache_key(sid, res))
        if f is not None and out.get("verified"):              # only verified series are cached
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps({"key": cache_key(sid, res), "sid": sid, "version": CACHE_VERSION,
                                           "payload": out}, default=float), encoding="utf-8")
                os.replace(tmp, f)
                for old in f.parent.glob(f"{sid}-*.json"):      # older keys of this strategy are stale
                    if old != f:
                        old.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not write the chart cache %s", f)
        return out

    def _fill(self, key, sid: str, res: dict) -> None:
        try:
            out = self._compute_and_cache(sid, res)
        except Exception as exc:                 # never break the page; show the stored data instead
            log.exception("History chart for %s failed", sid)
            out = {"status": "error", "note": str(exc)}
        with self._lock:
            self._history[key] = out
            self._busy.discard(key)

    def _rows(self, tf: str, holdout: int) -> pd.DataFrame:
        df = pd.DataFrame(self.db.get_candles_range("binance", "BTCUSDT", tf))
        if df.empty:
            return df
        df = df.sort_values("open_time")
        return df[df["close_time"] < holdout].reset_index(drop=True)

    def compute_history(self, sid: str, res: dict | None = None) -> dict:
        res = res or self.lab.backtest_for(sid, self.lab.config(sid)["hash"])
        if sid == "dca":
            return self._history_dca(res)
        return self._history_trades(sid, res)

    def _history_trades(self, sid: str, res: dict) -> dict:
        holdout = res["period"]["holdout_start"]
        cfg = res["config"]
        mod = {"trend": trend, "range": rng, "breakout": bo}[sid]
        df4 = self._rows("4h", holdout)
        if sid == "trend":
            ind = trend.indicators(df4, self._rows("1d", holdout), cfg)
            start, end = trend.design_span(ind, holdout, cfg)
        elif sid == "breakout":
            ind = bo.features(df4, cfg)
            start, end = bo.WARMUP, len(df4) - 1
        else:
            ind = rng.features(df4, rng.regime_labels(df4, start=rng.WARMUP - 1), cfg)
            start, end = rng.WARMUP, len(df4) - 1
        sim = mod.simulate(ind, cfg, start, end)
        m = mod.metrics(sim["trades"], sim["equity"], end - start + 1)
        f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
        units = (1 - f) / (float(ind["open"].iloc[start]) * (1 + s))
        bh = [units * c * (1 - f - s) for c in ind["close"].iloc[start:end + 1]]
        mismatches = [f"{k}: stored {res['metrics'].get(k)!r} vs recomputed {v!r}" for k, v in m.items()
                      if res["metrics"].get(k) != v]
        keys = ("entry_time", "exit_time", "fill", "exit", "stop", "target", "r", "reason", "bars", "return") + (
            ("failed_breakout", "retest") if sid == "breakout" else ())
        if [{k: t[k] for k in keys} for t in sim["trades"]] != res["trade_list"]:
            mismatches.append("trade list differs from the stored one")
        if (bh[-1] - 1) * 100 != res["buy_and_hold"]["net_return_pct"]:
            mismatches.append("buy & hold return differs from the stored one")
        if int(ind["open_time"].iloc[start]) != res["period"]["first_day"]:
            mismatches.append("design window start differs from the stored one")
        ok = not mismatches
        win = ind.iloc[start:end + 1]
        times = [int(t) for t in win["open_time"]]
        fees = {t["entry_time"]: t["fees"] * 100 for t in sim["trades"]} if ok else {}
        trades = [{**t, "fees_pct": fees.get(t["entry_time"]), "win": t["return"] > 0} for t in res["trade_list"]]
        if ok:
            equity = {"time": times, "value": sim["equity"]}
            dd = {"time": times, "value": _drawdown(sim["equity"]), "source": "recomputed, verified"}
            buy_hold = {"time": times, "value": bh}
        else:
            es = res["equity_sample"]
            equity = {"time": [e["time"] for e in es], "value": [e["equity"] for e in es]}
            dd = {"time": equity["time"], "value": _drawdown(equity["value"]), "source": "stored equity sample"}
            buy_hold = None
        return {"status": "ok", "strategy": sid, "verified": ok, "mismatches": mismatches, "trades": trades,
                "price": {"time": times, "close": [float(x) for x in win["close"]]}, "equity": equity,
                "buy_hold": buy_hold, "drawdown": dd, "metrics": res["metrics"], "verdict": res["verdict"],
                "source": ("stored trades and metrics; full-resolution series recomputed in memory with the locked "
                           "configuration and checked against every stored metric" if ok else
                           "stored trades and the stored equity sample only: the recomputation did not reproduce the "
                           "stored result, so no recomputed numbers are shown"),
                "how_to": HOW_TO["history"]}

    def _history_dca(self, res: dict) -> dict:
        cfg, per = res["config"], res["period"]
        df = self._rows("1d", per["holdout_start"])
        sim = dca.simulate(df, cfg, per["first_day"], per["last_day"])
        full = res["full"]
        mismatches = [f"{k}: stored {full[k]!r} vs recomputed {sim[k]!r}" for k in
                      ("return_pct", "max_drawdown_pct", "fees_usd", "slippage_usd", "invested_usd", "btc", "budget_usd")
                      if full[k] != sim[k]]
        if len(sim["buys"]) != res["metrics"]["trades"]:
            mismatches.append("number of buys differs from the stored one")
        ok = not mismatches
        liq = 1 - cfg["fee_pct"] / 100 - cfg["slippage_pct"] / 100
        rows = sim["equity"] if ok else res["equity_sample"]
        times = [int(e["day"]) for e in rows]
        value = [e["btc"] * e["close"] * liq for e in rows]
        invested = [e["invested"] for e in rows]
        roi = [((v - i) / i * 100, t, v, i) for v, i, t in zip(value, invested, times) if i]
        worst = min(roi) if roi else None
        return {"status": "ok", "strategy": "dca", "verified": ok, "mismatches": mismatches,
                "price": {"time": times, "close": [e["close"] for e in rows]},
                "invested": {"time": times, "value": invested}, "value": {"time": times, "value": value},
                "budget_equity": {"time": times, "value": [e["equity"] for e in rows]},
                "buys": [{"day": b["day"], "fill_price": b["fill_price"], "btc": b["btc"]} for b in sim["buys"]] if ok else [],
                "worst": None if worst is None else {"roi_pct": worst[0], "day": worst[1], "value": worst[2], "invested": worst[3]},
                "metrics": res["metrics"], "verdict": res["verdict"],
                "source": "stored result; daily series recomputed in memory and checked" if ok else "stored equity sample only",
                "how_to": HOW_TO["history_dca"]}

    def warm(self) -> None:
        """Start preparing every history chart in the background (range needs minutes: it labels the regime of
        every design bar)."""
        from services.strategies import ENGINES
        for sid in ENGINES:
            try:
                self.history(sid)
            except Exception:
                log.exception("Could not start preparing the %s history chart", sid)

    # ---------------------------------------------------------------- forward
    def forward(self, sid: str) -> dict:
        cfg = self.lab.config(sid)
        t = self.db.strategy_forward_test(sid, cfg["preregistered_hash"])
        events = self.db.strategy_forward_events(t["id"]) if t else []
        marks = [e for e in events if e["kind"] == "mark"]
        if not marks:
            start = f" {_day(t['start_day'])}" if t else ""
            return {"status": "empty", "how_to": HOW_TO["forward"],
                    "note": f"No forward data yet: the chart stays empty until the first closed bar after the start"
                            f"{start} has been processed."}
        times = [int(m["day"]) for m in marks]
        out = {"status": "ok", "how_to": HOW_TO["forward"], "price": {"time": times, "close": [m["detail"]["close"] for m in marks]}}
        if sid == "dca":
            p = self.lab._engine("dca").validate_config(t["config"])
            liq = 1 - p["fee_pct"] / 100 - p["slippage_pct"] / 100
            out.update(invested={"time": times, "value": [m["detail"]["invested"] for m in marks]},
                       value={"time": times, "value": [m["detail"]["btc"] * m["detail"]["close"] * liq for m in marks]},
                       buys=[{"day": e["day"], "fill_price": e["detail"]["fill_price"]} for e in events if e["kind"] == "buy"])
        else:
            eq = [m["detail"]["equity"] for m in marks]
            out.update(equity={"time": times, "value": eq}, drawdown={"time": times, "value": _drawdown(eq)},
                       trades=[{**tr, "win": tr["return"] > 0} for tr in marks[-1]["detail"]["trades"]])
        return out

    # ---------------------------------------------------------------- comparison
    def compare(self) -> dict:
        curves, scatter = [], []
        from services.strategies import ENGINES
        from services.strategies.base import STRATEGIES
        for c in (m for m in STRATEGIES if m["id"] in ENGINES):     # stored results only: no live evaluation here
            res = self.lab.backtest_for(c["id"], self.lab.config(c["id"])["hash"])
            if not res or not res.get("equity_sample"):
                continue
            es = res["equity_sample"]
            if c["id"] == "dca":
                budget = res["full"]["budget_usd"]
                curve = {"time": [e["day"] for e in es], "value": [e["equity"] / budget * 100 for e in es]}
            else:
                curve = {"time": [e["time"] for e in es], "value": [e["equity"] * 100 for e in es]}
            curves.append({"id": c["id"], "name": c["name"], **curve, "verdict": res["verdict"]})
            scatter.append({"id": c["id"], "name": c["name"], "net_return_pct": res["metrics"]["net_return_pct"],
                            "max_drawdown_pct": res["metrics"]["max_drawdown_pct"], "verdict": res["verdict"],
                            "trades": res["metrics"]["trades"]})
        return {"label": "HYPOTHETICAL", "curves": curves, "scatter": scatter, "how_to": HOW_TO["compare"],
                "note": "Design-data backtests, each on its own window and basis (DCA on its budget); not ranked."}
