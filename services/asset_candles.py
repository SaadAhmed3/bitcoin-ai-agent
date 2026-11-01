"""Daily candles of the rotation assets (ETH, SOL, BNB) next to BTC (Strategy Lab round 5). Public data only.

Stored in the same candles table (provider "binance", their own symbol); BTC's rows are never touched. Two consumers:
  * candles_source(db): what the strategy forward tracker reads. "1d" -> BTCUSDT as before; "1d:ETHUSDT" -> that
    symbol. The tracker core passes its key through unchanged, so it serves several assets without being modified.
  * AssetRefresher: once a day, fetches each symbol's missing CLOSED daily candles from Binance (/api/v3/klines,
    no key). A failure is logged and retried after a back-off, never every minute; missing data stays missing (N/A).
"""
from __future__ import annotations

import logging
from typing import Callable

import pandas as pd

from database import now_ms
from services.history import download
from services.http_client import ProviderError

log = logging.getLogger(__name__)
DAY = 86_400_000
RETRY_MS = 15 * 60_000
DEFAULT_START = 1_619_827_200_000          # 2021-05-01 00:00 UTC: the first stored BTC daily candle (design window)


def split_key(key: str) -> tuple[str, str]:
    """'1d' -> ('1d', 'BTCUSDT'); '1d:ETHUSDT' -> ('1d', 'ETHUSDT')."""
    tf, _, sym = key.partition(":")
    return tf, sym or "BTCUSDT"


def candles_source(db) -> Callable[[str], list[dict]]:
    def rows(key: str) -> list[dict]:
        tf, sym = split_key(key)
        return db.get_candles_range("binance", sym, tf)
    return rows


def frames(db, keys, now: int) -> dict[str, pd.DataFrame]:
    """Closed candles (close_time < now) per key, as DataFrames (empty if none are stored)."""
    src, out = candles_source(db), {}
    for key in keys:
        df = pd.DataFrame([r for r in src(key) if r["close_time"] < now])
        out[key] = df.sort_values("open_time").reset_index(drop=True) if len(df) else df
    return out


class AssetRefresher:
    def __init__(self, db, http, symbols, start_ms: int = DEFAULT_START, clock: Callable[[], int] = now_ms):
        self.db, self.http, self.symbols, self.start_ms, self.clock = db, http, tuple(symbols), start_ms, clock
        self._next_try: dict[str, int] = {}
        self.errors: list[str] = []

    def maybe_refresh(self) -> int:
        """Fetch the missing closed daily candles of each symbol; returns how many were stored."""
        now, added = self.clock(), 0
        today = now // DAY * DAY
        for sym in self.symbols:
            stored = self.db.get_candles_range("binance", sym, "1d", max(self.start_ms, today - 3 * DAY))
            last = max((r["open_time"] for r in stored), default=None)
            if last is not None and last >= today - DAY:          # yesterday's candle is stored: up to date
                continue
            if now < self._next_try.get(sym, 0):
                continue
            if last is None:
                every = self.db.get_candles_range("binance", sym, "1d")
                last = max((r["open_time"] for r in every), default=None)
            start = self.start_ms if last is None else last + DAY
            try:
                added += download(self.db, self.http, "1d", start, now, clock=self.clock, symbol=sym)["downloaded"]
                self._next_try.pop(sym, None)
            except ProviderError as exc:
                self._next_try[sym] = now + RETRY_MS
                self.errors.append(f"{sym}: {exc}")
                log.warning("Could not refresh %s daily candles: %s", sym, exc)
        return added
