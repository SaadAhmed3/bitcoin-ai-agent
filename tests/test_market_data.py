from conftest import FIXED_NOW, FakeOverview, FakeProvider, make_rows

from services.http_client import HttpClient, ProviderError
from services.market_data import (BinanceProvider, CoinbaseProvider, MarketDataService, aggregate_candles,
                                  detect_gaps, epoch_seconds_to_iso, validate_candles)


def service(db, config, providers, overview=None, now=FIXED_NOW):
    clock = {"t": now}
    svc = MarketDataService(db, config, http=HttpClient(min_interval=0), providers=providers,
                            overview_provider=overview or FakeOverview(), clock=lambda: clock["t"])
    return svc, clock


# ----------------------------------------------------------------------- validation
def test_open_candle_is_dropped():
    rows = make_rows(10, include_open=True, end_ms=FIXED_NOW)
    # make the extra candle still open
    rows[-1]["close_time"] = FIXED_NOW + 1000
    clean, _ = validate_candles(rows, "1h", FIXED_NOW)
    assert len(clean) == 10
    assert all(r["close_time"] < FIXED_NOW for r in clean)


def test_malformed_candles_are_discarded():
    rows = make_rows(5)
    rows[1]["high"] = rows[1]["low"] - 1      # impossible bar
    rows[2]["close"] = -5                     # negative price
    rows[3]["volume"] = "abc"                 # unparsable
    clean, warns = validate_candles(rows, "1h", FIXED_NOW)
    assert len(clean) == 2
    assert "3 malformed" in warns[0]


def test_duplicates_removed_and_sorted():
    rows = make_rows(5)
    clean, _ = validate_candles(list(reversed(rows)) + [rows[0]], "1h", FIXED_NOW)
    assert [r["open_time"] for r in clean] == sorted(r["open_time"] for r in rows)


def test_gap_detection():
    rows = make_rows(10)
    del rows[4:6]
    assert detect_gaps(rows, "1h") == 2


def test_aggregate_1h_to_4h_drops_incomplete_buckets():
    rows = make_rows(4 * 6 + 2, timeframe="1h", end_ms=1_760_011_200_000)  # 4h-aligned end
    out = aggregate_candles(rows, "1h", "4h")
    assert all(r["open_time"] % (4 * 3600_000) == 0 for r in out)
    first = [r for r in rows if r["open_time"] == out[0]["open_time"]][0]
    assert out[0]["open"] == first["open"]
    bucket = [r for r in rows if out[0]["open_time"] <= r["open_time"] < out[0]["open_time"] + 4 * 3600_000]
    assert out[0]["high"] == max(r["high"] for r in bucket)
    assert abs(out[0]["volume"] - sum(r["volume"] for r in bucket)) < 1e-9


# ------------------------------------------------------------------------ parsing
class StubHttp:
    def __init__(self, responses):
        self.responses = responses

    def get_json(self, url, params=None, headers=None):
        for key, val in self.responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val, 5.0
        raise ProviderError("unmatched " + url)


def test_binance_parses_klines_and_falls_back_to_mirror():
    kl = [[1, "1", "2", "0.5", "1.5", "10", 3599, "0", 0, "0", "0", "0"]]
    http = StubHttp({"api.binance.com": ProviderError("HTTP 451"), "data-api.binance.vision/api/v3/klines": kl})
    rows, _ = BinanceProvider(http).fetch_candles("1h", 10)
    assert rows[0]["open_time"] == 1 and rows[0]["close"] == "1.5" and rows[0]["close_time"] == 3599


def test_binance_ticker_parsing():
    http = StubHttp({"ticker/24hr": {"lastPrice": "65000.5", "closeTime": 123, "priceChangePercent": "-1.2"}})
    q = BinanceProvider(http).fetch_price()
    assert q["price"] == 65000.5 and q["change_24h_pct"] == -1.2 and q["observed_at"] == 123


def test_coinbase_candle_column_order():
    # Coinbase: [time, low, high, open, close, volume], newest first
    data = [[7200, 9, 12, 10, 11, 5], [3600, 8, 11, 9, 10, 4]]
    http = StubHttp({"/candles": data})
    rows, _ = CoinbaseProvider(http, clock=lambda: FIXED_NOW).fetch_candles("1h", 2)
    r = [x for x in rows if x["open_time"] == 7_200_000][0]
    assert (r["low"], r["high"], r["open"], r["close"], r["volume"]) == (9, 12, 10, 11, 5)
    assert r["close_time"] == 7_200_000 + 3_600_000 - 1


def test_coinbase_request_window_is_iso_utc():
    class Capture(StubHttp):
        def get_json(self, url, params=None, headers=None):
            self.params = params
            return [], 1.0
    http = Capture({})
    CoinbaseProvider(http, clock=lambda: FIXED_NOW).fetch_candles("1h", 10)
    assert http.params["end"] == epoch_seconds_to_iso(FIXED_NOW // 1000)
    assert http.params["start"].endswith("+00:00")


def test_epoch_iso_handles_pre_1970_and_clamped_window():
    # datetime.fromtimestamp() raises OSError for negative values on Windows.
    assert epoch_seconds_to_iso(-3600) == "1969-12-31T23:00:00+00:00"
    http = StubHttp({"/candles": []})
    rows, _ = CoinbaseProvider(http, clock=lambda: 10_000_000).fetch_candles("1h", 2)
    assert rows == []


def test_bad_ticker_format_raises():
    http = StubHttp({"ticker/24hr": {"unexpected": True}})
    try:
        BinanceProvider(http).fetch_price()
    except ProviderError as exc:
        assert "unexpected ticker" in str(exc)
    else:
        raise AssertionError("expected ProviderError")


# ------------------------------------------------------------------------ service
def test_price_fallback_to_second_provider_and_health(db, config):
    p1, p2 = FakeProvider("fake1", fail=True), FakeProvider("fake2", symbol="BTC-USD", quote="USD", price=64_990)
    svc, _ = service(db, config, [p1, p2])
    out = svc.get_price()
    assert out["provider"] == "fake2" and out["quote"] == "USD" and out["status"] == "fresh"
    assert any("fallback" in w for w in out["warnings"])
    health = {h["source"]: h for h in db.get_health()}
    assert health["fake1"]["status"] == "error" and health["fake2"]["status"] == "ok"


def test_all_providers_fail_uses_cached_price_marked_stale(db, config):
    good = FakeProvider("fake1")
    svc, clock = service(db, config, [good])
    svc.get_price()
    good.fail = True
    clock["t"] += 10 * 60_000
    out = svc.get_price()
    assert out["status"] == "cached_fallback" and out["is_stale"] is True
    assert "NOT current" in " ".join(out["warnings"])


def test_no_providers_and_no_cache(db, config):
    svc, _ = service(db, config, [FakeProvider("fake1", fail=True)])
    out = svc.get_price()
    assert out["price"] is None and out["status"] == "unavailable"


def test_old_observation_flagged_stale(db, config):
    svc, _ = service(db, config, [FakeProvider("fake1", observed_at=FIXED_NOW - 600_000)])
    out = svc.get_price()
    assert out["is_stale"] is True and out["status"] == "stale"


def test_candles_cached_within_ttl(db, config):
    p = FakeProvider("fake1")
    svc, clock = service(db, config, [p])
    r1 = svc.get_candles("1h", 200)
    r2 = svc.get_candles("1h", 200)
    assert p.candle_calls == 1 and r2.from_cache and not r1.from_cache
    clock["t"] += 3600_000
    svc.get_candles("1h", 200)
    assert p.candle_calls == 2


def test_candles_never_mix_providers_and_fallback(db, config):
    p1 = FakeProvider("fake1", fail=True)
    p2 = FakeProvider("fake2", symbol="BTC-USD", quote="USD")
    svc, _ = service(db, config, [p1, p2])
    res = svc.get_candles("1h", 100)
    assert res.provider == "fake2" and res.quote == "USD" and len(res.df) == 100
    assert any("fallback" in w for w in res.warnings)


def test_candles_all_fail_returns_cached_stale(db, config):
    p = FakeProvider("fake1")
    svc, clock = service(db, config, [p])
    svc.get_candles("1h", 100)
    p.fail = True
    clock["t"] += 5 * 3600_000
    res = svc.get_candles("1h", 100)
    assert res.is_stale and res.from_cache and len(res.df) == 100


def test_candles_no_data_at_all(db, config):
    svc, _ = service(db, config, [FakeProvider("fake1", fail=True)])
    res = svc.get_candles("1h", 100)
    assert res.df.empty and res.is_stale and "No candle data" in res.warnings[0]


def test_missing_candles_reported(db, config):
    rows = make_rows(50)
    del rows[10:13]
    svc, _ = service(db, config, [FakeProvider("fake1", rows=rows)])
    res = svc.get_candles("1h", 100)
    assert res.gaps == 3 and any("missing" in w for w in res.warnings)


def test_overview_cached_fallback(db, config):
    ov = FakeOverview()
    svc, clock = service(db, config, [FakeProvider("fake1")], overview=ov)
    assert svc.get_overview()["btc_dominance_pct"] == 57.3
    ov.fail = True
    clock["t"] += 3600_000
    out = svc.get_overview()
    assert out["status"] == "cached_fallback" and out["from_cache"]
