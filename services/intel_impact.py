"""Global Intelligence: rule-based impact assessment. Context, not part of the signal. No AI.

An event's TYPE never gives a direction: there is no "war = bearish" or "rate cut = bullish" here. Only two things can:
  1. what this category's past reactions showed (the reaction study), when the sample allows it
  2. what the reports say BTC itself just did, short term only and weakly
Without either the label is Uncertain, whatever the headline. The other factors only scale the size of the score.

The score runs from -100 to +100. It is a heuristic and NOT a probability. Whether the labels are any good is what the accuracy
tracker measures (PREREGISTRATION_NEWS_IMPACT.md). A rule change must raise RULES_VERSION, and results are never pooled across versions.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from database import Database, now_ms
from services.intel_events import article_set_hash
from services.intel_reactions import EXCLUDED_FLAGS, NORMAL_LOOKBACK_H, Candles, ReactionService, align_base

LABEL = "Context, not part of the signal"
HOUR = 3_600_000
RULES_VERSION = 2                  # 2: see Amendment 1 in PREREGISTRATION_NEWS_IMPACT.md
LABELS = ("Bullish", "Bearish", "Neutral", "Mixed", "Uncertain")
# Impact horizon -> the study's row. The study's longest is +30d, the closest it has to 1-3 months.
IMPACT_HORIZONS = {"0-24h": "24h", "1-7d": "7d", "1-3m": "30d"}
HORIZON_LABELS = {"0-24h": "0-24h", "1-7d": "1-7d", "1-3m": "1-3 months"}
WORDING_WEIGHT = {"0-24h": 0.5, "1-7d": 0.25, "1-3m": 0.0}
SMALL_SAMPLE_WEIGHT = 0.5
NEUTRAL_BAND = 15
CONFLICT_MIN = 0.2
ASSESS_WINDOW_MS = 168 * HOUR
ELIGIBLE_WITHIN_MS = 24 * HOUR
SCORE_NOTE = "The score is a heuristic for how much evidence points one way. It is not a probability."
VERIFICATION = {"official_statement": 1.0, "confirmed": 0.8, "developing": 0.5, "unverified": 0.3, "speculative": 0.2, "disputed": 0.2}
MAJOR_PUBLISHERS = {"bbc news", "reuters", "associated press", "ap news", "bloomberg", "financial times", "the wall street journal", "wall street journal",
                    "the new york times", "cnbc", "the economist", "the guardian"}
CRYPTO_PUBLISHERS = {"coindesk", "cointelegraph", "the block", "decrypt", "bitcoin magazine"}
RELEVANCE = {"crypto_industry": 0.8, "crypto_incident": 0.8, "regulation": 0.7, "macro": 0.6, "central_banks": 0.6, "energy_commodities": 0.5,
             "war_geopolitics": 0.4, "elections_politics": 0.4, "other": 0.2}
_SUBJECT_RE = re.compile(r"\b(bitcoin|btc|cryptocurrenc(?:y|ies)|crypto market|crypto markets)\b", re.I)
# The direction word must directly follow the name: "Bitcoin drops" counts; "didn't crash", "crash bitcoin nodes" and "Bitcoin ETFs sink" do not.
_NAMED = r"\b(?:bitcoin|btc|cryptocurrenc(?:y|ies)|crypto markets?)(?:['\u2019]s)?(?:\s+(?:price|prices|briefly|sharply|again|also|just|now|further|quickly))*\s+"
_UP_RE = re.compile(_NAMED + r"(?:surge[sd]?|soar(?:s|ed)?|jump(?:s|ed)?|rall(?:y|ies|ied)|rebound(?:s|ed)?|recover(?:s|ed)?|climb(?:s|ed)?|gain(?:s|ed)?|rises|rose|hits? (?:a )?(?:record|new) high)\b", re.I)
_DOWN_RE = re.compile(_NAMED + r"(?:falls|fell|slides?|slid|plunge[sd]?|tumble[sd]?|drop(?:s|ped)?|sinks?|sank|crash(?:es|ed)|sell-?off|slump(?:s|ed)?)\b", re.I)
_SEVERE_RE = re.compile(r"\b(war|invasion|attacks?|strikes?|sanctions?|defaults?|bankrupt\w*|collapse[sd]?|hack(?:s|ed)?|halt(?:s|ed)?|emergency|ban(?:s|ned)?|crisis|resign\w*)\b", re.I)
_LARGE_RE = re.compile(r"\b\d[\d,.]*\s*(billion|bn|trillion)\b|\b[1-9]\d{2,}(?:[.,]\d+)?\s*million\b", re.I)

_READINGS = {
    "war_geopolitics": ("Investors may cut risky assets, BTC among them, while the conflict is unresolved.",
                        "Some buyers may treat BTC as an asset outside the affected financial systems."),
    "elections_politics": ("A result seen as hostile to crypto or to risk assets could weigh on BTC.",
                           "A result seen as friendly to crypto or to growth could support BTC."),
    "macro": ("Data that points to tighter policy or a weaker economy could weigh on BTC.",
              "Data that points to easier policy could support BTC."),
    "central_banks": ("A decision or tone tighter than the market expected could weigh on BTC.",
                      "A decision or tone easier than the market expected could support BTC, though a cut can also signal economic stress."),
    "energy_commodities": ("Higher energy costs could feed inflation fears and pressure risk assets.",
                           "Commodity stress could push some buyers toward assets seen as stores of value."),
    "regulation": ("Tighter rules or enforcement could reduce demand or access.",
                   "Clearer rules could bring in institutions that were waiting for them."),
    "crypto_industry": ("Bad news for a large firm could spread through the sector.",
                        "Growing adoption or new products could lift demand."),
    "crypto_incident": ("Losses or a loss of trust could lead holders to sell.",
                        "A contained incident, or funds recovered, could matter little, and past incidents have sometimes faded quickly."),
    "other": ("This could weigh on sentiment if it spreads to wider markets.",
              "This could help sentiment if it spreads to wider markets."),
}
_NO_LINK = "It may also have no lasting link to BTC: other news is always moving the price at the same time."


def interpretations(category: str) -> list[dict]:
    """Competing readings, never a verdict. Every category has one each way."""
    weigh, support = _READINGS.get(category, _READINGS["other"])
    return [{"stance": "could_weigh", "text": weigh}, {"stance": "could_support", "text": support}, {"stance": "no_clear_link", "text": _NO_LINK}]


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _text(a: dict) -> str:
    return f"{a.get('title') or ''} {a.get('summary') or ''}"


def _severity(articles: list[dict]) -> tuple[float, str]:
    text = " ".join(_text(a) for a in articles)
    words, large = bool(_SEVERE_RE.search(text)), bool(_LARGE_RE.search(text))
    value = 0.2 + (0.4 if words else 0.0) + (0.4 if large else 0.0)
    return value, ("Serious wording and large amounts in the reports." if words and large else "Serious wording in the reports." if words
                   else "Large amounts in the reports." if large else "No serious wording or large amounts in the reports.")


def _credibility(articles: list[dict]) -> tuple[float, str]:
    best, name = 0.4, "publishers the app has no rating for"
    for a in articles:
        p = (a.get("publisher") or "").strip().lower()
        if a.get("source_type") == "official":
            best, name = 1.0, "an official source"
            break
        if p in MAJOR_PUBLISHERS and best < 0.8:
            best, name = 0.8, "a major news publisher"
        elif p in CRYPTO_PUBLISHERS and best < 0.6:
            best, name = 0.6, "a crypto trade publisher"
    return best, f"Best source: {name}."


def _novelty(event_ms: int | None, now: int) -> tuple[float, str]:
    if event_ms is None:
        return 0.2, "No publication time."
    age = (now - event_ms) / HOUR
    value = 1.0 if age < 2 else 0.7 if age < 24 else 0.4 if age < 72 else 0.2
    return value, f"First published {max(0.0, age):.0f} h before this assessment."


def _relevance(category: str, articles: list[dict]) -> tuple[float, str]:
    if any(_SUBJECT_RE.search(_text(a)) for a in articles):
        return 1.0, "The reports name Bitcoin or crypto markets."
    return RELEVANCE.get(category, 0.2), "From the category; the reports do not name Bitcoin."


def _wording(articles: list[dict]) -> tuple[int, int]:
    """Reports that say BTC itself went up or down. A headline about oil falling is not this."""
    up = down = 0
    for a in articles:
        title = a.get("title") or ""
        up += bool(_UP_RE.search(title))
        down += bool(_DOWN_RE.search(title))
    return up, down


def _history_signal(stat: dict | None) -> tuple[bool, float]:
    """(usable, signed strength). An interval that includes zero is evidence of no clear direction."""
    if not stat or stat.get("sample_flag") == "too_few" or not stat.get("n") or not stat.get("normal_abs_pct"):
        return False, 0.0
    ci = stat.get("ci95")
    if not ci or ci[0] <= 0 <= ci[1]:
        return True, 0.0
    signal = _clamp(stat["median_pct"] / stat["normal_abs_pct"])
    return True, signal * (SMALL_SAMPLE_WEIGHT if stat["sample_flag"] == "small" else 1.0)


def assess(event: dict, articles: list[dict], history: dict[str, dict | None], priced: dict, now: int) -> dict:
    """Pure: the same inputs always give the same assessment. `history` is the study's row per impact horizon."""
    severity, severity_note = _severity(articles)
    credibility, credibility_note = _credibility(articles)
    verification = VERIFICATION.get(event["status"], 0.3)
    novelty, novelty_note = _novelty(event.get("event_time_at"), now)
    relevance, relevance_note = _relevance(event["category"], articles)
    priced_value = _clamp(priced.get("priced") or 0.0, 0.0, 1.0)
    strength = (severity + credibility + verification + novelty + relevance) / 5
    up, down = _wording(articles)
    wording = (up - down) / (up + down) if up + down else None
    wording_conflict = bool(up and down and max(up, down) / min(up, down) <= 2)
    horizons = {}
    for h, study_h in IMPACT_HORIZONS.items():
        usable, hist = _history_signal(history.get(h))
        word = (wording or 0.0) * WORDING_WEIGHT[h]
        has_words = wording is not None and WORDING_WEIGHT[h] > 0
        score = int(round(100 * _clamp(word + hist) * strength * (1 - 0.5 * priced_value))) + 0
        if event.get("event_time_at") is None:
            label, score, why = "Uncertain", 0, "No publication time."
        elif event["status"] in ("unverified", "speculative"):
            label, score, why = "Uncertain", 0, f"The reports are {event['status']}, so no direction is given."
        elif event["status"] == "disputed":
            label, score, why = "Mixed", 0, "One report denies the others."
        elif not usable and not has_words:
            label, score, why = "Uncertain", 0, "No evidence of direction: too few past reactions, and no report of BTC moving."
        elif (has_words and wording_conflict) or (has_words and word * hist < 0 and abs(word) >= CONFLICT_MIN and abs(hist) >= CONFLICT_MIN):
            label, why = "Mixed", "The evidence points both ways."
        elif abs(score) < NEUTRAL_BAND:
            label, why = "Neutral", "The evidence shows no clear direction."
        else:
            label, why = ("Bullish" if score > 0 else "Bearish"), f"{'Past reactions' if usable and hist else 'Reports of BTC moving'} point this way."
        horizons[h] = {"label": label, "score": score, "why": why, "label_text": HORIZON_LABELS[h], "study_horizon": study_h}
    by_h = {h: None if not s else {k: s.get(k) for k in ("n", "episodes", "median_pct", "ci95", "normal_abs_pct", "sample_flag", "sample_note")}
            for h, s in ((h, history.get(h)) for h in IMPACT_HORIZONS)}
    move, normal = priced.get("move_pct"), priced.get("normal_abs_pct")
    factors = [
        {"key": "severity", "label": "Severity", "value": round(severity, 2), "note": severity_note},
        {"key": "source_credibility", "label": "Source credibility", "value": round(credibility, 2), "note": credibility_note},
        {"key": "verification", "label": "Verification status", "value": round(verification, 2), "note": f"Status: {event['status'].replace('_', ' ')}. From the wording and sources, not from checking the facts."},
        {"key": "novelty", "label": "Novelty", "value": round(novelty, 2), "note": novelty_note},
        {"key": "market_relevance", "label": "Market relevance", "value": round(relevance, 2), "note": relevance_note},
        {"key": "already_priced_in", "label": "Already priced in", "value": round(priced_value, 2), "move_pct": move, "normal_abs_pct": normal,
         "note": "No closed candles to compare yet." if move is None else f"BTC moved {move:+.2f}% since the event, against a normal {normal:.2f}%." if normal else f"BTC moved {move:+.2f}% since the event; no normal move to compare it with."},
        {"key": "historical_reaction", "label": "Historical reaction", "value": None, "by_horizon": by_h,
         "note": "This category's past reactions from the study. Too few means it does not count. The 1-3 months row uses the study's +30d, its longest."},
    ]
    return {"horizons": horizons, "factors": factors, "score_note": SCORE_NOTE, "interpretations": interpretations(event["category"]), "wording": {"reports_up": up, "reports_down": down},
            "strength": round(strength, 2), "rules_version": RULES_VERSION}


class ImpactService:
    def __init__(self, db: Database, reactions: ReactionService, clock: Callable[[], int] = now_ms):
        self.db = db
        self.reactions = reactions
        self.clock = clock

    @staticmethod
    def _priced(candles: Candles | None, event_ms: int | None) -> dict:
        """BTC's move from the last candle closed before the event to the last closed now, against a normal move of that length."""
        none = {"move_pct": None, "normal_abs_pct": None, "priced": 0.0}
        if candles is None or event_ms is None:
            return none
        i, last = candles.index(align_base(event_ms)), candles.last_index()
        if i is None or last is None or last <= i:
            return none
        move = (float(candles.close[last]) / float(candles.close[i]) - 1) * 100
        normal = candles.normal(i, last - i)[0]
        return {"move_pct": move, "normal_abs_pct": normal, "priced": min(1.0, abs(move) / (2 * normal)) if normal else 0.0}

    def update(self) -> dict:
        """Assess each recent event whose article set has no assessment yet, and store it as it is. Existing rows are never changed."""
        now = self.clock()
        pending = []
        for e in self.db.intel_events(now - ASSESS_WINDOW_MS, limit=5000):
            articles = self.db.intel_event_articles(e["id"])
            digest = article_set_hash([a["id"] for a in articles])
            if not self.db.intel_assessment_exists(digest, e["event_time_at"], RULES_VERSION):
                pending.append((e, articles, digest))
        if not pending:
            return {"assessed": 0}
        study = self.reactions.study()
        windows = self.reactions.windows()
        by_category = {c["category"]: c for c in study["categories"]}
        start = min(align_base(e["event_time_at"] or now) for e, _, _ in pending) - (NORMAL_LOOKBACK_H + 48) * HOUR
        candles = Candles(self.db, start, now)
        added = 0
        for e, articles, digest in pending:
            ms = e["event_time_at"]
            bucket = windows.bucket(ms) if ms is not None else "unknown"
            marked = ms is None or bucket != "clean" or windows.bucket(now) != "clean"
            group = (by_category.get(e["category"]) or {}).get("macro_window" if bucket == "macro_window" else "clean") or {"horizons": {}}
            history = {h: group["horizons"].get(study_h) for h, study_h in IMPACT_HORIZONS.items()}
            payload = assess(e, articles, history, self._priced(candles, ms), now)
            eligible = (ms is not None and e["time_flag"] not in EXCLUDED_FLAGS and 0 <= now - ms <= ELIGIBLE_WITHIN_MS)
            added += self.db.intel_add_assessment({
                "article_set_hash": digest, "article_ids": sorted(a["id"] for a in articles), "anchor_article_id": e["anchor_article_id"], "event_time_at": ms,
                "category": e["category"], "status": e["status"], "rules_version": RULES_VERSION, "assessed_at": now, "eligible": int(eligible),
                "macro_window": int(marked), "time_flag": e["time_flag"], "labels": {h: payload["horizons"][h]["label"] for h in IMPACT_HORIZONS}, "payload": payload}) is not None
        return {"assessed": added}

    def event_impact(self, event_id: int) -> dict | None:
        e = self.db.intel_event(event_id)
        if e is None:
            return None
        digest = article_set_hash([a["id"] for a in self.db.intel_event_articles(event_id)])
        row = self.db.intel_assessment_for(digest, e["event_time_at"], RULES_VERSION)
        out = {"id": event_id, "label": LABEL, "article_set_hash": digest, "rules_version": RULES_VERSION, "assessment": None, "assessed_at": None, "eligible": None, "macro_window": None,
               "note": "Not assessed yet. Assessments run in the background, and new articles change what is being assessed."}
        if row:
            out.update(assessment=json.loads(row["payload_json"]), assessed_at=row["assessed_at"], eligible=bool(row["eligible"]), macro_window=bool(row["macro_window"]),
                       note="Stored at the time shown and never edited. This event type alone never gives a direction.")
        return out
