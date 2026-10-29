"""Shared deterministic fixtures. Nothing here touches the network.

The synthetic candles below exist ONLY for unit tests; the application never
uses generated prices.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import TIMEFRAMES, Config  # noqa: E402
from database import Database  # noqa: E402
from services.http_client import ProviderError  # noqa: E402

FIXED_NOW = 1_760_000_000_000  # fixed "current time" in ms (UTC)


def make_rows(n: int, timeframe: str = "1h", start_price: float = 60_000.0, seed: int = 7,
              end_ms: int = FIXED_NOW, include_open: bool = False) -> list[dict]:
    """Deterministic random-walk candles ending just before end_ms (all closed)."""
    rng = np.random.default_rng(seed)
    step = TIMEFRAMES[timeframe] * 1000
    last_open = (end_ms // step) * step - step  # last fully closed candle
    first_open = last_open - (n - 1) * step
    price = start_price
    rows = []
    for i in range(n + (1 if include_open else 0)):
        o = price
        c = max(100.0, o * (1 + rng.normal(0, 0.006)))
        h = max(o, c) * (1 + abs(rng.normal(0, 0.002)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.002)))
        v = float(abs(rng.normal(100, 25)) + 1)
        t = first_open + i * step
        rows.append({"open_time": t, "open": o, "high": h, "low": l, "close": c, "volume": v,
                     "close_time": t + step - 1})
        price = c
    return rows


@pytest.fixture
def config(tmp_path):
    cfg = Config()
    cfg.db_path = str(tmp_path / "test.sqlite3")
    cfg.providers = ["fake1", "fake2"]
    cfg.price_cache_seconds = 0
    return cfg


@pytest.fixture
def db(config):
    d = Database(config.db_path)
    d.init()
    return d


class FakeProvider:
    def __init__(self, name, symbol="BTCUSDT", quote="USDT", price=65_000.0, fail=False, rows=None,
                 observed_at=FIXED_NOW - 1_000):
        self.name, self.symbol, self.quote = name, symbol, quote
        self.price, self.fail, self.rows, self.observed_at = price, fail, rows, observed_at
        self.price_calls = 0
        self.candle_calls = 0

    def fetch_price(self):
        self.price_calls += 1
        if self.fail:
            raise ProviderError(f"{self.name}: timeout after 10s")
        return {"price": self.price, "observed_at": self.observed_at, "change_24h_pct": 1.5, "latency_ms": 42.0}

    def fetch_candles(self, timeframe, limit):
        self.candle_calls += 1
        if self.fail:
            raise ProviderError(f"{self.name}: HTTP 503")
        return (self.rows if self.rows is not None else make_rows(min(limit, 300), timeframe)), 10.0


class FakeOverview:
    def __init__(self, fail=False):
        self.fail = fail

    def fetch_overview(self):
        if self.fail:
            raise ProviderError("coingecko: HTTP 429")
        return {"price_usd": 65_000.0, "market_cap_usd": 1.28e12, "volume_24h_usd": 3e10,
                "change_24h_pct": 1.2, "observed_at": FIXED_NOW - 60_000, "btc_dominance_pct": 57.3,
                "latency_ms": 50.0}


def local_client(app):
    """Flask test client that behaves like the app's own JS: it sends the CSRF header on every request
    (Phase 9 rejects state-changing requests from other origins). Cross-origin tests use app.test_client()."""
    c = app.test_client()
    c.environ_base["HTTP_X_REQUESTED_WITH"] = "BTCSignalLab"
    return c
