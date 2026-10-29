"""Watchlist (Phase 8): last price and 24h change for up to 10 Binance spot symbols. DISPLAY ONLY - no signals.

* Symbols are validated against Binance exchangeInfo (status TRADING); results are cached 24 h per symbol.
* Prices come from ONE batched public request (GET /api/v3/ticker/24hr?symbols=[...]) cached >= 60 s.
* If Binance fails, Coinbase (<BASE>-USD /stats, quoted in USD) is tried per symbol where a product exists;
  otherwise the last cached tickers are shown with a stale label.
"""
from __future__ import annotations

import json
from typing import Callable

from database import Database, now_ms
from services.http_client import HttpClient, ProviderError

BINANCE_BASES = ["https://api.binance.com", "https://data-api.binance.vision"]
COINBASE_BASE = "https://api.exchange.coinbase.com"
TICKER_CACHE_S = 60
EXCHANGE_INFO_CACHE_S = 24 * 3600
STALE_AFTER_S = 300
QUOTES = ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "EUR", "TRY", "BRL")


def _binance(http: HttpClient, path: str, params: dict) -> tuple[object, float]:
    errors = []
    for base in BINANCE_BASES:
        try:
            return http.get_json(base + path, params=params)
        except ProviderError as exc:
            errors.append(str(exc))
    raise ProviderError("; ".join(errors))


def coinbase_product(symbol: str) -> str | None:
    """BTCUSDT -> BTC-USD (USD-stablecoin pairs only; Coinbase quotes in USD)."""
    for q in ("USDT", "USDC", "FDUSD"):
        if symbol.endswith(q) and len(symbol) > len(q):
            return f"{symbol[:-len(q)]}-USD"
    return None


class WatchlistService:
    def __init__(self, db: Database, http: HttpClient, symbols: Callable[[], list[str]],
                 clock: Callable[[], int] = now_ms):
        self.db = db
        self.http = http
        self.symbols = symbols
        self.clock = clock

    # ---------------------------------------------------------------- validation
    def unknown_symbols(self, symbols: list[str]) -> list[str]:
        """Symbols that are not TRADING Binance spot pairs. Raises ProviderError if Binance can't be reached
        for symbols that aren't cached yet (so a network failure is never mistaken for 'unknown')."""
        now = self.clock()
        unknown, todo = [], []
        for s in symbols:
            hit = self.db.cache_get(f"binance:exchangeinfo:{s}")
            if hit and now - hit[1] < EXCHANGE_INFO_CACHE_S * 1000:
                if not hit[0]:
                    unknown.append(s)
            else:
                todo.append(s)
        if not todo:
            return unknown
        try:
            data, _ = _binance(self.http, "/api/v3/exchangeInfo", {"symbols": json.dumps(todo, separators=(",", ":"))})
            found = {x.get("symbol"): x.get("status") == "TRADING" for x in (data or {}).get("symbols", [])}
        except ProviderError as exc:
            if "HTTP 400" not in str(exc):
                raise
            found = {}                     # one invalid symbol rejects the batch: check them one by one
            for s in todo:
                try:
                    d, _ = _binance(self.http, "/api/v3/exchangeInfo", {"symbol": s})
                    found[s] = any(x.get("status") == "TRADING" for x in (d or {}).get("symbols", []))
                except ProviderError as e:
                    if "HTTP 400" not in str(e):
                        raise
                    found[s] = False
        for s in todo:
            ok = bool(found.get(s))
            self.db.cache_set(f"binance:exchangeinfo:{s}", ok, now)
            if not ok:
                unknown.append(s)
        return unknown

    # ---------------------------------------------------------------- prices
    def _fetch_binance(self, symbols: list[str]) -> list[dict]:
        data, latency = _binance(self.http, "/api/v3/ticker/24hr",
                                 {"symbols": json.dumps(symbols, separators=(",", ":"))})
        if not isinstance(data, list):
            raise ProviderError("binance: unexpected ticker format")
        self.db.record_health("binance:watchlist", True, latency_ms=latency)
        by = {d.get("symbol"): d for d in data}
        rows = []
        for s in symbols:
            d = by.get(s)
            if not d:
                rows.append({"symbol": s, "price": None, "change_24h_pct": None, "observed_at": None,
                             "provider": "binance", "quote": None, "error": "not returned by Binance"})
                continue
            rows.append({"symbol": s, "price": float(d["lastPrice"]), "change_24h_pct": float(d["priceChangePercent"]),
                         "observed_at": int(d["closeTime"]), "provider": "binance",
                         "quote": next((q for q in QUOTES if s.endswith(q)), None)})
        return rows

    def _fetch_coinbase(self, symbols: list[str]) -> list[dict]:
        rows = []
        for s in symbols:
            pid = coinbase_product(s)
            if not pid:
                rows.append({"symbol": s, "price": None, "change_24h_pct": None, "observed_at": None,
                             "provider": "coinbase", "quote": None, "error": "no Coinbase USD product"})
                continue
            try:
                st, _ = self.http.get_json(f"{COINBASE_BASE}/products/{pid}/stats")
                last, open_ = float(st["last"]), float(st["open"])
                rows.append({"symbol": s, "price": last, "change_24h_pct": (last / open_ - 1) * 100 if open_ else None,
                             "observed_at": self.clock(), "provider": "coinbase", "quote": "USD", "product": pid})
            except (ProviderError, KeyError, TypeError, ValueError) as exc:
                rows.append({"symbol": s, "price": None, "change_24h_pct": None, "observed_at": None,
                             "provider": "coinbase", "quote": None, "error": str(exc)[:120]})
        return rows

    def get(self, force: bool = False) -> dict:
        symbols = list(self.symbols())
        now = self.clock()
        key = "watchlist:tickers:" + ",".join(symbols)
        cached = self.db.cache_get(key)
        if cached and not force and now - cached[1] < TICKER_CACHE_S * 1000:
            return self._decorate(cached[0], cached[1], now, from_cache=True)
        warnings = []
        try:
            rows = self._fetch_binance(symbols)
        except ProviderError as exc:
            self.db.record_health("binance:watchlist", False, error=str(exc))
            warnings.append(f"Binance failed ({exc}).")
            rows = None
            if not cached:
                rows = self._fetch_coinbase(symbols)
                if all(r["price"] is None for r in rows):
                    rows = None
                else:
                    warnings.append("Using Coinbase fallback (USD quotes) where a product exists.")
        if rows is None:
            if cached:
                out = self._decorate(cached[0], cached[1], now, from_cache=True)
                out["warnings"] = warnings + ["Showing the last cached watchlist - NOT current."]
                out["status"] = "cached_fallback"
                return out
            return {"symbols": symbols, "rows": [], "status": "unavailable", "fetched_at": None, "is_stale": True,
                    "warnings": warnings + ["No watchlist source is reachable and nothing is cached."],
                    "note": "Display only - the app makes no signals for these coins."}
        self.db.cache_set(key, {"rows": rows, "warnings": warnings}, now)
        return self._decorate({"rows": rows, "warnings": warnings}, now, now, from_cache=False)

    def _decorate(self, payload: dict, fetched_at: int, now: int, from_cache: bool) -> dict:
        age = (now - fetched_at) / 1000
        rows = []
        for r in payload["rows"]:
            r = dict(r)
            r["age_seconds"] = round((now - r["observed_at"]) / 1000, 1) if r.get("observed_at") else None
            r["is_stale"] = r["age_seconds"] is None or r["age_seconds"] > STALE_AFTER_S
            rows.append(r)
        stale = age > STALE_AFTER_S
        return {"symbols": [r["symbol"] for r in rows], "rows": rows, "fetched_at": fetched_at, "age_seconds": round(age, 1),
                "from_cache": from_cache, "is_stale": stale, "status": "stale" if stale else "fresh",
                "warnings": list(payload.get("warnings") or []),
                "note": "Display only - the app makes no signals for these coins. Binance quotes in USDT."}
