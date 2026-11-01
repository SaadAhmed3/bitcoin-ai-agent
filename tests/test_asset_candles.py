"""Daily candles of the rotation assets (ETH, SOL, BNB) from Binance's public klines endpoint, and the candle source
that serves "1d:<SYMBOL>" to the strategy forward tracker without changing its core."""
from __future__ import annotations

D = 86_400_000
T0 = 1_700_000_000_000 // D * D


class FakeHttp:
    """Binance klines for any symbol: close = 100 + day index; records every request."""

    def __init__(self, first=T0, fail=False):
        self.first, self.fail, self.calls = first, fail, []

    def get_json(self, url, params=None):
        from services.http_client import ProviderError
        self.calls.append((url, dict(params or {})))
        if self.fail:
            raise ProviderError("offline")
        start = max(params["startTime"], self.first)
        out, t = [], start // D * D
        while t + D - 1 < params["endTime"] and len(out) < params["limit"]:
            i = (t - self.first) // D
            out.append([t, str(100 + i), str(101 + i), str(99 + i), str(100 + i), "10", t + D - 1])
            t += D
        return out, 1.0


def test_download_stores_another_symbol_under_its_own_name(db):
    from services.history import download
    http = FakeHttp()
    r = download(db, http, "1d", T0, T0 + 30 * D, clock=lambda: T0 + 40 * D, symbol="ETHUSDT")
    assert r["stored_in_range"] == 30 and all(c[1]["symbol"] == "ETHUSDT" for c in http.calls)
    assert len(db.get_candles_range("binance", "ETHUSDT", "1d")) == 30
    assert db.get_candles_range("binance", "BTCUSDT", "1d") == []                     # BTC untouched


def test_candle_source_serves_btc_and_tf_colon_symbol(db):
    from services.asset_candles import candles_source
    db.upsert_candles("binance", "SOLUSDT", "1d", [{"open_time": T0, "open": 1, "high": 2, "low": 0.5, "close": 1.5,
                                                    "volume": 1, "close_time": T0 + D - 1}], 1)
    src = candles_source(db)
    assert [r["close"] for r in src("1d:SOLUSDT")] == [1.5] and src("1d") == [] and src("1d:ETHUSDT") == []


def test_refresher_fetches_only_missing_closed_days_and_backs_off_on_failure(db):
    from services.asset_candles import AssetRefresher
    now = [T0 + 10 * D + 3_600_000]                                                   # 01:00 on day 10
    http = FakeHttp()
    ref = AssetRefresher(db, http, ("ETHUSDT", "SOLUSDT"), start_ms=T0, clock=lambda: now[0])
    assert ref.maybe_refresh() == 20                                                  # days 0..9, both symbols
    n = len(http.calls)
    assert ref.maybe_refresh() == 0 and len(http.calls) == n                           # up to date: no request
    now[0] += D                                                                       # day 10 has closed
    assert ref.maybe_refresh() == 2
    down = AssetRefresher(db, FakeHttp(fail=True), ("BNBUSDT",), start_ms=T0, clock=lambda: now[0])
    assert down.maybe_refresh() == 0 and down.errors
    calls = len(down.http.calls)
    assert down.maybe_refresh() == 0 and len(down.http.calls) == calls                 # retried later, not every minute
    now[0] += 16 * 60_000
    down.maybe_refresh()
    assert len(down.http.calls) > calls
