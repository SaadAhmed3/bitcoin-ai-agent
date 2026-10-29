"""Market data collection with provider fallbacks, caching and freshness checks.

Providers (all free, no API key required):
  * Binance spot public market data  -> BTC/USDT  (api.binance.com, mirror data-api.binance.vision)
  * Coinbase Exchange public data    -> BTC/USD   (api.exchange.coinbase.com)
  * CoinGecko public / Demo API      -> market cap, dominance (optional, overview only)

Rules enforced here:
  * Only fully closed candles are returned (the in-progress candle is dropped).
  * Candles from different providers are never mixed in one series.
  * A cached or old price is never labelled as fresh.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from config import TIMEFRAMES, Config
from database import Database, now_ms
from services.http_client import HttpClient, ProviderError

CANDLE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]


# --------------------------------------------------------------------------- helpers
def _iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def epoch_seconds_to_iso(seconds: int) -> str:
    """UTC ISO-8601 string for epoch seconds.

    Uses epoch + timedelta instead of datetime.fromtimestamp(), which raises
    OSError on Windows for timestamps before 1970.
    """
    return (_EPOCH + timedelta(seconds=seconds)).isoformat()


def validate_candles(rows: list[dict], timeframe: str, now: int) -> tuple[list[dict], list[str]]:
    """Drop malformed and still-open candles; sort and de-duplicate. Returns (rows, warnings)."""
    warnings: list[str] = []
    clean: dict[int, dict] = {}
    bad = 0
    open_dropped = 0
    for r in rows:
        try:
            o, h, l, c, v = (float(r[k]) for k in ("open", "high", "low", "close", "volume"))
            ot, ct = int(r["open_time"]), int(r["close_time"])
        except (KeyError, TypeError, ValueError):
            bad += 1
            continue
        if any(math.isnan(x) or math.isinf(x) for x in (o, h, l, c, v)):
            bad += 1
            continue
        if min(o, h, l, c) <= 0 or v < 0 or h < max(o, c, l) or l > min(o, c, h):
            bad += 1
            continue
        if ct >= now:  # candle has not closed yet
            open_dropped += 1
            continue
        clean[ot] = {"open_time": ot, "open": o, "high": h, "low": l, "close": c, "volume": v, "close_time": ct}
    if bad:
        warnings.append(f"Discarded {bad} malformed candle(s) from provider data.")
    out = [clean[k] for k in sorted(clean)]
    return out, warnings


def detect_gaps(rows: list[dict], timeframe: str) -> int:
    """Number of missing candles between the first and last returned candle."""
    if len(rows) < 2:
        return 0
    step = TIMEFRAMES[timeframe] * 1000
    missing = 0
    for prev, cur in zip(rows, rows[1:]):
        diff = cur["open_time"] - prev["open_time"]
        if diff > step:
            missing += int(diff // step) - 1
    return missing


def aggregate_candles(rows: list[dict], from_tf: str, to_tf: str) -> list[dict]:
    """Aggregate closed lower-timeframe candles (e.g. 1h -> 4h). Incomplete buckets are dropped."""
    src, dst = TIMEFRAMES[from_tf] * 1000, TIMEFRAMES[to_tf] * 1000
    per_bucket = dst // src
    buckets: dict[int, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r["open_time"] - r["open_time"] % dst, []).append(r)
    out = []
    for start in sorted(buckets):
        group = sorted(buckets[start], key=lambda x: x["open_time"])
        if len(group) != per_bucket:
            continue
        out.append({
            "open_time": start,
            "open": group[0]["open"],
            "high": max(g["high"] for g in group),
            "low": min(g["low"] for g in group),
            "close": group[-1]["close"],
            "volume": sum(g["volume"] for g in group),
            "close_time": start + dst - 1,
        })
    return out


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("time")


# --------------------------------------------------------------------------- providers
class BinanceProvider:
    name = "binance"
    symbol = "BTCUSDT"
    quote = "USDT"
    base_urls = ["https://api.binance.com", "https://data-api.binance.vision"]
    intervals = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}

    def __init__(self, http: HttpClient):
        self.http = http

    def _get(self, path: str, params: dict) -> tuple[object, float]:
        errors = []
        for base in self.base_urls:
            try:
                return self.http.get_json(base + path, params=params)
            except ProviderError as exc:
                errors.append(str(exc))
        raise ProviderError("; ".join(errors))

    def fetch_price(self) -> dict:
        data, latency = self._get("/api/v3/ticker/24hr", {"symbol": self.symbol})
        try:
            return {
                "price": float(data["lastPrice"]),
                "observed_at": int(data["closeTime"]),
                "change_24h_pct": float(data["priceChangePercent"]),
                "latency_ms": latency,
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"binance: unexpected ticker format ({exc})") from exc

    def fetch_candles(self, timeframe: str, limit: int) -> tuple[list[dict], float]:
        data, latency = self._get(
            "/api/v3/klines",
            {"symbol": self.symbol, "interval": self.intervals[timeframe], "limit": min(limit + 1, 1000)},
        )
        if not isinstance(data, list):
            raise ProviderError("binance: unexpected klines format")
        rows = []
        for k in data:
            try:
                rows.append({"open_time": int(k[0]), "open": k[1], "high": k[2], "low": k[3],
                             "close": k[4], "volume": k[5], "close_time": int(k[6])})
            except (IndexError, TypeError, ValueError):
                continue
        return rows, latency


class CoinbaseProvider:
    name = "coinbase"
    symbol = "BTC-USD"
    quote = "USD"
    base_url = "https://api.exchange.coinbase.com"
    # Coinbase supports 60, 300, 900, 3600, 21600, 86400. 4h is built from 1h.
    granularity = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
    max_per_request = 300
    max_pages = 4

    def __init__(self, http: HttpClient, clock: Callable[[], int] = now_ms):
        self.http = http
        self.clock = clock

    def fetch_price(self) -> dict:
        data, latency = self.http.get_json(f"{self.base_url}/products/{self.symbol}/ticker")
        try:
            price = float(data["price"])
            observed = _iso_to_ms(data.get("time"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"coinbase: unexpected ticker format ({exc})") from exc
        change = None
        try:
            stats, _ = self.http.get_json(f"{self.base_url}/products/{self.symbol}/stats")
            open_24h = float(stats["open"])
            if open_24h > 0:
                change = (price - open_24h) / open_24h * 100
        except (ProviderError, KeyError, TypeError, ValueError):
            pass  # 24h change is optional
        return {"price": price, "observed_at": observed, "change_24h_pct": change, "latency_ms": latency}

    def _fetch_raw(self, gran: int, count: int) -> tuple[list[dict], float]:
        rows: list[dict] = []
        total_latency = 0.0
        end_s = self.clock() // 1000
        pages = min(self.max_pages, math.ceil(count / self.max_per_request))
        for _ in range(pages):
            start_s = max(0, end_s - gran * self.max_per_request)
            params = {
                "granularity": gran,
                "start": epoch_seconds_to_iso(start_s),
                "end": epoch_seconds_to_iso(end_s),
            }
            data, latency = self.http.get_json(f"{self.base_url}/products/{self.symbol}/candles", params=params)
            total_latency += latency
            if not isinstance(data, list):
                raise ProviderError("coinbase: unexpected candles format")
            for c in data:  # [time, low, high, open, close, volume]
                try:
                    t = int(c[0]) * 1000
                    rows.append({"open_time": t, "low": c[1], "high": c[2], "open": c[3],
                                 "close": c[4], "volume": c[5], "close_time": t + gran * 1000 - 1})
                except (IndexError, TypeError, ValueError):
                    continue
            if len(data) < self.max_per_request:
                break
            end_s = start_s
        return rows, total_latency

    def fetch_candles(self, timeframe: str, limit: int) -> tuple[list[dict], float]:
        if timeframe == "4h":
            raw, latency = self._fetch_raw(3600, (limit + 1) * 4)
            closed, _ = validate_candles(raw, "1h", self.clock())
            return aggregate_candles(closed, "1h", "4h"), latency
        return self._fetch_raw(self.granularity[timeframe], limit + 1)


class CoinGeckoProvider:
    name = "coingecko"
    base_url = "https://api.coingecko.com/api/v3"

    def __init__(self, http: HttpClient, demo_key: str = ""):
        self.http = http
        self.headers = {"x-cg-demo-api-key": demo_key} if demo_key else None

    def fetch_overview(self) -> dict:
        price, latency = self.http.get_json(
            f"{self.base_url}/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd", "include_market_cap": "true",
                    "include_24hr_vol": "true", "include_24hr_change": "true", "include_last_updated_at": "true"},
            headers=self.headers,
        )
        try:
            btc = price["bitcoin"]
            out = {
                "price_usd": float(btc["usd"]),
                "market_cap_usd": float(btc["usd_market_cap"]),
                "volume_24h_usd": float(btc.get("usd_24h_vol") or 0) or None,
                "change_24h_pct": float(btc["usd_24h_change"]) if btc.get("usd_24h_change") is not None else None,
                "observed_at": int(btc["last_updated_at"]) * 1000 if btc.get("last_updated_at") else None,
                "btc_dominance_pct": None,
                "latency_ms": latency,
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"coingecko: unexpected price format ({exc})") from exc
        try:
            glob, _ = self.http.get_json(f"{self.base_url}/global", headers=self.headers)
            out["btc_dominance_pct"] = float(glob["data"]["market_cap_percentage"]["btc"])
        except (ProviderError, KeyError, TypeError, ValueError):
            pass  # dominance is optional
        return out


# --------------------------------------------------------------------------- service
@dataclass
class CandleResult:
    timeframe: str
    df: pd.DataFrame
    provider: str | None = None
    symbol: str | None = None
    quote: str | None = None
    fetched_at: int | None = None
    from_cache: bool = False
    is_stale: bool = False
    gaps: int = 0
    warnings: list[str] = field(default_factory=list)

    def meta(self) -> dict:
        last_close = int(self.df["close_time"].iloc[-1]) if not self.df.empty else None
        return {
            "timeframe": self.timeframe, "provider": self.provider, "symbol": self.symbol,
            "quote": self.quote, "fetched_at": self.fetched_at, "from_cache": self.from_cache,
            "is_stale": self.is_stale, "gaps": self.gaps, "count": int(len(self.df)),
            "last_closed_candle_close": last_close, "warnings": self.warnings,
        }


class MarketDataService:
    def __init__(self, db: Database, config: Config, http: HttpClient | None = None,
                 providers: list | None = None, overview_provider: CoinGeckoProvider | None = None,
                 clock: Callable[[], int] = now_ms):
        self.db = db
        self.config = config
        self.clock = clock
        self.http = http or HttpClient(
            timeout=config.request_timeout, retries=config.max_retries, backoff=config.retry_backoff,
            min_interval=config.min_request_interval, user_agent=config.user_agent,
        )
        if providers is None:
            registry = {"binance": BinanceProvider(self.http), "coinbase": CoinbaseProvider(self.http, clock)}
            providers = [registry[p] for p in config.providers if p in registry]
        self.providers = providers
        self.overview_provider = overview_provider or CoinGeckoProvider(self.http, config.coingecko_demo_key)

    # ---------------------------------------------------------------- price
    def get_price(self, force: bool = False) -> dict:
        now = self.clock()
        cached = self.db.cache_get("price:latest")
        if cached and not force and now - cached[1] < self.config.price_cache_seconds * 1000:
            return self._decorate_price(cached[0], now, from_cache=True)

        errors = []
        for p in self.providers:
            try:
                q = p.fetch_price()
            except ProviderError as exc:
                self.db.record_health(p.name, False, error=str(exc))
                errors.append(f"{p.name}: {exc}")
                continue
            self.db.record_health(p.name, True, latency_ms=q.get("latency_ms"))
            record = {"price": q["price"], "provider": p.name, "symbol": p.symbol, "quote": p.quote,
                      "observed_at": q.get("observed_at"), "fetched_at": now,
                      "change_24h_pct": q.get("change_24h_pct")}
            self.db.save_price(p.name, p.symbol, p.quote, q["price"], q.get("observed_at"), now)
            self.db.cache_set("price:latest", record, now)
            out = self._decorate_price(record, now, from_cache=False)
            if errors:
                out["warnings"].append("Primary source failed; using fallback " + p.name + ".")
            return out

        if cached:
            out = self._decorate_price(cached[0], now, from_cache=True)
            out["warnings"].append("All price sources failed. Showing last cached price - NOT current.")
            out["is_stale"] = True
            out["status"] = "cached_fallback"
            out["errors"] = errors
            return out
        return {"price": None, "status": "unavailable", "is_stale": True, "errors": errors,
                "warnings": ["No price source is reachable and no cached price exists."]}

    def _decorate_price(self, record: dict, now: int, from_cache: bool) -> dict:
        out = dict(record)
        ref = record.get("observed_at") or record.get("fetched_at")
        age = (now - ref) / 1000 if ref else None
        out["age_seconds"] = round(age, 1) if age is not None else None
        out["from_cache"] = from_cache
        out["is_stale"] = age is None or age > self.config.price_stale_seconds
        out["status"] = "stale" if out["is_stale"] else "fresh"
        out["warnings"] = []
        if out["is_stale"]:
            out["warnings"].append(f"Price is {int(age or 0)}s old - treat as delayed.")
        return out

    # --------------------------------------------------------------- candles
    def get_candles(self, timeframe: str, limit: int | None = None, force: bool = False) -> CandleResult:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        limit = limit or self.config.candle_limit
        now = self.clock()
        ttl = self.config.candle_cache_seconds(timeframe) * 1000
        errors: list[str] = []

        for p in self.providers:
            last_fetch = self.db.latest_candle_fetch(p.name, p.symbol, timeframe)
            if not force and last_fetch and now - last_fetch < ttl:
                return self._build_result(p, timeframe, limit, now, from_cache=True, warnings=[])
            try:
                raw, latency = p.fetch_candles(timeframe, limit)
            except ProviderError as exc:
                self.db.record_health(p.name, False, error=str(exc))
                errors.append(f"{p.name}: {exc}")
                continue
            rows, warns = validate_candles(raw, timeframe, now)
            if not rows:
                self.db.record_health(p.name, False, error="no usable closed candles")
                errors.append(f"{p.name}: no usable closed candles")
                continue
            self.db.record_health(p.name, True, latency_ms=latency)
            self.db.upsert_candles(p.name, p.symbol, timeframe, rows, now)
            if errors:
                warns.append("Primary source failed; candles from fallback " + p.name + ".")
            return self._build_result(p, timeframe, limit, now, from_cache=False, warnings=warns)

        # All providers failed - use whatever is cached, clearly marked.
        for p in self.providers:
            if self.db.latest_candle_fetch(p.name, p.symbol, timeframe):
                res = self._build_result(p, timeframe, limit, now, from_cache=True,
                                         warnings=["All candle sources failed. Showing cached candles."])
                res.is_stale = True
                return res
        return CandleResult(timeframe=timeframe, df=rows_to_frame([]), is_stale=True,
                            warnings=["No candle data available. " + " | ".join(errors)])

    def _build_result(self, p, timeframe: str, limit: int, now: int, from_cache: bool,
                      warnings: list[str]) -> CandleResult:
        rows = self.db.get_candles(p.name, p.symbol, timeframe, limit)
        rows = [{k: r[k] for k in CANDLE_COLUMNS} for r in rows]
        fetched = self.db.latest_candle_fetch(p.name, p.symbol, timeframe)
        res = CandleResult(timeframe=timeframe, df=rows_to_frame(rows), provider=p.name, symbol=p.symbol,
                           quote=p.quote, fetched_at=fetched, from_cache=from_cache,
                           gaps=detect_gaps(rows, timeframe), warnings=list(warnings))
        if res.gaps:
            res.warnings.append(f"{res.gaps} missing candle(s) detected in the series.")
        if rows:
            interval_ms = TIMEFRAMES[timeframe] * 1000
            if now - rows[-1]["close_time"] > 2 * interval_ms:
                res.is_stale = True
                res.warnings.append("Latest closed candle is older than two intervals - data is stale.")
        return res

    # -------------------------------------------------------------- overview
    def get_overview(self, force: bool = False) -> dict:
        now = self.clock()
        cached = self.db.cache_get("overview:coingecko")
        if cached and not force and now - cached[1] < self.config.overview_cache_seconds * 1000:
            return {**cached[0], "fetched_at": cached[1], "from_cache": True, "status": "ok"}
        try:
            data = self.overview_provider.fetch_overview()
        except ProviderError as exc:
            self.db.record_health("coingecko", False, error=str(exc))
            if cached:
                return {**cached[0], "fetched_at": cached[1], "from_cache": True, "status": "cached_fallback",
                        "warnings": ["CoinGecko unreachable; showing cached overview."]}
            return {"status": "unavailable", "warnings": [f"CoinGecko unavailable: {exc}"]}
        self.db.record_health("coingecko", True, latency_ms=data.get("latency_ms"))
        self.db.cache_set("overview:coingecko", data, now)
        return {**data, "fetched_at": now, "from_cache": False, "status": "ok"}


# Documentation surfaced on the dashboard's data-source panel.
SOURCE_DOCS = [
    {"source": "binance", "provides": "BTC/USDT price, 24h change, OHLCV (5m-1d)", "api_key": "No",
     "limits": "Weight-based (6,000/min per IP); app uses a tiny fraction",
     "refresh": "Price ~10s cache; candles every 1/5 of interval", "delay": "Near real-time (exchange data)",
     "failure": "Tries data-api.binance.vision mirror, then Coinbase, then cached data marked stale",
     "notes": "Unavailable in some jurisdictions (e.g. HTTP 451). Public market data only."},
    {"source": "coinbase", "provides": "BTC/USD price, OHLCV (4h built from 1h)", "api_key": "No",
     "limits": "Public endpoints ~10 req/s per IP; 300 candles/request", "refresh": "Same as above",
     "delay": "Near real-time (exchange data)", "failure": "Falls back to cached data marked stale",
     "notes": "Quoted in USD (Binance quotes USDT) - series are never mixed."},
    {"source": "coingecko", "provides": "Market cap, 24h volume, BTC dominance", "api_key": "Optional free Demo key",
     "limits": "Public ~5-15 calls/min; Demo key ~30 calls/min, monthly cap", "refresh": "5 min cache",
     "delay": "Aggregated; can lag 1-5 minutes", "failure": "Panel shows cached values or 'unavailable'",
     "notes": "Attribution required by CoinGecko terms when displaying their data."},
    {"source": "binance:watchlist", "provides": "Watchlist last price + 24h change (up to 10 spot symbols)", "api_key": "No",
     "limits": "ONE batched /api/v3/ticker/24hr?symbols=[...] request (weight 2-40) per refresh; exchangeInfo checks cached 24 h",
     "refresh": "60 s cache", "delay": "Near real-time (exchange data)",
     "failure": "Coinbase <BASE>-USD stats where a product exists, else the last cached list marked stale",
     "notes": "Display only - no signals for other coins. Same public market-data terms as above."},
    {"source": "calendar:fomc", "provides": "FOMC meeting dates (rate decisions)", "api_key": "No",
     "limits": "One page fetch per day (retry after 1 h on failure)", "refresh": "Daily",
     "delay": "Published by the Fed months ahead", "failure": "Last cached copy, or an honest empty state",
     "notes": "federalreserve.gov meeting calendar (US-government work, public domain; Board credited). Page lists dates "
              "only - 2:00 p.m. ET is shown as the USUAL statement time."},
    {"source": "calendar:bls", "provides": "CPI, PPI and jobs-report release times", "api_key": "No (contact e-mail)",
     "limits": "One iCalendar fetch per day", "refresh": "Daily", "delay": "Official schedule",
     "failure": "Skipped with a reason; FOMC dates still shown",
     "notes": "Optional. BLS blocks anonymous bots (HTTP 403) and asks scripts to identify their owner: set "
              "CALENDAR_CONTACT_EMAIL in .env to enable. GDP (BEA) has no machine-readable schedule feed."},
]
