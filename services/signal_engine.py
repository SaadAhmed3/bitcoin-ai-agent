"""Transparent signal engine (Phase 5).

Turns the multi-timeframe technical analysis, the news bias and data quality into
one of five research signals:

    STRONG BUY SETUP | BUY SETUP | HOLD | SELL / REDUCE EXPOSURE | NO TRADE

How it works
------------
1. Six components, each scored in [-1, +1] with written reasons:
     trend          1d + 4h structure, EMA stack, price vs EMA200, SMA50/200
     momentum       4h + 1h RSI, MACD histogram, ADX/DI
     volume         4h volume vs 20-bar average on the last candle, volume-confirmed breakouts
     structure      4h breakout/false-breakout state, room to the nearest level, divergence
     overextension  4h + 1h RSI extremes and Bollinger %B (a brake against chasing)
     news           rule-based news bias x confidence (from the news engine)
2. Weighted sum -> score in [-1, +1]. Weights are documented, adjustable and
   re-normalised over the components that have data. They are NOT optimised:
   validating or changing them is the job of Phase 6 walk-forward testing.
3. Setup from thresholds; a BUY setup also needs reward/risk >= min_rr after fees.
4. Gates turn the final signal into NO TRADE when data is stale or missing,
   volatility is extreme, evidence contradicts itself, the setup fights the daily
   trend, fresh high-impact news opposes it, or the strategy has no validated edge.

The score is a weighted evidence count. It is NOT a probability of profit.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

ENGINE_VERSION = "signal-1.8"   # batch 2: 1h data check, cross-source price check, news only blocks

STRONG_BUY, BUY, HOLD, SELL, NO_TRADE = "STRONG BUY SETUP", "BUY SETUP", "HOLD", "SELL / REDUCE EXPOSURE", "NO TRADE"

# Entry rules (Phase 6.2). "standard" is the original behaviour. "breakout" and "pullback" turn a buy setup that
# was downgraded for low reward/risk into a PENDING order instead. Their parameters are FIXED IN ADVANCE and
# were not tuned on any backtest: do not change them to improve results.
ENTRY_MODES = ("standard", "breakout", "pullback")
PENDING_VALID_BARS = 6          # a pending order lives for one day of 4h bars
PULLBACK_MAX_DIST_ATR = 3.0     # a pullback limit further than this below the live price is not placed

DEFAULT_WEIGHTS = {
    "trend": 0.30,
    "momentum": 0.25,
    "volume": 0.10,
    "structure": 0.10,
    "overextension": 0.10,
    "news": 0.0,   # signal-1.8: not in the score (no backtest ever included it); news can only block
}
WEIGHT_RATIONALE = {
    "trend": "Largest weight: higher-timeframe trend is the most persistent technical feature; signals against it fail more often.",
    "momentum": "Confirms the trend is currently being expressed on the trading timeframes.",
    "volume": "Moves on above-average volume are more likely to be genuine participation.",
    "structure": "Breakouts, failed breakouts and nearby levels change the immediate odds and the room to move.",
    "overextension": "Brake against chasing stretched moves (RSI extremes, closes outside the Bollinger Bands).",
    "news": "Not part of the score since signal-1.8: headline sentiment was never in any backtest, so it may only block "
            "(confirmed, unverified or speculative high-impact news against the setup - see the NO TRADE rules).",
}


@dataclass
class EngineConfig:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    strong_threshold: float = 0.50
    buy_threshold: float = 0.25
    sell_threshold: float = -0.25
    min_agreement: float = 0.60        # weighted share of components agreeing with the score's sign
    strong_min_agreement: float = 0.75
    counter_trend_limit: float = 0.30  # daily trend score against the setup beyond this -> NO TRADE
    min_rr: float = 1.5                # reward/risk to target 1, after costs
    fee_pct: float = 0.10              # per side, %
    slippage_pct: float = 0.05         # per side, %
    min_primary_bars: int = 100
    max_gaps: int = 5
    require_validated_edge: bool = True
    horizon: str = "1-3 days (4h primary, 1d trend filter, 1h confirmation)"
    entry_mode: str = "standard"       # "standard" | "breakout" | "pullback" (see ENTRY_MODES)
    max_source_divergence_pct: float = 0.5   # signal-1.8: two exchanges disagreeing by more -> NO TRADE

    def __post_init__(self):
        if self.entry_mode not in ENTRY_MODES:
            raise ValueError(f"entry_mode must be one of: {', '.join(ENTRY_MODES)}")


def validate_weights(weights: dict[str, Any]) -> dict[str, float]:
    """Check user-supplied weights. Unknown keys and negatives are rejected; result is normalised to sum 1."""
    if not isinstance(weights, dict) or not weights:
        raise ValueError("weights must be a non-empty object")
    unknown = set(weights) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise ValueError(f"Unknown weight(s): {', '.join(sorted(unknown))}")
    out = dict(DEFAULT_WEIGHTS)
    for k, v in weights.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"Weight '{k}' must be a number") from None
        if math.isnan(f) or f < 0 or f > 10:
            raise ValueError(f"Weight '{k}' must be between 0 and 10")
        out[k] = f
    total = sum(out.values())
    if total <= 0:
        raise ValueError("At least one weight must be positive")
    if out["news"] > 0:
        raise ValueError("'news' must be 0: since signal-1.8 news is not part of the score (no backtest ever included "
                         "it); news can only block")
    if out["trend"] == 0 and out["momentum"] == 0:
        raise ValueError("Trend and momentum cannot both be zero")
    return {k: round(v / total, 4) for k, v in out.items()}


# --------------------------------------------------------------------------- helpers
def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _ok(a: dict | None) -> bool:
    return bool(a) and a.get("status") == "ok"


def _ind(a: dict, key: str):
    return (a.get("indicators") or {}).get(key)


@dataclass
class Component:
    """A scored component. `facts` are (text, polarity) with polarity +1 bullish, -1 bearish, 0 neutral."""
    name: str
    score: float | None
    facts: list[tuple[str, int]]

    @property
    def reasons(self) -> list[str]:
        return [t for t, _ in self.facts]

    def as_dict(self, weight: float) -> dict:
        contrib = None if self.score is None else round(self.score * weight, 4)
        return {"name": self.name, "score": None if self.score is None else round(self.score, 3),
                "weight": weight, "contribution": contrib, "reasons": self.reasons,
                "facts": [{"text": t, "polarity": p} for t, p in self.facts],
                "rationale": WEIGHT_RATIONALE[self.name]}


def _seq(values: list | None) -> str:
    """84,898 ↓ 82,061 ↑ 82,500 - each arrow is the step from the previous swing."""
    vals = values or []
    if not vals:
        return "n/a"
    out = f"{vals[0]:,.0f}"
    for a, b in zip(vals, vals[1:]):
        out += f" {'↑' if b > a else '↓'} {b:,.0f}"
    return out


def _pol(x: float, eps: float = 0.05) -> int:
    return 1 if x > eps else -1 if x < -eps else 0


# --------------------------------------------------------------------------- components
def trend_tf(a: dict, label: str) -> tuple[float | None, list[tuple[str, int]]]:
    if not _ok(a):
        return None, [(f"{label}: insufficient data", 0)]
    parts, why = [], []
    st = a.get("structure") or {}
    st_score = st.get("score")
    if st_score is not None:
        parts.append(st_score)
        why.append((f"{label} structure {st.get('trend')} ({st_score:+.2f} over recent swings): "
                    f"highs {_seq(st.get('high_sequence'))}; lows {_seq(st.get('low_sequence'))}", _pol(st_score)))
    e9, e21, e50, e200 = (_ind(a, k) for k in ("ema9", "ema21", "ema50", "ema200"))
    if None not in (e9, e21, e50):
        if e9 > e21 > e50:
            parts.append(1.0); why.append((f"{label} EMA 9>21>50 (bullish stack)", 1))
        elif e9 < e21 < e50:
            parts.append(-1.0); why.append((f"{label} EMA 9<21<50 (bearish stack)", -1))
        else:
            parts.append(0.0); why.append((f"{label} EMAs mixed", 0))
    price = a.get("price")
    if e200 is not None and price is not None:
        above = price > e200
        parts.append(1.0 if above else -1.0)
        why.append((f"{label} close {price:,.0f} {'above' if above else 'below'} EMA200 {e200:,.0f}", 1 if above else -1))
    s50, s200 = _ind(a, "sma50"), _ind(a, "sma200")
    if s50 is not None and s200 is not None:
        parts.append(0.5 if s50 > s200 else -0.5)
        why.append((f"{label} SMA50 {'>' if s50 > s200 else '<'} SMA200 ({'golden' if s50 > s200 else 'death'}-cross regime)",
                    1 if s50 > s200 else -1))
    return _mean(parts), why


def score_trend(a1d: dict, a4h: dict) -> tuple[Component, float | None]:
    d, why_d = trend_tf(a1d, "1d")
    h, why_h = trend_tf(a4h, "4h")
    if d is None and h is None:
        return Component("trend", None, why_d + why_h), None
    score = d if h is None else h if d is None else 0.5 * d + 0.5 * h
    head = [(f"Net trend {score:+.2f} = 1d {'n/a' if d is None else f'{d:+.2f}'} and 4h {'n/a' if h is None else f'{h:+.2f}'} (50/50)", 0)]
    return Component("trend", _clip(score), head + why_d + why_h), d


def momentum_tf(a: dict, label: str) -> tuple[float | None, list[tuple[str, int]]]:
    if not _ok(a):
        return None, [(f"{label}: insufficient data", 0)]
    parts, why = [], []
    atr_v = _ind(a, "atr14")
    rsi = _ind(a, "rsi14")
    if rsi is not None:
        v = _clip((rsi - 50) / 20)
        parts.append(v); why.append((f"{label} RSI {rsi:.1f}", _pol(v)))
    hist = _ind(a, "macd_hist")
    if hist is not None:
        # Scaled by ATR: a histogram of half an ATR or more counts fully (0.7); a flat histogram counts ~0.
        v = 0.7 * _clip(hist / (0.5 * atr_v)) if atr_v else (0.7 if hist > 0 else -0.7)
        size = "strongly " if abs(v) >= 0.5 else "slightly " if abs(v) < 0.2 else ""
        why.append((f"{label} MACD {size}{'above' if hist > 0 else 'below'} signal (hist {hist:+,.1f})", _pol(v)))
        parts.append(v)
    adx, pdi, mdi = _ind(a, "adx"), _ind(a, "plus_di"), _ind(a, "minus_di")
    if adx is not None and pdi is not None and mdi is not None:
        spread = (pdi - mdi) / max(1.0, pdi + mdi)
        if adx >= 20:
            # Wording and score come from the same spread, so the text can never contradict the polarity.
            if abs(spread) < 0.05:
                v, side = 0.0, "roughly balanced"
            else:
                v = _clip(spread * 3) * min(1.0, adx / 40)
                who = "buyers" if spread > 0 else "sellers"
                side = f"slight edge to {who}" if abs(spread) < 0.15 else f"{who} in control"
            why.append((f"{label} ADX {adx:.0f}, +DI {pdi:.0f} / -DI {mdi:.0f}: {side}", _pol(v)))
        else:
            v = 0.0
            why.append((f"{label} ADX {adx:.0f}: no trend strength", 0))
        parts.append(v)
    return _mean(parts), why


def score_momentum(a4h: dict, a1h: dict) -> Component:
    h4, w4 = momentum_tf(a4h, "4h")
    h1, w1 = momentum_tf(a1h, "1h")
    if h4 is None and h1 is None:
        return Component("momentum", None, w4 + w1)
    score = h4 if h1 is None else h1 if h4 is None else 0.6 * h4 + 0.4 * h1
    return Component("momentum", _clip(score), w4 + w1)


def score_volume(a4h: dict) -> Component:
    if not _ok(a4h):
        return Component("volume", None, [("4h: insufficient data", 0)])
    vr, up = _ind(a4h, "vol_ratio"), _ind(a4h, "last_candle_up")
    br = a4h.get("breakout") or {}
    if vr is None:
        return Component("volume", None, [("4h volume average not available", 0)])
    sign = 1 if up else -1
    if br.get("volume_confirmed") and br.get("state") in ("bullish_breakout", "bearish_breakdown"):
        s = 1.0 if br["state"] == "bullish_breakout" else -1.0
        return Component("volume", s, [(f"4h {br['state'].replace('_', ' ')} on {vr:.1f}x average volume", int(s))])
    if vr >= 1.5:
        return Component("volume", sign * 1.0, [(f"4h last candle {'up' if up else 'down'} on {vr:.1f}x average volume", sign)])
    if vr >= 1.0:
        return Component("volume", sign * 0.4, [(f"4h volume {vr:.1f}x average on a {'rising' if up else 'falling'} candle", sign)])
    return Component("volume", 0.0, [(f"4h volume below average ({vr:.1f}x): weak participation", 0)])


def score_structure(a4h: dict) -> Component:
    if not _ok(a4h):
        return Component("structure", None, [("4h: insufficient data", 0)])
    s, why = 0.0, []
    state = (a4h.get("breakout") or {}).get("state")
    table = {"bullish_breakout": 0.8, "bearish_breakdown": -0.8, "false_breakout_up": -0.6, "false_breakdown": 0.6}
    if state in table:
        s += table[state]; why.append((f"4h {state.replace('_', ' ')}", _pol(table[state])))
    elif state:
        why.append((f"4h {state.replace('_', ' ')}", 0))
    price, atr = a4h.get("price"), _ind(a4h, "atr14")
    lv = a4h.get("levels") or {}
    if price and atr:
        res = lv.get("resistance") or []
        sup = lv.get("support") or []
        if res and (res[0]["price"] - price) < atr:
            s -= 0.3; why.append((f"Resistance {res[0]['price']:,.0f} within 1 ATR of the 4h close: limited upside room", -1))
        major = next((l for l in sup if l["touches"] >= 2), None)
        if major and (price - major["price"]) < atr:
            s += 0.2; why.append((f"Support {major['price']:,.0f} ({major['touches']} touches) within 1 ATR of the 4h close", 1))
        zone = lv.get("current_zone")
        if zone:
            why.append((f"4h close inside a level zone {zone['low']:,.0f}-{zone['high']:,.0f} ({zone['touches']} touches)", 0))
    div = a4h.get("divergence") or {}
    if div.get("bullish"):
        s += 0.5; why.append(("4h bullish RSI divergence (experimental)", 1))
    if div.get("bearish"):
        s -= 0.5; why.append(("4h bearish RSI divergence (experimental)", -1))
    return Component("structure", _clip(s), why or [("No notable structure signal", 0)])


def score_overextension(a4h: dict, a1h: dict) -> Component:
    parts, why = [], []
    for a, label in ((a4h, "4h"), (a1h, "1h")):
        if not _ok(a):
            continue
        rsi, pb = _ind(a, "rsi14"), _ind(a, "bb_pct_b")
        if rsi is not None:
            if rsi >= 75:
                parts.append(-1.0); why.append((f"{label} RSI {rsi:.0f}: strongly overbought", -1))
            elif rsi >= 70:
                parts.append(-0.5); why.append((f"{label} RSI {rsi:.0f}: overbought", -1))
            elif rsi <= 25:
                parts.append(1.0); why.append((f"{label} RSI {rsi:.0f}: strongly oversold", 1))
            elif rsi <= 30:
                parts.append(0.5); why.append((f"{label} RSI {rsi:.0f}: oversold", 1))
            else:
                parts.append(0.0)
        if pb is not None:
            if pb > 1:
                parts.append(-0.5); why.append((f"{label} close above upper Bollinger Band", -1))
            elif pb < 0:
                parts.append(0.5); why.append((f"{label} close below lower Bollinger Band", 1))
    if not parts:
        return Component("overextension", None, [("insufficient data", 0)])
    return Component("overextension", _clip(_mean(parts)), why or [("Not overextended", 0)])


def score_news(news_summary: dict | None) -> Component:
    if not news_summary or news_summary.get("bias_label") in (None, "insufficient_news"):
        return Component("news", None, [("Not enough recent directional news. News is not in the score since "
                                         "signal-1.8: it can only block.", 0)])
    coverage = min(1.0, float(news_summary.get("weight_total", 0)) / 3.0)
    b = float(news_summary.get("bias_score", 0))
    v = _clip(b * coverage)
    return Component("news", v,
                     [(f"News bias {news_summary['bias_label']} ({b:+.2f}); news coverage {coverage:.0%} of full weight "
                       f"({news_summary.get('directional_active', 0)} directional events). Coverage is not a confidence level. "
                       "Not in the score since signal-1.8: news can only block.",
                       _pol(v))])


def news_blocks(events: list[dict] | None, against: str | None) -> tuple[str | None, list[str]]:
    """signal-1.8: news can only BLOCK. High-impact news against the setup (bearish vs a BUY setup, bullish vs a
    SELL setup; a HOLD is never blocked):
      confirmed            new (< 2 h) or recent (< 24 h, not priced in)  -> block
      unverified / rumour  new (< 2 h)                                     -> block ("unverified, short block")
      speculative          new (< 2 h)                                     -> block ("speculative, short block")
      speculative          recent                                          -> warning only
    signal-1.7 blocked any verification within 2 h only; nothing it blocked is unblocked here.
    An event without a verification level is treated as unverified. Returns (reason or None, warnings)."""
    if not against:
        return None, []

    def text(e: dict, label: str) -> str:
        age = e.get("age_hours")
        when = f"{age:.1f} h ago" if isinstance(age, (int, float)) else e.get("freshness", "recent")
        return (f"high-impact {against} news ({label}, {when}): \"{e.get('title')}\" - "
                f"{e.get('publisher') or 'unknown publisher'} {e.get('link') or ''}".rstrip())

    warnings = []
    for e in events or []:
        if e.get("impact") != "high" or e.get("sentiment") != against:
            continue
        verification, fresh = e.get("verification") or "unverified", e.get("freshness")
        if verification == "confirmed" and fresh in ("new", "recent"):
            lead = "Fresh" if fresh == "new" else "Recent"
            return f"{lead} {text(e, 'confirmed')}. News can only block; it is not in the score.", warnings
        if verification in ("unverified", "speculative") and fresh == "new":
            return (f"Fresh {text(e, verification + ', short block')}. Blocks for 2 h until it is confirmed or "
                    "digested.", warnings)
        if verification == "speculative" and fresh == "recent":
            warnings.append(f"Speculative {text(e, 'speculative')}: warning only, not a block.")
    return None, warnings


# --------------------------------------------------------------------------- trade plan
def _costs_pct(cfg: EngineConfig) -> float:
    return 2 * (cfg.fee_pct + cfg.slippage_pct) / 100  # round trip, as a fraction of price


MIN_MAJOR_TOUCHES = 2


def live_levels(a4h: dict, price: float | None) -> dict:
    """Re-sort the 4h levels against the LIVE price and mark single-touch levels as minor.

    The analysis classifies levels against the last closed candle; between closes the
    price can cross a level, so a 'support' can already be above the live price.
    """
    lv = a4h.get("levels") or {}
    if not price:
        return {"support": lv.get("support") or [], "resistance": lv.get("resistance") or [], "zone": None,
                "major_support": None, "major_resistance": None, "basis": "last closed 4h candle"}
    allv = list(lv.get("support") or []) + list(lv.get("resistance") or [])
    if lv.get("current_zone"):
        allv.append(lv["current_zone"])
    out_s, out_r, zone = [], [], None
    for l in allv:
        l = {**l, "minor": l.get("touches", 1) < MIN_MAJOR_TOUCHES}
        lo, hi = l.get("low", l["price"]), l.get("high", l["price"])
        if lo <= price <= hi:
            zone = l
        elif hi < price:
            out_s.append(l)
        else:
            out_r.append(l)
    out_s.sort(key=lambda l: -l["price"])
    out_r.sort(key=lambda l: l["price"])
    return {"support": out_s, "resistance": out_r, "zone": zone,
            "major_support": next((l for l in out_s if not l["minor"]), None),
            "major_resistance": next((l for l in out_r if not l["minor"]), None),
            "basis": "live price"}


def build_buy_plan(a4h: dict, cfg: EngineConfig, price: float | None = None, levels: dict | None = None) -> dict | None:
    """Entry zone, stop, targets and reward/risk after costs, from the live price, 4h levels and ATR.

    The entry floor and stop use only major levels (2+ touches); targets may use any resistance.
    """
    price = price or a4h.get("price")
    atr = _ind(a4h, "atr14")
    if not price or not atr:
        return None
    lv = levels or live_levels(a4h, price)
    supports = [l for l in lv["support"] if not l.get("minor")]
    resistances = lv["resistance"]
    entry_low = max(price - 0.75 * atr, supports[0]["price"]) if supports else price - 0.75 * atr
    entry_high = price
    entry = (entry_low + entry_high) / 2
    stop_basis = "2 ATR below entry (no nearby support)"
    stop = entry - 2 * atr
    for s in supports:
        if s["price"] < entry_low and entry - s["price"] <= 3 * atr:
            stop = s["price"] - 0.5 * atr
            stop_basis = f"0.5 ATR below support {s['price']:,.0f} ({s['touches']} touch{'es' if s['touches'] != 1 else ''})"
            break
    if entry - stop < atr:
        stop, stop_basis = entry - atr, "1 ATR minimum stop distance"
    # Targets sit at the LOWER edge of a resistance zone: price only has to reach the zone, not trade into it.
    # T1 is the nearest MAJOR resistance (2+ touches) however close: it is the first real barrier, and if it is
    # near, reward/risk is poor and the setup is downgraded - that is the honest outcome.
    def edge(r: dict) -> float:
        return r.get("low", r["price"])

    def touches(n: int) -> str:
        return f"{n} touch" if n == 1 else f"{n} touches"

    def zone_txt(r: dict) -> str:
        lo, hi = edge(r), r.get("high", r["price"])
        return f"{lo:,.0f}" if lo == hi else f"{lo:,.0f}-{hi:,.0f}"

    above = [r for r in resistances if edge(r) > entry_high]
    majors = [r for r in above if not r.get("minor") and r.get("touches", 1) >= MIN_MAJOR_TOUCHES]
    targets = []
    t1 = majors[0] if majors else next((r for r in above if edge(r) >= entry_high + atr), None)
    if t1:
        kind = "major" if t1 in majors else "minor"
        targets.append({"price": round(edge(t1), 2),
                        "basis": f"lower edge of {kind} resistance {zone_txt(t1)} ({touches(t1['touches'])})"})
    else:
        targets.append({"price": round(entry_high + 2 * atr, 2), "basis": "2 ATR projection (no resistance found)"})
    later = [r for r in majors if edge(r) >= targets[0]["price"] + 0.5 * atr]
    t2 = later[0] if later else next((r for r in above if edge(r) >= targets[0]["price"] + 0.5 * atr), None)
    targets.append({"price": round(edge(t2), 2),
                    "basis": f"lower edge of {'major' if t2 in majors else 'minor'} resistance {zone_txt(t2)} ({touches(t2['touches'])})"}
                   if t2 else {"price": round(targets[0]["price"] + 1.5 * atr, 2), "basis": "1.5 ATR beyond target 1"})
    obstacles = [f"minor resistance {zone_txt(r)} ({touches(r['touches'])})"
                 for r in above if r.get("minor") and edge(r) < targets[0]["price"]]
    obstacles_t2 = [f"{'minor' if r.get('minor') else 'major'} resistance {zone_txt(r)} ({touches(r['touches'])})"
                    for r in above if targets[0]["price"] < edge(r) < targets[1]["price"]]
    first_barrier_atr = round((targets[0]["price"] - entry_high) / atr, 2)

    def rr(at: float, target: float) -> float | None:
        cost = at * _costs_pct(cfg)
        risk = at - stop + cost
        return round((target - at - cost) / risk, 2) if risk > 0 else None

    for t in targets:
        # Primary R/R assumes buying at the live price (top of the entry zone): the worst fill inside the zone.
        t["rr_after_costs"] = rr(entry_high, t["price"])
        t["rr_mid_zone"] = rr(entry, t["price"])
        t["pct"] = round((t["price"] / entry_high - 1) * 100, 2)
    return {
        "side": "long (spot)",
        "entry_zone": [round(entry_low, 2), round(entry_high, 2)],
        "entry_reference": round(entry, 2),
        "stop": round(stop, 2),
        "stop_basis": stop_basis,
        "stop_pct": round((stop / entry_high - 1) * 100, 2),
        "rr_basis": "rr_after_costs = buying at the live price (top of the zone); rr_mid_zone = a mid-zone fill",
        "obstacles_before_t1": obstacles,
        "obstacles_before_t2": obstacles_t2,
        "room_note": (f"No room before resistance: target 1 is {targets[0]['pct']:.2f}% away, inside or near the "
                      f"{_costs_pct(cfg) * 100:.2f}% round-trip cost band." if targets[0]["rr_after_costs"] is not None
                      and targets[0]["rr_after_costs"] <= 0.25 else None),
        "t1_distance_atr": first_barrier_atr,
        "targets": targets,
        "rr_t1": targets[0]["rr_after_costs"],
        "assumed_costs": f"{cfg.fee_pct}% fee + {cfg.slippage_pct}% slippage per side "
                         f"({_costs_pct(cfg) * 100:.2f}% round trip)",
        "stop_warning": "A stop is a reference level, not a guarantee: gaps and fast markets can fill well below it.",
    }


def _rr_after_costs(at: float, stop: float, target: float, cfg: EngineConfig) -> float | None:
    """Same reward/risk formula as build_buy_plan: costs added to the risk and taken from the reward."""
    cost = at * _costs_pct(cfg)
    risk = at - stop + cost
    return round((target - at - cost) / risk, 2) if risk > 0 else None


def build_pending_order(mode: str, a4h: dict, cfg: EngineConfig, price: float, levels: dict,
                        plan: dict) -> tuple[dict | None, str | None]:
    """Pending order for a buy setup that was downgraded for low reward/risk. Returns (order, rejected_reason).

    breakout: buy after a 4h close above the nearest MAJOR resistance zone (trigger = its upper edge).
    pullback: buy if price trades below the nearest MAJOR support zone (limit = its upper edge).
    Both are rejected when their own reward/risk is below cfg.min_rr. All distances are fixed multiples of ATR.
    """
    atr = _ind(a4h, "atr14")
    if not atr or not price:
        return None, "no_atr"

    def lo(l: dict) -> float:
        return l.get("low", l["price"])

    def hi(l: dict) -> float:
        return l.get("high", l["price"])

    def zone_txt(l: dict) -> str:
        return f"{lo(l):,.0f}" if lo(l) == hi(l) else f"{lo(l):,.0f}-{hi(l):,.0f}"

    if mode == "breakout":
        r = levels.get("major_resistance")
        if not r:
            return None, "no_major_resistance"
        trigger = hi(r)
        stop = min(lo(r) - 0.5 * atr, trigger - atr)
        majors = [x for x in levels["resistance"] if not x.get("minor") and lo(x) >= trigger + 0.5 * atr]
        if majors:
            target, t_basis = lo(majors[0]), f"lower edge of the next major resistance {zone_txt(majors[0])}"
        else:
            any_r = next((x for x in levels["resistance"] if lo(x) >= trigger + atr), None)
            if any_r:
                target, t_basis = lo(any_r), f"lower edge of resistance {zone_txt(any_r)} (no major one above)"
            else:
                target, t_basis = trigger + 2 * atr, "2 ATR above the trigger (no resistance above)"
        order = {"type": "breakout", "price": round(trigger, 2), "condition": f"4h close above {trigger:,.0f}",
                 "basis": f"upper edge of major resistance {zone_txt(r)} ({r['touches']} touches); target = {t_basis}"}
    elif mode == "pullback":
        s = levels.get("major_support")
        if not s:
            return None, "no_major_support"
        limit = hi(s)
        if price - limit > PULLBACK_MAX_DIST_ATR * atr:
            return None, "support_too_far"
        stop = min(lo(s) - 0.5 * atr, limit - atr)
        target = plan["targets"][0]["price"]
        order = {"type": "pullback", "price": round(limit, 2), "condition": f"price trades below {limit:,.0f}",
                 "basis": f"upper edge of major support {zone_txt(s)} ({s['touches']} touches); "
                          f"target = the standard plan's target 1 ({plan['targets'][0]['basis']})"}
    else:
        return None, None
    rr = _rr_after_costs(order["price"], stop, target, cfg)
    if rr is None or rr < cfg.min_rr:
        return None, "low_rr"
    order.update({"stop": round(stop, 2), "target": round(target, 2), "rr_t1": rr, "valid_bars": PENDING_VALID_BARS})
    return {k: order[k] for k in ("type", "price", "stop", "target", "rr_t1", "valid_bars", "condition", "basis")}, None


# --------------------------------------------------------------------------- engine
FORWARD_OBSERVATION_DAYS = 56   # after a validation the gate stays on this long (paper / forward observation)
DAY_MS = 86_400_000


def _utc_date(ms: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%d")


def in_forward_observation(validation: dict | None, now_ms: int) -> bool:
    v = validation or {}
    return bool(v.get("validated") and v.get("observation_until") and now_ms < v["observation_until"])


def validation_reason(validation: dict | None, now_ms: int | None = None) -> str:
    """NO TRADE reason that quotes the actual backtest state."""
    v = validation or {}
    if now_ms is not None and in_forward_observation(v, now_ms):
        return (f"Validated on {_utc_date(v['as_of'])}; forward observation period until "
                f"{_utc_date(v['observation_until'])}.")
    # Records written before Phase 6.2 have no period / entry mode: those runs were full-period, standard entry.
    period = v.get("period_kind") or (v.get("period") or {}).get("kind") or "full"
    if v.get("run_id") and period != "holdout":
        # 0.9.2: only a holdout run can validate, so a full or design run is not "the validation backtest" - stale
        # or not (batch 2: the signal-1.8 bump made run #1 stale, and "re-run it" was wrong advice).
        what = ("a full-period run (it includes design data)" if period == "full"
                else f"a {period}-period run (in-sample for rule choices)")
        return f"Not validated: no holdout run yet. Run #{v['run_id']} is {what} and cannot validate."
    if v.get("stale"):
        # Only holdout runs write validation records, so this holdout period is spent: re-running cannot validate.
        return (f"Not validated: the rules changed since the holdout run (run #{v.get('run_id', '?')}): settings, "
                "engine version or code. Validating the current rules needs a new holdout period.")
    if v.get("run_id"):
        failed = v.get("failed") or []
        why = f" Failed: {'; '.join(failed)}." if failed else ""
        entry = v.get("entry_mode") or "standard"
        return (f"Not validated: the validation backtest (run #{v['run_id']}, {period} period, {entry} entry, engine "
                f"{v.get('engine', '?')}) found no demonstrated edge after costs.{why}")
    return "Not validated: no backtest has been run yet - run one from the Backtest & validation panel."


def generate_signal(analyses: dict[str, dict], data_meta: dict[str, dict], price: dict | None,
                    news_summary: dict | None, news_events: list[dict] | None, cfg: EngineConfig,
                    validation: dict | None, now_ms: int) -> dict:
    a1h, a4h, a1d = analyses.get("1h") or {}, analyses.get("4h") or {}, analyses.get("1d") or {}
    trend, daily_trend = score_trend(a1d, a4h)
    comps = [trend, score_momentum(a4h, a1h), score_volume(a4h), score_structure(a4h),
             score_overextension(a4h, a1h), score_news(news_summary)]

    price_now = (price or {}).get("price") or a4h.get("price")
    lv_live = live_levels(a4h, price_now) if _ok(a4h) else {"support": [], "resistance": [], "zone": None,
                                                              "major_support": None, "major_resistance": None,
                                                              "basis": "n/a"}

    available = {c.name: cfg.weights[c.name] for c in comps if c.score is not None and cfg.weights.get(c.name, 0) > 0}
    wsum = sum(available.values())
    eff_w = {k: v / wsum for k, v in available.items()} if wsum else {}
    score = sum(c.score * eff_w[c.name] for c in comps if c.name in eff_w) if eff_w else 0.0

    # Agreement: weighted share of directional components on the same side as the score.
    directional = [c for c in comps if c.name in eff_w and abs(c.score) >= 0.1]
    agree_w = sum(eff_w[c.name] for c in directional if (c.score > 0) == (score > 0))
    dir_w = sum(eff_w[c.name] for c in directional)
    agreement = agree_w / dir_w if dir_w else 0.0
    # Share of total weight that carries any direction at all (agreement alone hides cancelled components).
    directional_weight = dir_w

    # ---- setup
    if score >= cfg.buy_threshold:
        setup = STRONG_BUY if score >= cfg.strong_threshold and agreement >= cfg.strong_min_agreement else BUY
    elif score <= cfg.sell_threshold:
        setup = SELL
    else:
        setup = HOLD
    raw_setup = setup                 # before the reward/risk check (diagnostics)
    downgrade = None
    setup_notes = []
    plan = None
    if setup in (STRONG_BUY, BUY):
        plan = build_buy_plan(a4h, cfg, price_now, lv_live) if _ok(a4h) else None
        if plan is None:
            setup_notes.append("No trade plan possible (missing 4h price/ATR) - downgraded to HOLD.")
            setup, downgrade = HOLD, "no_plan"
        elif plan["rr_t1"] is None or plan["rr_t1"] < cfg.min_rr:
            why = (f" The first barrier, {plan['targets'][0]['basis']}, is only {plan['t1_distance_atr']} ATR above the price."
                   if plan.get("t1_distance_atr") is not None and plan["t1_distance_atr"] < 1 else "")
            setup_notes.append(f"Reward/risk to target 1 is {plan['rr_t1']} after costs, below {cfg.min_rr} - downgraded to HOLD.{why}")
            setup, downgrade = HOLD, "low_rr"

    # ---- pending order (entry_mode "breakout" / "pullback" only; the setup itself stays HOLD)
    pending_order, pending_rejected = None, None
    if cfg.entry_mode != "standard" and downgrade == "low_rr" and plan:
        pending_order, pending_rejected = build_pending_order(cfg.entry_mode, a4h, cfg, price_now, lv_live, plan)
        if pending_order:
            setup_notes.append(f"Pending {pending_order['type']} order (research only): {pending_order['condition']}, "
                               f"reward/risk {pending_order['rr_t1']} after costs, valid {PENDING_VALID_BARS} bars.")

    # ---- gates (each with a short key, used by the backtest funnel)
    keyed: list[tuple[str, str]] = []
    if not price or price.get("price") is None:
        keyed.append(("no_price", "No current BTC price available."))
    elif price.get("is_stale"):
        keyed.append(("stale_price", f"BTC price is stale ({price.get('age_seconds')}s old)."))
    # signal-1.8: a second exchange's price (live only; the backtest has none). Unavailable = a warning, not a block.
    xc = (price or {}).get("cross_check") or {}
    if xc.get("status") == "checked" and xc["diff_pct"] > cfg.max_source_divergence_pct:
        a, b = xc["primary"], xc["reference"]
        keyed.append(("price_divergence",
                      f"Price sources disagree by {xc['diff_pct']:.2f}% (limit {cfg.max_source_divergence_pct:g}%): "
                      f"{a['provider']} {a['price']:,.2f} {a['quote']} vs {b['provider']} {b['price']:,.2f} {b['quote']}. "
                      "One of them may be wrong or delayed."))
    if not _ok(a4h) or a4h.get("bars", 0) < cfg.min_primary_bars:
        keyed.append(("insufficient_data", f"Primary 4h timeframe has too little data (need {cfg.min_primary_bars} closed candles)."))
    if trend.score is None or comps[1].score is None:
        keyed.append(("insufficient_data", "Trend or momentum could not be calculated."))
    # signal-1.8: 1h is checked too - it drives 40% of momentum and half of overextension.
    used_for = {"1h": "momentum and overextension", "4h": "every component", "1d": "the daily trend"}
    for tf in ("1h", "4h", "1d"):
        m = data_meta.get(tf) or {}
        if m.get("is_stale"):
            keyed.append(("stale_candles", f"{tf} candles are stale (the latest closed candle is more than two "
                                           f"intervals old); {used_for[tf]} would use old data."))
        if (m.get("gaps") or 0) > cfg.max_gaps:
            keyed.append(("gaps", f"{tf} series has {m['gaps']} missing candles (more than {cfg.max_gaps}); "
                                  f"{used_for[tf]} would be computed over holes in the data."))
    extreme_vol = False
    for a, tf in ((a4h, "4h"), (a1h, "1h")):
        if (a.get("volatility") or {}).get("regime") == "extreme":
            extreme_vol = True
            keyed.append(("extreme_volatility", f"{tf} volatility is extreme (ATR in the top 5% of its recent range)."))
    contradictory = bool(abs(score) >= cfg.buy_threshold and dir_w and agreement < cfg.min_agreement)
    if contradictory:
        keyed.append(("contradictory", f"Evidence is contradictory: only {agreement:.0%} of weighted evidence agrees with the score."))
    raw_counter_trend = bool(raw_setup in (STRONG_BUY, BUY) and daily_trend is not None and daily_trend < -cfg.counter_trend_limit)
    if setup in (STRONG_BUY, BUY) and raw_counter_trend:
        keyed.append(("counter_trend", f"Buy setup fights the daily downtrend (1d trend {daily_trend:+.2f})."))
    if setup == SELL and daily_trend is not None and daily_trend > cfg.counter_trend_limit:
        keyed.append(("counter_trend", f"Sell setup fights the daily uptrend (1d trend {daily_trend:+.2f})."))
    against = None
    if setup in (STRONG_BUY, BUY):
        against = "bearish"
    elif setup == SELL:
        against = "bullish"
    news_block, news_warn = news_blocks(news_events, against)
    if news_block:
        keyed.append(("fresh_news", news_block))
    # A validation only lifts the gate after the forward-observation period (FORWARD_OBSERVATION_DAYS).
    validated = bool(validation and validation.get("validated")) and not in_forward_observation(validation, now_ms)
    if cfg.require_validated_edge and not validated:
        keyed.append(("not_validated", validation_reason(validation, now_ms)))
    gates = [t for _, t in keyed]

    final = NO_TRADE if gates else setup

    # ---- evidence, invalidation, what would change it
    weighted = [c for c in comps if c.name in eff_w]
    # Evidence is grouped by its own direction, including facts inside components that net to zero.
    bullish_ev = [f"{c.name}: {t}" for c in weighted for t, pol in c.facts if pol > 0]
    bearish_ev = [f"{c.name}: {t}" for c in weighted for t, pol in c.facts if pol < 0]
    sign = 1 if score >= 0 else -1
    support, opposing = (bullish_ev, bearish_ev) if sign > 0 else (bearish_ev, bullish_ev)
    neutral_parts = [c.name for c in weighted if abs(c.score) <= 0.05]

    res = (lv_live["resistance"] or [None])[0]   # nearest resistance of any strength
    sup = lv_live["major_support"]               # nearest support with 2+ touches
    invalidation, changes = [], []
    if setup in (STRONG_BUY, BUY) and plan:
        invalidation.append(f"4h close below the stop reference {plan['stop']:,.0f}.")
        invalidation.append("Daily structure turning into lower highs and lower lows.")
        changes.append("A confirmed high-impact bearish news event (it blocks the signal; news is not in the score).")
    elif setup == SELL:
        if res:
            invalidation.append(f"4h close back above resistance {res['price']:,.0f}.")
        invalidation.append("4h momentum turning positive (RSI back above 50 and MACD above signal).")
    else:
        if res:
            changes.append(f"A volume-confirmed 4h close above resistance {res['price']:,.0f} would strengthen the bull case.")
        if sup:
            changes.append(f"A 4h close below support {sup['price']:,.0f} would strengthen the bear case.")
    if gates and setup != HOLD:
        changes.append("The setup becomes actionable only after every NO TRADE condition below clears.")
    gap_to_buy = cfg.buy_threshold - score
    if setup == HOLD and 0 < gap_to_buy < 0.15:
        changes.append(f"Score is {gap_to_buy:.2f} below the BUY threshold.")

    atr4 = _ind(a4h, "atr_pct") if _ok(a4h) else None
    atr1d = _ind(a1d, "atr_pct") if _ok(a1d) else None
    risk = {
        "atr_pct_4h": atr4, "atr_pct_1d": atr1d,
        "volatility_4h": (a4h.get("volatility") or {}).get("regime"),
        "volatility_1h": (a1h.get("volatility") or {}).get("regime"),
        "volatility_1d": (a1d.get("volatility") or {}).get("regime"),
        "volatility_pct_4h": (a4h.get("volatility") or {}).get("percentile"),
        "volatility_pct_1h": (a1h.get("volatility") or {}).get("percentile"),
        "volatility_pct_1d": (a1d.get("volatility") or {}).get("percentile"),
        "volatility_note": "Regime = where current ATR% sits within its own recent history on that timeframe "
                           "(low <20th pct, normal <70th, high <95th, extreme >=95th); timeframes can differ.",
        "typical_daily_move_pct": atr1d,
        "downside_to_stop_pct": plan["stop_pct"] if plan else None,
        "downside_to_support_pct": round((sup["price"] / price_now - 1) * 100, 2) if sup and price_now else None,
        "room_to_resistance_pct": round((res.get("low", res["price"]) / price_now - 1) * 100, 2) if res and price_now else None,
        "room_to_resistance_atr": round((res.get("low", res["price"]) - price_now) / _ind(a4h, "atr14"), 2)
                                  if res and price_now and _ok(a4h) and _ind(a4h, "atr14") else None,
        "nearest_resistance": {k: res.get(k) for k in ("low", "high", "touches", "minor")} if res else None,
    }
    warnings = []
    for tf, m in data_meta.items():
        warnings += [f"{tf}: {w}" for w in (m.get("warnings") or [])]
    if price:
        warnings += price.get("warnings") or []
    if xc.get("status") == "unavailable":
        warnings.insert(0, f"Price not cross-checked: {xc.get('reason')}. This does not block the signal.")
    warnings[:0] = news_warn        # older speculative news against the setup: shown, never blocking

    return {
        "signal": final,
        "setup": setup,
        "actionable": final not in (NO_TRADE, HOLD),
        "score": round(score, 3),
        "agreement": round(agreement, 3),
        "directional_weight": round(directional_weight, 3),
        "price_basis": {"header": "latest live price",
                        "indicators": "last CLOSED candle of each timeframe (in-progress candles are never used)",
                        "close_4h": a4h.get("price"), "close_1d": a1d.get("price"), "close_1h": a1h.get("price")},
        "direction": "bullish" if score > 0.05 else "bearish" if score < -0.05 else "neutral",
        "strength": "strong" if abs(score) >= cfg.strong_threshold else "moderate" if abs(score) >= cfg.buy_threshold else "weak",
        "generated_at": now_ms,
        "horizon": cfg.horizon,
        "price": {"value": price_now, "provider": (price or {}).get("provider"), "quote": (price or {}).get("quote"),
                  "observed_at": (price or {}).get("observed_at"), "status": (price or {}).get("status"),
                  "cross_check": xc or None},
        "components": [c.as_dict(round(eff_w.get(c.name, 0.0), 4)) for c in comps],
        "evidence_bullish": bullish_ev[:10],
        "evidence_bearish": bearish_ev[:10],
        "evidence_for": support[:10],
        "evidence_against": opposing[:10],
        "neutral_components": neutral_parts,
        "no_trade_reasons": gates,
        "diagnostics": {"raw_setup": raw_setup, "downgrade": downgrade,
                        "rr_t1": (plan or {}).get("rr_t1"), "t1_distance_atr": (plan or {}).get("t1_distance_atr"),
                        "gate_keys": sorted({k for k, _ in keyed}), "raw_counter_trend": raw_counter_trend,
                        "contradictory": contradictory, "extreme_volatility": extreme_vol,
                        "daily_trend": None if daily_trend is None else round(daily_trend, 3),
                        "score_raw": score, "entry_mode": cfg.entry_mode,
                        "component_scores": {c.name: c.score for c in comps},   # unrounded (research / score study)
                        "pending_order": pending_order, "pending_rejected": pending_rejected},
        "setup_notes": setup_notes,
        "plan": plan,
        "pending_order": pending_order,
        "plan_status": None if not plan else ("actionable" if final in (STRONG_BUY, BUY) else "research only - not actionable"),
        "key_levels": {"resistance": lv_live["resistance"][:4], "support": lv_live["support"][:4],
                       "current_zone": lv_live["zone"], "basis": lv_live["basis"],
                       "note": "Sorted against the live price. 'minor' = one touch; stops and the bear trigger use 2+ touch levels."},
        "invalidation": invalidation,
        "would_change": changes,
        "risk": risk,
        "news": {"bias_label": (news_summary or {}).get("bias_label"), "bias_score": (news_summary or {}).get("bias_score"),
                 "top_events": [{"title": e["title"], "link": e["link"], "publisher": e["publisher"],
                                 "sentiment": e["sentiment"], "impact": e["impact"], "freshness": e["freshness"]}
                                for e in (news_events or [])
                                if e.get("sentiment") != "neutral" and e.get("freshness") not in ("too_old", "superseded")][:5]},
        "validation": validation or {"validated": False, "note": "No walk-forward validation yet (Phase 6)."},
        "confidence": "Not calibrated. The score counts weighted evidence; it is not a probability of profit.",
        "data_warnings": warnings[:12],
        "config": {**asdict(cfg), "rationale": WEIGHT_RATIONALE},
        "engine": ENGINE_VERSION,
    }
