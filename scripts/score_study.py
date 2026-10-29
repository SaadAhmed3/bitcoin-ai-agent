"""Score study (research only): does the engine's score, or any component, predict the forward return?

Runs on the DESIGN period only (4 years back -> HOLDOUT_START). Holdout candles are never used:
decisions whose forward-return bar would reach HOLDOUT_START are dropped, and this is asserted.
Changes no rules. Default weights, standard entry mode.

Usage (from the project folder, .venv active):
    python scripts\\score_study.py
Writes data\\score_study_design.json and data\\score_study_design.md.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from database import Database, now_ms  # noqa: E402
from services.backtest_service import PROVIDER, SYMBOL  # noqa: E402
from services.backtesting import (BAR_MS, BARS_PER_YEAR, HOLDOUT_START, BacktestConfig, _frame,  # noqa: E402
                                  replay)
from services.signal_engine import EngineConfig  # noqa: E402

HORIZON = 18                  # bars: the signal horizon and the backtest time stop
ROUND_TRIP = 0.003            # 0.10% fee + 0.05% slippage, per side, both sides
COMPONENTS = ("trend", "momentum", "volume", "structure", "overextension")
BUCKETS = ("<= -0.25", "(-0.25, -0.10]", "(-0.10, 0.10)", "[0.10, 0.25)", "[0.25, 0.40)", ">= 0.40")
BOOT_RUNS, BOOT_BLOCK, BOOT_SEED = 1000, 18, 7


def bucket_of(score: float) -> int:
    """Index into BUCKETS (edges exactly as the bucket names say)."""
    if score <= -0.25:
        return 0
    if score <= -0.10:
        return 1
    if score < 0.10:
        return 2
    if score < 0.25:
        return 3
    if score < 0.40:
        return 4
    return 5


def forward_returns(c4h: pd.DataFrame, bars: list[int], horizon: int = HORIZON,
                    cutoff: int = HOLDOUT_START) -> tuple[list[int], np.ndarray]:
    """Return (kept indexes into `bars`, forward returns before costs).

    Entry = open of the bar after the decision; exit = close `horizon` bars later counting the entry bar
    (the same definition as the backtest hit rate). Decisions whose exit bar closes at or after `cutoff`
    are dropped, so no holdout candle is used.
    """
    o, c = c4h["open"].to_numpy(), c4h["close"].to_numpy()
    ct = c4h["close_time"].to_numpy()
    keep, rets = [], []
    for k, i in enumerate(bars):
        e, x = i + 1, i + horizon
        if x >= len(c) or ct[x] >= cutoff or ct[i] >= cutoff:
            continue
        keep.append(k)
        rets.append(c[x] / o[e] - 1)
    assert all(ct[bars[k] + horizon] < cutoff for k in keep), "a forward-return bar reaches the holdout"
    return keep, np.array(rets, dtype=float)


def bucket_table(scores: np.ndarray, rets: np.ndarray) -> list[dict]:
    idx = np.array([bucket_of(s) for s in scores])
    rows = []
    for k, name in enumerate(BUCKETS):
        r = rets[idx == k]
        rows.append({"bucket": name, "bars": int(len(r)),
                     "mean_pct": round(float(np.mean(r)) * 100, 3) if len(r) else None,
                     "median_pct": round(float(np.median(r)) * 100, 3) if len(r) else None,
                     "positive_pct": round(float(np.mean(r > 0)) * 100, 1) if len(r) else None,
                     "beats_costs_pct": round(float(np.mean(r > ROUND_TRIP)) * 100, 1) if len(r) else None})
    return rows


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    ok = ~(np.isnan(a) | np.isnan(b))
    if ok.sum() < 3:
        return None
    return float(pd.Series(a[ok]).rank().corr(pd.Series(b[ok]).rank()))


def block_bootstrap(scores: np.ndarray, rets: np.ndarray, top_lo: float = 0.40) -> dict:
    """Moving-block bootstrap over the time-ordered (overlapping) series: blocks of 18 consecutive bars."""
    rng = np.random.default_rng(BOOT_SEED)
    n = len(rets)
    nblocks = int(np.ceil(n / BOOT_BLOCK))
    top_means, diffs, corrs = [], [], []
    for _ in range(BOOT_RUNS):
        starts = rng.integers(0, n - BOOT_BLOCK + 1, size=nblocks)
        idx = (starts[:, None] + np.arange(BOOT_BLOCK)[None, :]).ravel()[:n]
        s, r = scores[idx], rets[idx]
        top = r[s >= top_lo]
        if len(top):
            top_means.append(np.mean(top))
            diffs.append(np.mean(top) - np.mean(r))
        corrs.append(spearman(s, r))

    def ci(values: list, scale: float) -> list | None:
        if not values:
            return None
        v = np.array(values, dtype=float)
        return [round(float(np.percentile(v, 2.5)) * scale, 4), round(float(np.percentile(v, 97.5)) * scale, 4)]

    return {"runs": BOOT_RUNS, "block": BOOT_BLOCK, "seed": BOOT_SEED,
            "top_bucket_mean_pct_ci95": ci(top_means, 100),
            "top_minus_all_mean_pct_ci95": ci(diffs, 100),
            "score_spearman_ci95": ci(corrs, 1)}


def study(c1h_rows, c4h_rows, c1d_rows, years: float = 4.0, progress=None) -> dict:
    c1h, c4h, c1d = _frame(c1h_rows), _frame(c4h_rows), _frame(c1d_rows)
    cfg = BacktestConfig(years=years, period="design")
    ct4 = c4h["close_time"].to_numpy()
    # Same start and design end as run_backtest (warm-up on every timeframe, then `years`, then the cut-off).
    first_t = max(int(c4h["close_time"].iloc[min(cfg.window, len(c4h) - 1)]),
                  int(c1h["close_time"].iloc[min(cfg.window, len(c1h) - 1)]),
                  int(c1d["close_time"].iloc[min(220, len(c1d) - 1)]))
    start = max(int(np.searchsorted(ct4, first_t)), len(c4h) - 1 - int(years * BARS_PER_YEAR))
    end = min(len(c4h) - 1, int(np.searchsorted(ct4, HOLDOUT_START - BAR_MS)))
    ec = EngineConfig()                       # default weights, standard entry mode
    bars, decs = replay(c1h, c4h, c1d, ec, {"default": dict(ec.weights)}, cfg, start, progress, end_index=end)
    d = decs["default"]
    keep, rets = forward_returns(c4h, bars, HORIZON, HOLDOUT_START)
    assert all(ct4[bars[k]] < HOLDOUT_START for k in keep), "a decision bar reaches the holdout"
    scores = np.array([d[k].score for k in keep], dtype=float)
    def comp_score(dec, name: str) -> float:
        v = (dec.components or {}).get(name)
        return np.nan if v is None else float(v)

    comp = {name: np.array([comp_score(d[k], name) for k in keep], dtype=float) for name in COMPONENTS}
    every = np.arange(0, len(rets), HORIZON)          # non-overlapping sample: every 18th decision bar
    base = {"all_bars_mean_pct": round(float(np.mean(rets)) * 100, 3),
            "all_bars_median_pct": round(float(np.median(rets)) * 100, 3),
            "all_bars_positive_pct": round(float(np.mean(rets > 0)) * 100, 1),
            "all_bars_beats_costs_pct": round(float(np.mean(rets > ROUND_TRIP)) * 100, 1),
            "non_overlapping_mean_pct": round(float(np.mean(rets[every])) * 100, 3)}
    out = {
        "generated_at": now_ms(), "research_only": True,
        "definition": (f"Decision at each closed 4h bar (default weights, standard mode). Forward return = close "
                       f"{HORIZON} bars after the next open (counting the entry bar) / next open - 1, before costs; "
                       f"'beats costs' = above the {ROUND_TRIP * 100:.2f}% round trip."),
        "period": {"first_decision": int(ct4[bars[keep[0]]]), "last_decision": int(ct4[bars[keep[-1]]]),
                   "last_forward_bar_close": int(ct4[bars[keep[-1]] + HORIZON]), "holdout_start": HOLDOUT_START,
                   "decision_bars": len(keep), "dropped_near_cutoff": len(bars) - len(keep),
                   "non_overlapping_bars": int(len(every))},
        "baseline": base,
        "buckets": bucket_table(scores, rets),
        "buckets_non_overlapping": bucket_table(scores[every], rets[every]),
        "spearman": {"score": spearman(scores, rets), **{n: spearman(v, rets) for n, v in comp.items()}},
        "spearman_non_overlapping": {"score": spearman(scores[every], rets[every]),
                                     **{n: spearman(v[every], rets[every]) for n, v in comp.items()}},
        "bootstrap": block_bootstrap(scores, rets),
    }
    out["spearman"] = {k: None if v is None else round(v, 4) for k, v in out["spearman"].items()}
    out["spearman_non_overlapping"] = {k: None if v is None else round(v, 4) for k, v in out["spearman_non_overlapping"].items()}
    return out


def markdown(r: dict) -> str:
    import datetime as dt
    D = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    p, b, bs = r["period"], r["baseline"], r["bootstrap"]
    lines = ["# Score study - DESIGN period only (research)", "",
             f"Decisions {D(p['first_decision'])} -> {D(p['last_decision'])} ({p['decision_bars']:,} bars; "
             f"{p['dropped_near_cutoff']} dropped near the cut-off; last forward bar closes {D(p['last_forward_bar_close'])}, "
             f"before the holdout start {D(p['holdout_start'])}).", "", r["definition"], "",
             f"All-bar baseline: mean {b['all_bars_mean_pct']}%, median {b['all_bars_median_pct']}%, "
             f"{b['all_bars_positive_pct']}% positive, {b['all_bars_beats_costs_pct']}% beat costs.", "",
             "| Score bucket | Bars | Mean % | Median % | % positive | % beat costs | Non-overlap bars | Non-overlap mean % |",
             "|---|---|---|---|---|---|---|---|"]
    for a, n in zip(r["buckets"], r["buckets_non_overlapping"]):
        lines.append(f"| {a['bucket']} | {a['bars']} | {a['mean_pct']} | {a['median_pct']} | {a['positive_pct']} | "
                     f"{a['beats_costs_pct']} | {n['bars']} | {n['mean_pct']} |")
    lines += ["", "| Spearman vs forward return | All bars | Non-overlapping |", "|---|---|---|"]
    for k in r["spearman"]:
        lines.append(f"| {k} | {r['spearman'][k]} | {r['spearman_non_overlapping'][k]} |")
    lines += ["", f"Block bootstrap ({bs['runs']} resamples, block {bs['block']}, seed {bs['seed']}), 95% CI:",
              f"- top bucket (>= 0.40) mean: {bs['top_bucket_mean_pct_ci95']} %",
              f"- top bucket minus all-bar mean: {bs['top_minus_all_mean_pct_ci95']} percentage points",
              f"- score Spearman: {bs['score_spearman_ci95']}", "",
              "Research only: no rule was changed because of this study."]
    return "\n".join(lines) + "\n"


def main() -> int:
    cfg = Config()
    db = Database(cfg.db_path)
    rows = {tf: db.get_candles_range(PROVIDER, SYMBOL, tf) for tf in ("1h", "4h", "1d")}
    t0 = time.time()
    r = study(rows["1h"], rows["4h"], rows["1d"],
              progress=lambda f, m: print(f"\r{m}  ({time.time() - t0:.0f}s)", end="", flush=True))
    print()
    out = ROOT / "data" / "score_study_design.json"
    out.write_text(json.dumps(r, indent=1))
    (ROOT / "data" / "score_study_design.md").write_text(markdown(r), encoding="utf-8")
    print(markdown(r))
    print(f"Saved {out} and score_study_design.md in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
