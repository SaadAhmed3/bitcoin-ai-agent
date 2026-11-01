"""Rotation survivorship stress test (REPORTED ONLY; PREREGISTRATION_ROTATION_STRESS.md). Not a strategy.

Runs Rotation's own weekly rule (rotation.decide, the same costs and configuration) over a wider, point-in-time
universe: the top 20 by market cap on 2021-06-27 without stablecoins and wrapped BTC, plus LUNA and FTT. Assets that
later collapsed or were delisted stay in, up to the end of their history. The Rotation strategy, its stored result,
its verdict and its forward test are not touched; nothing is written to strategy_backtests.

The simulation mirrors rotation.py for any list of assets; with rotation's own four assets it reproduces
rotation.simulate exactly (tested), so the study cannot drift from the strategy's rule.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.backtesting import HOLDOUT_START
from services.strategies import rotation as rot
from services.strategies.base import config_hash

UNIVERSE = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",                       # the strategy's own four first
            "ADAUSDT", "DOGEUSDT", "XRPUSDT", "DOTUSDT", "UNIUSDT", "BCHUSDT", "LTCUSDT", "LINKUSDT", "MATICUSDT",
            "THETAUSDT", "XLMUSDT", "ICPUSDT",                                 # the rest of the 2021-06-27 top 20
            "LUNAUSDT", "FTTUSDT")                                             # named collapses
MAX_GAP_DAYS = 7
DAY, WEEK, MONDAY = rot.DAY, rot.WEEK, rot.MONDAY
RESULT_FILE = Path(__file__).resolve().parents[1] / "data" / "rotation_stress_design.json"
NOTE = ("Reported only: a survivorship stress test of the Rotation rule. The Rotation strategy, its stored design "
        "result and its PASS verdict are unchanged.")


def truncate(df: pd.DataFrame) -> tuple[pd.DataFrame, int | None]:
    """An asset's history ends at its first gap of more than 7 days; later rows under the same ticker are treated as a
    different asset (e.g. LUNA 2.0) and dropped. Returns (rows kept, open_time of the last kept day or None)."""
    if df is None or not len(df):
        return df, None
    df = df.sort_values("open_time").reset_index(drop=True)
    gaps = np.flatnonzero(np.diff(df["open_time"].to_numpy()) > (MAX_GAP_DAYS + 1) * DAY)
    if not len(gaps):
        return df, None
    k = int(gaps[0])
    return df.iloc[:k + 1].reset_index(drop=True), int(df["open_time"].iloc[k])


# --------------------------------------------------------------------------- rotation's rule over any asset list
def panel(frames: dict[str, pd.DataFrame], assets) -> dict:
    out: dict[str, dict] = {"open": {}, "close": {}}
    for sym in assets:
        df = frames.get(sym)
        for fld in out:
            out[fld][sym] = {} if df is None or not len(df) else {int(t): float(v) for t, v in zip(df["open_time"], df[fld])}
    return out


def _px(p, fld, sym, t):
    v = p[fld][sym].get(t)
    return None if v is None or not v > 0 else v


def tradable_returns(p, assets, t: int, cfg: dict) -> dict:
    L = cfg["lookback_days"]
    out = {}
    for s in assets:
        c1, c0 = _px(p, "close", s, t - DAY), _px(p, "close", s, t - DAY - L * DAY)
        out[s] = None if c1 is None or c0 is None or _px(p, "open", s, t) is None else c1 / c0 - 1
    return out


def simulate(p, assets, cfg: dict, first_day: int, last_day: int, choose: Callable | None = None) -> dict[str, Any]:
    """rotation.step_day's logic for `assets` (same decision, same costs, same marking)."""
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    cash, holding, units, costs, last_close = 1.0, None, 0.0, 0.0, {}
    equity, weeks, switches = [], [], []
    for t in range(first_day, last_day + DAY, DAY):
        if t % WEEK == MONDAY:
            ret = tradable_returns(p, assets, t, cfg)
            new, signal = choose(t, ret, holding) if choose else rot.decide(ret, holding, cfg)
            weeks.append({"monday": t, "holding": new, "signal": signal,
                          "returns": {k: (None if v is None else float(v)) for k, v in ret.items()}})
            if new != holding:
                if holding is not None:
                    px = _px(p, "open", holding, t) or last_close[holding]          # delisted: its last close
                    gross = units * px
                    cash += gross * (1 - s) * (1 - f)
                    costs += gross - gross * (1 - s) * (1 - f)
                fill = None
                if new is not None:
                    o = _px(p, "open", new, t)
                    fill = o * (1 + s)
                    units = cash * (1 - f) / fill
                    costs += cash - units * o
                    cash = 0.0
                else:
                    units = 0.0
                switches.append({"kind": "switch", "time": t, "from": holding, "to": new, "fill": fill})
                holding = new
        for sym in assets:
            c = _px(p, "close", sym, t)
            if c is not None:
                last_close[sym] = c
        held = last_close.get(holding) if holding else None
        equity.append({"day": t, "equity": cash + (units * held * (1 - f - s) if held else 0.0), "holding": holding})
    return {"equity": equity, "weeks": weeks, "switches": switches, "costs": costs}


def random_rotations(p, assets, weeks, cfg, first, last, runs=1000, seed=7) -> list[float]:
    """As rotation.random_rotations: cash in the strategy's cash weeks, else a random asset with data that week."""
    rng = np.random.default_rng(seed)
    held = {w["monday"]: w["holding"] for w in weeks}
    pool = {w["monday"]: [s for s in assets if w["returns"][s] is not None] for w in weeks}
    out = []
    for _ in range(runs):
        picks = {m: (None if held[m] is None or not pool[m] else pool[m][int(rng.integers(len(pool[m])))]) for m in held}
        sim = simulate(p, assets, cfg, first, last, choose=lambda t, ret, cur: (picks[t], "random"))
        out.append((sim["equity"][-1]["equity"] - 1) * 100)
    return out


def study(rows: dict[str, list[dict]], universe, original: dict, holdout_start: int = HOLDOUT_START,
          random_runs: int = 1000) -> dict[str, Any]:
    """The locked Rotation configuration over `universe`, on the original's design window; reported next to it."""
    cfg = rot.validate_config(original["config"])
    frames, available, missing, ended = {}, [], [], {}
    for sym in universe:
        df = pd.DataFrame(rows.get(sym) or [])
        if len(df):
            df = df[df["close_time"] < holdout_start]
        if not len(df):
            missing.append(sym)
            continue
        df, end = truncate(df)
        frames[sym] = df
        available.append(sym)
        last = int(df["open_time"].iloc[-1])
        if end is not None or last + DAY < original["period"]["last_day"]:
            ended[sym] = last
    p = panel(frames, available)
    first, last = original["period"]["first_monday"], original["period"]["last_day"]
    sim = simulate(p, available, cfg, first, last)
    eq = [e["equity"] for e in sim["equity"]]
    m = {"net_return_pct": (eq[-1] - 1) * 100, "max_drawdown_pct": rot.max_dd(eq), "switches": len(sim["switches"]),
         "weeks": len(sim["weeks"]), "costs_pct": sim["costs"] * 100}
    m["return_to_drawdown"] = rot.ratio(m["net_return_pct"], m["max_drawdown_pct"])
    rnd = random_rotations(p, available, sim["weeks"], cfg, first, last, runs=random_runs)
    beaten = float(np.mean([m["net_return_pct"] > x for x in rnd])) * 100 if rnd else None
    btc = original["btc_buy_and_hold"]
    criteria, verdict = rot.evaluate_criteria(m, btc, beaten)
    held: dict[str, int] = {}
    for w in sim["weeks"]:
        held[w["holding"] or "cash"] = held.get(w["holding"] or "cash", 0) + 1
    om = original["metrics"]
    return {
        "study": "rotation survivorship stress test", "reported_only": True, "note": NOTE,
        "preregistration": "PREREGISTRATION_ROTATION_STRESS.md", "config": cfg, "config_hash": config_hash(cfg),
        "period": {"first_monday": first, "last_day": last, "holdout_start": holdout_start},
        "universe": {"requested": list(universe), "available": available, "not_available": missing,
                     "history_ends": ended, "max_gap_days": MAX_GAP_DAYS},
        "stress": {**m, "random_beaten_pct": beaten, "random_median_pct": float(np.median(rnd)) if rnd else None},
        "criteria_reported": criteria, "verdict_if_it_were_judged": verdict,
        "original": {"net_return_pct": om["net_return_pct"], "max_drawdown_pct": om["max_drawdown_pct"],
                     "return_to_drawdown": om["return_to_drawdown"], "switches": om["switches"],
                     "random_beaten_pct": original["random_rotations"]["beaten_pct"], "verdict": original["verdict"]},
        "btc_buy_and_hold": btc, "weeks_held": dict(sorted(held.items(), key=lambda kv: -kv[1])),
        "weeks": [{"monday": w["monday"], "holding": w["holding"], "signal": w["signal"]} for w in sim["weeks"]],
        "switch_list": sim["switches"],
    }


def load_result(path: Path | None = None) -> dict | None:
    import json
    f = path or RESULT_FILE
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except (OSError, ValueError):
        return None


def markdown(r: dict) -> str:
    from datetime import datetime, timezone
    day = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731

    def pc(v):
        return "n/a" if v is None else f"{v:+.2f}%"

    o, s, u, b = r["original"], r["stress"], r["universe"], r["btc_buy_and_hold"]
    L = ["# Rotation survivorship stress test (reported only)", "", f"**{r['note']}**", "",
         f"Pre-registered in `{r['preregistration']}`; run once on design data. Same rule and configuration "
         f"(`{r['config_hash']}`), same window (Mondays from {day(r['period']['first_monday'])} to "
         f"{day(r['period']['last_day'])}), same costs.", "",
         "## Universe", "",
         f"Requested ({len(u['requested'])}): " + ", ".join(f"`{x}`" for x in u["requested"]) + ".", "",
         f"Available on Binance ({len(u['available'])}): " + ", ".join(f"`{x}`" for x in u["available"]) + ".", "",
         f"Not available ({len(u['not_available'])}): " + (", ".join(f"`{x}`" for x in u["not_available"]) or "none") + ".", ""]
    if u["history_ends"]:
        L += [f"History ends inside the window (first gap > {u['max_gap_days']} days, delisting or end of data; later rows "
              "under the same ticker are a different asset and are not used): "
              + ", ".join(f"`{k}` {day(v)}" for k, v in u["history_ends"].items()) + ".", ""]
    L += ["## Result next to the original", "", "| | Original (BTC, ETH, SOL, BNB) | Wider universe | BTC buy & hold |",
          "|---|---|---|---|",
          f"| Net return | {pc(o['net_return_pct'])} | {pc(s['net_return_pct'])} | {pc(b['net_return_pct'])} |",
          f"| Max drawdown | {o['max_drawdown_pct']:.2f}% | {s['max_drawdown_pct']:.2f}% | {b['max_drawdown_pct']:.2f}% |",
          f"| Return / max drawdown | {o['return_to_drawdown']:.2f} | {s['return_to_drawdown']:.2f} | "
          f"{rot.ratio(b['net_return_pct'], b['max_drawdown_pct']):.2f} |",
          f"| Switches | {o['switches']} | {s['switches']} | – |",
          f"| Beats random weekly rotations | {o['random_beaten_pct']:.1f}% | {s['random_beaten_pct']:.1f}% | – |",
          f"| Verdict | {o['verdict']} (stands) | {r['verdict_if_it_were_judged']} if it were judged (it is not) | – |", "",
          "Weeks held in the wider universe: " + ", ".join(f"{k.replace('USDT', '')} {v}" for k, v in r["weeks_held"].items())
          + ".", "", "## Criteria applied to the wider universe (reported only)", "", "| # | Rule | Passed |", "|---|---|---|"]
    for c in r["criteria_reported"]:
        L.append(f"| {c['id']} | {c['rule']} | {'yes' if c['passed'] else 'no'} |")
    L += ["", "Limitations: a static list fixed at the start (later top-20 entrants are not added); one price source "
              "(Binance spot USDT pairs); ticker migrations (MATIC to POL) are not followed; delisting is a sale at the "
              "last close, which may be better than what was actually possible.", ""]
    return "\n".join(L)
