"""Regression tests from the first live review (29 Oct 2026, 219 real headlines).

Each case is a headline the v1 rules got wrong, as reported in the review.
Headlines that were truncated in the review are completed with plausible
endings that keep the key wording. Expected labels are what a crypto market
analyst would assign, not what the rules happen to output.
"""
import pytest
from conftest import FIXED_NOW

from services.news_analyzer import analyse, analyse_item, build_event, cluster_events, event_signature

MIN = 60_000


def it(title, minutes_ago=30, publisher="CoinDesk", summary="", feed="coindesk", link=None):
    t = FIXED_NOW - minutes_ago * MIN
    return {"id": f"{publisher}-{abs(hash(title))}", "feed": feed, "publisher": publisher, "title": title,
            "link": link or f"https://{publisher.lower().replace(' ', '')}.test/{abs(hash(title))}",
            "summary": summary, "published_at": t, "collected_at": t + MIN}


def ev(*items):
    return build_event([analyse_item(i, aggregator=i["feed"] == "googlenews") for i in items], FIXED_NOW)


# ---------------------------------------------------------------- sentiment direction
@pytest.mark.parametrize("title,summary,expected", [
    # #6: ETF headline said "add", summary mentioned a hawkish Fed -> was bearish
    ("Bitcoin ETFs add $137M, pushing October back into the green",
     "Spot bitcoin ETFs saw net additions despite a hawkish Federal Reserve on Wednesday.", "bullish"),
    # Fed "didn't raise" -> was bearish (negation missed "didn't")
    ("Does Anyone Know Why the Fed Didn't Raise Rates?", "", "neutral"),
    ("Does Anyone Know Why the Fed Didn’t Raise Rates?", "", "neutral"),  # curly apostrophe
    # Shareholder vote is not a regulatory approval -> was bullish
    ("Strategy shareholders approve daily dividends on preferred stock", "", "neutral"),
    # Ordinary adjective "approved" -> was bullish
    ("Ripple, BNY team up to finance approved invoices on-chain", "", "neutral"),
    # Chart language "rejected" is not regulation; a technical rejection is mildly bearish (v2.1)
    ("Bitcoin price bounce rejected at 50-day trend line ahead of FOMC", "", "bearish"),
    # Hack of a government agency is not a crypto security incident
    ("Crypto PAC spending under scrutiny after reported FEC hack", "", "neutral"),
    # Earnings line, not BTC price -> was bearish
    ("Coinbase earnings beat as Robinhood crypto revenue sinks 55%", "", "neutral"),
    # Digest with an unrelated "soars" -> was bullish
    ("Morning Minute: ETF flows steady, Robinhood Chain soars", "", "neutral"),
    # Fraud trial; "charges" appeared only in the summary -> was bearish
    ("Canadian 'crypto king' case to go to jury", "He faces charges of fraud.", "neutral"),
    # A denied *systems* breach is not bullish news -> was +0.32
    ("Coinbase denies systems breach after vendor incident", "", "neutral"),
    # Price dip with hawkish Fed - correct direction kept
    ("Bitcoin dips below $80,000 as tech stocks and a hawkish Fed weigh on crypto", "", "bearish"),
    # Nasdaq futures "slide" -> was neutral
    ("Nasdaq futures slide as mixed Big Tech results test AI trade", "", "bearish"),
    # Real exchange breach - still bearish
    ("Bithumb discloses data leak after internal system breach", "", "bearish"),
    # Hawkish hold
    ("Fed holds rates steady as inflation stays elevated, two officials dissent", "", "bearish"),
])
def test_headline_sentiment(title, summary, expected):
    e = ev(it(title, summary=summary))
    assert e["sentiment"] == expected, (title, e["score"], e["btc_impact"])


def test_nasdaq_slide_is_only_mildly_bearish():
    e = ev(it("Nasdaq futures slide as mixed Big Tech results test AI trade"))
    assert -0.5 < e["score"] < 0


# ---------------------------------------------------------------- categories & impact
def test_etf_story_not_driven_by_summary_fed_mention():
    e = ev(it("Bitcoin ETFs add $137M, pushing October back into the green",
              summary="...despite a hawkish Federal Reserve and FOMC uncertainty."))
    assert e["categories"][0] == "etf_flows" and "fed_rates" not in e["categories"]
    assert e["impact"] in ("medium", "high")


@pytest.mark.parametrize("title,must_have,must_not", [
    ("Utah charges predictions platform Kalshi with illegal gambling", "regulation", "crypto_market"),
    ("AI found a way to crash bitcoin nodes on Windows and Macs", "security_incident", None),
    ("Security failure at Indian certificate firm bites Ethereum infrastructure", "security_incident", None),
    ("Mystery Wall Street Journal ad sparks new fight over tokenized stocks", None, "equities"),
    ("Strategy shareholders approve daily dividends on preferred stock", None, "equities"),
    ("Ripple Wins Final OCC Approval for national trust charter", "regulation", None),
    ("Bitcoin price bounce rejected at 50-day trend line ahead of FOMC", "price_commentary", None),
])
def test_categories(title, must_have, must_not):
    cats = analyse_item(it(title))["categories"]
    if must_have:
        assert must_have in cats, cats
    if must_not:
        assert must_not not in cats, cats


def test_digest_is_low_impact_neutral():
    e = ev(it("Here's what happened in crypto today", summary="Bitcoin, ETFs and crypto regulation news."))
    assert e["categories"] == ["digest"] and e["impact"] == "low" and e["sentiment"] == "neutral"


def test_foreign_inflation_is_low_impact():
    e = ev(it("South African rand weakens ahead of producer inflation data", publisher="Reuters", feed="googlenews"))
    assert e["impact"] == "low" and e["relevance"] < 1


def test_retrospective_hack_stats_is_damped():
    e = ev(it("Centralized Exchange and DeFi Losses Dominate 2026 Crypto Hacks",
              summary="Lightning and bridge exploits also featured."))
    assert e["impact"] in ("low", "medium") and "network_mining" not in e["categories"]


def test_altcoin_only_news_is_down_weighted():
    e = ev(it("Ripple Wins Final OCC Approval for national trust charter"))
    assert e["relevance"] < 1 and e["sentiment"] == "neutral" and e["impact"] != "high"


def test_off_topic_and_too_short_aggregator_items_dropped():
    items = [it("Readers sound off on capital's control, Flavio Bolsonaro and Bob Woodward",
                publisher="NY Daily News", feed="googlenews"),
             it("Federal Reserve", publisher="Bakersfield.com", feed="googlenews"),
             it("Bitcoin ETFs add $137M, pushing October back into the green")]
    events, summary = analyse(items, FIXED_NOW)
    assert len(events) == 1
    assert summary["excluded"] == {"off_topic": 1, "too_short": 1}


# ---------------------------------------------------------------- verification
@pytest.mark.parametrize("title,summary,expected", [
    # "allegedly" describes the accusation, not the reliability of the report
    ("Utah files criminal charges against Kalshi, executives", "The firm allegedly ran illegal gambling.", "confirmed"),
    # Company disclosure is confirmation; "leak" means data leak, not leaked info
    ("Bithumb discloses data leak after internal system breach", "", "confirmed"),
    # Forecasts and advice are speculative
    ("Bitwise Raises Bitcoin Target to $1.6 Million by 2035", "", "speculative"),
    ('Galaxy: "Now Is The Time" to Buy Bitcoin', "", "speculative"),
    ("Analysis: Warsh is learning to wield the Fed's power", "", "speculative"),
    # "prediction markets" is a noun phrase, not a forecast
    ("CFTC sues Utah to block action against prediction markets", "", "confirmed"),
    # "reported hack" is unconfirmed
    ("Crypto PAC spending under scrutiny after reported FEC hack", "", "unverified"),
])
def test_verification(title, summary, expected):
    assert ev(it(title, summary=summary))["verification"] == expected


def test_buy_advice_is_not_an_actual_purchase():
    e = ev(it('Galaxy: "Now Is The Time" to Buy Bitcoin'))
    assert not any("buying adds demand" in w for w in e["btc_impact"])


# ---------------------------------------------------------------- clustering
def _clusters(items):
    return cluster_events([analyse_item(i, aggregator=i["feed"] == "googlenews") for i in items])


FED_VARIANTS = [
    ("Fed holds rates steady at 3.75%-4% as two officials dissent", "Reuters"),
    ("Federal Reserve leaves interest rates unchanged", "CNBC"),
    ("Fed stands pat on rates, says inflation remains elevated", "Bloomberg"),
    ("FOMC keeps rates on hold in split decision", "WSJ"),
    ("Powell: Fed puts rates on hold, watching inflation", "AP"),
    ("Fed rate decision: policymakers hold steady", "Yahoo Finance"),
]


def test_fed_decision_variants_are_one_event():
    items = [it(t, 60 + i * 10, p, feed="googlenews") for i, (t, p) in enumerate(FED_VARIANTS)]
    items.append(it("Warsh's AI Optimism May Box Him In at the Fed", 20, "Bloomberg", feed="googlenews"))
    items.append(it("Bitcoin ETFs add $137M, pushing October back into the green", 15))
    clusters = _clusters(items)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 1, 6], sizes  # 6 decision variants together; the Warsh column and ETF story separate


def test_fed_forward_guidance_is_not_the_decision():
    assert event_signature("Fed officials open door to rate cuts in December") is None
    assert event_signature("Fed signals rate hikes ahead after hot jobs report") is None
    assert event_signature("Federal Reserve leaves interest rates unchanged") == "fed_decision"


def test_foreign_cpi_does_not_share_us_signature():
    assert event_signature("UK CPI rises to 3.8% in September") is None
    assert event_signature("US CPI cools to 2.4% in September") == "cpi_release"


@pytest.mark.parametrize("a,b", [
    ("Utah files criminal charges against Kalshi, executives", "Utah charges predictions platform Kalshi with illegal gambling"),
    ("CFTC sues Utah to block action against prediction markets", "CFTC sues Utah as battle over prediction markets heats up"),
    ("Coinbase Customer Data Hit in Vendor Breach", "Coinbase denies systems breach after vendor incident"),
])
def test_same_story_different_wording_merges(a, b):
    items = [it(a, 90, "The Block", feed="theblock"), it(b, 60, "CoinDesk")]
    # Pad with unrelated items so IDF weights resemble a real batch.
    items += [it(t, 30 + i, "Decrypt", feed="decrypt") for i, t in enumerate([
        "Ethereum developers schedule next network upgrade", "Solana DEX volume hits monthly high",
        "Bitcoin miners expand in Texas", "Stablecoin supply reaches new record", "Tether mints more USDT",
        "Galaxy reports quarterly earnings", "Coinbase earnings beat as trading volume rises",
        "Binance lists new token pairs", "Kraken expands in Europe", "Bitcoin price holds near $80,000"])]
    clusters = _clusters(items)
    merged = [c for c in clusters if {m["title"] for m in c} >= {a, b}]
    assert merged, [[m["title"] for m in c] for c in clusters if len(c) > 1]


@pytest.mark.parametrize("a,b", [
    ("Coinbase Customer Data Hit in Vendor Breach", "Coinbase earnings beat as trading volume rises"),
    ("Exchange Alpha hacked for $50 million", "Bridge Beta exploited, $20 million drained"),
    ("Fed officials open door to rate cuts in December", "Fed signals rate hikes ahead after hot jobs report"),
])
def test_different_stories_stay_separate(a, b):
    clusters = _clusters([it(a, 60), it(b, 30, "Reuters")])
    assert len(clusters) == 2


# ---------------------------------------------------------------- event sentiment & bias
def test_fed_decision_event_is_bearish_not_diluted_by_neutral_rewrites():
    titles = ["Fed holds rates steady as inflation stays elevated",
              "Fed signals hawkish hold with two dissents",
              "Federal Reserve leaves interest rates unchanged",
              "FOMC keeps rates on hold in split decision",
              "Fed rate decision: policymakers hold steady"]
    e = build_event([analyse_item(it(t, 60 + i, f"P{i}", feed="googlenews"), aggregator=True)
                     for i, t in enumerate(titles)], FIXED_NOW)
    assert e["sentiment"] == "bearish" and e["impact"] == "high"


def test_neutral_events_do_not_dilute_bias():
    items = [it("Bitcoin ETFs add $137M, pushing October back into the green", 30)]
    items += [it(f"Bitcoin conference {i} schedule announced for next year in city {i}", 40 + i, f"P{i}") for i in range(20)]
    _, summary = analyse(items, FIXED_NOW)
    assert summary["bias_label"] == "bullish" and summary["counts"]["neutral"] >= 1


# ================================================================ v2.1 (second live review, 212 articles)
PAD = ["Ethereum developers schedule next network upgrade", "Solana DEX volume hits monthly high",
       "Bitcoin miners expand in Texas", "Stablecoin supply reaches new record", "Tether mints more USDT",
       "Galaxy reports quarterly earnings", "Binance lists new token pairs", "Kraken expands in Europe",
       "Bitcoin price holds near $80,000", "Circle adds new stablecoin chain"]


def _batch(*pairs):
    items = [it(t, 200 - i * 10, p, feed=f) for i, (t, p, f) in enumerate(pairs)]
    items += [it(t, 30 + i, "Padding", feed="decrypt") for i, t in enumerate(PAD)]
    return _clusters(items)


def _together(clusters, a, b):
    return any({m["title"] for m in c} >= {a, b} for c in clusters)


@pytest.mark.parametrize("a,b", [
    # Two different exchanges' breaches share only generic words
    ("Bithumb discloses data leak after internal system breach: Report",
     "Coinbase Customer Data Hit in Analytics Vendor Breach, Funds 'Not Impacted'"),
    # Product launch vs criminal case about the same company
    ("Phantom launches Kalshi-powered prediction markets for US users",
     "Utah charges predictions platform Kalshi with illegal gambling"),
    # Two separate Ripple announcements
    ("Ripple, Robinhood expand partnership to bring RLUSD to retail and institutional markets",
     "Ripple expects $900 million annualized product revenue run rate as institutional business expands"),
    # Two different days' newsletters
    ("Morning Minute: Tokenized Stock Battle Looms Over Robinhood's Earnings Call",
     "Morning Minute: Robinhood Misses, But Crypto Beats and Robinhood Chain Soars"),
])
def test_v21_wrong_merges_now_separate(a, b):
    clusters = _batch((a, "Cointelegraph", "cointelegraph"), (b, "Decrypt", "decrypt"))
    assert not _together(clusters, a, b)


@pytest.mark.parametrize("a,b", [
    ("Bithumb discloses data leak after internal system breach: Report",
     "Bithumb Says Hackers Accessed Internal System, Leaking Personal Data"),
    ("Coinbase denies systems breach, says vendor exposed customer data",
     "Coinbase Customer Data Hit in Analytics Vendor Breach, Funds 'Not Impacted'"),
    ("Utah charges predictions platform Kalshi with illegal gambling",
     "Utah files criminal charges against Kalshi, executives over alleged illegal gambling"),
    ("Eighth Circuit Invalidates Federal Reserve's 21-Cent Debit Card Interchange Fee Cap, Holding Rule Unlawful",
     "Eighth Circuit strikes down Federal Reserve's 21-cent debit card fee cap"),
    ("Strategy (MSTR) Shareholders Approve Daily Dividends on Four Preferred Stocks",
     "Strategy shareholders approve daily dividends for four preferred stocks"),
])
def test_v21_correct_merges_kept(a, b):
    clusters = _batch((a, "Cointelegraph", "cointelegraph"), (b, "Decrypt", "decrypt"))
    assert _together(clusters, a, b)


# ---------------------------------------------------------------- Fed signatures
@pytest.mark.parametrize("title,sig", [
    ("Fed holds interest rates steady in last decision before midterms", "fed_decision"),
    ("Fed leaves rates unchanged as it awaits 'accumulation of more information'", "fed_decision"),
    ("10-year Treasury yield creeps higher even as Fed keeps rates the same", "fed_decision"),
    ("Live updates: Bitcoin trading just above $81,000 as Fed delivers hold on rates", "fed_decision"),
    ("Federal Reserve skips rate hike despite elevated inflation", "fed_decision"),
    ("WATCH LIVE: Federal Reserve chair holds briefing after interest rate decision", "fed_decision"),
    # previews
    ("Federal Reserve expected to hold interest rates steady in last decision before midterm elections", "fed_preview"),
    ("Fed likely to hold rates steady ahead of midterms", "fed_preview"),
    ("Federal Reserve likely to skip rate hike despite elevated inflation", "fed_preview"),
    ("5 things to watch for in this week's Federal Reserve meeting", "fed_preview"),
    ("The Federal Reserve Will Release Its Interest Rate Decision Today. Here's What to Watch.", "fed_preview"),
    # not the decision at all
    ("Fed will hold fewer interest rate meetings in coming years", None),
    ("Euro steady before ECB decision, dollar holds ground as Fed holds rates", "foreign_cb"),
    ("Construction continues on the US Federal Reserve building in Washington", None),
    ("Fed officials open door to rate cuts in December", None),
])
def test_v21_fed_signatures(title, sig):
    assert event_signature(title) == sig


def test_v21_fed_decision_one_event_even_when_coverage_spans_days():
    # Coverage arrives over ~60h; the window must roll with the latest article, not the first.
    titles = [("Fed holds interest rates steady in last decision before midterms", 60 * 60),
              ("Fed leaves rates unchanged as it awaits more information", 40 * 60),
              ("Fed Holds Rates Steady Despite Lack of Progress on Inflation", 20 * 60),
              ("Federal Reserve holds interest rates steady as midterm elections loom", 5 * 60)]
    clusters = _clusters([it(t, m, f"P{i}", feed="googlenews") for i, (t, m) in enumerate(titles)])
    assert len(clusters) == 1


def test_v21_preview_and_decision_are_separate_events():
    clusters = _clusters([it("Fed likely to hold rates steady ahead of midterms", 30 * 60, "The Hill", feed="googlenews"),
                          it("Fed holds interest rates steady in last decision before midterms", 10 * 60, "Guardian", feed="googlenews")])
    assert len(clusters) == 2


# ---------------------------------------------------------------- impact / categories
@pytest.mark.parametrize("title", [
    "Construction continues on the US Federal Reserve building in Washington",
    "Eighth Circuit strikes down Federal Reserve's 21-cent debit card fee cap",
    "Warsh to attend Fed jobs and productivity task force hearing to refine views",
])
def test_v21_non_policy_fed_news_is_low_impact(title):
    e = ev(it(title, publisher="Reuters", feed="googlenews"))
    assert "fed_rates" not in e["categories"] and e["impact"] == "low"


def test_v21_media_advisory_is_low():
    e = ev(it("Federal Reserve meeting: Rice experts available to discuss interest rates, inflation and the economy",
              publisher="Rice", feed="googlenews"))
    assert e["impact"] == "low" and e["sentiment"] == "neutral"


def test_v21_incidental_fomc_mention_does_not_make_it_fed_news():
    a = analyse_item(it("Bitcoin price bounce rejected at 50-day trend line ahead of FOMC, earnings"))
    assert "fed_rates" not in a["categories"] and "price_commentary" in a["categories"]


def test_v21_preview_is_speculative_low():
    e = ev(it("5 things to watch for in this week's Federal Reserve meeting", publisher="PBS", feed="googlenews"))
    assert e["verification"] == "speculative" and e["impact"] == "low"


@pytest.mark.parametrize("title,must_have,must_not", [
    ("Strategy (MSTR) Shareholders Approve Daily Dividends on Four Preferred Stocks", None, "equities"),
    ("Federal Reserve expected to hold interest rates steady in last decision before midterm elections", None, "geopolitics"),
    ("Bitcoin Treasury Datavault AI Says It Can't Access Its BTC, Stock Tanks", "security_incident", None),
    ("Tampered Ledger wallets cost Singapore users $2.7M: Police", "security_incident", None),
])
def test_v21_categories(title, must_have, must_not):
    cats = analyse_item(it(title))["categories"]
    if must_have:
        assert must_have in cats, cats
    if must_not:
        assert must_not not in cats, cats


# ---------------------------------------------------------------- sentiment
@pytest.mark.parametrize("title", [
    "CFTC sues Utah to block action against prediction markets",
    "CFTC sues Utah as battle over prediction market regulation intensifies",
])
def test_v21_cftc_defending_markets_is_not_bearish(title):
    # Reviewer's expectation: neutral to mildly bullish. Must lean positive, never bearish.
    e = ev(it(title))
    assert e["sentiment"] in ("neutral", "bullish") and e["score"] > 0


@pytest.mark.parametrize("title,expected", [
    ("Federal Reserve likely to skip rate hike despite elevated inflation", "bearish"),
    ("Fed holds off on another interest rate hike despite persistent inflation", "bearish"),
    ("Kevin Warsh Admits Rising Oil Prices Are Fueling Inflation Expectations", "bearish"),
    ("Bitcoin Treasury Datavault AI Says It Can't Access Its BTC, Stock Tanks", "bearish"),
    ("Tampered Ledger wallets cost Singapore users $2.7M: Police", "bearish"),
])
def test_v21_sentiment(title, expected):
    e = ev(it(title))
    assert e["sentiment"] == expected, (title, e["score"], e["btc_impact"])


def test_v21_skip_hike_reason_is_hawkish_hold_not_rate_hike():
    e = ev(it("Federal Reserve likely to skip rate hike despite elevated inflation"))
    assert e["btc_impact"][0].startswith("Rates held while inflation stays elevated")
    assert not any("Higher rates / hawkish" in w for w in e["btc_impact"])


def test_v21_cftc_suit_not_explained_as_enforcement():
    e = ev(it("CFTC sues Utah to block action against prediction markets"))
    assert not any("Enforcement or legal action" in w for w in e["btc_impact"])


def test_v21_small_sale_is_weaker():
    small = ev(it("Ark Invest sells $15.9 million of its own Bitcoin ETF shares"))
    large = ev(it("Whale sells $900 million of bitcoin in one day"))
    assert abs(small["score"]) < abs(large["score"])
    assert small["impact"] in ("low", "medium")


def test_v21_offtopic_items_from_crypto_feeds_are_dropped():
    items = [it("Chip Maker SK Hynix Posts Record Quarter as AI Memory Demand Overwhelms Supply", feed="decrypt"),
             it("AI Security Startup Upwind Raises $400M at $3.8B Valuation", feed="decrypt",
                summary="The startup's technical analysis platform targets cloud security."),
             it("Bitcoin ETFs add $137M, pushing October back into the green")]
    events, summary = analyse(items, FIXED_NOW)
    assert len(events) == 1 and summary["excluded"]["off_topic"] == 2


# ================================================================ v2.2 (third live review)
@pytest.mark.parametrize("title,summary,feed", [
    ("Strategy (MSTR) Shareholders Approve Daily Dividends on Four Preferred Stocks", "", "bitcoinmagazine"),
    ("Luxor Announces $16M Funding Round to Scale AI Compute, Derivatives",
     "The bitcoin mining software firm raised new capital.", "bitcoinmagazine"),
    ("Fed Holds Fire as Warsh Telegraphs More Hikes Without Saying It", "", "googlenews"),
    ("Fed hits pause button ahead of midterms", "", "googlenews"),
    ("Did the Fed just make a huge mistake?", "", "googlenews"),
    ("Galaxy Digital swings to $323M profit, unveils $200M buyback", "", "coindesk"),
    ("Euronext halts CoinShares' Zcash ETP", "", "theblock"),
    ("Zcash co-founder proposes ending grants subsidy", "", "decrypt"),
])
def test_v22_relevant_items_not_excluded(title, summary, feed):
    events, summary_ = analyse([it(title, summary=summary, feed=feed)], FIXED_NOW)
    assert len(events) == 1, summary_["excluded"]


def test_v22_offtopic_still_excluded():
    items = [it("Chip Maker SK Hynix Posts Record Quarter as AI Memory Demand Overwhelms Supply", feed="decrypt"),
             it("Readers sound off on capital's control, Flavio Bolsonaro and Bob Woodward", feed="googlenews")]
    events, s_ = analyse(items, FIXED_NOW)
    assert events == [] and s_["excluded"]["off_topic"] == 2


@pytest.mark.parametrize("title,sig", [
    ("Fed's hawkish rate hold keeps pressure on Bitcoin: Analysts", "fed_decision"),
    ("Fed's Rate Hold Sends Bond Yields in Different Directions", "fed_decision"),
    ("Despite pausing rate hikes, 'inflation is too high': Fed chair", "fed_decision"),
    ("Fed Holds Fire as Warsh Telegraphs More Hikes Without Saying It", "fed_decision"),
    ("Fed hits pause button ahead of midterms", "fed_decision"),
    ("Fed Rate Decision: What Markets and Bitcoin Traders Can Expect", "fed_preview"),
    ("Fed's hawkish hold strengthens case for BOK's hike in November", "foreign_cb"),
    ("United States Dollar Index holds gains ahead of US PCE inflation data", None),
])
def test_v22_fed_signatures(title, sig):
    assert event_signature(title) == sig


def test_v22_foreign_cb_story_never_joins_fed_group():
    items = [it("Fed holds interest rates steady in last decision before midterms", 120, "Guardian", feed="googlenews"),
             it("Fed leaves rates unchanged as it awaits more information", 110, "Reuters", feed="googlenews"),
             it("Euro steady before ECB decision, dollar holds ground as Fed stands pat", 100, "Reuters FX", feed="googlenews")]
    clusters = _clusters(items)
    fed = [c for c in clusters if len(c) == 2]
    assert fed and all("ECB" not in m["title"] for m in fed[0])


def test_v22_preview_superseded_after_decision():
    items = [it("Fed likely to hold rates steady ahead of midterms", 20 * 60, "The Hill", feed="googlenews"),
             it("Fed Rate Decision: What Markets and Bitcoin Traders Can Expect", 19 * 60, "Decrypt", feed="decrypt"),
             it("Fed holds interest rates steady in last decision before midterms", 10 * 60, "Guardian", feed="googlenews")]
    events, _ = analyse(items, FIXED_NOW)
    preview = [e for e in events if e["signature"] == "fed_preview"][0]
    assert preview["freshness"] == "superseded" and preview["impact"] == "low"
    assert len(preview["sources"]) == 2


def test_v22_dollar_ahead_of_pce_is_not_pce_news():
    e = ev(it("United States Dollar Index holds gains ahead of US PCE inflation data", feed="googlenews"))
    assert "inflation_macro" not in e["categories"] and e["impact"] != "high"


def test_v22_inflation_target_is_not_price_target():
    a = analyse_item(it("Fed's Warsh: There is no soft inflation target", feed="googlenews"))
    assert not a["is_speculative"] and "price_commentary" not in a["categories"]
    assert analyse_item(it("Bitwise raises bitcoin price target to $1.6 million"))["is_speculative"]


def test_v22_datavault_stories_merge_and_are_bearish():
    a = "Bitcoin Treasury Datavault AI Says It Can't Access Its BTC, Stock Tanks"
    b = "Datavault AI stock plummets 50% after firm says it cannot withdraw bitcoin"
    clusters = _batch((a, "Decrypt", "decrypt"), (b, "The Block", "theblock"))
    assert _together(clusters, a, b)
    assert ev(it(b))["sentiment"] == "bearish"
