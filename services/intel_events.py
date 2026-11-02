"""Global Intelligence: rule-based event grouping (pure functions, no I/O). Context, not part of the signal.

Normalises headlines, clusters articles about the same event into ONE event, and classifies each event: category,
country, status and freshness. Runs beside the news engine and shares nothing with gate 5.

Status comes from the wording and the kind of sources, not from a judgement of the truth:
  official_statement  a government or central-bank source published it
  disputed            a report denies or disputes the others
  speculative         every report is a forecast ("could", "analyst predicts")
  unverified          every report is hedged ("reportedly", "rumor", "sources say")
  confirmed           at least two publishers report it firmly, in their own words (wire copies count once)
  developing          otherwise (a single publisher so far)
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable

CATEGORIES = ("war_geopolitics", "elections_politics", "macro", "central_banks", "energy_commodities", "regulation",
              "crypto_industry", "crypto_incident", "other")
CATEGORY_LABELS = {"war_geopolitics": "War / geopolitics", "elections_politics": "Elections / politics", "macro": "Macro",
                   "central_banks": "Central banks", "energy_commodities": "Energy / commodities", "regulation": "Regulation",
                   "crypto_industry": "Crypto industry", "crypto_incident": "Crypto incident", "other": "Other"}
STATUSES = ("official_statement", "confirmed", "developing", "unverified", "speculative", "disputed")
FILTER_GROUPS = {"war": ("war_geopolitics",), "elections": ("elections_politics",),
                 "macro": ("macro", "central_banks", "energy_commodities"),
                 "crypto": ("crypto_industry", "crypto_incident", "regulation")}

HOUR = 3_600_000
NEW_HOURS, RECENT_HOURS, EARLIER_HOURS = 2, 24, 72
CLUSTER_WINDOW_MS = 72 * HOUR       # an event can grow while its articles are within this window of each other
FIRM_LINK_WINDOW_MS = 24 * HOUR     # the looser "shared name + subject" link only applies this close in time
ANCHORS = 5                         # a new article is compared with an event's first articles only, so it cannot drift
COUNTRY_MIN_SHARE = 0.3             # without a country in the lead headline, this share of the headlines must name one

_SUFFIX_RE = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,40}$")
_WORD_RE = re.compile(r"[a-z0-9$%][a-z0-9$%.']*")
STOPWORDS = set("""a an the and or but of to in on for at by with from as is are was were be been being it its this that these
those has have had will would can could may might should shall do does did not no new after before over under into about than
then there their they he she his her we you your our i who what when where why how which while amid says say said saying
report reports reported today now latest update live more most very just also up down out off per via vs what's here""".split())
SYNONYMS = {"federal": "fed", "reserve": "fed", "fomc": "fed", "interest": "", "bn": "billion", "bln": "billion", "mln": "million",
            "hackers": "hack", "hacker": "hack", "hacked": "hack", "hacking": "hack", "steal": "stolen", "stole": "stolen",
            "theft": "stolen", "etfs": "etf", "withdrawals": "withdrawal", "withdraw": "withdrawal", "launched": "launch",
            "launches": "launch", "strikes": "strike", "struck": "strike", "holds": "hold", "held": "hold", "rates": "rate",
            "outflows": "outflow", "inflows": "inflow", "attacks": "attack", "cuts": "cut", "rises": "rise", "rose": "rise",
            "falls": "fall", "fell": "fall", "users": "user", "investors": "investor"}
# Names used to keep different events apart ("Fed" vs "ECB") and to link reports of one event ("Binance" + "hack").
ENTITIES = set("""fed ecb boe boj pboc opec nato un imf sec cftc treasury us usa uk eu china russia ukraine iran iraq israel gaza
hamas hezbollah lebanon syria yemen india pakistan japan korea taiwan germany france italy spain canada mexico brazil turkey
saudi venezuela kyiv moscow beijing tehran washington london tokyo trump biden putin xi powell bessent binance coinbase kraken
okx bybit bitfinex gemini tether circle blackrock microstrategy bitcoin btc ethereum eth solana xrp ripple dogecoin
gold brent wti oil etf""".split())
# Rare names: one of these plus one shared subject is enough. Everyone else (the Fed, Treasury, a country) is in the news
# all the time for unrelated reasons, so a broad name needs two shared subjects.
SPECIFIC_NAMES = set("""binance coinbase kraken okx bybit bitfinex gemini tether circle blackrock microstrategy ripple etf""".split())
# Words too common across crypto news to link two reports just because they share one name and this word.
WEAK_SUBJECT = set("""bitcoin btc crypto price prices market markets rise fall token tokens list lists new week says""".split())


def normalize_title(title: str) -> str:
    """Lower-case words without punctuation or a trailing " - Publisher"."""
    t = _SUFFIX_RE.sub("", (title or "").strip())
    return " ".join(_WORD_RE.findall(t.lower().replace("’", "'"))).replace("'s", "").strip(". ")


def title_tokens(title: str) -> frozenset[str]:
    out = set()
    for w in normalize_title(title).split():
        w = w.strip(".'")
        w = SYNONYMS.get(w, w)
        if w and w not in STOPWORDS and len(w) > 1:
            out.add(w)
    return frozenset(out)


def _similar_tokens(a: frozenset[str], b: frozenset[str], close_in_time: bool = True, strict: bool = False) -> bool:
    if not a or not b:
        return False
    inter = a & b
    ea, eb = a & ENTITIES, b & ENTITIES
    if (ea - eb) and (eb - ea):             # each side names someone the other doesn't: Fed vs ECB, Iran vs Iraq
        return False
    if not inter:
        return False
    if len(inter) / len(a | b) >= 0.5:
        return True
    if len(inter) >= 4 and len(inter) / min(len(a), len(b)) >= 0.6:
        return True
    if strict or not close_in_time:
        return False
    # A shared name plus a shared subject (Binance + hack), only for reports close together.
    subject = inter - ENTITIES - WEAK_SUBJECT
    if inter & SPECIFIC_NAMES:
        return bool(subject)
    return bool((inter & ENTITIES) - WEAK_SUBJECT) and len(subject) >= 2


def similar(a_title: str, b_title: str) -> bool:
    return _similar_tokens(title_tokens(a_title), title_tokens(b_title))


# ------------------------------------------------------------------------- clustering
def _when(a: dict) -> int:
    return a.get("published_at") or a.get("collected_at") or 0


def cluster(articles: Iterable[dict], existing: list[dict]) -> list[dict]:
    """Attach each article to the best matching event, or start a new one. Oldest first, so the result does not depend on
    input order. Compared with an event's first articles only (no drift along a chain of similar headlines), and an official
    release joins only a nearly identical report, so a press story never turns into an "official statement".
    `existing`: [{"id", "articles"}]. Returns the events that gained articles: {"id" (None if new), "articles" (all),
    "new" (the added ones)}."""
    events = [{"id": e["id"], "articles": list(e["articles"]), "new": [],
               "tokens": [title_tokens(a["title"]) for a in e["articles"]]} for e in existing]
    for art in sorted(articles, key=lambda a: (_when(a), a["id"])):
        toks, when = title_tokens(art["title"]), _when(art)
        official = art.get("source_type") == "official"
        best, best_score = None, 0.0
        for ev in events:
            if not any(abs(when - _when(x)) <= CLUSTER_WINDOW_MS for x in ev["articles"]):
                continue
            strict = official or any(x.get("source_type") == "official" for x in ev["articles"])
            for other, t in zip(ev["articles"][:ANCHORS], ev["tokens"][:ANCHORS]):
                if abs(when - _when(other)) > CLUSTER_WINDOW_MS:
                    continue
                if _similar_tokens(toks, t, abs(when - _when(other)) <= FIRM_LINK_WINDOW_MS, strict):
                    score = len(toks & t) / max(1, len(toks | t))
                    if score > best_score:
                        best, best_score = ev, score
        if best is None:
            best = {"id": None, "articles": [], "new": [], "tokens": []}
            events.append(best)
        best["articles"].append(art)
        best["new"].append(art)
        best["tokens"].append(toks)
    return [{"id": e["id"], "articles": e["articles"], "new": e["new"]} for e in events if e["new"]]


# ------------------------------------------------------------------------- category
def _rx(*terms: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(terms) + r")(?![a-z0-9])", re.I)


# Checked in this order: the more specific kind wins (a hacked exchange is an incident, not industry news).
CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("crypto_incident", _rx(r"hack(?:ed|s|ers?|ing)?", r"exploit(?:ed|s)?", r"drained", r"rug ?pull", r"stolen", r"thefts?", r"phishing",
                            r"(?:halts?|suspends?) withdrawals?", r"insolven\w+", r"depeg\w*")),
    ("regulation", _rx(r"sec", r"cftc", r"securities and exchange", r"regulat\w+", r"enforcement",
                       r"unregistered", r"licen[cs]\w+", r"crypto ban", r"bans? crypto", r"mica", r"genius act", r"clarity act")),
    ("central_banks", _rx(r"fed", r"federal reserve", r"fomc", r"powell", r"ecb", r"bank of england", r"boe", r"boj", r"bank of japan",
                          r"pboc", r"central banks?", r"rate (?:cut|hike|decision)s?", r"(?:cuts?|raises?|hikes?|holds?) (?:interest )?rates?",
                          r"interest rates?", r"monetary policy", r"quantitative (?:easing|tightening)")),
    ("war_geopolitics", _rx(r"wars?", r"missiles?", r"invasions?", r"invades?", r"airstrikes?", r"air strikes?", r"military", r"troops",
                            r"ceasefire", r"nato", r"sanctions?", r"hamas", r"hezbollah", r"houthis?", r"drone (?:attack|strike)s?",
                            r"(?:missile|nuclear|border) (?:strike|test|clash)\w*", r"geopolitic\w*", r"bomb\w*", r"shelling", r"kyiv")),
    ("energy_commodities", _rx(r"oil", r"brent", r"wti", r"crude", r"opec\+?", r"natural gas", r"gold", r"silver", r"copper",
                               r"commodit\w+", r"energy prices?")),
    ("macro", _rx(r"inflation", r"cpi", r"pce", r"ppi", r"gdp", r"recession", r"payrolls?", r"unemployment", r"jobs? report", r"nonfarm",
                  r"consumer prices?", r"tariffs?", r"treasury yields?", r"bond yields?", r"trade deficit", r"economic growth", r"retail sales")),
    ("elections_politics", _rx(r"elections?", r"midterms?", r"voters?", r"polls?", r"ballots?", r"presidential", r"parliament\w*",
                               r"congress", r"senate", r"prime minister", r"referendum", r"(?:election|presidential) campaign",
                               r"campaign (?:trail|push|rally)", r"democrats?", r"republicans?")),
    ("crypto_industry", _rx(r"bitcoin", r"btc", r"ethereum", r"ether", r"crypto\w*", r"blockchain", r"stablecoins?", r"defi", r"etfs?",
                            r"solana", r"xrp", r"tether", r"binance", r"coinbase", r"okx", r"microstrategy", r"mstr", r"btcpay", r"tokeni[sz]ed",
                            r"miners?", r"halving", r"altcoins?")),
]


def classify_category(title: str, summary: str = "") -> str:
    for text in (title or "", summary or ""):       # the headline decides; the summary is only a fallback
        for category, rx in CATEGORY_RULES:
            if rx.search(text):
                return category
    return "other"


# ------------------------------------------------------------------------- country
COUNTRY_RULES: list[tuple[str, re.Pattern]] = [
    ("Russia", _rx(r"russian?", r"moscow", r"kremlin", r"putin")),
    ("Ukraine", _rx(r"ukrain\w+", r"kyiv", r"zelensk\w+")),
    ("China", _rx(r"china", r"chinese", r"beijing", r"pboc", r"xi jinping", r"shanghai")),
    ("Iran", _rx(r"iran\w*", r"tehran")),
    ("Israel", _rx(r"israel\w*", r"netanyahu", r"tel aviv")),
    ("Gaza / Palestinian territories", _rx(r"gaza", r"hamas", r"palestinian\w*", r"west bank")),
    ("Japan", _rx(r"japan\w*", r"tokyo", r"boj", r"bank of japan")),
    ("Euro area", _rx(r"ecb", r"euro ?zone", r"euro area", r"european central bank", r"eu")),
    ("United Kingdom", _rx(r"uk", r"britain", r"british", r"london", r"bank of england", r"boe")),
    ("India", _rx(r"india\w*", r"delhi", r"mumbai")),
    ("Pakistan", _rx(r"pakistan\w*", r"islamabad", r"karachi")),
    ("South Korea", _rx(r"south korea\w*", r"seoul")),
    ("North Korea", _rx(r"north korea\w*", r"pyongyang")),
    ("Taiwan", _rx(r"taiwan\w*", r"taipei")),
    ("Saudi Arabia", _rx(r"saudi\w*", r"riyadh")),
    ("Turkey", _rx(r"turkey", r"turkish", r"ankara")),
    ("Germany", _rx(r"german\w*", r"berlin")),
    ("France", _rx(r"france", r"french", r"paris")),
    ("Canada", _rx(r"canad\w+", r"ottawa")),
    ("Brazil", _rx(r"brazil\w*")),
    ("Mexico", _rx(r"mexic\w+")),
    ("Venezuela", _rx(r"venezuela\w*", r"caracas")),
    ("United States", _rx(r"u\.?s\.?a?", r"united states", r"america\w*", r"washington", r"white house", r"fed", r"federal reserve", r"fomc",
                          r"treasury", r"sec", r"cftc", r"congress", r"senate", r"michigan", r"trump", r"biden", r"powell", r"wall street", r"nonfarm", r"midterms?")),
]


def detect_country(text: str, default: str | None = None) -> str:
    """The first country named, most specific rule first. An official source passes its own country as `default`."""
    for country, rx in COUNTRY_RULES:
        if rx.search(text or ""):
            return country
    return default or "Global"


# ------------------------------------------------------------------------- status
_DENIAL = _rx(r"denie[sd]", r"refutes?", r"refuted", r"disputes?", r"disputed", r"rejects? (?:claims?|reports?)",
              r"false reports?", r"debunk\w*", r"no (?:hack|evidence)")
_RUMOUR = _rx(r"reportedly", r"rumou?rs?", r"rumou?red", r"unconfirmed", r"allegedly", r"sources? say", r"sources? said",
              r"according to sources", r"purported\w*", r"unverified", r"said to")
_FORECAST = _rx(r"could", r"might", r"(?<!in )(?<!of )may(?! \d)", r"predicts?", r"expected to", r"poised to", r"likely to",
                r"set to", r"will (?:hit|reach|soar|crash)", r"analysts? (?:say|see|expect)\w*", r"price target", r"what if")


def classify_status(articles: list[dict]) -> tuple[str, str]:
    officials = [a for a in articles if a.get("source_type") == "official"]
    publishers = {(a.get("publisher") or "").strip().lower() for a in articles}
    if officials:
        return "official_statement", f"Published by {officials[0].get('publisher')}."
    titles = [a.get("title") or "" for a in articles]
    denied = [t for t in titles if _DENIAL.search(t)]
    if denied and len(denied) < len(titles):
        return "disputed", f"One report says otherwise: \"{denied[0][:120]}\"."
    if all(_RUMOUR.search(t) for t in titles):
        return "unverified", "Every report is hedged (reportedly, rumour, sources say)."
    if all(_FORECAST.search(t) for t in titles):
        return "speculative", "Every report is a forecast or an opinion about what could happen."
    if len(publishers) >= 2:
        if len({normalize_title(t) for t in titles}) >= 2:
            return "confirmed", f"Reported by {len(publishers)} publishers in their own words."
        return "developing", f"{len(publishers)} publishers ran the same wording, which may be one wire story."
    return "developing", "One publisher so far."


# ------------------------------------------------------------------------- freshness + summary
def freshness(first_ms: int, now: int) -> str:
    """Age of the story, from its FIRST publication: a late article does not make an old story new."""
    h = (now - first_ms) / HOUR
    return "new" if h < NEW_HOURS else "recent" if h < RECENT_HOURS else "earlier" if h < EARLIER_HOURS else "old"


def _event_country(lead: dict, arts: list[dict], official: bool) -> str:
    """The lead headline decides. If it names none, enough of the other headlines must agree."""
    country = detect_country(lead["title"], "United States" if official else None)
    if country != "Global":
        return country
    named = [detect_country(a["title"]) for a in arts]
    named = [c for c in named if c != "Global"]
    if named:
        top = max(sorted(set(named)), key=named.count)
        if named.count(top) >= max(2, COUNTRY_MIN_SHARE * len(arts)):
            return top
    return "Global"


def article_set_hash(article_ids: list[str]) -> str:
    """Identifies a cluster by its articles, because event IDs change when the grouping is rebuilt."""
    return hashlib.sha256("\n".join(sorted(article_ids)).encode("utf-8")).hexdigest()


def summarize_event(articles: list[dict], now: int) -> dict:
    """The stored fields of an event, recomputed from all its articles."""
    arts = sorted(articles, key=lambda a: (_when(a), a["id"]))
    official = next((a for a in arts if a.get("source_type") == "official"), None)
    lead = official or arts[0]
    status, reason = classify_status(arts)
    first, last = _when(arts[0]), _when(arts[-1])
    timed = sorted((a for a in arts if a.get("published_at")), key=lambda a: (a["published_at"], a["id"]))
    seen = [a["collected_at"] for a in arts if a.get("collected_at")]
    event_time, first_seen = (timed[0]["published_at"] if timed else None), (min(seen) if seen else None)
    if not timed:
        time_flag = "publication_missing"
    elif first_seen is not None and event_time > first_seen:
        time_flag = "published_after_first_seen"
    else:
        time_flag = "publication_partial" if len(timed) < len(arts) else None
    return {
        "title": lead["title"],
        "category": classify_category(lead["title"], lead.get("summary") or ""),
        "country": _event_country(lead, arts, bool(official)),
        "status": status,
        "status_reason": reason,
        "first_published_at": first,
        "last_published_at": last,
        "date_known": all(a.get("published_at") for a in arts),
        "event_time_at": event_time,
        "first_seen_at": first_seen,
        "anchor_article_id": timed[0]["id"] if timed else None,
        "time_flag": time_flag,
        "articles_count": len(arts),
        "sources_count": len({(a.get("publisher") or "").strip().lower() for a in arts}),
        "freshness": freshness(first, now),
    }
