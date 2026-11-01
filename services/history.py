"""Download historical Binance candles (BTC/USDT by default; any USDT pair via `symbol`) into SQLite.

Uses /api/v3/klines with startTime paging (1000 candles per request), the same
validation as live data (closed candles only, malformed rows dropped), and the
client's timeouts, retries and rate limiting. Run it from scripts/download_history.py
or the dashboard. Candles go into the same table the live app uses (provider "binance").
"""
from __future__ import annotations

from typing import Callable

from config import TIMEFRAMES
from database import Database, now_ms
from services.http_client import HttpClient, ProviderError
from services.market_data import BinanceProvider, detect_gaps, validate_candles

DAY_MS = 86_400_000


def download(db: Database, http: HttpClient, timeframe: str, start_ms: int, end_ms: int | None = None,
             progress: Callable[[str], None] | None = None, clock: Callable[[], int] = now_ms,
             symbol: str = BinanceProvider.symbol) -> dict:
    """Fetch [start_ms, end_ms) for one timeframe. Returns counts and gap info."""
    provider = BinanceProvider(http)
    provider.symbol = symbol
    step = TIMEFRAMES[timeframe] * 1000
    end_ms = end_ms or clock()
    cursor, total, pages = start_ms, 0, 0
    while cursor < end_ms:
        data, _ = provider._get("/api/v3/klines", {"symbol": provider.symbol, "interval": timeframe,
                                                    "startTime": cursor, "endTime": end_ms, "limit": 1000})
        if not isinstance(data, list):
            raise ProviderError("binance: unexpected klines format")
        if not data:
            break
        raw = [{"open_time": int(k[0]), "open": k[1], "high": k[2], "low": k[3], "close": k[4],
                "volume": k[5], "close_time": int(k[6])} for k in data]
        rows, _ = validate_candles(raw, timeframe, clock())
        if rows:
            db.upsert_candles(provider.name, provider.symbol, timeframe, rows, clock())
            total += len(rows)
        pages += 1
        last_open = int(data[-1][0])
        if last_open + step <= cursor:
            break
        cursor = last_open + step
        if progress and pages % 5 == 0:
            progress(f"{timeframe}: {total} candles so far")
        if len(data) < 1000:
            break
    stored = db.get_candles_range(provider.name, provider.symbol, timeframe, start_ms, end_ms)
    return {"timeframe": timeframe, "downloaded": total, "stored_in_range": len(stored),
            "gaps": detect_gaps(stored, timeframe), "pages": pages}


def download_all(db: Database, http: HttpClient, years: float = 4.5, progress: Callable[[str], None] | None = None,
                 clock: Callable[[], int] = now_ms, timeframes: tuple[str, ...] = ("1d", "4h", "1h")) -> list[dict]:
    end = clock()
    start = end - int(years * 365 * DAY_MS)
    out = []
    for tf in timeframes:
        if progress:
            progress(f"Downloading {tf} candles...")
        out.append(download(db, http, tf, start, end, progress, clock))
    return out
