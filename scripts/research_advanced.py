"""Advanced analysis research (Phase 4): regime, liquidity sweeps, volume profile on DESIGN data only.

Pre-registered in PREREGISTRATION_ADVANCED.md BEFORE this ran: the weights of variants B-E and the event-study
horizons are fixed, every variant runs once, nothing is tuned or picked. Results are "design-data evidence only".
Decisions use the design window of the app's design backtest (before the 2025-10-29 holdout start); no "full" period
and no holdout candle is used (asserted). The live signal (signal-1.8) is not touched.

Usage (from the project folder, .venv active):
    python scripts\\research_advanced.py
Writes data\\research_advanced_design.json and data\\research_advanced_design.md. The study is run ONCE: if either
file exists the script refuses to run. `--confirm-rerun` runs it again and saves to new timestamped files
(research_advanced_design_rerun-<UTC time>.json/.md); the original result is never overwritten.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from database import Database, now_ms  # noqa: E402
from services.backtest_service import PROVIDER, SYMBOL  # noqa: E402
from services.backtesting import (BARS_PER_YEAR, BAR_MS, HOLDOUT_START, BacktestConfig, Decision, _frame,  # noqa: E402
                                  metrics, replay, simulate, windows)
from services.liquidity_sweeps import CONFIRMED, detect_sweeps, sweep_score  # noqa: E402
from services.regime import REGIMES, regime_at, regime_score  # noqa: E402
from services.signal_engine import ENGINE_VERSION, EngineConfig  # noqa: E402
from services.volume_profile import profile_at, vp_score  # noqa: E402

# ---- pre-registered (PREREGISTRATION_ADVANCED.md); do not change after the run
VARIANTS: dict[str, dict[str, float]] = {
    "A": {},
    "B": {"regime": 0.15},
    "C": {"liquidity_sweep": 0.15},
    "D": {"volume_profile": 0.15},
    "E": {"regime": 0.10, "liquidity_sweep": 0.10, "volume_profile": 0.10},
}
HORIZONS = (6, 18)
BOOT_RUNS, BOOT_SEED = 2000, 7
LABEL = "design-data evidence only"
DATA_DIR = ROOT / "data"
STEM = "research_advanced_design"


def combined(score: float, comps: dict[str, float], weights: dict[str, float]) -> float:
    if not weights:
        return score
    return (1 - sum(weights.values())) * score + sum(w * comps[k] for k, w in weights.items())


def filter_decisions(decisions: list[Decision], comps: list[dict], weights: dict[str, float],
                     buy_threshold: float) -> list[Decision]:
    """Filter only: a tradable decision stays tradable while the combined score is >= the buy threshold. A decision
    the engine did not make tradable never becomes tradable (no plan, no forced trade)."""
    out = []
    for d, c in zip(decisions, comps):
        keep = d.tradable and combined(d.score, c, weights) >= buy_threshold
        out.append(d if keep == d.tradable else replace(d, tradable=False))
    return out


def forward_returns(c4h: pd.DataFrame, horizon: int, cutoff: int = HOLDOUT_START) -> np.ndarray:
    """close[i + h] / close[i] - 1 per bar; NaN when bar i + h does not exist or closes at/after the holdout start."""
    c = c4h["close"].to_numpy(float)
    ct = c4h["close_time"].to_numpy()
    out = np.full(len(c), np.nan)
    if len(c) > horizon:
        out[:-horizon] = c[horizon:] / c[:-horizon] - 1
        out[:-horizon][ct[horizon:] >= cutoff] = np.nan
    return out


def block_bootstrap_ci(rets: np.ndarray, mask: np.ndarray, block: int, runs: int = BOOT_RUNS,
                       seed: int = BOOT_SEED) -> list[float] | None:
    """95% CI of a group's mean return: moving-block bootstrap over the TIME AXIS of all bars (blocks of `block`
    consecutive bars, group membership travels with each bar); the group mean is taken over the group's bars in each
    resample, resamples without one are skipped. Works the same for dense groups (regime runs) and sparse ones
    (sweeps), and keeps the overlap of neighbouring forward returns."""
    n = len(rets)
    if n < 2 or mask.sum() < 2:
        return None
    b = max(1, min(block, n))
    rng = np.random.default_rng(seed)
    k = math.ceil(n / b)
    means = []
    for _ in range(runs):
        idx = (rng.integers(0, n - b + 1, k)[:, None] + np.arange(b)).ravel()[:n]
        sel = mask[idx]
        if sel.any():
            means.append(rets[idx][sel].mean())
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def event_row(rets: np.ndarray, mask: np.ndarray, block: int) -> dict:
    ok = np.isfinite(rets)
    r, m = rets[ok], mask[ok]
    base = float(r.mean()) if len(r) else float("nan")
    x = r[m]
    if len(x) == 0:
        return {"n": 0, "mean_pct": None, "vs_all_pct": None, "ci95_pct": None, "positive_pct": None}
    ci = block_bootstrap_ci(r, m, block)
    return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 4),
            "vs_all_pct": round((float(x.mean()) - base) * 100, 4),
            "ci95_pct": None if ci is None else [round(ci[0] * 100, 4), round(ci[1] * 100, 4)],
            "positive_pct": round(float((x > 0).mean()) * 100, 1)}


def _design_span(c1h, c4h, c1d, cfg: BacktestConfig) -> tuple[int, int]:
    """Exactly run_backtest's design window: warm-up on every timeframe, the last `years`, the design cut-off."""
    first_t = max(int(c4h["close_time"].iloc[min(cfg.window, len(c4h) - 1)]),
                  int(c1h["close_time"].iloc[min(cfg.window, len(c1h) - 1)]),
                  int(c1d["close_time"].iloc[min(220, len(c1d) - 1)]))
    ct4 = c4h["close_time"].to_numpy()
    start = max(int(np.searchsorted(ct4, first_t)), len(c4h) - 1 - int(cfg.years * BARS_PER_YEAR))
    end = min(len(c4h) - 1, int(np.searchsorted(ct4, cfg.holdout_start - BAR_MS)))
    return start, end


def study(c1h_rows, c4h_rows, c1d_rows, progress=None) -> dict:
    c1h, c4h, c1d = _frame(c1h_rows), _frame(c4h_rows), _frame(c1d_rows)
    cfg = BacktestConfig(period="design")
    ec = EngineConfig()
    start, end = _design_span(c1h, c4h, c1d, cfg)
    ct4 = c4h["close_time"].to_numpy()
    bars, decs = replay(c1h, c4h, c1d, ec, {"default": dict(ec.weights)}, cfg, start, progress, end_index=end)
    dec = decs["default"]
    assert all(ct4[i] < HOLDOUT_START for i in bars), "a decision bar reaches the holdout"

    # Shadow components at each decision bar, from that bar's past only.
    events = detect_sweeps(c4h.iloc[:bars[-1] + 1], c1h, c1d)
    comps, labels = [], []
    for n, i in enumerate(bars):
        r = regime_at(c4h, i, c1h, c1d)
        labels.append(r["regime"])
        comps.append({"regime": regime_score(r), "liquidity_sweep": sweep_score(c4h, events, int(ct4[i])),
                      "volume_profile": vp_score(profile_at(c4h, i), float(c4h["close"].iloc[i]))})
        if progress and n % 250 == 0:
            progress(n / len(bars), f"Shadow components {n + 1:,} of {len(bars):,}")

    # Part A: variants, each simulated once with the existing code.
    wins = windows(bars, c4h, cfg)
    times = [int(ct4[i]) for i in bars]
    label_at = dict(zip(bars, labels))
    variants = {}
    for name, w in VARIANTS.items():
        d = filter_decisions(dec, comps, w, ec.buy_threshold)
        trades, eq = simulate(c4h, bars, d, ec, cfg)
        m = metrics(trades, eq, len(eq), ec)
        m["false_signal_rate_pct"] = round(sum(t.ret <= 0 for t in trades) / len(trades) * 100, 1) if trades else None
        m["tradable_bars"] = sum(x.tradable for x in d)
        per_win = []
        for lo, hi in wins:
            t_w, e_w = simulate(c4h, bars, d, ec, cfg, lo, hi)
            per_win.append({"start": times[lo], "end": times[hi - 1], "return_pct": round(float(e_w[-1] - 1) * 100, 2),
                            "trades": len(t_w)})
        m["walk_forward"] = per_win
        m["trade_list"] = [{"entry_time": t.entry_time, "exit_time": t.exit_time, "ret_pct": round(t.ret * 100, 3),
                            "reason": t.reason, "regime_at_decision": label_at.get(t.entry_bar - 1)} for t in trades]
        variants[name] = {"weights": w, **m}
    by_regime = {}
    for t in variants["A"]["trade_list"]:
        g = by_regime.setdefault(t["regime_at_decision"] or "unknown", {"trades": 0, "sum_ret_pct": 0.0, "wins": 0})
        g["trades"] += 1
        g["sum_ret_pct"] = round(g["sum_ret_pct"] + t["ret_pct"], 3)
        g["wins"] += t["ret_pct"] > 0

    # Part B: event study at +6 and +18 bars.
    idx = np.array(bars)
    lab = np.array([x or "" for x in labels])
    confirmed = [e for e in events if e["confirmed_at"] is not None and any(u["status"] == CONFIRMED for u in e["updates"])]
    bar_times = ct4[idx]
    by_dir = {d: np.isin(bar_times, [e["confirmed_at"] for e in confirmed if e["direction"] == d])
              for d in ("buy_side", "sell_side")}                 # a confirmation bar per direction (both may coincide)
    ev = {}
    for h in HORIZONS:
        rets = forward_returns(c4h, h)[idx]
        valid = np.isfinite(rets)
        assert all(ct4[i + h] < HOLDOUT_START for i, ok in zip(bars, valid) if ok), "a forward bar reaches the holdout"
        rows = {"ALL BARS": event_row(rets, np.ones(len(rets), bool), h)}
        for g in REGIMES:
            rows[g] = event_row(rets, lab == g, h)
        rows["CONFIRMED SWEEP: buy-side"] = event_row(rets, by_dir["buy_side"], h)
        rows["CONFIRMED SWEEP: sell-side"] = event_row(rets, by_dir["sell_side"], h)
        ev[f"+{h}"] = {"unconditional_mean_pct": rows["ALL BARS"]["mean_pct"], "dropped_near_cutoff": int((~valid).sum()),
                       "groups": rows}

    return {
        "generated_at": now_ms(), "label": LABEL, "engine": ENGINE_VERSION, "preregistration": "PREREGISTRATION_ADVANCED.md",
        "period": {"first_decision": times[0], "last_decision": times[-1], "decision_bars": len(bars),
                   "holdout_start": HOLDOUT_START},
        "variants": variants, "performance_by_regime_A": by_regime,
        "regime_bar_counts": {g: int((lab == g).sum()) for g in REGIMES},
        "sweeps": {"events_incl_warm_up": len(events),
                   "confirmed_in_design_bars": {d: int(m.sum()) for d, m in by_dir.items()}},
        "event_study": ev,
    }


def _iso(ms: int) -> str:
    return pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")


def markdown(r: dict) -> str:
    L = ["# Advanced analysis on design data (" + LABEL + ")", "",
         f"Pre-registered in `{r['preregistration']}` before this run. Engine {r['engine']} unchanged. "
         f"Decisions {_iso(r['period']['first_decision'])} to {_iso(r['period']['last_decision'])} "
         f"({r['period']['decision_bars']:,} closed 4h bars), all before the holdout start "
         f"{_iso(r['period']['holdout_start'])}. Run once; nothing tuned or picked.", "",
         "## Part A: variants (filter only)", "",
         "| Variant | Weights | Trades | Win rate | Net return | Max DD | Profit factor | Avg trade | False-signal rate |",
         "|---|---|---|---|---|---|---|---|---|"]
    f = lambda v, s="": "n/a" if v is None else f"{v}{s}"  # noqa: E731
    for name, v in r["variants"].items():
        w = ", ".join(f"{k} {x:.2f}" for k, x in v["weights"].items()) or "engine only"
        L.append(f"| {name} | {w} | {v['trades']} | {f(v['win_rate_pct'], '%')} | {v['net_return_pct']}% | "
                 f"{v['max_drawdown_pct']}% | {f(v['profit_factor'])} | {f(v['avg_trade_pct'], '%')} | "
                 f"{f(v['false_signal_rate_pct'], '%')} |")
    L += ["", "### Walk-forward windows (return % / trades)", "",
          "| Window | " + " | ".join(r["variants"]) + " |", "|---|" + "---|" * len(r["variants"])]
    for k, wdw in enumerate(r["variants"]["A"]["walk_forward"]):
        cells = [f"{v['walk_forward'][k]['return_pct']}% / {v['walk_forward'][k]['trades']}" for v in r["variants"].values()]
        L.append(f"| {_iso(wdw['start'])[:10]} to {_iso(wdw['end'])[:10]} | " + " | ".join(cells) + " |")
    L += ["", "### Variant A trades by regime at the decision bar", "", "| Regime | Trades | Wins | Sum of returns |",
          "|---|---|---|---|"]
    for g, x in r["performance_by_regime_A"].items():
        L.append(f"| {g} | {x['trades']} | {x['wins']} | {x['sum_ret_pct']}% |")
    L += ["", f"Regime labels over the design bars: " + ", ".join(f"{g} {n:,}" for g, n in r["regime_bar_counts"].items())
          + f". Confirmed sweeps at design bars: buy-side {r['sweeps']['confirmed_in_design_bars']['buy_side']}, "
          f"sell-side {r['sweeps']['confirmed_in_design_bars']['sell_side']}.", "",
          "## Part B: event study (forward return before costs, block-bootstrap 95% CI of the group mean)", ""]
    for h, e in r["event_study"].items():
        L += [f"### {h} bars (unconditional mean {e['unconditional_mean_pct']}%)", "",
              "| Group | n | Mean | vs all bars | 95% CI | Positive |", "|---|---|---|---|---|---|"]
        for g, x in e["groups"].items():
            ci = "n/a" if x["ci95_pct"] is None else f"[{x['ci95_pct'][0]}%, {x['ci95_pct'][1]}%]"
            L.append(f"| {g} | {x['n']} | {f(x['mean_pct'], '%')} | {f(x['vs_all_pct'], '%')} | {ci} | "
                     f"{f(x['positive_pct'], '%')} |")
        L.append("")
    L += ["## Reading (fixed in advance)", "",
          "Design-data evidence only. Nothing here changes the live signal, its weights, thresholds or NO TRADE gates. "
          "With about five engine trades, variants A-E cannot be told apart statistically. An event-study group whose "
          "CI excludes the unconditional mean is at most a candidate for a separate, pre-registered forward test; the "
          "app stores the shadow outputs live from 2026-10-31 for that purpose.", ""]
    return "\n".join(L)


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def output_paths(confirm_rerun: bool) -> tuple[Path, Path]:
    """The original files on the first run. After that: refuse (FileExistsError) unless confirm_rerun, then new
    timestamped files that never replace an existing one."""
    original = (DATA_DIR / f"{STEM}.json", DATA_DIR / f"{STEM}.md")
    if not any(p.exists() for p in original):
        return original
    if not confirm_rerun:
        raise FileExistsError(
            f"{original[0].name} already exists: this pre-registered study is run once. Nothing was run. "
            "Use --confirm-rerun to run it again; the rerun is saved to new timestamped files and the original is "
            "kept unchanged.")
    base, n = f"{STEM}_rerun-{utc_stamp()}", 1
    while True:
        name = base if n == 1 else f"{base}-{n}"
        paths = (DATA_DIR / f"{name}.json", DATA_DIR / f"{name}.md")
        if not any(p.exists() for p in paths):
            return paths
        n += 1


def load_rows() -> dict[str, list[dict]]:
    db = Database(Config().db_path, read_only=True)
    return {tf: db.get_candles_range(PROVIDER, SYMBOL, tf) for tf in ("1h", "4h", "1d")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered design-data study (run once).")
    ap.add_argument("--confirm-rerun", action="store_true",
                    help="run again although a result exists; saves to new timestamped files")
    args = ap.parse_args(argv)
    try:
        out_json, out_md = output_paths(args.confirm_rerun)      # decided before any computation
    except FileExistsError as exc:
        print(exc)
        return 2
    rows = load_rows()
    t0 = time.time()
    r = study(rows["1h"], rows["4h"], rows["1d"],
              progress=lambda f, m: print(f"\r{m}  ({time.time() - t0:.0f}s)", end="", flush=True))
    print()
    # "x": create only - even a file that appeared meanwhile is never overwritten
    with open(out_json, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(r, indent=1, default=float))
    with open(out_md, "x", encoding="utf-8") as fh:
        fh.write(markdown(r))
    print(markdown(r))
    print(f"Saved {out_json} and {out_md.name} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
