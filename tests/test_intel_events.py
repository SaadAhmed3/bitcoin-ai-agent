"""Event grouping: pure rules for dedup, clustering, category, country, status, freshness. Fixed clocks, invented headlines
(test data only; the app never invents headlines)."""
from __future__ import annotations

import pytest
from conftest import FIXED_NOW

from services.intel_events import (CATEGORIES, FILTER_GROUPS, STATUSES, classify_category, classify_status, cluster,
                                   detect_country, freshness, normalize_title, similar, summarize_event)

HOUR = 3_600_000


def art(title, publisher="Wire", source_type="news", hours_ago=1.0, aid=None, summary="", link=None):
    i = aid or f"a{abs(hash((title, publisher))) % 10 ** 12}"
    return {"id": i, "source_id": publisher.lower(), "source_type": source_type, "publisher": publisher, "title": title,
            "link": link or f"https://{publisher.lower().replace(' ', '')}.test/{i}", "summary": summary,
            "published_at": FIXED_NOW - int(hours_ago * HOUR), "collected_at": FIXED_NOW}


# ---------------------------------------------------------------- normalise / similar
def test_normalize_removes_case_punctuation_and_publisher_suffix():
    assert normalize_title("  Fed HOLDS Rates Steady! - Reuters ") == normalize_title("Fed holds rates steady")


@pytest.mark.parametrize("a,b", [
    ("Fed holds rates steady as inflation stays high", "Federal Reserve holds interest rates steady amid high inflation"),
    ("Binance hacked, $1.5 billion stolen", "Binance users withdraw $3.6bn after largest-ever crypto hack"),
    ("Russia launches missile strikes on Kyiv", "Missile strikes hit Kyiv as Russia launches overnight attack"),
    ("Spot bitcoin ETFs see largest outflows in four months", "Bitcoin ETF investors head for the exits with largest outflows"),
])
def test_same_event_is_similar(a, b):
    assert similar(a, b)


@pytest.mark.parametrize("a,b", [
    ("Fed holds rates steady", "ECB holds rates steady"),
    ("US strikes targets in Iran", "US strikes targets in Iraq"),
    ("Binance lists new token", "Binance hacked, $1.5 billion stolen"),
    ("Oil prices rise on supply fears", "Gold prices rise on safe haven demand"),
    ("Bitcoin price rises today", "Ethereum price rises today"),
])
def test_different_events_are_not_similar(a, b):
    assert not similar(a, b)


# ---------------------------------------------------------------- cluster
TEN = ["Binance hacked, $1.5 billion stolen", "Binance hack: $1.5 billion stolen in record attack",
       "Hackers steal $1.5 billion from Binance", "Binance confirms $1.5 billion hack",
       "Record $1.5bn hack hits Binance", "Binance users withdraw billions after hack",
       "Crypto exchange Binance hacked for $1.5 billion", "Binance hack is the largest ever",
       "Largest ever crypto hack hits Binance", "Binance hack: what we know about the stolen $1.5 billion"]


def test_ten_articles_about_one_event_become_one_event():
    arts = [art(t, publisher=f"Pub{i}", hours_ago=10 - i, aid=f"a{i}") for i, t in enumerate(TEN)]
    events = cluster(arts, [])
    assert len(events) == 1 and len(events[0]["articles"]) == 10


def test_exact_duplicates_and_syndication_count_once_per_publisher():
    arts = [art("Fed holds rates steady", publisher="Wire A", aid="a1"), art("Fed holds rates steady", publisher="Wire A", aid="a2"),
            art("Fed holds rates steady", publisher="Wire B", aid="a3")]
    ev = summarize_event(cluster(arts, [])[0]["articles"], FIXED_NOW)
    assert ev["articles_count"] == 3 and ev["sources_count"] == 2


def test_unrelated_articles_stay_apart():
    arts = [art("Fed holds rates steady", aid="a1"), art("Russia launches missile strikes on Kyiv", aid="a2"),
            art("Binance hacked, $1.5 billion stolen", aid="a3")]
    assert len(cluster(arts, [])) == 3


def test_same_words_days_apart_are_new_events():
    arts = [art("Fed holds rates steady", hours_ago=24 * 45, aid="a1"), art("Fed holds rates steady", hours_ago=1, aid="a2")]
    assert len(cluster(arts, [])) == 2


def test_new_article_joins_an_existing_event_and_order_does_not_matter():
    old = [{"id": 7, "articles": [art("Fed holds rates steady", publisher="A", aid="a1", hours_ago=5)]}]
    out = cluster([art("Federal Reserve holds rates steady", publisher="B", aid="a2")], old)
    assert len(out) == 1 and out[0]["id"] == 7 and {a["id"] for a in out[0]["new"]} == {"a2"}
    shuffled = [art(t, publisher=f"Pub{i}", hours_ago=10 - i, aid=f"a{i}") for i, t in enumerate(TEN)]
    assert len(cluster(list(reversed(shuffled)), [])) == 1


# ---------------------------------------------------------------- classification
@pytest.mark.parametrize("title,expected", [
    ("Russia launches missile strikes on Kyiv", "war_geopolitics"),
    ("Israel and Hamas agree ceasefire", "war_geopolitics"),
    ("How a Democrat in Michigan rocked the party days before the US midterms", "elections_politics"),
    ("Voters head to the polls in presidential election", "elections_politics"),
    ("US inflation hits 3.6% in September", "macro"),
    ("Payrolls rise as unemployment holds at 4.2%", "macro"),
    ("Federal Reserve issues FOMC statement", "central_banks"),
    ("ECB cuts interest rates by a quarter point", "central_banks"),
    ("Brent crude jumps as OPEC cuts output", "energy_commodities"),
    ("Gold hits record high", "energy_commodities"),
    ("SEC sues crypto firm over unregistered securities", "regulation"),
    ("CFTC to Host Roundtable on 24/7 Trading", "regulation"),
    ("Spot bitcoin ETFs see largest outflows in four months", "crypto_industry"),
    ("MicroStrategy buys more bitcoin", "crypto_industry"),
    ("Binance hacked, $1.5 billion stolen", "crypto_incident"),
    ("DeFi protocol drained in $40m exploit", "crypto_incident"),
    ("Local bakery wins national award", "other"),
])
def test_category(title, expected):
    assert classify_category(title) == expected
    assert expected in CATEGORIES


def test_incident_outranks_industry_and_title_outranks_summary():
    assert classify_category("Bitcoin exchange hacked") == "crypto_incident"
    assert classify_category("Bakery opens", "the owner mentioned inflation") == "macro"       # summary only as a fallback
    assert classify_category("Gold hits record high", "analysts mention inflation") == "energy_commodities"


def test_word_boundaries_avoid_false_matches():
    assert classify_category("Warning for Warren Buffett fans") != "war_geopolitics"
    assert classify_category("Secretary visits school") != "regulation"       # "sec" inside a word
    assert detect_country("Chinatown bakery opens in Russiaville") == "Global"


def test_filter_groups_cover_the_monitors():
    assert FILTER_GROUPS["war"] == ("war_geopolitics",) and FILTER_GROUPS["elections"] == ("elections_politics",)
    assert set(FILTER_GROUPS["macro"]) == {"macro", "central_banks", "energy_commodities"}
    assert set(FILTER_GROUPS["crypto"]) == {"crypto_industry", "crypto_incident", "regulation"}


@pytest.mark.parametrize("text,country", [
    ("Russia launches missile strikes on Kyiv", "Russia"), ("Beijing announces new stimulus", "China"),
    ("Fed holds rates steady", "United States"), ("ECB cuts rates", "Euro area"), ("Bakery wins award", "Global"),
    ("Japan's BoJ raises rates", "Japan"), ("UK inflation rises", "United Kingdom"),
])
def test_country(text, country):
    assert detect_country(text) == country


def test_country_default_for_official_sources():
    assert detect_country("Agency announces new rule", default="United States") == "United States"


# ---------------------------------------------------------------- status
def st(*arts):
    s, reason = classify_status(list(arts))
    assert s in STATUSES and reason
    return s


def test_status_official_statement_wins():
    assert st(art("Federal Reserve issues FOMC statement", publisher="Federal Reserve", source_type="official")) == "official_statement"
    assert st(art("Fed denies report", publisher="Federal Reserve", source_type="official"),
              art("Report says Fed will cut", publisher="B")) == "official_statement"


def test_status_confirmed_needs_two_independent_publishers():
    assert st(art("Binance hacked", publisher="A"), art("Binance hacked for $1bn", publisher="B")) == "confirmed"
    assert st(art("Binance hacked", publisher="A"), art("Binance hacked for $1bn", publisher="A", aid="x")) == "developing"


def test_status_single_source_is_developing():
    assert st(art("Binance hacked", publisher="A")) == "developing"


def test_status_unverified_and_speculative():
    assert st(art("Binance reportedly hacked, unconfirmed", publisher="A")) == "unverified"
    assert st(art("Rumor: ETF approval coming", publisher="A"), art("Sources say ETF approval is near", publisher="B")) == "unverified"
    assert st(art("Bitcoin could hit $200k, analyst predicts", publisher="A")) == "speculative"


def test_status_one_hedged_report_among_firm_ones_is_not_downgraded():
    assert st(art("Binance hacked", publisher="A"), art("Binance reportedly hacked", publisher="B"), art("Binance confirms hack", publisher="C")) == "confirmed"


@pytest.mark.parametrize("second", ["Binance denies hack report", "Binance says no hack happened, disputes claims", "Exchange refutes report of Binance hack"])
def test_status_conflicting_reports_are_disputed(second):
    assert st(art("Binance hacked", publisher="A"), art(second, publisher="B")) == "disputed"


# ---------------------------------------------------------------- freshness / old news
@pytest.mark.parametrize("hours,label", [(0.5, "new"), (1.99, "new"), (2.1, "recent"), (23, "recent"), (25, "earlier"), (71, "earlier"), (73, "old")])
def test_freshness(hours, label):
    assert freshness(FIXED_NOW - int(hours * HOUR), FIXED_NOW) == label


def test_old_story_stays_old_when_a_new_article_joins():
    ev = summarize_event([art("Fed holds rates steady", publisher="A", hours_ago=30, aid="a1"),
                          art("Fed holds rates steady today", publisher="B", hours_ago=1, aid="a2")], FIXED_NOW)
    assert ev["freshness"] == "earlier" and ev["first_published_at"] == FIXED_NOW - 30 * HOUR
    assert ev["last_published_at"] == FIXED_NOW - HOUR


def test_unknown_publish_time_is_flagged_not_hidden():
    a = art("Fed holds rates steady", aid="a1")
    a["published_at"] = None
    ev = summarize_event([a], FIXED_NOW)
    assert ev["date_known"] is False and ev["first_published_at"] == a["collected_at"]


# ---------------------------------------------------------------- summary
def test_summary_prefers_the_official_title_and_country():
    ev = summarize_event([art("Report: Fed holds rates", publisher="B", hours_ago=2, aid="a1"),
                          art("Federal Reserve issues FOMC statement", publisher="Federal Reserve", source_type="official", hours_ago=1, aid="a2")], FIXED_NOW)
    assert ev["title"] == "Federal Reserve issues FOMC statement" and ev["status"] == "official_statement"
    assert ev["category"] == "central_banks" and ev["country"] == "United States"


# ---------------------------------------------------------------- regressions from the first live run (2026-11-02, real headlines)
@pytest.mark.parametrize("a,b", [
    ("Federal Reserve Board announces approval of application by The Bank of Nova Scotia",
     "What the Fed's interest rate decision means for your bank accounts, loans, credit"),
    ("Federal reserve overflowing with expired PPE from pandemic, AG report finds", "Federal Reserve keeps rates unchanged despite elevated inflation"),
    ("Treasury Sanctions Facilitators of DPRK IT Worker Fraud Targeting U.S. Businesses",
     "Samara Asset Group CEO: Bitcoin Treasury Companies a Disservice to Shareholders"),
    ("Treasury Sanctions Facilitators of DPRK IT Worker Fraud Targeting U.S. Businesses",
     "Treasury Sanctions Rwandan Ministry of Defence and Financial Institution Supporting"),
])
def test_live_overmerge_cases_stay_apart(a, b):
    assert not similar(a, b)


@pytest.mark.parametrize("a,b", [
    ("Binance investigating hack as suspicious outflows reach up to $5.8 billion", "Bitcoin slides below $77,000, BNB drops amid Binance hack"),
    ("Federal Reserve keeps rates unchanged despite elevated inflation", "Fed leaves rates unchanged as it awaits more information"),
    ("Intel, IBM, Oracle and IonQ latest to object to OKX-ICE tokenized stocks", "JPMorgan, Coca-Cola, Dell also say no to OKX-ICE tokenized stock trading plan"),
])
def test_live_same_event_cases_still_merge(a, b):
    assert similar(a, b)


def test_an_official_release_only_joins_a_nearly_identical_report():
    fed = art("Federal Reserve Board announces approval of application by The Bank of Nova Scotia", publisher="Federal Reserve",
              source_type="official", aid="o1", hours_ago=3)
    press = art("Fed rate decision: what it means for bank accounts", publisher="Yahoo Finance", aid="n1", hours_ago=2)
    assert len(cluster([fed, press], [])) == 2
    same = art("Federal Reserve Board announces approval of application by The Bank of Nova Scotia", publisher="Wire", aid="n2", hours_ago=1)
    groups = cluster([fed, press, same], [])
    assert sorted(len(g["articles"]) for g in groups) == [1, 2]


def test_clusters_do_not_drift_through_a_chain():
    """Each headline shares words only with the one before it: without a limit one event would swallow them all."""
    chain = ["Binance hack drains hot wallet", "Binance hot wallet review after outage", "Binance outage hits traders in Asia",
             "Binance traders in Asia get refunds", "Binance refunds delayed for Korean users", "Binance Korean users file complaint",
             "Binance complaint reaches regulator", "Binance regulator opens new licence talks"]
    arts = [art(t, publisher=f"P{i}", hours_ago=8 - i, aid=f"c{i}") for i, t in enumerate(chain)]
    assert max(len(g["articles"]) for g in cluster(arts, [])) < len(chain)


def test_syndicated_wire_copies_are_not_independent_confirmation():
    same = "Inflation accelerated last month, highlighting affordability issues for consumers"
    a, b, c = art(same, publisher="kare11.com", aid="s1"), art(same, publisher="KTVB", aid="s2"), art(same + " - WRAL", publisher="WRAL", aid="s3")
    status, reason = classify_status([a, b, c])
    assert status == "developing" and "same wording" in reason
    assert classify_status([a, art("Inflation accelerated in September, data shows", publisher="Reuters", aid="s4")])[0] == "confirmed"


def test_country_comes_from_the_lead_headline_not_any_member():
    arts = [art("Binance drained of $5.5 billion in suspected hack", publisher="A", hours_ago=9, aid="k1"),
            art("North Korea suspected in Binance hack", publisher="B", hours_ago=8, aid="k2"),
            art("Binance hack: withdrawals resume", publisher="C", hours_ago=7, aid="k3"),
            art("Binance hack losses grow", publisher="D", hours_ago=6, aid="k4"),
            art("Binance hack: what users should do", publisher="E", hours_ago=5, aid="k5")]
    assert summarize_event(arts, FIXED_NOW)["country"] == "Global"
    fed = [art("Federal Reserve meeting: experts available", publisher="A", hours_ago=3, aid="f1"), art("Fed meeting watched in London", publisher="B", hours_ago=2, aid="f2")]
    assert summarize_event(fed, FIXED_NOW)["country"] == "United States"
    many = [art("Hack hits exchange", publisher="A", hours_ago=3, aid="m1"), art("Korean exchange hack in Seoul", publisher="B", hours_ago=2, aid="m2"),
            art("Seoul exchange hack spreads", publisher="C", hours_ago=1, aid="m3")]
    assert summarize_event(many, FIXED_NOW)["country"] == "South Korea"


@pytest.mark.parametrize("title,summary,expected", [
    ("Christmas shoebox appeal launches to help children", "The campaign asks families to fill shoeboxes.", "other"),
    ("BTCPay Server warns of hot wallet thefts running on outdated software", "", "crypto_incident"),
    ("Intel, Oracle, IBM Join Objections to OKX-ICE Tokenized Stock Venue", "", "crypto_industry"),
    ("Strategy (MSTR) Shareholders Approve Daily Dividends on Four Preferred Stocks", "", "crypto_industry"),
    ("Trump makes fevered campaign push in final weekend before election", "", "elections_politics"),
])
def test_live_category_cases(title, summary, expected):
    assert classify_category(title, summary) == expected


def _status_of(*titles):
    return classify_status([art(t, publisher=f"P{i}", aid=f"w{i}") for i, t in enumerate(titles)])[0]


def test_live_status_wording_cases():
    assert _status_of("Fed holds rates, helps Warsh deny Tehran a say over policy", "Fed holds rates steady") == "confirmed"
    assert _status_of("Coinbase denies systems breach", "Coinbase systems breach exposes customers") == "disputed"
    assert classify_status([art("Polymarket pauses trading as prediction market investigates security incident", aid="w1")])[0] == "developing"
    assert classify_status([art("Intel stock suffers worst crash as weak forecast overshadows demand", aid="w1")])[0] == "developing"
    assert classify_status([art("Bitcoin fell sharply in May, data shows", aid="w1")])[0] == "developing"
    assert classify_status([art("Tight conditions may challenge the inflation hedge", aid="w1")])[0] == "speculative"
    assert classify_status([art("Israel extradites alleged Nomad exploiter to US", aid="w1")])[0] == "developing"
    assert classify_status([art("Exchange allegedly lost client funds", aid="w1")])[0] == "unverified"


def test_a_lawsuit_alone_is_not_financial_regulation():
    assert classify_category("Judge dismisses Trump's lawsuit against Wall Street Journal over birthday book") != "regulation"
    assert classify_category("CFTC sues to block state enforcement in Utah") == "regulation"
