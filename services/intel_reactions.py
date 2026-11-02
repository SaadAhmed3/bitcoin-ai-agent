"""Global Intelligence: what BTC did around events and macro releases. Descriptive, context only, not part of the signal.

Candles: Binance BTCUSDT 1h, closed only. A candle counts as closed when its close time has passed AND it was fetched after
that, because a row saved part-way through the hour keeps partial values until it is fetched again.

Timing, for an event at time t:
  base    the last candle closed at or before t (never the one containing t)
  target  the first candle closing at least the horizon after t, so a "+1h" window can span up to 2 hours (window_hours)
A missing candle stays missing; a neighbour is never used. "Normal" is the same window length over the year before the base
candle, so it uses nothing from after the event.

Reactions are stored per article, because event membership can be rebuilt. An event reads the row of its anchor, the article
with the earliest publication time. Stored rows are append-only.
"""
from __future__ import annotations

import bisect
import datetime as dt
import logging
import zlib
from typing import Callable

import numpy as np
import pandas as pd

from database import Database, now_ms
from services.intel_events import CATEGORIES, CATEGORY_LABELS
from services.intel_markets import MARKET_SERIES
from services.regime import classify
from services.technical_analysis import atr

log = logging.getLogger("btc_agent.intel")

LABEL = "Context, not part of the signal"
PROVIDER, SYMBOL = "binance", "BTCUSDT"
HOUR = 3_600_000
DAY = 24 * HOUR
HORIZONS = {"1h": 1, "4h": 4, "24h": 24, "7d": 168, "30d": 720}
METHOD = "reactions-1"
MACRO_KINDS = ("FOMC", "CPI", "NFP", "GDP", "PCE")
MACRO_LABELS = {"FOMC": "FOMC decision", "CPI": "CPI release", "NFP": "Jobs report release", "GDP": "GDP release", "PCE": "PCE release"}
MACRO_WINDOW_MS = 2 * HOUR
NORMAL_LOOKBACK_H = 365 * 24
NORMAL_MIN_SAMPLES = 30
VOLUME_WINDOW = 24
ATR_TAIL = 300
BOOTSTRAP_N = 1000
MIN_EPISODES_FOR_INTERVAL = 5
SMALL_N = 30
SMALL_EPISODES = 20
STUDY_CACHE_MS = 300_000
MARKET_AFTER = {"1d": 1, "7d": 7, "30d": 30}
MARKET_MAX_GAP_DAYS = 5            # weekends and holidays; further away than this is "no value", not the nearest one
EXCLUDED_FLAGS = ("publication_missing", "published_after_first_seen")
TIME_FLAG_TEXT = {
    "publication_missing": "No article has a publication time, so no reaction is measured and the event is left out of the statistics.",
    "published_after_first_seen": "The publication time is later than when the app first saw the event, so it must be wrong. Left out of the statistics.",
    "publication_partial": "Some articles have no publication time; the event time comes from the ones that do.",
}
NOTES = [
    "Descriptive only: what BTC did after past events. It is not a forecast, and nothing here reaches the signal.",
    "Correlation is not causation: a move after an event need not have been caused by it.",
    "Events overlap and BTC reacts to many things at once. Windows that overlap count as one episode for the interval and the sample flag.",
    "Many groups and horizons are compared at once, so some intervals will leave out zero by chance.",
    "Longer windows contain more unrelated news, so they say less about any one event.",
    "Each window starts at the last closed 1h candle before the event and ends at the first close at least that long after it, so +1h can span up to 2 hours.",
    "Events within 2 hours of a macro release are shown separately, so CPI or FOMC moves are not credited to unrelated news.",
    "Grouping and categories are rule-based and can be wrong. Macro times are scheduled times.",
]
CONSENSUS_NOTE = ("No verified free consensus source exists, so a release cannot be called above or below expectations. "
                  "Results are by release type only.")


def align_base(event_ms: int) -> int:
    """Open time of the last 1h candle closed at or before the event."""
    return event_ms // HOUR * HOUR - HOUR


def align_target(event_ms: int, hours: int) -> int:
    """Open time of the first 1h candle closing at least `hours` after the event."""
    return -(-(event_ms + hours * HOUR) // HOUR) * HOUR - HOUR


class Candles:
    """Closed 1h candles on an hourly grid; a missing hour is NaN and is never filled."""

    def __init__(self, db: Database, start: int, now: int):
        self.db = db
        self.now = now
        rows = db.intel_closed_candles(PROVIDER, SYMBOL, "1h", start, now)
        self.first = rows[0]["open_time"] if rows else 0
        n = (rows[-1]["open_time"] - self.first) // HOUR + 1 if rows else 0
        self.close, self.high, self.low, self.volume = (np.full(n, np.nan) for _ in range(4))
        for r in rows:
            i = (r["open_time"] - self.first) // HOUR
            self.close[i], self.high[i], self.low[i], self.volume[i] = r["close"], r["high"], r["low"], r["volume"]
        self._normal: dict[tuple[int, int], tuple] = {}
        self._atr: dict[int, float | None] = {}
        self._regime: dict[int, str | None] = {}
        self._frames: tuple | None = None

    def index(self, open_time: int) -> int | None:
        if not len(self.close) or open_time < self.first:
            return None
        i = (open_time - self.first) // HOUR
        return i if i < len(self.close) and np.isfinite(self.close[i]) else None

    def last_index(self) -> int | None:
        found = np.flatnonzero(np.isfinite(self.close))
        return int(found[-1]) if len(found) else None

    def volume_ratio(self, i: int) -> float | None:
        prior = self.volume[max(0, i - VOLUME_WINDOW):i]
        if len(prior) < VOLUME_WINDOW or not np.isfinite(prior).all() or prior.mean() <= 0:
            return None
        return float(self.volume[i] / prior.mean())

    def atr_pct(self, i: int) -> float | None:
        if i not in self._atr:
            a = max(0, i - ATR_TAIL + 1)
            df = pd.DataFrame({"high": self.high[a:i + 1], "low": self.low[a:i + 1], "close": self.close[a:i + 1]}).dropna()
            value = float(atr(df, 14).iloc[-1]) if len(df) > 14 else float("nan")
            self._atr[i] = value / float(self.close[i]) * 100 if np.isfinite(value) else None
        return self._atr[i]

    def regime(self, event_ms: int) -> str | None:
        """The app's own regime label at the last 4h candle closed before the event. None without enough history."""
        if self._frames is None:
            self._frames = tuple(pd.DataFrame(self.db.intel_closed_candles(PROVIDER, SYMBOL, tf, 0, self.now)) for tf in ("4h", "1h", "1d"))
        c4h, c1h, c1d = self._frames
        if c4h.empty:
            return None
        j = int(np.searchsorted(c4h["close_time"].to_numpy(), event_ms, side="left"))
        if j not in self._regime:
            self._regime[j] = classify(c4h.iloc[max(0, j - 500):j], c1h if not c1h.empty else None,
                                       c1d if not c1d.empty else None, now=event_ms).get("regime")
        return self._regime[j]

    def normal(self, i: int, window: int) -> tuple[float | None, float | None, int]:
        """Median absolute and median signed return over `window` hours, only from candles up to the base candle."""
        if (i, window) not in self._normal:
            closes = self.close[max(0, i - NORMAL_LOOKBACK_H):i + 1]
            if len(closes) <= window:
                self._normal[(i, window)] = (None, None, 0)
            else:
                moves = closes[window:] / closes[:-window] - 1
                moves = moves[np.isfinite(moves)]
                self._normal[(i, window)] = ((float(np.median(np.abs(moves))) * 100, float(np.median(moves)) * 100, len(moves))
                                             if len(moves) >= NORMAL_MIN_SAMPLES else (None, None, len(moves)))
        return self._normal[(i, window)]


def measure(candles: Candles, event_ms: int, need_baseline: bool, horizons: list[str]) -> tuple[dict | None, dict[str, dict]]:
    """The baseline and the outcomes that closed candles can answer now. No base candle: nothing at all."""
    i = candles.index(align_base(event_ms))
    if i is None:
        return None, {}
    base_open, base_close = align_base(event_ms), float(candles.close[i])
    baseline = None
    if need_baseline:
        baseline = {"event_ms": event_ms, "base_open_time": base_open, "base_close": base_close, "volume_ratio": candles.volume_ratio(i),
                    "atr_pct": candles.atr_pct(i), "regime": candles.regime(event_ms), "method": METHOD}
    outcomes = {}
    for h in horizons:
        target = align_target(event_ms, HORIZONS[h])
        j = candles.index(target)
        if j is None:
            continue
        window = j - i
        normal_abs, normal_median, samples = candles.normal(i, window)
        outcomes[h] = {"target_open_time": target, "target_close": float(candles.close[j]), "return_pct": (float(candles.close[j]) / base_close - 1) * 100,
                       "window_hours": window, "normal_abs_pct": normal_abs, "normal_median_pct": normal_median, "normal_samples": samples}
    return baseline, outcomes


# ------------------------------------------------------------------------- statistics
def episodes(rows: list[dict]) -> list[list[dict]]:
    """Rows whose windows overlap form one episode, because they are not separate observations of a BTC move."""
    out: list[list[dict]] = []
    end = None
    for r in sorted(rows, key=lambda x: x["base_time"]):
        if end is None or r["base_time"] >= end:
            out.append([r])
            end = r["target_time"]
        else:
            out[-1].append(r)
            end = max(end, r["target_time"])
    return out


def _median(values: list[float]) -> float | None:
    return round(float(np.median(values)), 4) if values else None


def summarize(rows: list[dict], key: str) -> dict:
    """Count, median, range, size against a normal move, and a 95% interval from resampling whole episodes (fixed seed per group)."""
    if not rows:
        return {"n": 0, "episodes": 0, "median_pct": None, "min_pct": None, "max_pct": None, "ci95": None, "median_abs_pct": None,
                "normal_abs_pct": None, "abs_vs_normal": None, "normal_median_pct": None, "sample_flag": "too_few", "sample_note": "Nothing measured yet."}
    returns = [r["return_pct"] for r in rows]
    eps = episodes(rows)
    median_abs = _median([abs(x) for x in returns])
    normal_abs = _median([r["normal_abs_pct"] for r in rows if r["normal_abs_pct"] is not None])
    ci = None
    if len(eps) >= MIN_EPISODES_FOR_INTERVAL:
        groups = [np.array([r["return_pct"] for r in e]) for e in eps]
        rng = np.random.default_rng(zlib.crc32(key.encode("utf-8")))
        medians = [np.median(np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))])) for _ in range(BOOTSTRAP_N)]
        ci = [round(float(np.percentile(medians, 2.5)), 4), round(float(np.percentile(medians, 97.5)), 4)]
    if len(rows) < MIN_EPISODES_FOR_INTERVAL or len(eps) < MIN_EPISODES_FOR_INTERVAL:
        flag, note = "too_few", f"Too few to describe: {len(rows)} measured, {len(eps)} separate episode{'' if len(eps) == 1 else 's'}."
    elif len(rows) < SMALL_N or len(eps) < SMALL_EPISODES:
        flag, note = "small", f"Small sample: {len(rows)} measured, {len(eps)} separate episodes. Read with care."
    else:
        flag, note = "ok", f"{len(rows)} measured, {len(eps)} separate episodes. Still descriptive only."
    return {"n": len(rows), "episodes": len(eps), "median_pct": _median(returns), "min_pct": round(min(returns), 4), "max_pct": round(max(returns), 4),
            "ci95": ci, "median_abs_pct": median_abs, "normal_abs_pct": normal_abs,
            "abs_vs_normal": round(median_abs / normal_abs, 2) if normal_abs else None,
            "normal_median_pct": _median([r["normal_median_pct"] for r in rows if r["normal_median_pct"] is not None]),
            "sample_flag": flag, "sample_note": note}


class MacroWindows:
    """Which events sit near a macro release. Without every kind of release, or outside their span, the answer is "unknown"."""

    def __init__(self, releases: list[dict]):
        self.releases = sorted(releases, key=lambda r: r["at"])
        self.times = [r["at"] for r in self.releases]
        kinds = {k: [r["at"] for r in self.releases if r["kind"] == k] for k in MACRO_KINDS}
        self.missing = [k for k, v in kinds.items() if not v]
        self.complete = not self.missing
        self.start = max(min(v) for v in kinds.values()) if self.complete else None
        self.end = min(max(v) for v in kinds.values()) if self.complete else None

    def near(self, ms: int) -> list[dict]:
        lo = bisect.bisect_left(self.times, ms - MACRO_WINDOW_MS)
        hi = bisect.bisect_right(self.times, ms + MACRO_WINDOW_MS)
        return self.releases[lo:hi]

    def bucket(self, ms: int) -> str:
        if not self.complete or ms < self.start or ms > self.end:
            return "unknown"
        return "macro_window" if self.near(ms) else "clean"

    def warning(self) -> str | None:
        if self.complete:
            return None
        names = ", ".join(self.missing)
        return (f"Release dates are unavailable for: {names}. Events cannot be shown to be clear of macro releases, "
                "so none are counted as clean.")


class ReactionService:
    def __init__(self, db: Database, calendar=None, clock: Callable[[], int] = now_ms):
        self.db = db
        self.calendar = calendar
        self.clock = clock
        self._study: tuple[int, dict] | None = None

    # ------------------------------------------------------------------ measure (background thread only)
    def _refresh_releases(self, now: int) -> None:
        """Keep a snapshot of the release dates, so reading the study never fetches. A failed fetch keeps the last snapshot."""
        if self.calendar is None:
            return
        try:
            releases = self.calendar.known_releases()["releases"]
        except Exception as exc:       # any calendar failure leaves the last good snapshot
            log.warning("Reaction study: release dates not refreshed (%s)", type(exc).__name__)
            return
        if releases:
            self.db.cache_set("intel:macro_releases", {"releases": releases}, now)

    def windows(self) -> MacroWindows:
        return MacroWindows(self._releases())

    def _releases(self) -> list[dict]:
        hit = self.db.cache_get("intel:macro_releases")
        return hit[0]["releases"] if hit else []

    def update(self) -> dict:
        """Measure what newly closed candles can answer. Existing rows are never changed."""
        now = self.clock()
        self._refresh_releases(now)
        self._study = None
        added = {"baselines_added": 0, "outcomes_added": 0}
        span = self.db.candle_span(PROVIDER, SYMBOL, "1h")
        if not span["count"]:
            return added
        state = self.db.intel_reaction_state()
        subjects = [("article", a["id"], a["published_at"]) for a in self.db.intel_timed_articles()]
        subjects += [("macro", f"{r['id']}@{r['at']}", r["at"]) for r in self._releases()]
        due = []
        for kind, sid, ms in subjects:
            if ms > now or align_base(ms) < span["first"]:
                continue
            st = state.get((kind, sid), {"baseline": None, "outcomes": {}})
            have_base = st["baseline"] is not None
            need = [h for h, hrs in HORIZONS.items() if h not in st["outcomes"] and align_target(ms, hrs) + HOUR - 1 <= span["last"]]
            if not need and (have_base or align_base(ms) + HOUR - 1 > span["last"]):
                continue
            due.append((kind, sid, ms, not have_base, need))
        if not due:
            return added
        start = min(align_base(d[2]) for d in due) - (NORMAL_LOOKBACK_H + 48) * HOUR
        candles = Candles(self.db, start, now)
        for kind, sid, ms, need_baseline, need in due:
            baseline, outcomes = measure(candles, ms, need_baseline, need)
            if baseline is None and not outcomes:
                continue
            b, o = self.db.intel_add_reaction(kind, sid, baseline, outcomes, now)
            added["baselines_added"] += b
            added["outcomes_added"] += o
        self._study = None
        return added

    # ------------------------------------------------------------------ read (SQLite only)
    @staticmethod
    def _rows(state: dict, keys: list[tuple[str, str]], horizon: str) -> list[dict]:
        out = []
        for key in keys:
            st = state.get(key)
            o = st["outcomes"].get(horizon) if st and st["baseline"] else None
            if o:
                out.append({"return_pct": o["return_pct"], "normal_abs_pct": o["normal_abs_pct"], "normal_median_pct": o["normal_median_pct"],
                            "base_time": st["baseline"]["base_open_time"] + HOUR, "target_time": o["target_open_time"] + HOUR})
        return out

    def _group(self, state: dict, keys: list[tuple[str, str]], name: str) -> dict:
        return {h: summarize(self._rows(state, keys, h), f"{name}|{h}") for h in HORIZONS}

    def study(self) -> dict:
        now = self.clock()
        if self._study and now - self._study[0] < STUDY_CACHE_MS:
            return self._study[1]
        state = self.db.intel_reaction_state()
        windows = MacroWindows(self._releases())
        buckets: dict[str, dict[str, list]] = {c: {"clean": [], "macro_window": []} for c in CATEGORIES}
        excluded = {"publication_missing": 0, "published_after_first_seen": 0, "macro_unknown": 0}
        for e in self.db.intel_event_times():
            if e["time_flag"] in EXCLUDED_FLAGS or e["event_time_at"] is None:
                excluded[e["time_flag"] if e["time_flag"] in EXCLUDED_FLAGS else "publication_missing"] += 1
                continue
            bucket = windows.bucket(e["event_time_at"])
            if bucket == "unknown":
                excluded["macro_unknown"] += 1
                continue
            buckets.setdefault(e["category"], {"clean": [], "macro_window": []})[bucket].append(("article", e["anchor_article_id"]))
        categories = [{"category": c, "label": CATEGORY_LABELS.get(c, c),
                       **{b: {"events": len(keys), "horizons": self._group(state, keys, f"{c}|{b}")} for b, keys in groups.items()}}
                      for c, groups in buckets.items()]
        measured = [r for r in windows.releases if state.get(("macro", f"{r['id']}@{r['at']}"), {}).get("baseline")]
        macro = []
        for kind in MACRO_KINDS:
            mine = [r for r in measured if r["kind"] == kind]
            shared = sum(1 for r in mine if any(o["kind"] != kind and abs(o["at"] - r["at"]) <= MACRO_WINDOW_MS for o in measured))
            macro.append({"kind": kind, "label": MACRO_LABELS[kind], "events": len(mine), "shares_window": shared,
                          "horizons": self._group(state, [("macro", f"{r['id']}@{r['at']}") for r in mine], f"macro|{kind}")})
        span = self.db.candle_span(PROVIDER, SYMBOL, "1h")
        out = {"label": LABEL, "notes": NOTES, "consensus_note": CONSENSUS_NOTE, "horizons": list(HORIZONS), "default_horizon": "24h",
               "generated_at": now, "candles_through": span["last"], "excluded": excluded,
               "calendar": {"complete": windows.complete, "coverage_start": windows.start, "coverage_end": windows.end,
                            "releases_known": len(windows.releases), "warning": windows.warning()},
               "totals": {"events": sum(excluded.values()) + sum(len(k) for g in buckets.values() for k in g.values()),
                          "clean": sum(len(g["clean"]) for g in buckets.values()),
                          "macro_window": sum(len(g["macro_window"]) for g in buckets.values())},
               "categories": categories, "macro_releases": macro}
        self._study = (now, out)
        return out

    def _other_markets(self, event_ms: int | None) -> list[dict]:
        day = dt.datetime.fromtimestamp(event_ms / 1000, dt.timezone.utc).date() if event_ms is not None else None
        out = []
        for s in MARKET_SERIES:
            pts = self.db.intel_market_range(s["id"]) if day else []
            before = next((p for p in reversed(pts) if p["data_date"] < day.isoformat()), None)
            row = {k: s[k] for k in ("id", "name", "unit", "source", "delay_label")}
            row["before"] = {"date": before["data_date"], "value": before["value"]} if before else None
            row["after"] = {}
            for label, days in MARKET_AFTER.items():
                row["after"][label] = None
                if not day:
                    continue
                low, high = day + dt.timedelta(days=days), day + dt.timedelta(days=days + MARKET_MAX_GAP_DAYS)
                p = next((p for p in pts if low.isoformat() <= p["data_date"] <= high.isoformat()), None)
                if p:
                    row["after"][label] = {"date": p["data_date"], "value": p["value"], "change": p["value"] - before["value"] if before else None,
                                           "change_pct": (p["value"] / before["value"] - 1) * 100 if before and before["value"] else None}
            out.append(row)
        return out

    def event_detail(self, event_id: int) -> dict | None:
        e = self.db.intel_event(event_id)
        if e is None:
            return None
        now = self.clock()
        ms, anchor = e["event_time_at"], e["anchor_article_id"]
        st = self.db.intel_reaction_state().get(("article", anchor), {"baseline": None, "outcomes": {}}) if anchor else {"baseline": None, "outcomes": {}}
        baseline = st["baseline"]
        horizons = []
        for h, hours in HORIZONS.items():
            o = st["outcomes"].get(h) if baseline else None
            row = {"horizon": h, "status": "unavailable", "return_pct": None, "window_hours": None, "normal_abs_pct": None, "target_close": None, "target_close_time": None}
            if ms is not None:
                target_close = align_target(ms, hours) + HOUR
                row["target_close_time"] = target_close
                if o:
                    row.update(status="measured", return_pct=o["return_pct"], window_hours=o["window_hours"], normal_abs_pct=o["normal_abs_pct"], target_close=o["target_close"])
                elif align_base(ms) + HOUR > now or (baseline and target_close > now):
                    row["status"] = "pending"
                elif baseline:
                    row["status"] = "missing"
            horizons.append(row)
        note = None
        if baseline is None:
            note = ("No publication time, so no reaction is measured." if ms is None
                    else "The closed candle just before the event is not available." if align_base(ms) + HOUR <= now
                    else "The candle before the event has not closed yet.")
        windows = MacroWindows(self._releases())
        near = windows.near(ms) if ms is not None else []
        return {"id": event_id, "title": e["title"], "category": e["category"], "category_label": CATEGORY_LABELS.get(e["category"], e["category"]),
                "event_time_at": ms, "first_seen_at": e["first_seen_at"], "time_flag": e["time_flag"], "time_flag_text": TIME_FLAG_TEXT.get(e["time_flag"]),
                "anchor_article": self.db.intel_article(anchor) if anchor else None, "baseline": baseline, "baseline_note": note, "horizons": horizons,
                "macro_window": {"overlaps": bool(near), "known": ms is not None and windows.bucket(ms) != "unknown",
                                 "releases": [{k: r[k] for k in ("kind", "at", "event", "link")} for r in near]},
                "other_markets": self._other_markets(ms),
                "other_markets_note": "Daily data, not hourly: \"before\" is the last value dated before the event's UTC day, each later value the first dated at least that many days after it. Often days late, and not comparable with BTC's hourly moves.",
                "label": LABEL, "notes": NOTES[:3]}
