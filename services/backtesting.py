"""Walk-forward backtest of the signal engine (Phase 6).

What it does
------------
1. REPLAY. For every closed 4h candle, rebuild exactly what the live app would have
   seen at that moment: the last 500 closed 4h candles, the 1h candles closed by
   then, and the daily candles closed by then. Run the same `analyze` and
   `generate_signal` code the live app runs. Nothing after the decision time is
   visible (enforced by tests).
2. SIMULATE spot, long-only trades:
   * a BUY / STRONG BUY setup that passes every safeguard (except the validation
     gate, which is what we are testing) enters at the NEXT 4h open;
   * stop and target 1 come from the plan built at decision time;
   * exits: stop, target 1, a SELL setup (next open), or the time stop
     (default 18 bars = 3 days, the signal horizon);
   * if one candle touches both the stop and the target, the stop is assumed first;
     if a candle opens beyond the stop, the fill is at that worse open;
   * fees and slippage are charged on both sides.
3. COMPARE with buy-and-hold, an EMA 50/200 crossover and random entries with the
   same number of trades and holding times.
4. WALK FORWARD. Results are split into consecutive out-of-sample test windows.
   The default weights are never fitted, so all of their results are out-of-sample.
   Alternative weight sets are chosen ONLY on the preceding training window and then
   judged on the next test window.
5. VALIDATE against fixed, published criteria. The NO TRADE gate lifts only if all pass.

News cannot be replayed (no historical headline archive), so the news component is
absent in the backtest and its weight is re-normalised away. The report says so.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from bisect import bisect_right
from dataclasses import asdict, dataclass, field, replace
from typing import Callable

import numpy as np
import pandas as pd

from services.signal_engine import (BUY, ENGINE_VERSION, PENDING_VALID_BARS, SELL, STRONG_BUY, EngineConfig,
                                    generate_signal)
from services.technical_analysis import analyze, ema

BAR_MS = 4 * 3_600_000
BARS_PER_YEAR = 6 * 365
DAY_MS = 86_400_000

# Design / holdout split (Phase 6.2). FIXED before any holdout run: rules are chosen on the design period only,
# then tested ONCE on the holdout. A holdout needs only 15 trades (it is ~1 year), stated before it was run.
HOLDOUT_START = 1_761_696_000_000      # 2025-10-29 00:00 UTC
HOLDOUT_MIN_TRADES = 15
PERIODS = ("full", "design", "holdout")
DESIGN_BADGE = "DESIGN PERIOD - in-sample for rule choices, cannot validate"
# 0.9.2: a full run overlaps the design data the rules were developed on, so it is informational only.
FULL_BADGE = "FULL PERIOD - includes design data, cannot validate"

WEIGHT_CANDIDATES = {
    "default": None,  # filled from the engine config at run time
    "trend_heavy": {"trend": 0.45, "momentum": 0.25, "volume": 0.10, "structure": 0.10, "overextension": 0.10, "news": 0.0},
    "momentum_heavy": {"trend": 0.20, "momentum": 0.45, "volume": 0.10, "structure": 0.15, "overextension": 0.10, "news": 0.0},
    "equal": {"trend": 0.2, "momentum": 0.2, "volume": 0.2, "structure": 0.2, "overextension": 0.2, "news": 0.0},
    "no_brake": {"trend": 0.35, "momentum": 0.30, "volume": 0.15, "structure": 0.20, "overextension": 0.0, "news": 0.0},
}


@dataclass
class ValidationCriteria:
    min_trades: int = 30
    min_profit_factor: float = 1.2
    max_drawdown_pct: float = 25.0
    min_net_return_pct: float = 0.0
    min_random_percentile: float = 90.0     # beat at least 90% of random-entry runs
    beat_ema_return_over_dd: bool = True    # better return/max-drawdown than the EMA 50/200 crossover
    min_positive_windows: float = 0.6       # share of out-of-sample windows with a positive result


@dataclass
class BacktestConfig:
    window: int = 500                 # same candle window as the live app
    max_hold_bars: int = 18           # 3 days of 4h bars = the signal horizon
    exit_on_sell: bool = True
    test_window_days: int = 182
    train_window_days: int = 365
    random_runs: int = 200
    seed: int = 7
    years: float | None = 4.0         # backtest span (after warm-up); None = everything available
    period: str = "full"              # "full" | "design" (start -> HOLDOUT_START) | "holdout" (HOLDOUT_START -> end)
    criteria: ValidationCriteria = field(default_factory=ValidationCriteria)

    def __post_init__(self):
        if self.period not in PERIODS:
            raise ValueError(f"period must be one of: {', '.join(PERIODS)}")


def config_hash(engine_cfg: EngineConfig) -> str:
    """Identifies the settings a validation applies to (engine version + weights + thresholds + costs)."""
    d = asdict(engine_cfg)
    d.pop("require_validated_edge", None)
    d.pop("horizon", None)
    blob = json.dumps({"engine": ENGINE_VERSION, **d}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def hypothesis_hash(engine_cfg: EngineConfig, cfg: BacktestConfig) -> str:
    """What a holdout run is a test OF: the trading rules (config_hash: engine version, weights, thresholds, costs,
    entry rule), the trade management (candle window, time stop, exit on SELL) and the fixed holdout start.

    Run length, random seed / runs, walk-forward window sizes and the pass criteria are deliberately left out:
    changing them must not buy a second holdout attempt for the same rules."""
    blob = json.dumps({"config": config_hash(engine_cfg), "window": cfg.window, "max_hold_bars": cfg.max_hold_bars,
                       "exit_on_sell": cfg.exit_on_sell, "holdout_start": HOLDOUT_START}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- replay
@dataclass
class Decision:
    setup: str
    tradable: bool            # buy setup that passed every safeguard except the validation gate
    score: float
    stop: float | None
    target: float | None
    # diagnostics for the funnel (default weights)
    raw_setup: str = ""
    downgrade: str | None = None
    rr_t1: float | None = None
    gate_keys: tuple = ()
    raw_counter_trend: bool = False
    contradictory: bool = False
    extreme_volatility: bool = False
    pending: dict | None = None    # clean pending order (breakout / pullback entry modes only)
    components: dict | None = None  # unrounded component scores (research only: scripts/score_study.py)


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("time")


def replay(c1h: pd.DataFrame, c4h: pd.DataFrame, c1d: pd.DataFrame, engine_cfg: EngineConfig,
           weight_sets: dict[str, dict], cfg: BacktestConfig, start_index: int,
           progress: Callable[[float, str], None] | None = None,
           cancelled: Callable[[], bool] | None = None,
           end_index: int | None = None) -> tuple[list[int], dict[str, list[Decision]]]:
    """Decisions at the close of every 4h bar from start_index to the second-to-last bar (or end_index, exclusive)."""
    ct1h = c1h["close_time"].to_numpy()
    ct1d = c1d["close_time"].to_numpy()
    bars = list(range(start_index, min(len(c4h) - 1, end_index if end_index is not None else len(c4h))))
    decisions: dict[str, list[Decision]] = {k: [] for k in weight_sets}
    cache_1d: dict[int, dict] = {}
    meta_ok = {tf: {"is_stale": False, "gaps": 0, "warnings": []} for tf in ("1h", "4h", "1d")}
    for n, i in enumerate(bars):
        if cancelled and cancelled():
            raise RuntimeError("cancelled")
        t = int(c4h["close_time"].iloc[i])
        w4 = c4h.iloc[max(0, i - cfg.window + 1):i + 1]
        j1 = bisect_right(ct1h, t)          # 1h candles whose close_time <= t
        w1 = c1h.iloc[max(0, j1 - cfg.window):j1]
        jd = bisect_right(ct1d, t)          # daily candles closed by t
        if jd not in cache_1d:
            cache_1d = {jd: analyze(c1d.iloc[max(0, jd - cfg.window):jd], "1d")}
        analyses = {"4h": analyze(w4, "4h"), "1h": analyze(w1, "1h"), "1d": cache_1d[jd]}
        close = float(c4h["close"].iloc[i])
        price = {"price": close, "provider": "backtest", "quote": "USDT", "observed_at": t,
                 "is_stale": False, "status": "fresh", "warnings": []}
        for name, weights in weight_sets.items():
            ec = EngineConfig(**{**asdict(engine_cfg), "weights": weights, "require_validated_edge": False})
            s = generate_signal(analyses, meta_ok, price, None, [], ec, None, t)
            buy = s["setup"] in (BUY, STRONG_BUY)
            plan = s.get("plan") or {}
            dg = s.get("diagnostics") or {}
            gate_keys = tuple(dg.get("gate_keys") or ())
            # A pending order only counts when no safeguard other than the validation gate fired and the raw
            # setup did not fight the daily trend (the same safeguards a standard entry must pass).
            pending = s.get("pending_order")
            if pending and (not set(gate_keys) <= {"not_validated"} or dg.get("raw_counter_trend")):
                pending = None
            decisions[name].append(Decision(
                setup=s["setup"], tradable=buy and s["signal"] == s["setup"],
                # the UNROUNDED score: the setup is decided on it, so the funnel must count from it too
                score=dg.get("score_raw", s["score"]),
                stop=plan.get("stop"), target=(plan.get("targets") or [{}])[0].get("price"),
                raw_setup=dg.get("raw_setup", s["setup"]), downgrade=dg.get("downgrade"), rr_t1=dg.get("rr_t1"),
                gate_keys=gate_keys, raw_counter_trend=bool(dg.get("raw_counter_trend")),
                contradictory=bool(dg.get("contradictory")), extreme_volatility=bool(dg.get("extreme_volatility")),
                pending=pending, components=dg.get("component_scores")))
        if progress and n % 25 == 0:
            progress(n / max(1, len(bars)), f"Replaying bar {n + 1:,} of {len(bars):,}")
    return bars, decisions


# --------------------------------------------------------------------------- simulation
@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    stop: float
    target: float
    reason: str
    ret: float          # net return after costs
    r_multiple: float
    bars: int
    entry_type: str = "standard"    # "standard" | "breakout" | "pullback"


def _cost(engine_cfg: EngineConfig) -> float:
    return (engine_cfg.fee_pct + engine_cfg.slippage_pct) / 100   # per side


PENDING_STATS = ("created", "replaced", "expired", "cancelled_by_sell", "skipped_gap", "filled",
                 "dropped_for_standard_entry", "open_at_window_end")


def simulate(c4h: pd.DataFrame, bars: list[int], decisions: list[Decision], engine_cfg: EngineConfig,
             cfg: BacktestConfig, lo: int = 0, hi: int | None = None,
             stats: dict | None = None) -> tuple[list[Trade], np.ndarray]:
    """Trade decisions[lo:hi] (indexes into `bars`). Returns trades and the equity curve (one point per bar).

    Pending orders (entry modes "breakout" / "pullback") are only handled while flat:
      * a newer pending order replaces an older one; a SELL setup cancels it; it expires after
        PENDING_VALID_BARS bars (counted from the bar after the decision);
      * breakout: a 4h CLOSE above the trigger buys at the NEXT bar's open;
      * pullback: a low STRICTLY below the limit fills in that bar at min(open, limit) - a touch does not fill;
      * a fill at or beyond the stop or the target is skipped ("skipped_gap");
      * in a pullback's fill bar only the stop is checked (pessimistic), not the target.
    If `stats` is given it is filled with pending-order counts (PENDING_STATS).
    """
    hi = len(bars) if hi is None else hi
    o, h, l, c = (c4h[k].to_numpy() for k in ("open", "high", "low", "close"))
    ct = c4h["close_time"].to_numpy()
    side = _cost(engine_cfg)
    equity, eq_curve, trades = 1.0, [], []
    pos = None    # dict(entry_bar, entry, entry_eff, stop, target, type)
    pend = None   # dict(order, last_bar): a live pending order
    armed = None  # breakout order whose trigger closed: buys at the next bar's open
    st = {k: 0 for k in PENDING_STATS}

    def open_at(b: int, px: float, stop: float, target: float, kind: str) -> dict:
        return {"entry_bar": b, "entry": px, "entry_eff": px * (1 + side), "stop": stop, "target": target, "type": kind}

    for k in range(lo, hi):
        i = bars[k]          # decision bar: decisions[k] is made at its close
        b = i + 1            # orders from that decision execute in the next bar
        d = decisions[k]
        no_target_this_bar = False
        # -- 1. act on the decision made at the close of bar i (and on a breakout that triggered at that close)
        if pos is None:
            if d.setup == SELL and (pend is not None or armed is not None):
                st["cancelled_by_sell"] += 1
                pend = armed = None
            if armed is not None:
                if armed["stop"] < o[b] < armed["target"]:
                    pos = open_at(b, o[b], armed["stop"], armed["target"], "breakout")
                    st["filled"] += 1
                else:
                    st["skipped_gap"] += 1
                armed = None
            if pos is None and d.tradable and d.stop and d.target:
                if d.stop < o[b] < d.target:          # skip if the open already gapped through a level
                    if pend is not None:
                        st["dropped_for_standard_entry"] += 1
                        pend = None
                    pos = open_at(b, o[b], d.stop, d.target, "standard")
            if pos is None and d.pending is not None:
                if pend is not None:
                    st["replaced"] += 1
                pend = {"order": d.pending, "last_bar": b + PENDING_VALID_BARS - 1}
                st["created"] += 1
        # -- 1b. a pullback limit resting during bar b
        if pos is None and pend is not None and pend["order"]["type"] == "pullback":
            od = pend["order"]
            if l[b] < od["price"]:
                fill = min(o[b], od["price"])
                pend = None
                if od["stop"] < fill < od["target"]:
                    pos = open_at(b, fill, od["stop"], od["target"], "pullback")
                    st["filled"] += 1
                    no_target_this_bar = True
                else:
                    st["skipped_gap"] += 1
        # -- 2. manage an open position during bar b
        exit_px, reason = None, None
        if pos is not None:
            if b > pos["entry_bar"] and o[b] <= pos["stop"]:
                exit_px, reason = o[b], "stop (gap)"
            elif l[b] <= pos["stop"]:
                exit_px, reason = pos["stop"], "stop"            # stop first if both touched (pessimistic)
            elif not no_target_this_bar and h[b] >= pos["target"]:
                exit_px, reason = pos["target"], "target"
        if pos is not None and exit_px is None:
            held = b - pos["entry_bar"] + 1
            nxt = decisions[k + 1] if k + 1 < hi else None      # decision at the close of bar b
            if nxt is None:
                exit_px, reason = c[b], "end of window"
            elif cfg.exit_on_sell and nxt.setup == SELL:
                exit_px, reason = c[b], "sell setup"            # ~ next open; close used as the fill
            elif held >= cfg.max_hold_bars:
                exit_px, reason = c[b], "time stop"
        if pos is not None and exit_px is not None:
            net = exit_px * (1 - side) / pos["entry_eff"] - 1
            risk = pos["entry"] - pos["stop"]
            trades.append(Trade(pos["entry_bar"], b, int(ct[pos["entry_bar"]] - BAR_MS + 1), int(ct[b]),
                                float(pos["entry"]), float(exit_px), float(pos["stop"]), float(pos["target"]),
                                reason, float(net), float((exit_px - pos["entry"]) / risk) if risk > 0 else 0.0,
                                b - pos["entry_bar"] + 1, pos["type"]))
            equity *= 1 + net
            pos = None
        # -- 3. a breakout trigger is judged on the CLOSE of bar b; the buy happens at the next open
        if pos is None and pend is not None and pend["order"]["type"] == "breakout" and c[b] > pend["order"]["price"]:
            armed, pend = pend["order"], None
        # -- 4. expiry after PENDING_VALID_BARS bars
        if pend is not None and b >= pend["last_bar"]:
            st["expired"] += 1
            pend = None
        # -- 5. mark to market at the close of bar b
        eq_curve.append(equity * (c[b] * (1 - side) / pos["entry_eff"]) if pos is not None else equity)
    if pend is not None or armed is not None:
        st["open_at_window_end"] += 1
    if stats is not None:
        stats.update(st)
    return trades, np.array(eq_curve, dtype=float)


# --------------------------------------------------------------------------- metrics
def max_drawdown(eq: np.ndarray) -> float:
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.max(1 - eq / peak)) * 100


def metrics(trades: list[Trade], eq: np.ndarray, n_bars: int, engine_cfg: EngineConfig) -> dict:
    rets = [t.ret for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    total = float(eq[-1] - 1) * 100 if len(eq) else 0.0
    years = max(n_bars / BARS_PER_YEAR, 1e-9)
    cagr = float(((eq[-1]) ** (1 / years) - 1) * 100) if len(eq) and eq[-1] > 0 else -100.0
    bar_rets = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([])
    sharpe = float(np.mean(bar_rets) / np.std(bar_rets) * math.sqrt(BARS_PER_YEAR)) if len(bar_rets) > 1 and np.std(bar_rets) > 0 else 0.0
    mdd = max_drawdown(eq)
    exposure = sum(t.bars for t in trades) / max(1, n_bars) * 100
    side = _cost(engine_cfg) * 100
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    return {
        "trades": len(trades),
        "net_return_pct": round(total, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(mdd, 2),
        "return_over_max_dd": round(total / mdd, 3) if mdd > 0 else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) != 0 else (None if not wins else float("inf")),
        "win_rate_pct": round(len(wins) / len(rets) * 100, 1) if rets else None,
        "avg_trade_pct": round(float(np.mean(rets)) * 100, 3) if rets else None,
        "avg_r_multiple": round(float(np.mean([t.r_multiple for t in trades])), 3) if trades else None,
        "avg_bars_held": round(float(np.mean([t.bars for t in trades])), 1) if trades else None,
        "exposure_pct": round(exposure, 1),
        "turnover_sides": 2 * len(trades),
        "costs_paid_pct": round(2 * side * len(trades), 2),
        "sharpe_annualised": round(sharpe, 2),
        "exit_reasons": reasons,
    }


def _json_safe(x):
    if isinstance(x, float) and (math.isinf(x) or math.isnan(x)):
        return None if math.isnan(x) else "inf"
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_json_safe(v) for v in x]
    return x


# --------------------------------------------------------------------------- baselines
def buy_and_hold(c4h: pd.DataFrame, b0: int, b1: int, engine_cfg: EngineConfig) -> tuple[dict, np.ndarray]:
    side = _cost(engine_cfg)
    o, c = c4h["open"].to_numpy(), c4h["close"].to_numpy()
    entry = o[b0] * (1 + side)
    eq = c[b0:b1 + 1] * (1 - side) / entry
    n = b1 - b0 + 1
    return {"net_return_pct": round(float(eq[-1] - 1) * 100, 2), "max_drawdown_pct": round(max_drawdown(eq), 2),
            "return_over_max_dd": round(float(eq[-1] - 1) * 100 / max_drawdown(eq), 3) if max_drawdown(eq) > 0 else None,
            "cagr_pct": round(float(((eq[-1]) ** (BARS_PER_YEAR / n) - 1) * 100), 2), "trades": 1, "exposure_pct": 100.0}, eq


def ema_crossover(c4h: pd.DataFrame, b0: int, b1: int, engine_cfg: EngineConfig) -> tuple[dict, np.ndarray]:
    """Long while EMA50 > EMA200 on 4h closes (decided at a close, executed at the next open)."""
    side = _cost(engine_cfg)
    close = c4h["close"]
    e50, e200 = ema(close, 50).to_numpy(), ema(close, 200).to_numpy()
    o, c = c4h["open"].to_numpy(), c4h["close"].to_numpy()
    equity, eq, in_pos, entry_eff, trades, bars_in = 1.0, [], False, 0.0, 0, 0
    for b in range(b0, b1 + 1):
        want = bool(e50[b - 1] > e200[b - 1]) if not (np.isnan(e50[b - 1]) or np.isnan(e200[b - 1])) else False
        if want and not in_pos:
            in_pos, entry_eff, trades = True, o[b] * (1 + side), trades + 1
        elif not want and in_pos:
            equity *= o[b] * (1 - side) / entry_eff
            in_pos = False
        if in_pos:
            bars_in += 1
        eq.append(equity * (c[b] * (1 - side) / entry_eff) if in_pos else equity)
    eq = np.array(eq)
    mdd = max_drawdown(eq)
    tot = float(eq[-1] - 1) * 100
    return {"net_return_pct": round(tot, 2), "max_drawdown_pct": round(mdd, 2),
            "return_over_max_dd": round(tot / mdd, 3) if mdd > 0 else None, "trades": trades,
            "exposure_pct": round(bars_in / max(1, len(eq)) * 100, 1)}, eq


def random_entries(c4h: pd.DataFrame, b0: int, b1: int, trades: list[Trade], engine_cfg: EngineConfig,
                   runs: int, seed: int) -> dict:
    """Same number of trades and the same holding times as the strategy, at random non-overlapping times."""
    if not trades:
        return {"runs": 0, "note": "strategy made no trades"}
    side = _cost(engine_cfg)
    o, c = c4h["open"].to_numpy(), c4h["close"].to_numpy()
    holds = [t.bars for t in trades]
    rng = random.Random(seed)
    results = []
    span = b1 - b0
    for _ in range(runs):
        hs = holds[:]
        rng.shuffle(hs)
        total_hold = sum(hs)
        if total_hold >= span:
            break
        free = span - total_hold
        cuts = sorted(rng.randint(0, free) for _ in hs)
        eq, pos, prev = 1.0, b0, 0
        for cut, hold in zip(cuts, hs):
            pos += cut - prev
            prev = cut
            entry, exit_b = pos, pos + hold - 1
            eq *= c[exit_b] * (1 - side) / (o[entry] * (1 + side))
            pos = exit_b + 1
        results.append((eq - 1) * 100)
    if not results:
        return {"runs": 0, "note": "not enough room for random placement"}
    arr = np.array(results)
    return {"runs": len(arr), "median_return_pct": round(float(np.median(arr)), 2),
            "p10_return_pct": round(float(np.percentile(arr, 10)), 2),
            "p90_return_pct": round(float(np.percentile(arr, 90)), 2), "values": arr.tolist()}


# --------------------------------------------------------------------------- forward-return hit rate
def hit_rate(c4h: pd.DataFrame, bars: list[int], decisions: list[Decision], cfg: BacktestConfig,
             engine_cfg: EngineConfig, last_bar: int | None = None) -> dict:
    """Did BUY setups precede a forward return above round-trip costs over the signal horizon?

    Classification view required by the spec. There are no probabilities, so Brier
    score and calibration are not applicable to the rule engine.
    """
    o, c = c4h["open"].to_numpy(), c4h["close"].to_numpy()
    cost = 2 * _cost(engine_cfg)
    y, p = [], []
    for k, i in enumerate(bars):
        e = i + 1
        x = e + cfg.max_hold_bars - 1
        if x >= len(c) or (last_bar is not None and x > last_bar):   # labels never look past the period's end
            break
        y.append(c[x] / o[e] - 1 > cost)
        p.append(decisions[k].tradable)
    y, p = np.array(y), np.array(p)
    tp = int(np.sum(y & p)); fp = int(np.sum(~y & p)); fn = int(np.sum(y & ~p)); tn = int(np.sum(~y & ~p))
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    spec = tn / (tn + fp) if tn + fp else None
    bal = (rec + spec) / 2 if rec is not None and spec is not None else None
    return {"definition": f"label = close {cfg.max_hold_bars} bars after the next open beats round-trip costs; "
                          "prediction = tradable BUY setup",
            "base_rate": round(float(np.mean(y)), 3) if len(y) else None,
            "signals": int(np.sum(p)), "precision": None if prec is None else round(prec, 3),
            "recall": None if rec is None else round(rec, 3),
            "balanced_accuracy": None if bal is None else round(bal, 3),
            "brier_score": "n/a - the rule engine outputs no probabilities"}


# --------------------------------------------------------------------------- funnel diagnostics
def funnel(decisions: list[Decision], trades: list[Trade], engine_cfg: EngineConfig,
           pending_stats: dict | None = None) -> dict:
    """Where buy opportunities are lost: score -> reward/risk check -> each safeguard -> trades.

    Decision.score is the UNROUNDED score, the same number the setup is decided on, so
    bars_score_at_or_above_buy equals raw_buy_setups. (Before Phase 6.2 the funnel counted the score
    rounded to 3 decimals, so e.g. 0.2496 -> 0.250 was counted as >= 0.25 although its setup was HOLD.)
    """
    n = len(decisions)
    scores = np.array([d.score for d in decisions]) if n else np.array([0.0])
    raw_buy = [d for d in decisions if d.raw_setup in (BUY, STRONG_BUY)]
    low_rr = [d for d in raw_buy if d.downgrade == "low_rr"]
    no_plan = [d for d in raw_buy if d.downgrade == "no_plan"]
    after_rr = [d for d in raw_buy if d.downgrade is None]
    blocked: dict[str, int] = {}
    for d in after_rr:
        for k in d.gate_keys:
            if k != "not_validated":
                blocked[k] = blocked.get(k, 0) + 1
    tradable = [d for d in decisions if d.tradable]
    rr_vals = np.array([d.rr_t1 for d in low_rr if d.rr_t1 is not None])
    # Raw buys that only failed reward/risk: would they also have been blocked by a safeguard?
    def clean(d: Decision) -> bool:
        return not (d.raw_counter_trend or d.contradictory or d.extreme_volatility)
    what_if = {}
    for x in (1.5, 1.2, 1.0, 0.8):
        what_if[f"min_rr_{x}"] = sum(1 for d in raw_buy if d.rr_t1 is not None and d.rr_t1 >= x and clean(d))
    pct = lambda q: round(float(np.percentile(scores, q)), 3)
    return {
        "bars": n,
        "score_percentiles": {"p50": pct(50), "p75": pct(75), "p90": pct(90), "p95": pct(95), "p99": pct(99),
                              "max": round(float(scores.max()), 3), "min": round(float(scores.min()), 3)},
        "bars_score_at_or_above_buy": int(np.sum(scores >= engine_cfg.buy_threshold)),
        "bars_score_at_or_below_sell": int(np.sum(scores <= engine_cfg.sell_threshold)),
        "rounding_check": {"bars_rounded_score_at_or_above_buy": int(np.sum(np.round(scores, 3) >= engine_cfg.buy_threshold)),
                           "note": "Count using the score rounded to 3 decimals (the pre-6.2 funnel). Any excess over "
                                   "raw_buy_setups is bars whose unrounded score was just below the threshold."},
        "raw_buy_setups": len(raw_buy),
        "downgraded_low_rr": len(low_rr),
        "downgraded_no_plan": len(no_plan),
        "low_rr_values": {"median": round(float(np.median(rr_vals)), 2) if len(rr_vals) else None,
                          "p75": round(float(np.percentile(rr_vals, 75)), 2) if len(rr_vals) else None,
                          "share_at_or_above_1.0": round(float(np.mean(rr_vals >= 1.0)), 3) if len(rr_vals) else None,
                          "share_no_room": round(float(np.mean(rr_vals <= 0)), 3) if len(rr_vals) else None},
        "buy_setups_after_rr": len(after_rr),
        "blocked_by_safeguard": dict(sorted(blocked.items(), key=lambda kv: -kv[1])),
        "raw_buys_also_flagged": {"counter_trend": sum(d.raw_counter_trend for d in raw_buy),
                                  "contradictory": sum(d.contradictory for d in raw_buy),
                                  "extreme_volatility": sum(d.extreme_volatility for d in raw_buy)},
        "tradable_buy_bars": len(tradable),
        "pending_order_bars": sum(1 for d in decisions if d.pending is not None),
        "pending_orders": {k: (pending_stats or {}).get(k, 0) for k in PENDING_STATS},
        "trades": len(trades),
        "trades_by_entry": {kind: sum(1 for t in trades if t.entry_type == kind)
                            for kind in ("standard", "breakout", "pullback")},
        "note": "Consecutive bars often repeat the same setup, so 'bars' are not independent opportunities. "
                "One position at a time: tradable bars during an open trade cannot start a new one.",
        "what_if_min_rr": {**what_if, "note": "DIAGNOSTIC ONLY: raw buy bars whose reward/risk would pass a lower "
                                              "minimum and that no safeguard flags. Not a backtest of that setting."},
    }


# --------------------------------------------------------------------------- walk-forward
def windows(bars: list[int], c4h: pd.DataFrame, cfg: BacktestConfig) -> list[tuple[int, int]]:
    """Consecutive out-of-sample test windows, as index ranges into `bars`."""
    times = [int(c4h["close_time"].iloc[i]) for i in bars]
    out, k = [], 0
    step = cfg.test_window_days * DAY_MS
    while k < len(bars):
        end_t = times[k] + step
        j = bisect_right(times, end_t)
        if j - k < 30:
            break
        out.append((k, j))
        k = j
    return out


def evaluate_criteria(m: dict, ema_m: dict, rnd: dict, pos_share: float | None, cr: ValidationCriteria) -> list[dict]:
    pf = m["profit_factor"]
    pf_val = float("inf") if pf == "inf" else pf
    pct = None
    if rnd.get("values"):
        pct = round(float(np.mean(np.array(rnd["values"]) < m["net_return_pct"])) * 100, 1)
    checks = [
        ("Enough trades", m["trades"] >= cr.min_trades, f"{m['trades']} trades (need >= {cr.min_trades})"),
        ("Profit factor", pf_val is not None and pf_val >= cr.min_profit_factor,
         f"{pf if pf is not None else 'n/a (no losing or no trades)'} (need >= {cr.min_profit_factor})"),
        ("Positive net return after costs", m["net_return_pct"] > cr.min_net_return_pct, f"{m['net_return_pct']}%"),
        ("Max drawdown", m["max_drawdown_pct"] <= cr.max_drawdown_pct,
         f"{m['max_drawdown_pct']}% (limit {cr.max_drawdown_pct}%)"),
        ("Beats random entries", pct is not None and pct >= cr.min_random_percentile,
         f"better than {pct}% of random runs (need >= {cr.min_random_percentile}%)" if pct is not None else "no random runs"),
        ("Beats EMA 50/200 on return/drawdown",
         (not cr.beat_ema_return_over_dd) or (m["return_over_max_dd"] is not None and ema_m["return_over_max_dd"] is not None
                                              and m["return_over_max_dd"] > ema_m["return_over_max_dd"]),
         f"{m['return_over_max_dd'] if m['return_over_max_dd'] is not None else 'n/a (no drawdown or no trades)'} "
         f"vs EMA {ema_m['return_over_max_dd']}"),
        ("Consistent across windows", pos_share is not None and pos_share >= cr.min_positive_windows,
         (f"{round(pos_share * 100)}% of windows with trades were profitable" if pos_share is not None
          else "no window had any trades") + f" (need >= {round(cr.min_positive_windows * 100)}%)"),
    ]
    return [{"name": n, "passed": bool(ok), "detail": d} for n, ok, d in checks]


def run_backtest(c1h_rows: list[dict], c4h_rows: list[dict], c1d_rows: list[dict], engine_cfg: EngineConfig,
                 cfg: BacktestConfig | None = None, progress: Callable[[float, str], None] | None = None,
                 cancelled: Callable[[], bool] | None = None, now_ms: int = 0) -> dict:
    cfg = cfg or BacktestConfig()
    c1h, c4h, c1d = _frame(c1h_rows), _frame(c4h_rows), _frame(c1d_rows)
    if min(len(c1h), len(c4h), len(c1d)) == 0:
        raise ValueError("No historical candles. Download history first (scripts/download_history.py).")
    # Warm-up: need a full window on every timeframe before the first decision.
    first_t = max(int(c4h["close_time"].iloc[min(cfg.window, len(c4h) - 1)]),
                  int(c1h["close_time"].iloc[min(cfg.window, len(c1h) - 1)]),
                  int(c1d["close_time"].iloc[min(220, len(c1d) - 1)]))
    ct4 = c4h["close_time"].to_numpy()
    start = int(np.searchsorted(ct4, first_t))
    if cfg.years:
        start = max(start, len(c4h) - 1 - int(cfg.years * BARS_PER_YEAR))
    # Period split. A decision at the close of bar i trades in bar i+1, so:
    #   design  = decisions whose NEXT bar also closes before HOLDOUT_START (no design trade touches the holdout);
    #   holdout = decisions made at or after HOLDOUT_START (earlier candles are only indicator warm-up).
    end = len(c4h) - 1
    if cfg.period == "design":
        end = min(end, int(np.searchsorted(ct4, HOLDOUT_START - BAR_MS)))
    elif cfg.period == "holdout":
        start = max(start, int(np.searchsorted(ct4, HOLDOUT_START)))
    if end - start < 200:
        raise ValueError(f"Not enough history after warm-up for period '{cfg.period}' ({max(0, end - start)} bars). "
                         "Download more years.")
    criteria_cfg = replace(cfg.criteria, min_trades=HOLDOUT_MIN_TRADES) if cfg.period == "holdout" else cfg.criteria

    weight_sets = {k: (v if v is not None else dict(engine_cfg.weights)) for k, v in WEIGHT_CANDIDATES.items()}
    bars, decisions = replay(c1h, c4h, c1d, engine_cfg, weight_sets, cfg, start, progress, cancelled, end_index=end)
    if progress:
        progress(0.97, "Simulating trades and baselines")

    b0, b1 = bars[0] + 1, bars[-1] + 1
    pending_stats: dict = {}
    trades, eq = simulate(c4h, bars, decisions["default"], engine_cfg, cfg, stats=pending_stats)
    m = metrics(trades, eq, len(eq), engine_cfg)
    bh_m, bh_eq = buy_and_hold(c4h, b0, b1, engine_cfg)
    ema_m, ema_eq = ema_crossover(c4h, b0, b1, engine_cfg)
    rnd = random_entries(c4h, b0, b1, trades, engine_cfg, cfg.random_runs, cfg.seed)

    # Walk-forward windows: default (never fitted) and "chosen on the previous training window".
    wins = windows(bars, c4h, cfg)
    win_rows, chosen_eq, chosen_trades = [], [], []
    times = [int(c4h["close_time"].iloc[i]) for i in bars]
    for lo, hi in wins:
        t_d, e_d = simulate(c4h, bars, decisions["default"], engine_cfg, cfg, lo, hi)
        tr_lo = bisect_right(times, times[lo] - cfg.train_window_days * DAY_MS)
        pick, pick_score, why = "default", None, "no full training year yet"
        if lo - tr_lo >= 0.8 * cfg.train_window_days * 6:   # only select when a full training window exists
            why = "no weight set made >= 5 trades in the training year"
            for name in weight_sets:
                t_tr, e_tr = simulate(c4h, bars, decisions[name], engine_cfg, cfg, tr_lo, lo)
                if len(t_tr) < 5:
                    continue
                mm = metrics(t_tr, e_tr, len(e_tr), engine_cfg)
                sc = mm["return_over_max_dd"] if mm["return_over_max_dd"] is not None else -1e9
                if pick_score is None or sc > pick_score:
                    pick, pick_score, why = name, sc, "best return/drawdown in the training year"
        t_c, e_c = simulate(c4h, bars, decisions[pick], engine_cfg, cfg, lo, hi)
        chosen_trades += t_c
        scale = chosen_eq[-1] if chosen_eq else 1.0
        chosen_eq += list(e_c * scale)
        bh_w, _ = buy_and_hold(c4h, bars[lo] + 1, bars[hi - 1] + 1, engine_cfg)
        win_rows.append({"start": times[lo], "end": times[hi - 1], "default_return_pct": round(float(e_d[-1] - 1) * 100, 2),
                         "default_trades": len(t_d), "chosen_weights": pick, "chosen_reason": why,
                         "chosen_return_pct": round(float(e_c[-1] - 1) * 100, 2), "chosen_trades": len(t_c),
                         "buy_hold_return_pct": bh_w["net_return_pct"]})
    traded = [w for w in win_rows if w["default_trades"] > 0]
    # Consistency is judged on windows where the strategy actually traded; flat windows are reported separately.
    pos_share = (sum(1 for w in traded if w["default_return_pct"] > 0) / len(traded)) if traded else None
    chosen_m = metrics(chosen_trades, np.array(chosen_eq), len(chosen_eq), engine_cfg) if chosen_eq else None

    criteria = evaluate_criteria(m, ema_m, rnd, pos_share, criteria_cfg)
    all_passed = all(c["passed"] for c in criteria)
    # Only the holdout is out-of-sample for the rules: design and full runs can never validate, whatever their
    # numbers. (Whether THIS holdout run counts - only the first run of a hypothesis does - is decided by the
    # backtest service's holdout registry.)
    validated = all_passed and cfg.period == "holdout"
    passed_txt = f"{'All' if all_passed else sum(c['passed'] for c in criteria)} of {len(criteria)} criteria passed"
    if cfg.period == "design":
        verdict = f"{DESIGN_BADGE}. {passed_txt} on design data."
    elif cfg.period == "full":
        verdict = f"{FULL_BADGE}. {passed_txt}."
    elif validated:
        verdict = "VALIDATED: every criterion passed on out-of-sample data."
    else:
        verdict = "NOT VALIDATED: no demonstrated edge after costs - the live signal stays NO TRADE."

    def thin(arr: np.ndarray, n: int = 600) -> list[float]:
        if len(arr) <= n:
            return [round(float(x), 5) for x in arr]
        idx = np.linspace(0, len(arr) - 1, n).astype(int)
        return [round(float(arr[i]), 5) for i in idx]

    eq_times = [int(c4h["close_time"].iloc[bars[k] + 1]) for k in range(len(bars))]
    idx = np.linspace(0, len(eq_times) - 1, min(600, len(eq_times))).astype(int)
    result = {
        "engine": ENGINE_VERSION,
        "config_hash": config_hash(engine_cfg),
        "created_at": now_ms,
        "entry_mode": engine_cfg.entry_mode,
        "period_kind": cfg.period,
        "design_badge": DESIGN_BADGE if cfg.period == "design" else None,
        "period_badge": {"design": DESIGN_BADGE, "full": FULL_BADGE}.get(cfg.period),
        "hypothesis_hash": hypothesis_hash(engine_cfg, cfg),
        "holdout_start": HOLDOUT_START,
        "period": {"kind": cfg.period, "start": times[0], "end": times[-1], "bars": len(bars),
                   "years": round(len(bars) / BARS_PER_YEAR, 2)},
        "settings": {"backtest": {k: v for k, v in asdict(cfg).items() if k != "criteria"},
                     "engine": {**asdict(engine_cfg), "require_validated_edge": False},
                     "criteria": asdict(criteria_cfg)},
        "strategy": m,
        "baselines": {"buy_and_hold": bh_m, "ema_50_200": ema_m,
                      "random_entries": {k: v for k, v in rnd.items() if k != "values"}},
        "walk_forward": {"windows": win_rows, "positive_window_share": None if pos_share is None else round(pos_share, 3),
                         "windows_with_trades": len(traded), "windows_total": len(win_rows),
                         "chosen_on_train": chosen_m,
                         "note": "Default weights are never fitted, so their results are out-of-sample. 'Chosen' picks, "
                                 "for each test window, the weight set with the best return/drawdown on the PREVIOUS "
                                 f"{cfg.train_window_days} days only."},
        "hit_rate": hit_rate(c4h, bars, decisions["default"], cfg, engine_cfg, last_bar=b1),
        "funnel": funnel(decisions["default"], trades, engine_cfg, pending_stats),
        "criteria": criteria,
        "validated": validated,
        "verdict": verdict,
        "limitations": [
            "News is not replayed (no historical headline archive): the news component is absent and its weight re-normalised.",
            "Fills assume the next 4h open plus slippage; real fills, especially stops in fast markets, can be worse.",
            "One position at a time, 100% of the simulated account per trade; position sizing is handled separately.",
            "Past performance does not guarantee future results; market regimes change.",
        ],
        "equity": {"time": [eq_times[i] for i in idx], "strategy": thin(eq), "buy_and_hold": thin(bh_eq),
                   "ema_50_200": thin(ema_eq)},
        "trades": [asdict(t) for t in trades[-300:]],
    }
    return _json_safe(result)
