"""Strategy 5: weekly relative-strength rotation between BTC, ETH, SOL and BNB (daily closed candles). Research only.

Rules fixed in PREREGISTRATION_ROTATION.md before the first backtest. At each Monday 00:00 UTC open:
  rank      = each asset's 28-day return on closes up to Sunday (close of the Sunday daily candle vs 28 days before);
              an asset without both closes (or without a Monday open to trade at) is N/A, never zero
  hold      = the top asset, 100%; if its return is <= 0, cash (absolute momentum); fewer than two rankable: cash
  NEUTRAL   = the top two within 2 percentage points: keep the current holding if it is one of them, else the top
  signals   = BTC STRONGER (BTC on top), ALTCOIN STRENGTH (an alt on top), NO TRADE (cash), NEUTRAL (as above)
  costs     = 0.10% fee + 0.05% slippage per side; a switch between two assets is two sides; marked at liquidation value
Price, relative strength and volume only (volume is shown, not used by the rule). News is live context only and never
part of the backtest. Survivorship bias: SOL and BNB were picked knowing they survived.

Self-contained (its forward fingerprint = this module + the tracker core). The tracker serves the other assets through
its candle source as "1d:<SYMBOL>" (services/asset_candles.py) without changing the tracker core.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.backtesting import HOLDOUT_START
from services.strategies.base import DataQuality, StrategyResult, config_hash

STRATEGY_ID = "rotation"
VERSION = "rotation-1"
PREREGISTRATION = "PREREGISTRATION_ROTATION.md"
ASSETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
ALTS = ASSETS[1:]
NAMES = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "BNBUSDT": "BNB"}
EVAL_TIMEFRAMES = ("1d",)
EXTRA_FRAMES = tuple(f"1d:{s}" for s in ALTS)
BACKTEST_TIMEFRAMES = ("1d",) + EXTRA_FRAMES
FORWARD_TIMEFRAME = "1d"
DAY, WEEK = 86_400_000, 7 * 86_400_000
MONDAY = 4 * DAY                       # 1970-01-01 was a Thursday: Mondays are t % WEEK == 4 days
WARMUP_DAYS = 57                       # the longest sensitivity lookback (56 days) + the Sunday close
REPLAY_WEEKS = 26
# Live context only (never a rule, never in the backtest): headlines that name an asset.
NEWS_WORDS = {"BTCUSDT": ("bitcoin", "btc"), "ETHUSDT": ("ethereum", "ether", "eth"), "SOLUSDT": ("solana", "sol"),
              "BNBUSDT": ("bnb", "bnb chain")}
SURVIVORSHIP_NOTE = ("Survivorship bias: SOL and BNB were picked knowing they survived (and grew); coins that faded "
                     "are not in the list, so these results may look better than reality.")

DEFAULTS: dict[str, Any] = {"lookback_days": 28, "cash_filter": "on", "neutral_band_pct": 2.0, "fee_pct": 0.10,
                            "slippage_pct": 0.05}
FIELDS: dict[str, dict] = {
    "lookback_days": {"kind": "int", "lo": 7, "hi": 120, "help": "Rank by the return over this many days"},
    "cash_filter": {"kind": "choice", "choices": ("on", "off"), "help": "Cash when the top asset's return is <= 0"},
    "neutral_band_pct": {"kind": "float", "lo": 0, "hi": 10, "help": "Top two within this many points: keep the holding"},
    "fee_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Fee per side (%)"},
    "slippage_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Slippage per side (%)"},
}
LIMITATIONS = [
    "Design data only (before 2025-10-29): evidence about the past, not validation.",
    SURVIVORSHIP_NOTE,
    "Four assets, one price source (Binance USDT pairs, daily); fixed costs; trades at the Monday open.",
    "Spot only and long only: a weak market means cash, never a short.",
]


def validate_config(payload: dict | None) -> dict[str, Any]:
    payload = dict(payload or {})
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown rotation setting(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for k, default in DEFAULTS.items():
        v, f = payload.get(k, default), FIELDS[k]
        if f["kind"] == "choice":
            if v not in f["choices"]:
                raise ValueError(f"'{k}' must be one of {', '.join(f['choices'])}")
            out[k] = v
            continue
        try:
            if isinstance(v, bool):
                raise ValueError
            if f["kind"] == "int":
                if float(v) != int(float(v)):
                    raise ValueError
                v = int(float(v))
            else:
                v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"'{k}' has the wrong value (expected {f['kind']})") from None
        if not f["lo"] <= v <= f["hi"]:
            raise ValueError(f"'{k}' must be between {f['lo']} and {f['hi']}")
        out[k] = v
    return out


# --------------------------------------------------------------------------- data
def panel(frames: dict[str, pd.DataFrame]) -> dict[str, dict[str, dict[int, float]]]:
    """{field: {symbol: {open_time: value}}} for open / close / volume; frames keyed "1d" (BTC) and "1d:<SYMBOL>"."""
    out: dict[str, dict] = {"open": {}, "close": {}, "volume": {}}
    for sym in ASSETS:
        df = frames.get("1d" if sym == "BTCUSDT" else f"1d:{sym}")
        for fld in out:
            out[fld][sym] = ({} if df is None or not len(df) else
                             {int(t): float(v) for t, v in zip(df["open_time"], df[fld] if fld in df else [0.0] * len(df))})
    return out


def _px(p, fld: str, sym: str, t: int) -> float | None:
    v = p[fld][sym].get(t)
    return None if v is None or not v > 0 else v


def returns_at(p, t: int, lookback: int) -> dict[str, float | None]:
    """Return of each asset over `lookback` days on closes up to the day before t (Sunday for a Monday open)."""
    out = {}
    for sym in ASSETS:
        c1, c0 = _px(p, "close", sym, t - DAY), _px(p, "close", sym, t - DAY - lookback * DAY)
        out[sym] = None if c1 is None or c0 is None else c1 / c0 - 1
    return out


def volume_change(p, t: int, lookback: int) -> dict[str, float | None]:
    """Shown only: volume of the last `lookback` days vs the `lookback` days before (never used by the rule)."""
    out = {}
    for sym in ASSETS:
        v = p["volume"][sym]
        a = [v.get(t - k * DAY) for k in range(1, lookback + 1)]
        b = [v.get(t - k * DAY) for k in range(lookback + 1, 2 * lookback + 1)]
        out[sym] = (None if any(x is None for x in a + b) or sum(b) <= 0 else sum(a) / sum(b) - 1)
    return out


# --------------------------------------------------------------------------- the weekly decision
def decide(returns: dict[str, float | None], current: str | None, cfg: dict) -> tuple[str | None, str]:
    ranked = sorted(((r, s) for s, r in returns.items() if r is not None), key=lambda x: -x[0])
    if len(ranked) < 2:                                   # nothing to rotate between: cash
        return None, "NO TRADE"
    (r1, top), second = ranked[0], ranked[1] if len(ranked) > 1 else None
    if cfg["cash_filter"] == "on" and r1 <= 0:
        return None, "NO TRADE"
    if second is not None and r1 - second[0] <= cfg["neutral_band_pct"] / 100:
        return (current if current in (top, second[1]) else top), "NEUTRAL"
    return top, "BTC STRONGER" if top == "BTCUSDT" else "ALTCOIN STRENGTH"


def tradable_returns(p, t: int, cfg: dict) -> dict[str, float | None]:
    """The returns used at a Monday open: an asset without that open price cannot be bought, so it is N/A."""
    return {s: (r if _px(p, "open", s, t) is not None else None)
            for s, r in returns_at(p, t, cfg["lookback_days"]).items()}


# --------------------------------------------------------------------------- one day of the simulation
def new_state() -> dict:
    return {"cash": 1.0, "holding": None, "units": 0.0, "last_close": {}, "entry_time": None, "entry_value": None,
            "switches": 0, "costs": 0.0, "periods": [], "equity": 1.0, "last_day": None}


def step_day(state: dict, p, t: int, cfg: dict, choose: Callable | None = None) -> tuple[dict, list[dict]]:
    """Advance one UTC day t: on Mondays decide at the open (switch at the open prices), then mark at the close."""
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    st = {**state, "last_close": dict(state["last_close"]), "periods": list(state["periods"])}
    events: list[dict] = []
    if t % WEEK == MONDAY:
        ret = tradable_returns(p, t, cfg)
        new, signal = choose(t, ret, st["holding"]) if choose else decide(ret, st["holding"], cfg)
        events.append({"kind": "week", "monday": t, "holding": new, "signal": signal,
                       "returns": {k: (None if v is None else float(v)) for k, v in ret.items()}})
        if new != st["holding"]:
            if st["holding"] is not None:
                px = _px(p, "open", st["holding"], t) or st["last_close"][st["holding"]]
                gross = st["units"] * px
                st["cash"] += gross * (1 - s) * (1 - f)
                st["costs"] += gross - gross * (1 - s) * (1 - f)
                st["periods"].append({"asset": st["holding"], "entry_time": st["entry_time"], "exit_time": t,
                                      "return": st["cash"] / st["entry_value"] - 1})
            fill = None
            if new is not None:
                o = _px(p, "open", new, t)
                fill = o * (1 + s)
                st["entry_value"] = st["cash"]
                st["units"] = st["cash"] * (1 - f) / fill
                st["costs"] += st["cash"] - st["units"] * o
                st["cash"], st["entry_time"] = 0.0, t
            else:
                st["units"], st["entry_time"], st["entry_value"] = 0.0, None, None
            events.append({"kind": "switch", "time": t, "from": st["holding"], "to": new, "fill": fill})
            st["holding"] = new
            st["switches"] += 1
    for sym in ASSETS:
        c = _px(p, "close", sym, t)
        if c is not None:
            st["last_close"][sym] = c
    held = st["last_close"].get(st["holding"]) if st["holding"] else None
    st["equity"] = st["cash"] + (st["units"] * held * (1 - f - s) if held else 0.0)
    st["last_day"] = t
    return st, events


def simulate(p, cfg: dict, first_day: int, last_day: int, choose: Callable | None = None) -> dict[str, Any]:
    st, equity, weeks, switches = new_state(), [], [], []
    for t in range(first_day, last_day + DAY, DAY):
        st, evs = step_day(st, p, t, cfg, choose)
        weeks += [e for e in evs if e["kind"] == "week"]
        switches += [e for e in evs if e["kind"] == "switch"]
        equity.append({"day": t, "equity": st["equity"], "holding": st["holding"]})
    return {"equity": equity, "weeks": weeks, "switches": switches, "periods": st["periods"], "costs": st["costs"],
            "state": st}


def replay_holding(p, t: int, cfg: dict) -> str | None:
    """The simulated holding just before t: the weekly rule replayed from cash over the last 26 Mondays before t."""
    holding = None
    first = (t - REPLAY_WEEKS * WEEK - MONDAY) // WEEK * WEEK + MONDAY
    for m in range(first, t, WEEK):
        if m >= t - REPLAY_WEEKS * WEEK:
            holding, _ = decide(tradable_returns(p, m, cfg), holding, cfg)
    return holding


# --------------------------------------------------------------------------- metrics, baselines, criteria
def max_dd(values) -> float:
    v = np.asarray(values, float)
    if not len(v):
        return 0.0
    peak = np.maximum.accumulate(v)
    return float(np.max((peak - v) / peak)) * 100


def buy_and_hold(p, sym: str, first_day: int, last_day: int, cfg: dict) -> dict | None:
    """Buy at the first day's open (same costs), marked at liquidation value; None if the asset is not there yet."""
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    o = _px(p, "open", sym, first_day)
    if o is None:
        return None
    units, last, curve = (1 - f) / (o * (1 + s)), o, []
    for t in range(first_day, last_day + DAY, DAY):
        last = _px(p, "close", sym, t) or last
        curve.append(units * last * (1 - f - s))
    return {"net_return_pct": (curve[-1] - 1) * 100, "max_drawdown_pct": max_dd(curve), "curve": curve}


DD_FLOOR_PCT = 0.01                    # drawdown floor for the ratio, so it stays finite (pre-registered)


def ratio(ret_pct: float, dd_pct: float) -> float:
    return ret_pct / max(dd_pct, DD_FLOOR_PCT)


def metrics(sim: dict) -> dict[str, Any]:
    eq = [e["equity"] for e in sim["equity"]]
    n = len(sim["weeks"])
    held = [w["holding"] for w in sim["weeks"]]
    ret, dd = (eq[-1] - 1) * 100 if eq else 0.0, max_dd(eq)
    return {"net_return_pct": ret, "max_drawdown_pct": dd, "return_to_drawdown": ratio(ret, dd),
            "switches": len(sim["switches"]), "trades": len(sim["switches"]), "weeks": n,
            "weeks_in_cash_pct": held.count(None) / n * 100 if n else None,
            "weeks_in_btc_pct": held.count("BTCUSDT") / n * 100 if n else None,
            "weeks_in_alts_pct": sum(h in ALTS for h in held) / n * 100 if n else None,
            "costs_pct": sim["costs"] * 100}


def evaluate_criteria(m: dict, btc: dict, beaten_pct: float | None) -> tuple[list[dict], str]:
    mine, theirs = ratio(m["net_return_pct"], m["max_drawdown_pct"]), ratio(btc["net_return_pct"], btc["max_drawdown_pct"])
    crit = [
        {"id": "C1", "rule": "return / max drawdown >= BTC buy & hold's (same window)", "value": mine,
         "btc": theirs, "passed": mine >= theirs},
        {"id": "C2", "rule": "max drawdown <= BTC buy & hold's", "value": m["max_drawdown_pct"],
         "btc": btc["max_drawdown_pct"], "passed": m["max_drawdown_pct"] <= btc["max_drawdown_pct"]},
        {"id": "C3", "rule": "beats >= 90% of 1,000 random weekly rotations among the same assets", "value": beaten_pct,
         "passed": beaten_pct is not None and beaten_pct >= 90},
    ]
    if m["switches"] < 30:
        return crit, "INCONCLUSIVE"
    return crit, "PASS" if all(c["passed"] for c in crit) else "FAIL"


def random_rotations(p, weeks: list[dict], cfg: dict, first_day: int, last_day: int, runs: int = 1000,
                     seed: int = 7) -> dict:
    """Each week: cash when the strategy was in cash (matched exposure), else a uniformly random asset among those
    with a tradable return that week. Same costs. The net returns of all runs."""
    rng = np.random.default_rng(seed)
    held = {w["monday"]: w["holding"] for w in weeks}
    pool = {w["monday"]: [s for s in ASSETS if w["returns"][s] is not None] for w in weeks}
    out = []
    for _ in range(runs):
        picks = {m: (None if held[m] is None or not pool[m] else pool[m][int(rng.integers(len(pool[m])))]) for m in held}
        sim = simulate(p, cfg, first_day, last_day, choose=lambda t, ret, cur: (picks[t], "random"))
        out.append((sim["equity"][-1]["equity"] - 1) * 100)
    return {"runs": runs, "returns_pct": out, "median_return_pct": float(np.median(out)) if out else None}


def _design(rows: list[list[dict]], holdout_start: int) -> dict[str, pd.DataFrame]:
    frames = {}
    for sym, rr in zip(ASSETS, rows):
        df = pd.DataFrame(rr)
        if len(df):
            df = df[df["close_time"] < holdout_start].sort_values("open_time").reset_index(drop=True)
        frames["1d" if sym == "BTCUSDT" else f"1d:{sym}"] = df
    return frames


def design_span(p, holdout_start: int) -> tuple[int, int]:
    btc = sorted(t for t in p["close"]["BTCUSDT"] if t + DAY - 1 < holdout_start)
    if len(btc) < WARMUP_DAYS + 30:
        raise ValueError("Not enough BTC daily candles before the holdout start.")
    ready = btc[0] + WARMUP_DAYS * DAY
    first = (ready - MONDAY + WEEK - 1) // WEEK * WEEK + MONDAY
    return first, btc[-1]


def run(p, cfg: dict, first: int, last: int) -> dict:
    """The locked run: simulation, metrics and the BTC buy & hold over the same window (also used by the charts)."""
    sim = simulate(p, cfg, first, last)
    btc = buy_and_hold(p, "BTCUSDT", first, last, cfg)
    return {"sim": sim, "metrics": {**metrics(sim), "buy_hold_return_pct": btc["net_return_pct"]}, "btc": btc}


def backtest(rows_btc, rows_eth, rows_sol, rows_bnb, cfg: dict, holdout_start: int = HOLDOUT_START,
             random_runs: int = 1000) -> dict[str, Any]:
    p = panel(_design([rows_btc, rows_eth, rows_sol, rows_bnb], holdout_start))
    first, last = design_span(p, holdout_start)
    assert last + DAY - 1 < holdout_start, "design data reaches the holdout"
    out = run(p, cfg, first, last)
    sim, m, btc = out["sim"], out["metrics"], out["btc"]
    rnd = random_rotations(p, sim["weeks"], cfg, first, last, runs=random_runs)
    beaten = float(np.mean([m["net_return_pct"] > x for x in rnd["returns_pct"]])) * 100 if rnd["returns_pct"] else None
    criteria, verdict = evaluate_criteria(m, btc, beaten)
    sens = []
    for lb in (14, 28, 56):
        for cf in ("on", "off"):
            c2 = {**cfg, "lookback_days": lb, "cash_filter": cf}
            m2 = metrics(simulate(p, c2, first, last))
            sens.append({"lookback_days": lb, "cash_filter": cf, "switches": m2["switches"],
                         "net_return_pct": m2["net_return_pct"], "max_drawdown_pct": m2["max_drawdown_pct"],
                         "return_to_drawdown": m2["return_to_drawdown"],
                         "is_default": lb == cfg["lookback_days"] and cf == cfg["cash_filter"]})
    eq = sim["equity"]
    windows, k0 = [], 0
    while k0 < len(eq):
        k1 = min(len(eq) - 1, k0 + 181)
        e0 = eq[k0 - 1]["equity"] if k0 else 1.0
        sw = [x for x in sim["switches"] if eq[k0]["day"] <= x["time"] <= eq[k1]["day"]]
        windows.append({"start": eq[k0]["day"], "end": eq[k1]["day"] + DAY - 1, "trades": len(sw),
                        "return_pct": (eq[k1]["equity"] / e0 - 1) * 100, "sum_r": None})
        k0 = k1 + 1
    idx = np.linspace(0, len(eq) - 1, min(400, len(eq))).astype(int)
    assets_bh = {}
    for s in ASSETS:
        b = buy_and_hold(p, s, first, last, cfg)
        assets_bh[s] = None if b is None else {k: b[k] for k in ("net_return_pct", "max_drawdown_pct")}
    return {
        "strategy": STRATEGY_ID, "version": VERSION, "config": cfg, "config_hash": config_hash(cfg),
        "period": {"kind": "design", "first_monday": first, "first_day": first, "last_day": last,
                   "last_day_close": last + DAY - 1, "holdout_start": holdout_start, "days": len(eq)},
        "metrics": {**m, "win_rate_pct": None, "profit_factor": None, "expectancy_r": None, "avg_reward_risk": None,
                    "costs_usd": None},
        "criteria": criteria, "verdict": verdict,
        "sample_warning": (f"Only {m['switches']} switches (fewer than 30): too few to judge; the verdict is "
                           "INCONCLUSIVE." if m["switches"] < 30 else None),
        "btc_buy_and_hold": {k: btc[k] for k in ("net_return_pct", "max_drawdown_pct")},
        "buy_and_hold": {k: btc[k] for k in ("net_return_pct", "max_drawdown_pct")},
        "asset_buy_and_hold": assets_bh,
        "random_rotations": {"runs": rnd["runs"], "beaten_pct": beaten, "median_return_pct": rnd["median_return_pct"]},
        "random_entries": {"runs": rnd["runs"], "beaten_pct": beaten, "median_return_pct": rnd["median_return_pct"]},
        "walk_forward": windows, "sensitivity": sens,
        "weeks": sim["weeks"], "switch_list": sim["switches"],
        "equity_sample": [{"time": eq[i]["day"], "equity": eq[i]["equity"]} for i in idx],
        "survivorship_note": SURVIVORSHIP_NOTE, "limitations": LIMITATIONS,
    }


# --------------------------------------------------------------------------- live research signal
def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def evaluate_frames(frames: dict[str, pd.DataFrame], cfg: dict, now: int, data: DataQuality, regime: str | None = None,
                    config_version: int = 1) -> StrategyResult:
    """The weekly rule applied to the last closed daily candle: a preview of the next Monday's decision, with the
    holding the rule would have now (its own decisions replayed over the last 26 Mondays)."""
    base = dict(strategy_id=STRATEGY_ID, strategy_version=VERSION, evaluated_at=now, asset="BTC vs ETH, SOL, BNB",
                timeframe="1d", fees_pct=cfg["fee_pct"], slippage_pct=cfg["slippage_pct"], data=data, regime=regime,
                config_version=config_version, config_hash=config_hash(cfg), strength=None, entry_zone=None, stop=None,
                targets=[], reward_risk=None)
    labels = (["LIVE DATA"] if not data.is_stale else []) + ["CALCULATED", "HYPOTHETICAL"]
    btc = frames.get("1d")
    if btc is None or len(btc) < cfg["lookback_days"] + 2:
        return StrategyResult(**base, signal="NO TRADE", supporting=[], contradictory=[], labels=labels,
                              explanation="Not enough closed BTC daily candles.", rejection="Not enough data.")
    p = panel(frames)
    t = int(btc["close_time"].max()) + 1
    ret = returns_at(p, t, cfg["lookback_days"])
    ranked = sorted(((r, s) for s, r in ret.items() if r is not None), key=lambda x: -x[0])
    supporting = [f"{NAMES[s]} {r * 100:+.2f}% over {cfg['lookback_days']} days" for r, s in ranked]
    contradictory = [f"{s}: N/A (no closed daily candles for {_day(t - DAY)} and {cfg['lookback_days']} days before)"
                     for s, r in ret.items() if r is None]
    problems = (["BTC daily data is stale"] if data.is_stale else []) + (
        [f"{data.missing_candles} missing daily candle(s)"] if data.missing_candles else [])
    if problems:
        return StrategyResult(**base, signal="NO TRADE", supporting=supporting, contradictory=contradictory + problems,
                              explanation="Data problem: no research signal.", rejection="; ".join(problems),
                              labels=labels)
    held = replay_holding(p, t, cfg)
    new, signal = decide(ret, held, cfg)
    nxt = (t - MONDAY + WEEK - 1) // WEEK * WEEK + MONDAY
    now_held = NAMES.get(held, "cash")
    action = "cash" if new is None else NAMES[new]
    expl = {"BTC STRONGER": f"BTC has the strongest {cfg['lookback_days']}-day return.",
            "ALTCOIN STRENGTH": f"{action} has the strongest {cfg['lookback_days']}-day return.",
            "NEUTRAL": f"The top two are within {cfg['neutral_band_pct']:g} points: keep the holding ({action}).",
            "NO TRADE": ("The strongest return is <= 0: cash." if len(ranked) > 1 else
                         "Fewer than two assets can be ranked: nothing to compare, cash.")}[signal]
    return StrategyResult(**base, signal=signal, supporting=supporting, contradictory=contradictory, labels=labels,
                          explanation=(f"{expl} Preview on the close of {_day(t - DAY)}; the rule acts only at the "
                                       f"Monday 00:00 UTC open ({_day(nxt)}). Simulated holding now: {now_held}."),
                          rejection=None if signal != "NO TRADE" else "Cash: no asset with a positive return.")


def news_assets(title: str) -> list[str]:
    import re
    words = set(re.findall(r"[a-z]+", title.lower()))
    text = " ".join(re.findall(r"[a-z]+", title.lower()))
    return [s for s, keys in NEWS_WORDS.items() if any((k in words) if " " not in k else (k in text) for k in keys)]


# --------------------------------------------------------------------------- forward test (strategy_forward.py)
def forward_step(cfg: dict, ctx: dict) -> tuple[list[dict], dict | None]:
    """One closed day. Waits (no event) until every asset's candle of that day is stored, and resumes from the last
    processed day only, so a gap is never skipped."""
    rows, k, state = ctx["rows"], ctx["k"], ctx["state"]
    t = int(rows[k]["open_time"])
    if state is not None and state.get("last_day") != t - DAY:
        return [], state
    lo = t - (2 * cfg["lookback_days"] + 3) * DAY
    frames = {"1d": pd.DataFrame([r for r in rows[max(0, k - 2 * cfg["lookback_days"] - 3):k + 1]])}
    for s in ALTS:
        alt = [r for r in ctx["candles"](f"1d:{s}") if lo <= r["open_time"] <= t]
        if not any(int(r["open_time"]) == t for r in alt):
            return [], state                                    # wait for this asset's candle of the day
        frames[f"1d:{s}"] = pd.DataFrame(alt)
    p = panel(frames)
    st, evs = step_day(state or new_state(), p, t, cfg)
    out = [{"kind": e["kind"], "detail": e} for e in evs]
    out.append({"kind": "mark", "detail": {"close": float(rows[k]["close"]), "btc_open": float(rows[k]["open"]),
                                           "equity": st["equity"], "holding": st["holding"],
                                           "state": {k2: v for k2, v in st.items() if k2 != "periods"},
                                           "trades": st["periods"]}})
    return out, st


def forward_state(events: list[dict]) -> dict | None:
    marks = [e for e in events if e["kind"] == "mark"]
    if not marks:
        return None
    d = marks[-1]["detail"]
    return {**d["state"], "periods": d["trades"]}


def forward_summary(events: list[dict], cfg: dict, done: bool) -> dict:
    marks = [e for e in events if e["kind"] == "mark"]
    out = {"trades": 0, "net_return_pct": None, "expectancy_r": None, "max_drawdown_pct": None, "criteria": [],
           "verdict": None, "btc_return_pct": None, "btc_max_drawdown_pct": None}
    if not marks:
        return out
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    eq = [m["detail"]["equity"] for m in marks]
    units = (1 - f) / (marks[0]["detail"]["btc_open"] * (1 + s))
    bh = [units * m["detail"]["close"] * (1 - f - s) for m in marks]
    switches = sum(1 for e in events if e["kind"] == "switch")
    m = {"net_return_pct": (eq[-1] - 1) * 100, "max_drawdown_pct": max_dd(eq), "switches": switches}
    btc = {"net_return_pct": (bh[-1] - 1) * 100, "max_drawdown_pct": max_dd(bh)}
    out.update(trades=switches, net_return_pct=m["net_return_pct"], max_drawdown_pct=m["max_drawdown_pct"],
               btc_return_pct=btc["net_return_pct"], btc_max_drawdown_pct=btc["max_drawdown_pct"])
    if done:
        crit = [{"id": "F1", "rule": "return / max drawdown >= BTC buy & hold's",
                 "value": ratio(m["net_return_pct"], m["max_drawdown_pct"]),
                 "passed": ratio(m["net_return_pct"], m["max_drawdown_pct"]) >= ratio(btc["net_return_pct"], btc["max_drawdown_pct"])},
                {"id": "F2", "rule": "max drawdown <= BTC buy & hold's", "value": m["max_drawdown_pct"],
                 "passed": m["max_drawdown_pct"] <= btc["max_drawdown_pct"]}]
        out["criteria"] = crit
        out["verdict"] = "INCONCLUSIVE" if switches < 30 else ("PASS" if all(c["passed"] for c in crit) else "FAIL")
    return out


def markdown(r: dict) -> str:
    def p(v):
        return "n/a" if v is None else f"{v:+.2f}%"

    def n(v, d=2):
        return "n/a" if v is None else f"{v:.{d}f}"

    m, bh = r["metrics"], r["btc_buy_and_hold"]
    L = ["# Rotation design backtest (design-data evidence only)", "",
         f"Pre-registered in `{PREREGISTRATION}` (config hash `{r['config_hash']}`), run once. Weekly decisions from "
         f"{_day(r['period']['first_monday'])} (a Monday) to {_day(r['period']['last_day'])} ({r['period']['days']:,} days), "
         f"all daily candles closed before the holdout start {_day(r['period']['holdout_start'])}. Simulated, spot, 100% "
         "in one asset or cash.", "", f"**{r['survivorship_note']}**", ""]
    if r["sample_warning"]:
        L += [f"**{r['sample_warning']}**", ""]
    L += [f"## Verdict: {r['verdict']} (design data, not validation)", "", "| # | Rule | Value | BTC | Passed |",
          "|---|---|---|---|---|"]
    for c in r["criteria"]:
        L.append(f"| {c['id']} | {c['rule']} | {n(c['value'])} | {n(c.get('btc'))} | {'yes' if c['passed'] else 'no'} |")
    L += ["", "## Results and comparisons (buy & hold of each asset reported only; N/A = not listed at the start)", "",
          "| | Rotation | BTC buy & hold | ETH | SOL | BNB |", "|---|---|---|---|---|---|"]
    a = r["asset_buy_and_hold"]
    L.append(f"| Net return | {p(m['net_return_pct'])} | {p(bh['net_return_pct'])} | "
             + " | ".join("N/A" if a[s] is None else p(a[s]["net_return_pct"]) for s in ALTS) + " |")
    L.append(f"| Max drawdown | {m['max_drawdown_pct']:.2f}% | {bh['max_drawdown_pct']:.2f}% | "
             + " | ".join("N/A" if a[s] is None else f"{a[s]['max_drawdown_pct']:.2f}%" for s in ALTS) + " |")
    L += ["", f"Switches {m['switches']} over {m['weeks']} weeks; weeks in cash {n(m['weeks_in_cash_pct'], 1)}%, in BTC "
              f"{n(m['weeks_in_btc_pct'], 1)}%, in an altcoin {n(m['weeks_in_alts_pct'], 1)}%; fees and slippage "
              f"{m['costs_pct']:.2f}% of the starting account; return / max drawdown {n(m['return_to_drawdown'])}.", "",
          f"Random weekly rotations ({r['random_rotations']['runs']} runs, cash in the same weeks, a random asset "
          f"otherwise): the strategy beat {n(r['random_rotations']['beaten_pct'], 1)}% of them (median run "
          f"{p(r['random_rotations']['median_return_pct'])}).", "",
          "## Walk-forward windows (182 days, fixed rules, reported only)", "", "| Window | Switches | Return |", "|---|---|---|"]
    for w in r["walk_forward"]:
        L.append(f"| {_day(w['start'])} to {_day(w['end'])} | {w['trades']} | {p(w['return_pct'])} |")
    L += ["", "## Sensitivity (reported only, never used to choose parameters)", "",
          "| Lookback | Cash filter | Switches | Net return | Max DD | Return / DD |", "|---|---|---|---|---|---|"]
    for x in r["sensitivity"]:
        L.append(f"| {x['lookback_days']} days{' (default)' if x['is_default'] else ''} | {x['cash_filter']} | "
                 f"{x['switches']} | {p(x['net_return_pct'])} | {x['max_drawdown_pct']:.2f}% | {n(x['return_to_drawdown'])} |")
    L += ["", "## Limitations", ""] + [f"- {x}" for x in r["limitations"]] + [""]
    return "\n".join(L)
