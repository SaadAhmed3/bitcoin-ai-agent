"""Global Intelligence: article sources beside the existing news feeds. Context, not part of the signal.

These go into intel_articles only. They are NOT added to news_collector.FEEDS, because everything in the news table is an
input to gate 5. Official press releases, BBC and (optionally) the GDELT DOC API; headline, short summary and link only.

Google News and the crypto feeds arrive through the existing collector and are read, never fetched here. bls.gov is never
fetched at all (BLS blocks automated clients). Each failure backs off exponentially, up to one day.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from urllib.parse import urlencode

from services.http_client import ProviderError
from services.news_collector import canonical_url, clean_text, parse_feed

MIN = 60_000
HOUR = 60 * MIN
BACKOFF_CAP_MS = 24 * HOUR
OFFICIAL_COUNTRY = "United States"

SOURCES: list[dict] = [
    {"id": "bbc_world", "name": "BBC News", "kind": "rss", "type": "news", "interval_min": 30,
     "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"id": "bbc_business", "name": "BBC News", "kind": "rss", "type": "news", "interval_min": 30,
     "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"id": "fed_press", "name": "Federal Reserve", "kind": "rss", "type": "official", "interval_min": 60,
     "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"id": "sec_press", "name": "U.S. SEC", "kind": "rss", "type": "official", "interval_min": 60,
     "url": "https://www.sec.gov/news/pressreleases.rss"},
    {"id": "cftc_general", "name": "U.S. CFTC", "kind": "rss", "type": "official", "interval_min": 60,
     "url": "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"},
    {"id": "cftc_enforcement", "name": "U.S. CFTC", "kind": "rss", "type": "official", "interval_min": 60,
     "url": "https://www.cftc.gov/RSS/RSSENF/rssenf.xml"},
    {"id": "treasury_press", "name": "U.S. Treasury", "kind": "treasury_html", "type": "official", "interval_min": 60,
     "url": "https://home.treasury.gov/news/press-releases"},
    # Optional: the app works without it. One query per hour, far below GDELT's one request per 5 seconds.
    {"id": "gdelt", "name": "GDELT", "kind": "gdelt", "type": "gdelt", "interval_min": 60, "backoff_start_min": 15,
     "url": "https://api.gdeltproject.org/api/v2/doc/doc",
     "params": {"query": '(bitcoin OR crypto OR "federal reserve" OR inflation OR war OR election OR opec) sourcelang:english',
                "mode": "ArtList", "format": "json", "maxrecords": "75", "timespan": "6h", "sort": "DateDesc"}},
]

_PRESS_RE = re.compile(r"<time[^>]*datetime=[\"']?([0-9TZ:-]+)[\"']?[^>]*>.*?"
                       r"<a[^>]*href=[\"']?(/news/press-releases/[a-z0-9-]+/?)[\"']?[^>]*>(.*?)</a>", re.S | re.I)


def backoff_ms(source: dict, failures: int) -> int:
    """15 min, 30, 60, ... (GDELT) or the source's interval, doubling; never above one day."""
    start = source.get("backoff_start_min", source.get("interval_min", 60)) * MIN
    return min(BACKOFF_CAP_MS, start * 2 ** max(0, failures - 1))


def _article(source: dict, title: str, link: str, published: int | None, collected: int, publisher: str | None = None,
             summary: str = "") -> dict:
    canon = canonical_url(link)
    return {"id": hashlib.sha1((canon or title).encode("utf-8")).hexdigest()[:20], "source_id": source["id"],
            "source_type": source.get("type", "news"), "publisher": publisher or source["name"], "title": title, "link": link,
            "summary": summary, "published_at": published, "collected_at": collected}


def parse_treasury_press(page: str, source: dict, collected: int) -> list[dict]:
    items = []
    for when, path, title in _PRESS_RE.findall(page):
        title = clean_text(html.unescape(title))
        try:
            published = int(dt.datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            published = None
        if title:
            items.append(_article(source, title, "https://home.treasury.gov" + path, published, collected))
    if not items:
        raise ProviderError("home.treasury.gov: no press releases found (page layout changed?)")
    return items


def parse_gdelt(data: dict, source: dict, collected: int) -> list[dict]:
    items = []
    for a in (data or {}).get("articles") or []:
        title, url = clean_text(a.get("title")), (a.get("url") or "").strip()
        if not title or not url.lower().startswith(("https://", "http://")):
            continue
        try:
            published = int(dt.datetime.strptime(a.get("seendate") or "", "%Y%m%dT%H%M%SZ")
                            .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        except ValueError:
            published = None
        items.append(_article(source, title, url, published, collected, publisher=clean_text(a.get("domain")) or source["name"]))
    return items


def fetch_source(source: dict, http, now: int) -> list[dict]:
    """One request. Raises ProviderError; the caller records it and backs off."""
    if source["kind"] == "gdelt":
        data, _ = http.get_json(source["url"] + "?" + urlencode(source["params"]))
        return parse_gdelt(data, source, now)
    body, _ = http.get_bytes(source["url"])
    if source["kind"] == "treasury_html":
        return parse_treasury_press(body.decode("utf-8", errors="replace"), source, now)
    items, _warnings = parse_feed(body, source, now)
    return [_article(source, i["title"], i["link"], i["published_at"], now, summary=i.get("summary") or "") for i in items]
