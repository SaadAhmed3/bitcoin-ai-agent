"""Glue between collection, analysis and market prices for the news panels."""
from __future__ import annotations

import threading
from typing import Callable

import pandas as pd

from database import Database, now_ms
from services.news_analyzer import HOUR, analyse
from services.news_collector import NewsCollector


class NewsService:
    def __init__(self, db: Database, collector: NewsCollector,
                 price_series: Callable[[], pd.Series | None] | None = None,
                 clock: Callable[[], int] = now_ms):
        self.db = db
        self.collector = collector
        self.price_series = price_series or (lambda: None)
        self.clock = clock
        self._lock = threading.Lock()  # one refresh at a time

    def refresh(self, force: bool = False) -> dict:
        with self._lock:
            return self.collector.refresh(force=force)

    def report(self, hours: int = 72, refresh: bool = True) -> dict:
        fetch = self.refresh() if refresh else None
        now = self.clock()
        # Look back slightly beyond the window so stories near the edge still cluster correctly.
        items = self.db.get_news(now - (hours + 36) * HOUR)
        try:
            prices = self.price_series()
        except Exception:
            prices = None  # news must keep working when market data is down
        events, summary = analyse(items, now, prices)
        events = [e for e in events if e["age_hours"] <= hours]
        warnings = []
        if not items:
            warnings.append("No news collected yet. Check feed health in the data-source panel.")
        failed = [r["feed"] for r in (fetch or {}).get("refreshed", []) if not r["ok"]]
        if failed:
            warnings.append("Feeds failed this refresh: " + ", ".join(failed) + ". Showing stored items.")
        if prices is None:
            warnings.append("Price data unavailable - 'priced in' check uses article age only.")
        summary["generated_at"] = now
        summary["window_hours"] = hours
        return {"events": events, "summary": summary, "warnings": warnings, "fetch": fetch}
