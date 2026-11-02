"""Global Intelligence: fetch, store, group and serve events beside the news engine. Context, not part of the signal.

One-way data flow: this layer reads the existing news table (read only) and its own tables. Nothing existing reads it, so it
cannot change the live signal, score, gates, strategies or paper trading.

Fetching happens only in IntelWorker, its own thread, so slow sources never delay the alert monitor. GET routes read SQLite.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from database import Database, now_ms
from services.http_client import HttpClient, ProviderError
from services.intel_events import (CATEGORIES, CATEGORY_LABELS, CLUSTER_WINDOW_MS, FILTER_GROUPS, STATUSES, cluster, freshness,
                                   summarize_event)
from services.intel_markets import IntelMarkets
from services.intel_reactions import ReactionService
from services.intel_sources import SOURCES, backoff_ms, fetch_source

log = logging.getLogger("btc_agent.intel")

LABEL = "Context, not part of the signal"
HOUR = 3_600_000
DAY = 24 * HOUR
MAX_ARTICLE_AGE_MS = 14 * DAY      # older items are not "live" events (feeds re-list old stories)
EXISTING_NEWS_WINDOW_MS = 7 * DAY
GROUPING_RULES_VERSION = 3         # raise when the rules or stored fields change: events are derived, so they are rebuilt from the articles
MAX_TITLE = 300
UNAVAILABLE_DATA = [
    {"name": "ETF flows", "reason": "No verified free source."},
    {"name": "Exchange flows", "reason": "No verified free source."},
    {"name": "Whale movements", "reason": "No verified free source."},
    {"name": "Consensus forecasts", "reason": "No verified free source, so a release cannot be called above or below expectations."},
]


class IntelService:
    def __init__(self, db: Database, http=None, markets: IntelMarkets | None = None, sources: list[dict] | None = None,
                 clock: Callable[[], int] = now_ms, fred_key: Callable[[], str] = lambda: "", calendar=None,
                 contact: Callable[[], str] = lambda: ""):
        self.db = db
        # No retries: a failure backs off instead. One request per 1.5 s per host, polite for official sites.
        self.http = http if http is not None else HttpClient(timeout=30, retries=0, min_interval=1.5,
                                                             user_agent="bitcoin-ai-agent/0.9 (local research tool)")
        self.sources = sources if sources is not None else SOURCES
        self.clock = clock
        self.markets = markets or IntelMarkets(db, self.http, fred_key=fred_key, clock=clock)
        self.reactions = ReactionService(db, calendar=calendar, clock=clock)
        self.contact = contact
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ ingest
    def add_articles(self, articles: list[dict]) -> int:
        """Store new articles. Only http(s) links, no stale items; the same id (same URL) is stored once."""
        now = self.clock()
        rows = []
        for a in articles:
            link = (a.get("link") or "").strip()
            title = " ".join(str(a.get("title") or "").split())[:MAX_TITLE]    # plain text; the page escapes it
            when = a.get("published_at") or a.get("collected_at") or now
            if not title or not link.lower().startswith(("https://", "http://")) or now - when > MAX_ARTICLE_AGE_MS:
                continue
            rows.append({**a, "title": title, "link": link})
        return self.db.intel_add_articles(rows)

    def _existing_news(self) -> list[dict]:
        """The stored crypto + Google News items, read only. The collector that fills them is not touched."""
        items = self.db.get_news(self.clock() - EXISTING_NEWS_WINDOW_MS, 2000)
        return [{"id": n["id"], "source_id": n["feed"], "source_type": "news", "publisher": n["publisher"], "title": n["title"],
                 "link": n["link"], "summary": n.get("summary") or "", "published_at": n["published_at"],
                 "collected_at": n["collected_at"]} for n in items]

    def _fetch_due(self, force: bool) -> tuple[int, dict]:
        added, results = 0, {}
        health = {h["source"]: h for h in self.db.intel_health()}
        for src in self.sources:
            now = self.clock()
            h = health.get(src["id"]) or {}
            if not force and h.get("next_attempt_at") and now < h["next_attempt_at"]:
                continue
            try:
                items = fetch_source(src, self.http, now, contact=self.contact())
            except ProviderError as exc:
                fails = int(h.get("consecutive_failures") or 0) + 1
                self.db.intel_record_health(src["id"], False, str(exc), now, now + backoff_ms(src, fails), kind="article")
                results[src["id"]] = str(exc)
                continue
            n = self.add_articles(items)
            self.db.intel_record_health(src["id"], True, None, now, now + src.get("interval_min", 60) * 60_000, kind="article", items=n)
            added += n
            results[src["id"]] = n
        return added, results

    def regroup(self) -> int:
        """Cluster the articles not yet in an event. Returns how many events were created or changed."""
        stored = self.db.cache_get("intel:grouping_version")
        if not stored or stored[0] != GROUPING_RULES_VERSION:
            self.db.intel_reset_events()                         # articles stay; only the derived grouping is redone
            self.db.cache_set("intel:grouping_version", GROUPING_RULES_VERSION, self.clock())
        pending = self.db.intel_unassigned_articles()
        if not pending:
            return 0
        oldest = min(a.get("published_at") or a["collected_at"] for a in pending)
        groups = cluster(pending, self.db.intel_open_events(oldest - CLUSTER_WINDOW_MS))
        now = self.clock()
        for g in groups:
            self.db.intel_save_event(g["id"], summarize_event(g["articles"], now), [a["id"] for a in g["new"]], now)
        return len(groups)

    def refresh(self, force: bool = False) -> dict:
        with self._lock:
            added, results = self._fetch_due(force)
            added += self.add_articles(self._existing_news())
            touched = self.regroup()
            markets = self.markets.refresh(force)
            try:
                reactions = self.reactions.update()
            except Exception as exc:       # the study must never stop fetching and grouping
                log.exception("Reaction study failed")
                reactions = {"error": type(exc).__name__}
        return {"articles_added": added, "events_touched": touched, "sources": results, "markets": markets, "reactions": reactions}

    # ------------------------------------------------------------------ read
    def events(self, group: str | None = None, category: str | None = None, status: str | None = None,
               country: str | None = None, hours: int = 168, limit: int = 100) -> dict:
        if group and group not in FILTER_GROUPS:
            raise ValueError(f"group must be one of: {', '.join(FILTER_GROUPS)}")
        if category and category not in CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(CATEGORIES)}")
        if status and status not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
        now = self.clock()
        cats = FILTER_GROUPS[group] if group else ((category,) if category else None)
        rows = self.db.intel_events(now - hours * HOUR, cats, status, country, limit)
        for e in rows:
            e["freshness"] = freshness(e["first_published_at"], now)
            e["category_label"] = CATEGORY_LABELS.get(e["category"], e["category"])
        return {"events": rows, "label": LABEL, "generated_at": now, "window_hours": hours}

    def overview(self, hours: int = 168) -> dict:
        now = self.clock()
        rows = self.db.intel_event_counts(now - hours * HOUR)
        by_category = {c: 0 for c in CATEGORIES}
        by_status = {s: 0 for s in STATUSES}
        for r in rows:
            by_category[r["category"]] = by_category.get(r["category"], 0) + r["n"]
            by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
        return {"total": sum(by_category.values()), "by_category": by_category, "category_labels": CATEGORY_LABELS,
                "by_status": by_status, "by_group": {g: sum(by_category[c] for c in cs) for g, cs in FILTER_GROUPS.items()},
                "window_hours": hours, "unavailable": UNAVAILABLE_DATA, "label": LABEL, "generated_at": now,
                "health": self.health(), "note": "Status comes from the wording and the kind of sources, not from checking the facts."}

    def health(self) -> list[dict]:
        now = self.clock()
        out = []
        for h in self.db.intel_health():
            retry = h.get("next_attempt_at")
            if h["status"] == "ok":
                text = "OK"
            elif retry and retry > now:
                text = f"Failing ({h['consecutive_failures']} in a row). Will retry in {max(1, round((retry - now) / 60_000))} min."
            else:
                text = "Failing. Will retry on the next refresh."
            out.append({**h, "status_text": text})
        return out


class IntelWorker:
    """Own background thread (never AlertMonitor's job list, whose jobs run one after another and would delay alerts).
    Started only by `python app.py`; never under tests. A failing cycle is logged and the thread goes on."""

    def __init__(self, service, interval: float = 300.0, first_delay: float = 20.0):
        self.service = service
        self.interval = interval
        self.first_delay = first_delay
        self.name = "IntelWorker"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        if self._stop.wait(self.first_delay):
            return
        while not self._stop.is_set():
            try:
                self.service.refresh()
            except Exception:
                log.exception("Global Intelligence refresh failed")
            if self._stop.wait(self.interval):
                return

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
