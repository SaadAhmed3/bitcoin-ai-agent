"""Global Intelligence: accuracy tracker for the impact labels. Context, not part of the signal.

The rules are PREREGISTRATION_NEWS_IMPACT.md, committed before this code and pinned below. Scores are written once, from closed
candles, measured from the moment the assessment was made. Small samples show no hit rate at all.
"""
from __future__ import annotations

import zlib
from typing import Callable

import numpy as np

from database import Database, now_ms
from services.intel_impact import RULES_VERSION
from services.intel_reactions import NORMAL_LOOKBACK_H, PROVIDER, SYMBOL, Candles, align_base, align_target, episodes

LABEL = "Context, not part of the signal"
PREREG_FILE = "PREREGISTRATION_NEWS_IMPACT.md"
PREREG_SHA256 = "2ab3d19a76fdf5f7b80cb790e7a592ba219390c878d389bb4b66acabb72bc15a"
HOUR = 3_600_000
SCORE_HORIZONS = {"0-24h": 24, "1-7d": 168, "1-3m": 2160}
LABEL_COLUMNS = {"0-24h": "label_0_24h", "1-7d": "label_1_7d", "1-3m": "label_1_3m"}
BAND = 0.5
MIN_SAMPLES = 30
MIN_EPISODES = 20
BOOTSTRAP_N = 1000
ZONE_OF_LABEL = {"Bullish": "up", "Bearish": "down", "Neutral": "band"}
SMALL_NOTE = "Small samples prove nothing."
NOTES = [
    "Small samples prove nothing. No hit rate is shown below 30 scored assessments and 20 separate episodes.",
    "Scored from the moment each assessment was made, on closed candles only. Mixed and Uncertain are not scored.",
    "Only the first assessment of a story counts, made within 24 hours of publication.",
    "Assessments near a macro release, or with unknown release dates, are left out of the main figures and shown apart.",
    "Even a good result would describe this period only. Nothing here reaches the signal.",
]


def classify_zone(return_pct: float, normal_abs_pct: float) -> str:
    x = BAND * normal_abs_pct
    return "up" if return_pct > x else "down" if return_pct < -x else "band"


def score_label(label: str, return_pct: float, normal_abs_pct: float) -> str:
    want = ZONE_OF_LABEL.get(label)
    if want is None:
        return "not_scored"
    return "hit" if classify_zone(return_pct, normal_abs_pct) == want else "miss"


def baselines(rows: list[dict]) -> dict:
    """On exactly the scored rows. Every return is in one of three zones, so a uniform random label is right one time in three."""
    if not rows:
        return {"always_neutral": None, "random_uniform": 1 / 3, "label_mix": None}
    n = len(rows)
    zone = {z: sum(r["zone"] == z for r in rows) / n for z in ("up", "down", "band")}
    mix = sum(sum(r["label"] == label for r in rows) / n * zone[z] for label, z in ZONE_OF_LABEL.items())
    return {"always_neutral": zone["band"], "random_uniform": 1 / 3, "label_mix": mix}


def verdict(ci: list[float] | None, base: dict) -> str:
    if not ci or any(v is None or ci[0] <= v for v in base.values()):
        return "not_established"
    return "beats_baselines"


def _block(rows: list[dict], key: str) -> dict:
    """The figures for one group. Below the pre-registered minimum only the sample size is given."""
    eps = episodes([{**r, "base_time": r["base_open_time"] + HOUR, "target_time": r["target_open_time"] + HOUR} for r in rows]) if rows else []
    out = {"n": len(rows), "episodes": len(eps), "shown": False, "hits": None, "hit_rate": None, "ci95": None, "baselines": None, "verdict": None,
           "note": f"{len(rows)} scored, {len(eps)} episode{'' if len(eps) == 1 else 's'}. {SMALL_NOTE}"}
    if len(rows) < MIN_SAMPLES or len(eps) < MIN_EPISODES:
        return out
    groups = [np.array([r["outcome"] == "hit" for r in e], dtype=float) for e in eps]
    rng = np.random.default_rng(zlib.crc32(key.encode("utf-8")))
    rates = [np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))]).mean() for _ in range(BOOTSTRAP_N)]
    hits = sum(r["outcome"] == "hit" for r in rows)
    rate = hits / len(rows)
    ci = [min(rate, round(float(np.percentile(rates, 2.5)), 4)), max(rate, round(float(np.percentile(rates, 97.5)), 4))]
    base = baselines(rows)
    v = verdict(ci, base)
    out.update(shown=True, hits=hits, hit_rate=rate, ci95=ci, baselines=base, verdict=v,
               note=("The interval is above all three baselines in this period." if v == "beats_baselines" else "Not established: the interval is not above all three baselines."))
    return out


class TrackerService:
    def __init__(self, db: Database, clock: Callable[[], int] = now_ms):
        self.db = db
        self.clock = clock

    def score_due(self) -> dict:
        """Score each horizon that has passed. A stored score is never changed. A missing candle is never filled in."""
        now = self.clock()
        span = self.db.candle_span(PROVIDER, SYMBOL, "1h")
        rows = self.db.intel_assessments()
        if not span["count"] or not rows:
            return {"scored": 0}
        done = {(s["assessment_id"], s["horizon"]) for s in self.db.intel_assessment_scores()}
        due = [(r, h, hours) for r in rows for h, hours in SCORE_HORIZONS.items()
               if (r["id"], h) not in done and align_target(r["assessed_at"], hours) + HOUR - 1 <= span["last"]]
        if not due:
            return {"scored": 0}
        start = min(align_base(r["assessed_at"]) for r, _, _ in due) - (NORMAL_LOOKBACK_H + 48) * HOUR
        candles = Candles(self.db, start, now)
        added = 0
        for r, h, hours in due:
            base_open, target_open = align_base(r["assessed_at"]), align_target(r["assessed_at"], hours)
            i, j = candles.index(base_open), candles.index(target_open)
            if i is None or j is None:
                continue
            normal = candles.normal(i, j - i)[0]
            if normal is None:
                continue
            base_close, target_close = float(candles.close[i]), float(candles.close[j])
            ret = (target_close / base_close - 1) * 100
            added += self.db.intel_add_score({"assessment_id": r["id"], "horizon": h, "label": r[LABEL_COLUMNS[h]], "base_open_time": base_open, "base_close": base_close,
                                              "target_open_time": target_open, "target_close": target_close, "return_pct": ret, "window_hours": j - i, "normal_abs_pct": normal,
                                              "threshold_pct": BAND * normal, "zone": classify_zone(ret, normal), "outcome": score_label(r[LABEL_COLUMNS[h]], ret, normal)}, now)
        return {"scored": added}

    def report(self) -> dict:
        now = self.clock()
        rows = self.db.intel_assessments()
        current = [r for r in rows if r["rules_version"] == RULES_VERSION]
        first: dict = {}
        for r in sorted((r for r in current if r["eligible"]), key=lambda r: (r["assessed_at"], r["id"])):
            first.setdefault(r["anchor_article_id"], r)
        counted = list(first.values())
        scores = {(s["assessment_id"], s["horizon"]): s for s in self.db.intel_assessment_scores()}
        span = self.db.candle_span(PROVIDER, SYMBOL, "1h")
        through = min(now, (span["last"] or 0) + 1)
        horizons = {}
        for h, hours in SCORE_HORIZONS.items():
            main, macro, not_scored, pending, unscorable, labels = [], [], 0, 0, 0, {}
            for r in counted:
                labels[r[LABEL_COLUMNS[h]]] = labels.get(r[LABEL_COLUMNS[h]], 0) + 1
                s = scores.get((r["id"], h))
                if s is None:
                    if align_target(r["assessed_at"], hours) + HOUR <= through:
                        unscorable += 1
                    else:
                        pending += 1
                elif s["outcome"] == "not_scored":
                    not_scored += 1
                else:
                    (macro if r["macro_window"] else main).append(s)
            horizons[h] = {"counted": _block(main, f"main|{h}"), "macro_window": _block(macro, f"macro|{h}"), "not_scored": not_scored, "pending": pending, "unscorable": unscorable,
                           "labels": labels, "scored_at": f"+{hours // 24} days" if hours >= 168 else f"+{hours} hours"}
        return {"label": LABEL, "notes": NOTES, "prereg_file": PREREG_FILE, "rules_version": RULES_VERSION, "generated_at": now, "min_samples": MIN_SAMPLES, "min_episodes": MIN_EPISODES,
                "totals": {"assessments": len(current), "counted_first_assessments": len(counted), "not_eligible": sum(not r["eligible"] for r in current)}, "horizons": horizons}
