"""Rule-based news intelligence (works without any AI model).

Pipeline per request:
  1. analyse_item()   - relevance, categories, sentiment (headline only), verification markers
  2. cluster_events() - group the same story from different publishers into ONE event
  3. build_event()    - event sentiment, conflicts, impact, verification, freshness, explanation
  4. summarise()      - news bias across directional events (each event counted once)

Design rules (v2, after the first live review of 219 real headlines):
  * Sentiment is read from the HEADLINE only. Summaries often mention unrelated
    context ("...despite a hawkish Fed") and polluted the score.
  * Rules fire only in their context (a regulator approving, an exchange being
    hacked), not on any use of the word.
  * Every item gets a BTC-relevance factor. Altcoin-only or non-crypto stories are
    damped; off-topic aggregator items are dropped.
  * Negation either flips a rule (ruling out a rate cut is hawkish) or cancels it
    (a denied breach is not bullish).

All scores are heuristic evidence. The news bias is NOT a probability.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

ANALYZER_VERSION = "rules-2.2"
HOUR = 3_600_000


def _r(p: str) -> re.Pattern:
    return re.compile(p, re.I)


APOS = r"['’]"  # straight and curly apostrophes


# --------------------------------------------------------------------------- relevance
BTC_TERMS = _r(r"\b(?:bitcoin|btc|cryptocurrenc(?:y|ies)|crypto(?:s)?|digital assets?|spot etfs?|sats|satoshi|saylor|microstrategy|strategy inc|mstr|strategy \(mstr\)|miners?|hash ?rate|halving)\b")
CRYPTO_TERMS = _r(r"\b(?:crypto\w*|stablecoins?|blockchain|tokeni[sz](?:ed|ation)|tokens?|defi|web3|exchange|coinbase|binance|kraken|bybit|okx|bithumb|upbit|gemini|tether|usdt|usdc|circle|wallets?|ledger|trezor|on-?chain|nfts?|dao|prediction markets?|kalshi|polymarket|etfs?|etps?|cftc|sec|galaxy digital|galaxy|coinshares|grayscale|bitwise|ark invest|mstr|riot|marathon|mara|cleanspark|core scientific)\b")
ALTCOIN = _r(r"\b(?:xrp|ripple|rlusd|ethereum|ether|eth|solana|sol|dogecoin|doge|cardano|ada|bnb|tron|trx|avalanche|avax|polkadot|litecoin|ltc|shiba|memecoins?|pepe|toncoin|sui|aptos|chainlink|hyperliquid|monero|xmr|zcash|zec)\b")
FOREIGN_MACRO = _r(r"\b(?:rand|south africa\w*|u\.?k\.?|britain|british|bank of england|boe|euro ?zone|ecb|european central bank|japan\w*|boj|bank of japan|china|chinese|pboc|india\w*|rbi|canada|canadian|bank of canada|australia\w*|rba|brazil\w*|turk\w*|mexic\w*|swiss|snb|korea\w*|russia\w*|nigeria\w*|pakistan\w*|sbp)\b")
US_MARKER = _r(r"\b(?:us|u\.s\.|united states|american|fed|federal reserve|fomc|powell|warsh|treasury yields?|wall street(?! journal))\b")
MACRO_CATS = {"fed_rates", "inflation_macro", "dollar_yields", "geopolitics", "equities", "crisis"}
DIGEST = _r(r"\b(?:here{0}s what happened|what happened in crypto|daily (?:recap|digest|wrap|brief)|morning (?:minute|brief)|week in review|this week in|newsletter|roundup|recap|markets? wrap|the daily)\b".format(APOS))
RETROSPECTIVE = _r(r"\b(?:20\d\d|q[1-4]|h[12]|year[- ]to[- ]date|ytd|so far this year|annual|monthly|in review|report finds|study finds|losses dominate)\b")
MIN_TOKENS = 3


# --------------------------------------------------------------------------- sentiment rules
@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    weight: float            # + bullish for BTC, - bearish
    reason: str
    negation: str = "cancel"  # "flip": negated rule counts half in the opposite direction; "cancel": ignored
    skip_before: re.Pattern | None = None  # ignore a match preceded by this (e.g. advice "time to buy")


REGULATOR = r"(?:sec|cftc|occ|fdic|doj|justice department|regulators?|watchdog|court|judge|congress|senate|lawmakers|government|treasury|state|attorney general|prosecutors?)"
EXCHANGE_TARGET = r"(?:exchange|wallet|protocol|bridge|defi|dex|custodian|platform|coinbase|binance|kraken|bybit|okx|bithumb|upbit|gemini|crypto firm|crypto lender)"
PRICE_SUBJ = (r"\b(?:bitcoin|btc|crypto(?:currenc(?:y|ies))?|crypto market|crypto prices?)(?:{0}s)?"
              r"(?:\s+(?!(?:revenue|revenues|stocks?|shares?|earnings|chain|fund|funds|firm|company|exchange|tokens?|pac|lobby|"
              r"miners?|mining|treasury|reserve|etfs?|ad|ads|hash ?rate|hashrate|hashprice|difficulty|network|nodes?|"
              r"fees?|dominance|adoption|supply|holders?|developers?|conference|community|lobby|pac)\b)\w+){{0,2}}\s+").format(APOS)
UP_VERBS = r"(?:surges?|soars?|rall(?:y|ies)|jumps?|climbs?|rebounds?|rises?|gains?|tops|reclaims?|hits? (?:a )?record|record high|all-time high|breaks? above|spikes?)"
DOWN_VERBS = r"(?:plunges?|crash(?:es)?|tumbles?|slumps?|sinks?|drops?|falls?|slides?|slips?|dips?|retreats?|sheds?|sell-?off|breaks? below|bleeds?)"

SENTIMENT_RULES: list[Rule] = [
    # Monetary policy
    Rule("rate_cut", _r(r"\brate cuts?\b|\b(?:cuts?|cutting|lowers?|lowering) (?:interest )?rates?\b|\bdovish\b|\bmonetary easing\b"), 2.0,
         "Lower rates / dovish policy usually support risk assets such as BTC.", negation="flip"),
    Rule("rate_hike", _r(r"\brate hikes?\b|\b(?:raises?|raise|raising|hikes?|hiking) (?:interest )?rates?\b|\bhawkish\b|\b(?:monetary )?tightening\b"), -2.0,
         "Higher rates / hawkish policy usually pressure risk assets.", negation="cancel"),
    Rule("hawkish_hold", _r(r"\b(?:holds?|keeps?|leaves?|steady|unchanged|stands? pat|on hold|skips?|skipped|skip|holds? off)\b.{0,60}\b(?:inflation (?:(?:stays|remains|still|is|running) )?(?:elevated|sticky|persistent|high|hot|above target|surges?)|(?:elevated|sticky|persistent|high|hot) (?:us )?inflation|(?:lack of progress|concerns?) (?:on|over|about) inflation|no (?:rush|hurry) to cut)"), -1.0,
         "Rates held while inflation stays elevated reads as a hawkish hold."),
    Rule("inflation_hot", _r(r"\b(?:inflation|cpi|ppi|pce)\b.{0,40}\b(?:hotter|accelerat\w*|rises?|rising|jumps?|surges?|above (?:expectations|forecasts?))|\b(?:rising|higher|surging|soaring)\b.{0,30}\b(?:fuel\w*|driv\w*|stok\w*|push\w*)\b.{0,15}\binflation"), -1.5,
         "Hotter inflation raises the odds of tighter monetary policy."),
    Rule("inflation_cool", _r(r"\b(?:inflation|cpi|ppi|pce)\b.{0,40}\b(?:cool\w*|eases?|easing|slows?|slowing|falls?|below (?:expectations|forecasts?))"), 1.5,
         "Cooling inflation raises the odds of easier monetary policy."),
    Rule("dollar_up", _r(r"\b(?:dollar|dxy|greenback)\b.{0,30}\b(?:strengthens|rall(?:y|ies)|surges?|jumps?|gains?|rises?|climbs?)"), -1.0,
         "A stronger US dollar has historically weighed on BTC."),
    Rule("dollar_down", _r(r"\b(?:dollar|dxy|greenback)\b.{0,30}\b(?:weakens|slides?|falls?|drops?|slumps?)"), 1.0,
         "A weaker US dollar has historically supported BTC."),
    Rule("yields_up", _r(r"\byields?\b.{0,25}\b(?:rise|rises|jump|jumps|surge|surges|climb|climbs|spike\w*)"), -1.0,
         "Rising Treasury yields tighten financial conditions."),
    Rule("yields_down", _r(r"\byields?\b.{0,25}\b(?:fall|falls|drop|drops|slide|slides|decline\w*)"), 1.0,
         "Falling Treasury yields ease financial conditions."),
    # ETF flows (the headline often says "add $137M" rather than "inflows")
    Rule("etf_inflow", _r(r"\betfs?\b.{0,40}\b(?:inflows?|add(?:s|ed)?|attract\w*|draws?|drew|pull(?:s|ed)? in|haul\w*|log (?:\$|net in))|\binflows?\b.{0,40}\betfs?\b"), 2.0,
         "Spot ETF inflows represent net new buying demand."),
    Rule("etf_outflow", _r(r"\betfs?\b.{0,40}\b(?:outflows?|bleed\w*|shed\w*|los(?:e|es|t) \$|withdrawals?)|\boutflows?\b.{0,40}\betfs?\b"), -2.0,
         "Spot ETF outflows represent net selling."),
    # Regulation - only when a regulator/court acts, or an ETF/application is the object
    Rule("approval", _r(r"\b" + REGULATOR + r"\b.{0,40}\bapprov\w*|\bapprov\w*\b.{0,40}\b(?:etfs?|applications?|licen[cs]e|charter|bill|listing|framework)\b|\bwins?\b.{0,30}\bapproval\b"), 1.5,
         "A regulatory approval reduces uncertainty.", negation="flip"),
    Rule("rejection", _r(r"\b" + REGULATOR + r"\b.{0,40}\b(?:rejects?|rejected|denies|denied|delays?|delayed|postpones?)\b.{0,40}\b(?:etfs?|applications?|licen[cs]e|listing|bitcoin|crypto)|\b(?:rejects?|delays?|postpones?)\b.{0,30}\b(?:etfs?|applications?)\b"), -1.0,
         "A regulatory rejection or delay prolongs uncertainty.", negation="flip"),
    Rule("pro_crypto_legal", _r(r"\b(?:cftc|sec|regulators?)\s+(?:sues?|sued|challenges?)\s+(?:the\s+)?(?:state|states|utah|nevada|new jersey|new york|arizona|maryland|ohio|massachusetts|montana|illinois|connecticut)\b|\b(?:sues?|lawsuit)\b.{0,40}\bto (?:block|stop|protect|defend|preserve)\b"), 0.75,
         "A federal regulator defending crypto/prediction markets against state action reduces legal risk."),
    Rule("enforcement", _r(r"\b" + REGULATOR + r"\b.{0,40}\b(?:sues|sued|charges|charged|crackdown|subpoena\w*|indicts?|indicted|fines?|fined|raids?)\b|\b(?:crackdown|enforcement action)\b"), -1.5,
         "Enforcement or legal action adds regulatory risk."),
    Rule("ban", _r(r"\b(?:bans?|banned|prohibit\w*)\b.{0,40}\b(?:bitcoin|btc|crypto\w*|mining|trading|stablecoins?)\b|\b(?:bitcoin|btc|crypto\w*|mining)\b.{0,30}\b(?:bans?|banned|prohibit\w*)\b"), -2.0,
         "A ban restricts access and demand.", negation="flip"),
    Rule("sovereign", _r(r"\b(?:strategic )?bitcoin reserve\b|\blegal tender\b"), 2.0,
         "Sovereign adoption signals long-term demand.", negation="flip"),
    # Flows
    Rule("buying", _r(r"\b(?:buy|buys|buying|bought|acquires?|acquired|acquiring|purchases?|purchased|adds|added|accumulat\w*)\b.{0,30}\b(?:btc|bitcoin)\b"), 1.5,
         "Institutional or corporate buying adds demand.",
         skip_before=_r(r"(?:time to|should you|why you should|how to|where to|best way to|is it time to|reasons to|when to)\s*$")),
    Rule("selling", _r(r"\b(?:sells|sold|dumps|dumped|offloads?|unloads?)\b.{0,30}\b(?:btc|bitcoin)\b"), -1.5,
         "Large holders selling adds supply."),
    # Security / solvency - only when an exchange, protocol or wallet is the target
    Rule("hack", _r(r"\b" + EXCHANGE_TARGET + r"\w*\b.{0,60}\b(?:hack(?:ed)?|exploit(?:ed)?|drained|breach(?:ed)?|stolen)\b|\b(?:hack|exploit|breach|attack)\w*\b.{0,25}\b" + EXCHANGE_TARGET + r"|\$\d[\d.,]*\s?(?:million|m|billion|bn?)\b.{0,20}\b(?:stolen|drained)\b"), -2.0,
         "Security incidents hurt confidence and can trigger forced selling."),
    Rule("lost_access", _r(r"\b(?:can{0}t|cannot|unable to|lost|loses|losing) (?:access|withdraw)\b.{{0,30}}\b(?:btc|bitcoin|funds|coins|wallets?|reserves?)\b".format(APOS)), -1.0,
         "A holder losing access to its BTC signals custody failure; limited market-wide effect."),
    Rule("wallet_compromise", _r(r"\b(?:tampered|counterfeit|fake|compromised)\b.{0,25}\b(?:wallets?|devices?|hardware)\b|\bstolen (?:private )?keys?\b|\bsupply[- ]chain attack\b"), -1.0,
         "Wallet/device compromise hurts confidence; usually limited market impact unless widespread."),
    Rule("technical_rejection", _r(r"\b(?:rejected|fails?|failed|stalls?|stalled)\s+(?:at|near|below)\b.{0,30}\b(?:resistance|trend ?line|moving average|\d+-day|\$\d)"), -0.5,
         "Price rejected at technical resistance - mildly bearish chart signal."),
    Rule("vulnerability", _r(r"\b(?:vulnerabilit\w*|critical bug|flaw|security failure|denial[- ]of[- ]service|zero-day)\b|\bcrash\w*\s+(?:bitcoin\s+|btc\s+)?nodes\b"), -0.75,
         "Software vulnerability; usually patched quickly with limited lasting market effect."),
    Rule("insolvency", _r(r"\b(?:bankrupt\w*|insolven\w*|halts withdrawals|freezes withdrawals|pauses withdrawals)\b|\b" + EXCHANGE_TARGET + r"\b.{0,20}\bcollapses?\b"), -2.5,
         "Insolvency risk can cause contagion across crypto."),
    Rule("liquidations", _r(r"\bliquidat\w*"), -1.0, "Large liquidations mean forced selling and leverage stress."),
    # Price action - subject must be BTC/crypto, at most two plain words before the verb
    Rule("price_up", _r(PRICE_SUBJ + UP_VERBS + r"\b"), 1.0, "Reported upward price momentum."),
    Rule("price_down", _r(PRICE_SUBJ + DOWN_VERBS + r"\b"), -1.0, "Reported downward price momentum."),
    # Macro risk
    Rule("geo_escalation", _r(r"\b(?:war|invasion|missile|airstrikes?|escalat\w*|sanctions?)\b"), -1.0,
         "Geopolitical escalation usually triggers short-term risk-off moves."),
    Rule("tariffs", _r(r"\b(?:new|higher|imposes?|raises?|threatens?)\b.{0,20}\btariffs?\b|\btariffs?\b.{0,20}\b(?:hike|increase|escalat\w*)"), -1.0,
         "New tariffs raise growth and inflation risks; markets often react risk-off."),
    Rule("de_escalation", _r(r"\b(?:ceasefire|peace (?:deal|talks)|de-?escalat\w*|trade deal)\b"), 1.0,
         "De-escalation tends to improve risk appetite.", negation="flip"),
    Rule("financial_stress", _r(r"\b(?:recession|bank (?:run|failure)|credit crisis|contagion|debt default)\b"), -1.5,
         "Financial stress usually hits risk assets first."),
    Rule("equities_up", _r(r"\b(?:stocks|s&p 500|nasdaq|dow|equities)(?: futures)?\b.{0,30}\b(?:rall\w*|record|surges?|gains?|rises?|climbs?|jumps?)"), 0.5,
         "Equity strength often coincides with risk-on BTC flows."),
    Rule("equities_down", _r(r"\b(?:stocks|s&p 500|nasdaq|dow|equities)(?: futures)?\b.{0,30}\b(?:plunges?|tumbles?|slumps?|falls?|drops?|slides?|slips?|sinks?|sell-?off)"), -0.5,
         "Equity weakness often coincides with risk-off BTC flows."),
    Rule("hashrate_up", _r(r"\bhash ?rate\b.{0,30}\b(?:record|all-time high|rises?)"), 0.5,
         "Rising hashrate signals miner confidence and network security."),
]

NEGATION = _r(r"\b(?:not|no|never|denies|denied|won{0}t|wont|didn{0}t|didnt|doesn{0}t|don{0}t|isn{0}t|wasn{0}t|unlikely|fails? to|failed to|rules? out|ruled out|without|halts?|rejects? claims? of|skips?|skipped|skip|holds? off on|pauses?|paused|forgoes?)\b[^.]{{0,25}}$".format(APOS))

# When the first rule fires, the second is redundant or misleading for the same headline.
SUPERSEDES = {"pro_crypto_legal": {"enforcement"}, "hawkish_hold": {"rate_hike"}}
SMALL_AMOUNT = _r(r"\$(\d+(?:\.\d+)?)\s?(?:million|mn|m)\b")
SIZE_SENSITIVE = {"buying", "selling"}

INNER_NEGATION = _r(r"\b(?:denies|denied|rejects? claims?|refutes?|dismisses? (?:reports?|claims?)|no evidence of|not)\b")

# --------------------------------------------------------------------------- categories (headline)
CATEGORY_RULES: dict[str, re.Pattern] = {
    "fed_rates": _r(r"\b(?:fed|federal reserve|fomc|powell|warsh|interest rates?|rate (?:cut|hike|decision)s?|central bank)\b"),
    "inflation_macro": _r(r"\b(?:inflation|cpi|ppi|pce|jobs report|payrolls|nfp|unemployment|gdp|jobless)\b"),
    "dollar_yields": _r(r"\b(?:us dollar|dollar index|dxy|greenback|treasuries|treasury (?:yields?|bonds?|notes?|bills?)|bond yields?|10-year|yields? (?:rise|rises|fall|falls|climb|jump)|bond market)\b"),
    "etf_flows": _r(r"\betfs?\b|\b(?:ibit|fbtc|gbtc)\b"),
    "regulation": _r(r"\b(?:sec|cftc|occ|doj|regulat\w*|lawsuit|sues|sued|charges|charged|criminal|indict\w*|enforcement|bill|legislation|congress|senate|compliance|licen[cs]e|court|judge|jury|prosecut\w*|attorney general|illegal|gambling)\b"),
    "government_policy": _r(r"\b(?:government|treasury department|white house|ministry|parliament|bitcoin reserve|legal tender|cbdc)\b"),
    "geopolitics": _r(r"\b(?:war|invasion|sanctions?|tariffs?|geopolitic\w*|missile|ceasefire|trade (?:war|deal))\b"),
    "equities": _r(r"\b(?:stock market|stocks|s&p 500|nasdaq|dow jones|the dow|equities|wall street(?! journal)|stock futures)\b"),
    "institutional": _r(r"\b(?:blackrock|fidelity|microstrategy|strategy inc|saylor|treasury company|corporate|institution\w*|buys|acquires|purchases)\b|\bbuy(?:s|ing)? (?:more )?(?:btc|bitcoin)\b|\bbitcoin (?:for (?:its |the )?)?treasury\b"),
    "security_incident": _r(r"\b(?:hack\w*|exploit\w*|stolen|breach\w*|drained|bankrupt\w*|insolven\w*|liquidat\w*|halts withdrawals|vulnerabilit\w*|flaw|security failure|data leak|zero-day|denial[- ]of[- ]service|tampered|counterfeit|supply[- ]chain attack)\b|\bcrash\w*\s+(?:bitcoin\s+)?nodes\b|\b(?:can{0}t|cannot|unable to|lost) (?:access|withdraw)\b".format(APOS)),
    "network_mining": _r(r"\b(?:hash ?rate|miners?|mining|halving|difficulty|lightning network|taproot|soft fork|bip-?\d+|mempool|ordinals|nodes?)\b"),
    "crisis": _r(r"\b(?:crisis|bank (?:run|failure)|contagion|debt default|emergency|recession)\b"),
    "price_action": _r(PRICE_SUBJ + r"(?:" + UP_VERBS + "|" + DOWN_VERBS + r")\b|\b(?:bitcoin|btc)\b.{0,20}\b(?:above|below) \$"),
    "price_commentary": _r(r"\b(?:price prediction|price analysis|technical analysis|chart|support|resistance|could (?:hit|reach)|price targets?|targets? (?:of )?\$|raises? \w+ (?:price )?target|trend ?line|moving average|\d+-day)\b"),
}

FED_POLICY = _r(r"\b(?:rates?|rate (?:cut|hike|decision)s?|hikes?|cuts?|monetary|policy|inflation|hawkish|dovish|fomc|decision|tightening|easing|balance sheet|dissent\w*|paus\w*|holds? fire)\b")
INCIDENTAL_FED = _r(r"\b(?:ahead of|before|after|into)\s+(?:the\s+)?(?:fomc|fed)\b(?:\s+(?:meeting|decision))?")
INCIDENTAL_MACRO = _r(r"\b(?:ahead of|before|awaits?|await|into)\s+(?:the\s+)?(?:us\s+|u\.s\.\s+)?(?:pce|cpi|ppi|gdp|jobs|payrolls|nfp)\b(?:\s+(?:inflation\s+)?(?:data|report|release|figures|numbers))?|\bawaits?\s+(?:us\s+)?inflation data\b")
NOT_EQUITY_STOCKS = _r(r"\b(?:tokeni[sz]ed|preferred|common)\s+stocks?\b")
ADVISORY = _r(r"\b(?:experts? available|media advisory|how to watch|press release)\b")

CATEGORY_BASE_IMPACT = {
    "fed_other": 1,
    "fed_rates": 3, "inflation_macro": 3, "crisis": 3, "etf_flows": 2, "regulation": 2,
    "security_incident": 2, "geopolitics": 2, "government_policy": 2, "institutional": 2,
    "dollar_yields": 2, "equities": 1, "network_mining": 1, "price_action": 1, "crypto_market": 1,
    "price_commentary": 0, "digest": 0,
}
MAGNITUDE = _r(r"\b(?:billion|\$\d+(?:\.\d+)?\s?b(?:n)?\b|record|largest|emergency|historic|all-time high|collapse\w*|bankrupt\w*|surprise)\b")

# --------------------------------------------------------------------------- verification
SPECULATIVE = _r(r"^(?:analysis|opinion|op-ed|commentary|column|explainer|view|perspective)\s*[:|-]|\b(?:could|may|might|would|predicts?|price prediction|prediction(?! markets?)|forecasts?|expects?|expected to|likely to|set to|poised to|things to watch|what to watch|what to expect|can expect|preview|eyes|price targets?|targets? (?:of )?\$|raises? \w+ (?:price )?target|analysts? (?:say|says|warn|warns)|what (?:if|next)|here{0}s why|opinion|bets?|time to buy|should you|is it time)\b|\?".format(APOS))
UNVERIFIED = _r(r"\b(?:reportedly|rumou?rs?|sources (?:say|said)|according to (?:people|sources|a person)|unconfirmed|possibly|speculation|people familiar|in talks|considering|reported (?:hack|breach|attack|deal|talks|plan)|leaked (?:memo|document|draft|email)s?)\b")
CONFIRMED = _r(r"\b(?:announces?|announced|confirms?|confirmed|approves|approved|signs?|signed|files?|filed|launches|launched|data shows|officially|releases?|released|votes? to|discloses?|disclosed|reveals?|revealed|completes?|completed|charges|charged|sues|sued)\b")

# --------------------------------------------------------------------------- clustering helpers
STOPWORDS = set("""a an the and or of to in on for at by with from as is are was were be been it its this that these those
after before over under into about amid than then new says said say will just more most up down out why how what who
bitcoin btc crypto cryptocurrency cryptocurrencies market markets price prices news today week report reports
amid despite while could may might would its their his her they them our we you your""".split())
SYNONYMS = {"federal": "fed", "fomc": "fed", "powell": "fed", "cuts": "cut", "cutting": "cut", "rates": "rate",
            "hikes": "hike", "hiking": "hike", "etfs": "etf", "inflows": "inflow", "outflows": "outflow",
            "billion": "bn", "million": "mn", "u.s.": "us", "usa": "us", "sec's": "sec", "basis": "bps",
            "acquires": "buy", "buys": "buy", "purchases": "buy", "bought": "buy", "sues": "sue", "sued": "sue",
            "suing": "sue", "lawsuit": "sue", "charges": "charge", "charged": "charge", "files": "file", "filed": "file",
            "hacked": "hack", "hacker": "hack", "hackers": "hack", "breached": "breach", "exploited": "exploit",
            "predictions": "prediction", "leaking": "leak", "leaked": "leak", "leaks": "leak",
            "approves": "approve", "approved": "approve", "approval": "approve", "shareholders": "shareholder",
            "access": "custody", "withdraw": "custody", "withdrawals": "custody"}
TOKEN_RE = re.compile(r"[a-z0-9$%.'’]+")
EVENT_WORDS = {"hack", "breach", "exploit", "sue", "charge", "etf", "inflow", "outflow", "approval", "approve",
               "ban", "buy", "sell", "rate", "cut", "hike", "fed", "inflation", "cpi", "file", "data", "crypto", "leak"}
# Words that say WHAT happened. Same company + same action = same story.
EVENT_ACTIONS = {"hack", "breach", "exploit", "leak", "sue", "charge", "ban", "approve", "file", "indict", "arrest",
                 "delist", "halt", "acquire", "buy", "sell", "launch", "list", "custody"}
# Common words that must never be the only overlap between two headlines.
GENERIC = EVENT_WORDS | {
    "system", "internal", "customer", "user", "personal", "firm", "company", "plan", "million", "mn", "bn",
    "partnership", "expand", "expands", "institutional", "launch", "launches", "prediction", "chief", "ceo",
    "share", "stock", "trading", "platform", "fund", "funding", "raise", "investor", "deal", "us", "year",
    "quarter", "q1", "q2", "q3", "q4", "revenue", "earning", "record", "high", "low", "says", "amid",
    "global", "first", "major", "big", "top", "latest", "update", "live", "watch", "thing", "time", "day",
    "week", "month", "investors", "traders", "trader", "analyst", "analysts", "exchange", "token", "coin",
    "billion", "growth", "business", "product", "service", "team", "group", "bank", "banks", "network",
    "security", "custody", "rule", "rules", "law", "state", "court", "case", "report", "data", "level"}

FED_SUBJECT = _r(r"\b(?:fed|federal reserve|fomc|powell|warsh)\b")
# Decision verbs must attach to RATES ("holds rates", "rates unchanged"), not to anything ("holds briefing").
FED_DECISION = _r(r"\b(?:holds?|held|keeps?|kept|leaves?|left|skips?|skipped|pauses?|paused|maintains?|delivers? (?:a )?hold on)\b"
                  r"(?:\s+[\w’'-]+){0,3}?\s+(?:interest\s+)?rates?\b(?!\s+meetings?)"
                  r"|\brates?\s+(?:unchanged|steady|on hold|the same)\b|\bstands? pat\b|\bon hold\b"
                  r"|\b(?:rate|policy|interest rate)\s+decision\b"
                  r"|\b(?:cuts?|raises?|hikes?|lowers?)\s+(?:interest\s+)?rates\b(?!\s+meetings?)"
                  r"|\bskips?\s+(?:a\s+|another\s+)?(?:interest\s+)?rate\s+hike\b|\bholds?\s+off\s+on\b"
                  r"|\b(?:rate|hawkish|dovish)\s+hold\b|\bpaus(?:e|es|ed|ing)\s+(?:\w+\s+){0,2}?(?:rate|hike|cut)s?\b"
                  r"|\bhits?\s+(?:the\s+)?pause\b|\bholds?\s+fire\b")
FED_PREVIEW = _r(r"\b(?:expected to|likely to|set to|poised to|will (?:release|announce|decide|hold|keep|cut|raise)|to watch|"
                 r"what to expect|can expect|preview|could|may|might|bets?|odds|things to know|what to know)\b")
FED_MEETING = _r(r"\b(?:meeting|decision|fomc)\b")
FOREIGN_CB = _r(r"\b(?:ecb|european central bank|boj|bank of japan|boe|bank of england|pboc|rba|snb|bank of canada|rbi|sbp|bok|bank of korea|rbnz)\b")

SIGNATURES = [
    ("cpi_release", _r(r"\b(?:cpi|consumer price index|consumer prices)\b"), None),
    ("ppi_release", _r(r"\b(?:ppi|producer price index|producer prices)\b"), None),
    ("jobs_report", _r(r"\b(?:jobs report|nonfarm|non-farm|payrolls|nfp)\b"), None),
    ("gdp_release", _r(r"\bgdp\b"), None),
    ("pce_release", _r(r"\bpce\b"), None),
]
SIGNATURE_WINDOW = 30 * HOUR


def tokens(title: str) -> set[str]:
    out = set()
    for raw in TOKEN_RE.findall(title.lower()):
        t = raw.strip(".'’").replace("’", "'")
        if t.endswith("'s"):
            t = t[:-2]
        t = SYNONYMS.get(t, t)
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        if len(t) > 1 and t not in STOPWORDS:
            out.add(t)
    return out


def event_signature(title: str) -> str | None:
    """Key for scheduled macro releases so every outlet's phrasing lands in one event.

    Fed headlines are split into the decision itself and pre-decision previews; Fed
    commentary that is neither gets no signature (and never falls through to CPI/jobs).
    """
    if FOREIGN_CB.search(title):
        return "foreign_cb"  # own tag: never merges into a Fed group
    if INCIDENTAL_MACRO.search(title):
        title = INCIDENTAL_MACRO.sub(" ", title)  # "ahead of PCE data" is timing, not the subject
    if FED_SUBJECT.search(title):
        decision = FED_DECISION.search(title)
        if FED_PREVIEW.search(title) and (decision or FED_MEETING.search(title)):
            return "fed_preview"
        return "fed_decision" if decision else None
    if FOREIGN_MACRO.search(title) and not US_MARKER.search(title):
        return None
    for key, subject, _ in SIGNATURES:
        if subject.search(title):
            return key
    return None


# --------------------------------------------------------------------------- per item
def score_sentiment(title: str) -> dict:
    """Score a HEADLINE. Returns score in [-1, 1], label and the rules that fired."""
    hits = []
    for rule in SENTIMENT_RULES:
        m = rule.pattern.search(title)
        if not m:
            continue
        before = title[max(0, m.start() - 40):m.start()]
        if rule.skip_before is not None and rule.skip_before.search(before):
            continue
        # Negation can precede the match ("rules out a rate cut") or sit inside it ("Coinbase denies ... breach").
        negated = bool(NEGATION.search(before) or INNER_NEGATION.search(m.group(0)))
        w = rule.weight
        if negated:
            if rule.negation == "cancel":
                hits.append({"rule": rule.name, "match": m.group(0)[:60], "weight": 0.0, "negated": True,
                             "reason": rule.reason})
                continue
            w = -w * 0.5
        hits.append({"rule": rule.name, "match": m.group(0)[:60], "weight": w, "negated": negated, "reason": rule.reason})
    fired = {h["rule"] for h in hits if h["weight"] != 0}
    drop = set().union(*(SUPERSEDES.get(r, set()) for r in fired)) if fired else set()
    hits = [h for h in hits if h["rule"] not in drop]
    amount = SMALL_AMOUNT.search(title)
    if amount and float(amount.group(1)) < 100:
        for h in hits:
            if h["rule"] in SIZE_SENSITIVE:
                h["weight"] *= 0.5
                h["reason"] += " (Small size - under $100M - so weighted half.)"
    bull = sum(h["weight"] for h in hits if h["weight"] > 0)
    bear = -sum(h["weight"] for h in hits if h["weight"] < 0)
    score = math.tanh((bull - bear) / 3)
    return {"score": round(score, 3), "label": label_for(score, bull, bear), "hits": hits}


LABEL_THRESHOLD = 0.15  # |score| below this is neutral; a single weak rule (weight 0.5) just clears it


def label_for(score: float, bull: float = 0.0, bear: float = 0.0) -> str:
    if bull >= 1 and bear >= 1 and abs(bull - bear) < 1:
        return "uncertain"
    if score >= LABEL_THRESHOLD:
        return "bullish"
    if score <= -LABEL_THRESHOLD:
        return "bearish"
    return "neutral"


def relevance(title: str, summary: str, categories: list[str], aggregator: bool) -> tuple[float, str]:
    """How directly a story bears on BTC. Returns (factor, label)."""
    if FOREIGN_MACRO.search(title) and not US_MARKER.search(title) and not BTC_TERMS.search(title) \
            and set(categories) & MACRO_CATS:
        return 0.4, "foreign_macro"
    if FOREIGN_CB.search(title) and not BTC_TERMS.search(title):
        return 0.4, "foreign_macro"
    if set(categories) & (MACRO_CATS | {"fed_other"}) and not ALTCOIN.search(title):
        return 1.0, "macro"
    if BTC_TERMS.search(title):
        return (0.6, "mixed") if ALTCOIN.search(title) and not re.search(r"\b(?:bitcoin|btc)\b", title, re.I) else (1.0, "direct")
    if ALTCOIN.search(title):
        return 0.3, "altcoin"
    if CRYPTO_TERMS.search(title):
        return 0.6, "crypto_adjacent"
    # Headline has no crypto word: the summary decides (e.g. "Strategy shareholders approve dividends").
    if BTC_TERMS.search(summary) or CRYPTO_TERMS.search(summary):
        return 0.6, "crypto_adjacent"
    if FED_SUBJECT.search(title):
        return 1.0, "macro"
    return 0.0, "off_topic"  # no crypto or macro connection in headline or summary


QUOTES = re.compile(r'["“”«»]')


def analyse_item(item: dict, aggregator: bool = False) -> dict:
    # Match on a quote-free copy ('"Now Is The Time" to Buy' -> 'Now Is The Time to Buy'); display the original.
    title = QUOTES.sub("", item["title"])
    summary = item.get("summary", "") or ""
    head_cats = [c for c, p in CATEGORY_RULES.items()
                 if p.search(NOT_EQUITY_STOCKS.sub(" ", title) if c == "equities" else title)]
    if "fed_rates" in head_cats:
        if not FED_POLICY.search(title):
            head_cats[head_cats.index("fed_rates")] = "fed_other"  # e.g. Fed building, debit-card rule
        elif INCIDENTAL_FED.search(title) and len(head_cats) > 1 and not FED_SUBJECT.search(INCIDENTAL_FED.sub(" ", title)):
            head_cats.remove("fed_rates")  # "...ahead of FOMC" is timing, not the subject
    if "inflation_macro" in head_cats and INCIDENTAL_MACRO.search(title) and len(head_cats) > 1 \
            and not CATEGORY_RULES["inflation_macro"].search(INCIDENTAL_MACRO.sub(" ", title)):
        head_cats.remove("inflation_macro")  # "...ahead of US PCE data" is timing, not the subject
    digest = bool(DIGEST.search(title) or ADVISORY.search(title))
    summary_cats: list[str] = []
    if digest:
        head_cats = ["digest"]
    elif not head_cats:
        summary_cats = [c for c, p in CATEGORY_RULES.items() if p.search(summary[:200])][:1]
    cats = head_cats or summary_cats or ["crypto_market"]
    rel, rel_label = relevance(title, summary, head_cats, aggregator)

    sent = score_sentiment(title)
    raw_score = sent["score"]
    retro = bool(RETROSPECTIVE.search(title))
    factor = 0.0 if digest else rel * (0.5 if retro else 1.0)
    score = round(raw_score * factor, 3)
    bull = sum(h["weight"] for h in sent["hits"] if h["weight"] > 0) * factor
    bear = -sum(h["weight"] for h in sent["hits"] if h["weight"] < 0) * factor
    first_sentence = summary[:160]
    return {
        **item,
        "categories": cats,
        "categories_from_summary": bool(summary_cats) and not head_cats,
        "sentiment": {"score": score, "raw_score": raw_score, "label": label_for(score, bull, bear),
                      "hits": sent["hits"]},
        "relevance": rel,
        "relevance_label": rel_label,
        "is_digest": digest,
        "is_retrospective": retro,
        "tokens": tokens(title),
        "signature": event_signature(title),
        "is_speculative": bool(SPECULATIVE.search(title)),
        "is_rumor": bool(UNVERIFIED.search(title) or UNVERIFIED.search(first_sentence)),
        "has_confirmation": bool(CONFIRMED.search(title) or CONFIRMED.search(first_sentence)),
        "has_magnitude": bool(MAGNITUDE.search(title)),
        "too_short": len(tokens(title)) < MIN_TOKENS,
        "time": item.get("published_at") or item["collected_at"],
    }


# --------------------------------------------------------------------------- clustering
def _idf(items: list[dict]) -> dict[str, float]:
    n = len(items)
    df = Counter(t for it in items for t in it["tokens"])
    return {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}


def _cosine(a: set[str], b: set[str], idf: dict[str, float]) -> float:
    shared = a & b
    if not shared:
        return 0.0
    num = sum(idf[t] ** 2 for t in shared)
    na = math.sqrt(sum(idf[t] ** 2 for t in a))
    nb = math.sqrt(sum(idf[t] ** 2 for t in b))
    return num / (na * nb)


SPECIFIC_CATS = {"security_incident", "regulation", "etf_flows", "institutional", "government_policy"}


def _distinctive(shared: set[str]) -> set[str]:
    return {t for t in shared if t not in GENERIC and not t.isdigit() and len(t) > 2}


def similarity(a: dict, b: dict, idf: dict[str, float], df_cap: float) -> float:
    """0..1 similarity; > 0 means same story.

    Every content-based match needs distinctive overlap (names, specific nouns),
    never just generic words like 'data', 'system', 'breach'.
    """
    if a["link"] == b["link"]:
        return 1.0
    if a.get("is_digest") or b.get("is_digest"):
        return 0.0  # newsletters are separate items by nature
    sig = a.get("signature")
    if sig and sig == b.get("signature") and abs(a["time"] - b["time"]) <= SIGNATURE_WINDOW:
        return 0.95
    if sig and b.get("signature") and sig != b.get("signature"):
        return 0.0  # e.g. Fed preview vs Fed decision
    shared = a["tokens"] & b["tokens"]
    distinct = _distinctive(shared)
    cos = _cosine(a["tokens"], b["tokens"], idf)
    if len(distinct) >= 4:
        return max(cos, 0.6)
    if len(distinct) >= 2 and ((len(shared) >= 2 and cos >= 0.45) or (len(shared) >= 3 and cos >= 0.32)):
        return cos
    within_day = abs(a["time"] - b["time"]) <= 24 * HOUR
    same_cat = set(a["categories"]) & set(b["categories"]) & SPECIFIC_CATS
    if within_day and same_cat:
        entities = {t for t in distinct if idf[t] >= df_cap}
        if len(entities) >= 2:
            return 0.5
        if entities and shared & EVENT_ACTIONS:
            return 0.45  # same company, same kind of event (e.g. Bithumb + breach/leak)
    return 0.0


def similar(a: set[str], b: set[str]) -> bool:
    """Back-compatible token-only check (used by older callers/tests)."""
    if not a or not b:
        return False
    inter = len(a & b)
    return inter / len(a | b) >= 0.5 or (inter >= 3 and inter / min(len(a), len(b)) >= 0.75)


def cluster_events(items: Iterable[dict], window_ms: int = 36 * HOUR) -> list[list[dict]]:
    """Greedy clustering of analysed items (oldest first) into events."""
    items = sorted(items, key=lambda x: x["time"])
    if not items:
        return []
    idf = _idf(items)
    n = len(items)
    # A token counts as an entity if it appears in at most ~3% of items (min 3).
    df_cap = math.log((n + 1) / (max(3, int(0.03 * n)) + 1)) + 1
    events: list[list[dict]] = []
    for it in items:
        best, best_sim = None, 0.0
        for ev in events:
            if it["time"] - ev[-1]["time"] > window_ms:  # window rolls with the latest article
                continue
            sim = max(similarity(it, m, idf, df_cap) for m in ev)
            if sim > best_sim:
                best, best_sim = ev, sim
        if best is not None and best_sim > 0:
            best.append(it)
        else:
            events.append([it])
    return events


# --------------------------------------------------------------------------- events
def _price_move_since(prices: pd.Series | None, since_ms: int) -> float | None:
    """% change of BTC close from the candle at publication to the latest close."""
    if prices is None or prices.empty:
        return None
    ts = pd.Timestamp(since_ms, unit="ms", tz="UTC")
    before = prices[prices.index <= ts]
    if before.empty:
        return None
    start = float(before.iloc[-1])
    return (float(prices.iloc[-1]) - start) / start * 100


def build_event(members: list[dict], now: int, prices: pd.Series | None = None) -> dict:
    members = sorted(members, key=lambda m: m["time"])
    original = members[0]
    publishers = sorted({m["publisher"] for m in members})
    independent = len({p.lower() for p in publishers})

    directional = [m for m in members if m["sentiment"]["score"] != 0 or m["sentiment"]["label"] == "uncertain"]
    labels = {m["sentiment"]["label"] for m in directional}
    conflicting = "bullish" in labels and "bearish" in labels
    if directional:
        # Neutral re-writes carry no directional information: average the directional ones,
        # damped when they are a small minority of the coverage.
        mean = sum(m["sentiment"]["score"] for m in directional) / len(directional)
        score = mean * min(1.0, 0.5 + len(directional) / len(members))
    else:
        score = 0.0
    label = "uncertain" if conflicting else label_for(score)
    if label == "neutral" and "uncertain" in labels:
        label = "uncertain"

    cat_counts = Counter(c for m in members for c in m["categories"])
    categories = sorted(cat_counts, key=lambda c: (-cat_counts[c], -CATEGORY_BASE_IMPACT.get(c, 1)))
    relevance_f = max(m["relevance"] for m in members)
    relevance_label = max(members, key=lambda m: m["relevance"])["relevance_label"]

    # Verification
    rumour_all = all(m["is_rumor"] for m in members)
    if all(m["is_speculative"] for m in members):
        verification, v_reason = "speculative", "Forecast, opinion or question - not an event."
    elif any(m["has_confirmation"] and not m["is_rumor"] for m in members):
        verification, v_reason = "confirmed", "Official/confirming language in the report."
    elif independent >= 2 and not rumour_all:
        verification, v_reason = "confirmed", f"Reported independently by {independent} publishers."
    elif any(m["is_rumor"] for m in members):
        verification, v_reason = "unverified", "Uses rumour/'reportedly' language - not established fact."
    else:
        verification, v_reason = "unverified", "Single source, not yet corroborated."

    # Impact (headline categories only; summary-derived categories cannot raise impact above low)
    head_members = [m for m in members if not m.get("categories_from_summary")]
    head_cats = {c for m in head_members for c in m["categories"]} or {"crypto_market"}
    base = max(CATEGORY_BASE_IMPACT.get(c, 1) for c in head_cats)
    pts, i_reasons = base, [f"category base {base} ({max(head_cats, key=lambda c: CATEGORY_BASE_IMPACT.get(c, 1)).replace('_', ' ')})"]
    if any(m["has_magnitude"] for m in members):
        pts += 1
        i_reasons.append("large-magnitude wording")
    if independent >= 3:
        pts += 1
        i_reasons.append(f"{independent} publishers")
    if verification == "speculative":
        pts -= 1
        i_reasons.append("speculative")
    if label == "neutral":
        pts -= 1
        i_reasons.append("no directional signal")
    elif label in ("bullish", "bearish") and abs(score) < 0.25:
        pts -= 1
        i_reasons.append("weak directional signal")
    if all(m["is_retrospective"] for m in members):
        pts -= 1
        i_reasons.append("retrospective/statistics piece")
    if relevance_f < 1.0:
        pts -= 1 if relevance_f >= 0.5 else 2
        i_reasons.append(f"not BTC-specific ({relevance_label.replace('_', ' ')})")
    if any(m["is_digest"] for m in members):
        pts = 0
        i_reasons.append("daily digest")
    impact = "high" if pts >= 3 else "medium" if pts >= 2 else "low"

    # Freshness / priced-in
    age_h = (now - original["time"]) / HOUR
    move = _price_move_since(prices, original["time"])
    if age_h >= 72:
        freshness, f_reason = "too_old", "Older than 72h - excluded from the current news bias."
    elif move is not None and ((label == "bullish" and move >= 1.5) or (label == "bearish" and move <= -1.5)):
        freshness, f_reason = "likely_priced_in", f"BTC already moved {move:+.1f}% in the implied direction since publication."
    elif age_h >= 24:
        freshness, f_reason = "likely_priced_in", "Older than 24h - markets usually absorb news within a day."
    elif age_h < 2:
        freshness, f_reason = "new", "Published within the last 2 hours."
    else:
        freshness, f_reason = "recent", "Published within the last 24 hours."

    hits = [h for m in members for h in m["sentiment"]["hits"]]
    seen, why = set(), []
    for h in sorted(hits, key=lambda h: -abs(h["weight"])):
        if h["reason"] in seen:
            continue
        seen.add(h["reason"])
        if h["negated"] and h["weight"] == 0:
            why.append(f"Negated, so ignored: '{h['match']}' - {h['reason']}")
        else:
            why.append(("(negated) " if h["negated"] else "") + h["reason"])
    if not why:
        why = ["No directional keywords matched in the headline; treated as neutral."]
    elif label == "neutral":
        why.insert(0, "Weak or damped signal only - not strong enough to call a direction.")
    if conflicting:
        why.insert(0, "Publishers disagree on direction for this story - treated as uncertain.")
    if relevance_f < 1.0:
        why.append(f"Down-weighted: {relevance_label.replace('_', ' ')} story (BTC relevance x{relevance_f}).")

    return {
        "id": original["id"],
        "title": original["title"],
        "link": original["link"],
        "publisher": original["publisher"],
        "summary": original.get("summary", ""),
        "published_at": original.get("published_at"),
        "first_seen_at": min(m["collected_at"] for m in members),
        "publish_time_known": original.get("published_at") is not None,
        "age_hours": round(age_h, 1),
        "categories": categories,
        "sentiment": label,
        "score": round(score, 3),
        "conflicting_reports": conflicting,
        "impact": impact,
        "impact_reason": ", ".join(i_reasons),
        "verification": verification,
        "verification_reason": v_reason,
        "freshness": freshness,
        "freshness_reason": f_reason,
        "relevance": relevance_f,
        "relevance_label": relevance_label,
        "signature": original.get("signature"),
        "price_move_since_pct": round(move, 2) if move is not None else None,
        "btc_impact": why[:4],
        "independent_sources": independent,
        "sources": [{"publisher": m["publisher"], "title": m["title"], "link": m["link"],
                     "published_at": m.get("published_at"), "sentiment": m["sentiment"]["label"]} for m in members],
    }


# --------------------------------------------------------------------------- summary
IMPACT_W = {"low": 1.0, "medium": 2.0, "high": 3.0}
VERIFY_W = {"confirmed": 1.0, "unverified": 0.5, "speculative": 0.25}
FRESH_W = {"new": 1.0, "recent": 0.7, "likely_priced_in": 0.3, "superseded": 0.0, "too_old": 0.0}


def find_cross_conflicts(events: list[dict], window_h: float = 24) -> list[dict]:
    """Opposite-direction, medium/high-impact events in the same category within the window."""
    out = []
    strong = [e for e in events if e["impact"] in ("medium", "high") and e["sentiment"] in ("bullish", "bearish")
              and e["age_hours"] <= window_h]
    for i, a in enumerate(strong):
        for b in strong[i + 1:]:
            shared = set(a["categories"][:2]) & set(b["categories"][:2]) - {"crypto_market", "price_commentary", "digest"}
            if shared and a["sentiment"] != b["sentiment"]:
                out.append({"category": sorted(shared)[0], "a": {"id": a["id"], "title": a["title"], "sentiment": a["sentiment"]},
                            "b": {"id": b["id"], "title": b["title"], "sentiment": b["sentiment"]}})
    return out[:10]


def summarise(events: list[dict], excluded: dict | None = None) -> dict:
    num = den = 0.0
    counts = {"bullish": 0, "bearish": 0, "neutral": 0, "uncertain": 0}
    by_cat: dict[str, dict] = {}
    for e in events:
        counts[e["sentiment"]] += 1
        if e["sentiment"] != "neutral":
            # Neutral events carry no directional information, so they do not dilute the bias.
            w = IMPACT_W[e["impact"]] * VERIFY_W[e["verification"]] * FRESH_W[e["freshness"]]
            num += w * e["score"]
            den += w
        for c in e["categories"][:1]:
            d = by_cat.setdefault(c, {"events": 0, "bullish": 0, "bearish": 0})
            d["events"] += 1
            if e["sentiment"] in ("bullish", "bearish"):
                d[e["sentiment"]] += 1
    bias = num / den if den else 0.0
    active = [e for e in events if e["freshness"] != "too_old"]
    label = ("insufficient_news" if den < 1 else "bullish" if bias >= 0.15 else "bearish" if bias <= -0.15 else "mixed/neutral")
    return {
        "bias_score": round(bias, 3),
        "bias_label": label,
        "weight_total": round(den, 2),
        "events_total": len(events),
        "events_active": len(active),
        "directional_active": sum(1 for e in active if e["sentiment"] != "neutral"),
        "high_impact_active": sum(1 for e in active if e["impact"] == "high"),
        "counts": counts,
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1]["events"])),
        "conflicts": find_cross_conflicts(events),
        "excluded": excluded or {},
        "method": ("Headline-only, rule-based. Weighted mean of directional event scores; weight = impact (1/2/3) x "
                   "verification (confirmed 1, unverified 0.5, speculative 0.25) x freshness (new 1, recent 0.7, "
                   "priced-in 0.3, >72h 0). Neutral events are listed but do not dilute the bias. Each event counts "
                   "once however many outlets carried it; non-BTC stories are down-weighted."),
        "disclaimer": "Rule-based sentiment estimate from headlines. Not a probability and not a trade signal.",
        "analyzer": ANALYZER_VERSION,
    }


AGGREGATOR_FEEDS = {"googlenews"}


def analyse(items: list[dict], now: int, prices: pd.Series | None = None) -> tuple[list[dict], dict]:
    analysed, excluded = [], {"off_topic": 0, "too_short": 0}
    for i in items:
        a = analyse_item(i, aggregator=i.get("feed") in AGGREGATOR_FEEDS)
        if a["too_short"]:
            excluded["too_short"] += 1
        elif a["relevance"] == 0:
            excluded["off_topic"] += 1
        else:
            analysed.append(a)
    events = [build_event(m, now, prices) for m in cluster_events(analysed)]
    # Once the Fed decision is out, earlier previews of it carry no new information.
    decisions = [e for e in events if e.get("signature") == "fed_decision"]
    for e in events:
        if e.get("signature") == "fed_preview" and any(
                d["first_seen_at"] >= (e["published_at"] or e["first_seen_at"]) for d in decisions):
            e["freshness"] = "superseded"
            e["freshness_reason"] = "Preview of a Fed decision that has since been announced - excluded from the bias."
            e["impact"] = "low"
    events.sort(key=lambda e: (e["freshness"] == "too_old", -IMPACT_W[e["impact"]], e["age_hours"]))
    return events, summarise(events, excluded)
