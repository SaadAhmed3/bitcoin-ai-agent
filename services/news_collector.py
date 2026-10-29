"""RSS news collection.

Fetches publicly accessible RSS feeds, normalises entries and stores them in
SQLite. Only headline, link, publisher, times and a short summary snippet are
kept; every item links back to its original article. No paywall, login or
rate-limit is bypassed.
"""
from __future__ import annotations

import calendar
import hashlib
import html
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser

from database import Database, now_ms
from services.http_client import HttpClient, ProviderError

FEEDS: list[dict] = [
    {"id": "cointelegraph", "name": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"id": "coindesk", "name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"id": "theblock", "name": "The Block", "url": "https://www.theblock.co/rss.xml"},
    {"id": "decrypt", "name": "Decrypt", "url": "https://decrypt.co/feed"},
    {"id": "bitcoinmagazine", "name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/feed"},
    {"id": "googlenews", "name": "Google News", "aggregator": True,
     "url": "https://news.google.com/rss/search?q=Bitcoin+OR+Federal+Reserve+OR+inflation&hl=en-US&gl=US&ceid=US:en"},
]

SUMMARY_MAX = 300
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|referrer$)", re.I)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(raw: str | None, limit: int | None = None) -> str:
    if not raw:
        return ""
    text = WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", raw))).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    p = urlparse(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not TRACKING_PARAMS.match(k)])
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower() or "https", p.netloc.lower(), path, "", query, ""))


def split_publisher_suffix(title: str) -> tuple[str, str | None]:
    """Google News titles look like 'Headline - Publisher'. Returns (headline, publisher)."""
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if 0 < len(tail) <= 60 and len(head) >= 15:
            return head.strip(), tail.strip()
    return title, None


def parse_feed(content: bytes, feed: dict, collected_at: int) -> tuple[list[dict], list[str]]:
    """Parse RSS/Atom bytes into normalised items. Returns (items, warnings)."""
    parsed = feedparser.parse(content)
    warnings: list[str] = []
    if parsed.bozo and not parsed.entries:
        raise ProviderError(f"{feed['id']}: unreadable feed ({type(parsed.bozo_exception).__name__})")
    if parsed.bozo:
        warnings.append(f"{feed['id']}: feed had minor XML errors; parsed what was readable.")
    items = []
    for e in parsed.entries:
        title = clean_text(e.get("title"))
        link = e.get("link") or ""
        if not title or not link:
            continue
        publisher = feed["name"]
        if feed.get("aggregator"):
            src = e.get("source")
            src_title = src.get("title") if isinstance(src, dict) else getattr(src, "title", None)
            title, suffix = split_publisher_suffix(title)
            publisher = clean_text(src_title) or suffix or feed["name"]
        struct = e.get("published_parsed") or e.get("updated_parsed")
        published = calendar.timegm(struct) * 1000 if struct else None
        if published and published > collected_at + 10 * 60_000:
            published = None  # a publish time in the future is not trustworthy
        canon = canonical_url(link)
        items.append({
            "id": hashlib.sha1((canon or title).encode("utf-8")).hexdigest()[:20],
            "feed": feed["id"],
            "publisher": publisher,
            "title": title,
            "link": link,
            "summary": clean_text(e.get("summary") or e.get("description"), SUMMARY_MAX),
            "published_at": published,
            "collected_at": collected_at,
        })
    return items, warnings


class NewsCollector:
    def __init__(self, db: Database, http: HttpClient, feeds: list[dict] | None = None,
                 refresh_seconds: int = 600, clock: Callable[[], int] = now_ms,
                 fetcher: Callable[[dict], tuple[bytes, float]] | None = None):
        self.db = db
        self.http = http
        self.feeds = feeds if feeds is not None else FEEDS
        self.refresh_ms = refresh_seconds * 1000
        self.clock = clock
        self.fetcher = fetcher or (lambda f: self.http.get_bytes(f["url"]))

    def _due(self, feed: dict, now: int) -> bool:
        last = self.db.cache_get(f"news:last_fetch:{feed['id']}")
        return last is None or now - last[1] >= self.refresh_ms

    def _fetch_one(self, feed: dict, now: int) -> dict:
        source = f"rss:{feed['id']}"
        # Record the attempt first so a failing feed is retried once per interval, not on every request.
        self.db.cache_set(f"news:last_fetch:{feed['id']}", {"attempt": True}, now)
        try:
            content, latency = self.fetcher(feed)
            items, warns = parse_feed(content, feed, now)
        except ProviderError as exc:
            self.db.record_health(source, False, error=str(exc))
            return {"feed": feed["id"], "ok": False, "error": str(exc), "new": 0}
        except Exception as exc:  # malformed content must never crash the app
            self.db.record_health(source, False, error=f"parse error: {exc.__class__.__name__}")
            return {"feed": feed["id"], "ok": False, "error": "parse error", "new": 0}
        self.db.record_health(source, True, latency_ms=latency)
        self.db.cache_set(f"news:last_fetch:{feed['id']}", {"count": len(items)}, now)
        new = self.db.insert_news(items)
        return {"feed": feed["id"], "ok": True, "items": len(items), "new": new, "warnings": warns,
                "empty": len(items) == 0}

    def refresh(self, force: bool = False) -> dict:
        now = self.clock()
        due = [f for f in self.feeds if force or self._due(f, now)]
        if not due:
            return {"refreshed": [], "skipped": len(self.feeds)}
        with ThreadPoolExecutor(max_workers=min(6, len(due))) as pool:
            results = list(pool.map(lambda f: self._fetch_one(f, now), due))
        return {"refreshed": results, "skipped": len(self.feeds) - len(due), "at": now}
