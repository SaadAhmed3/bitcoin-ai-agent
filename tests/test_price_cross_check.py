"""Batch 2 fix 3 (signal-1.8): the live price is cross-checked against a second exchange.

Divergence above the limit (default 0.5%, range-checked) -> NO TRADE showing both prices and quote currencies.
Prices observed more than 15 s apart are not compared. A missing second source only warns, never blocks, and its
alert respects the alert cooldown.
"""
import pytest
from conftest import FIXED_NOW, FakeOverview, FakeProvider
from test_app_and_db import make_client
from test_phase8 import make_alerts, price, rule
from test_signal_engine import PRICE_OK, run

from services.http_client import HttpClient
from services.market_data import MarketDataService
from services.signal_engine import BUY, NO_TRADE, STRONG_BUY, EngineConfig


def market(config, db, ref=65_000.0, ref_fail=False, ref_observed=FIXED_NOW - 1_000, usdt_rate=1.0,
           rate_observed=None, rate_fail=False):
    # The Coinbase fake also quotes USDT-USD, like the real Coinbase (batch 2 follow-up; default rate 1.0).
    p1 = FakeProvider("binance", price=65_000.0)
    p2 = FakeProvider("coinbase", symbol="BTC-USD", quote="USD", price=ref, fail=ref_fail, observed_at=ref_observed,
                      usdt_rate=usdt_rate, rate_observed_at=rate_observed, rate_fail=rate_fail)
    svc = MarketDataService(db, config, http=HttpClient(min_interval=0), providers=[p1, p2],
                            overview_provider=FakeOverview(), clock=lambda: FIXED_NOW)
    return svc, p2


def checked(diff, a=80_000.0):
    b = round(a * (1 + diff / 100), 2)
    return {"status": "checked", "diff_pct": diff,
            "primary": {"provider": "binance", "quote": "USDT", "price": a, "observed_at": FIXED_NOW},
            "reference": {"provider": "coinbase", "quote": "USD", "price": b, "observed_at": FIXED_NOW}}


# ------------------------------------------------------------------ market data: the comparison itself
def test_cross_check_compares_two_exchanges(config, db):
    svc, _ = market(config, db, ref=65_400.0)
    xc = svc.cross_check(svc.get_price())
    assert xc["status"] == "checked" and xc["diff_pct"] == pytest.approx(0.615, abs=0.001)
    assert xc["primary"]["provider"] == "binance" and xc["primary"]["quote"] == "USDT" and xc["primary"]["price"] == 65_000.0
    assert xc["reference"]["provider"] == "coinbase" and xc["reference"]["quote"] == "USD"
    assert xc["reference"]["price"] == 65_400.0


def test_cross_check_unavailable_when_second_source_fails_or_is_not_configured(config, db):
    svc, _ = market(config, db, ref_fail=True)
    xc = svc.cross_check(svc.get_price())
    assert xc["status"] == "unavailable" and xc["reference_configured"] is True and "coinbase" in xc["reason"]
    assert next(h for h in db.get_health() if h["source"] == "coinbase")["status"] == "error"   # counts for alerts
    single = MarketDataService(db, config, http=HttpClient(min_interval=0), providers=[FakeProvider("binance")],
                               overview_provider=FakeOverview(), clock=lambda: FIXED_NOW)
    xc = single.cross_check(single.get_price())
    assert xc["status"] == "unavailable" and xc["reference_configured"] is False
    assert xc["reason"] == "no second price source configured"


def test_prices_observed_more_than_15_s_apart_are_not_compared(config, db):
    svc, p2 = market(config, db, ref=70_000.0, ref_observed=FIXED_NOW - 1_000 - 16_000)   # 16 s before binance's
    xc = svc.cross_check(svc.get_price())
    assert xc["status"] == "unavailable" and "16 s apart" in xc["reason"]        # not a 7.7% "divergence"
    assert p2.price_calls == 1
    p2.observed_at = FIXED_NOW - 1_000 - 15_000                                   # exactly 15 s: comparable
    xc = svc.cross_check(svc.get_price())
    assert p2.price_calls == 2                                                    # the cached quote was too far apart
    assert xc["status"] == "checked" and xc["diff_pct"] == pytest.approx(7.692, abs=0.001)


# ------------------------------------------------------------------ the engine gate
def test_divergence_above_the_limit_is_no_trade_with_both_prices_and_quotes():
    s = run(price={**PRICE_OK, "cross_check": checked(0.8)})
    assert s["signal"] == NO_TRADE and "price_divergence" in s["diagnostics"]["gate_keys"]
    reason = next(r for r in s["no_trade_reasons"] if r.startswith("Price sources disagree"))
    assert "by 0.80%" in reason and "limit 0.5%" in reason
    assert "binance 80,000.00 USDT" in reason and "coinbase 80,640.00 USD" in reason
    assert s["price"]["cross_check"]["status"] == "checked"


def test_divergence_within_the_limit_does_not_block():
    s = run(price={**PRICE_OK, "cross_check": checked(0.4)})
    assert s["signal"] in (BUY, STRONG_BUY) and "price_divergence" not in s["diagnostics"]["gate_keys"]
    s = run(price={**PRICE_OK, "cross_check": checked(0.8)}, cfg=EngineConfig(max_source_divergence_pct=1.0))
    assert "price_divergence" not in s["diagnostics"]["gate_keys"]


def test_an_unavailable_cross_check_warns_but_does_not_block():
    s = run(price={**PRICE_OK, "cross_check": {"status": "unavailable", "reason": "coinbase: timeout after 10s",
                                               "reference_configured": True}})
    assert s["signal"] in (BUY, STRONG_BUY) and "price_divergence" not in s["diagnostics"]["gate_keys"]
    assert any(w.startswith("Price not cross-checked: coinbase: timeout") for w in s["data_warnings"])


def test_divergence_limit_is_range_checked(config, db):
    c = make_client(config, db, [FakeProvider("fake1")])
    for bad in (0, 0.05, 6, -1, "x"):
        r = c.put("/api/signal/config", json={"max_source_divergence_pct": bad})
        assert r.status_code == 400 and "max_source_divergence_pct" in r.get_json()["error"], bad
    assert c.put("/api/signal/config", json={"max_source_divergence_pct": 1.0}).status_code == 200
    assert c.get("/api/settings").get_json()["signal"]["ranges"]["max_source_divergence_pct"]["min"] == 0.1


def test_live_signal_blocks_on_divergence_and_only_warns_without_a_second_source(config, db):
    c = make_client(config, db, [FakeProvider("binance", price=65_000.0),
                                 FakeProvider("coinbase", symbol="BTC-USD", quote="USD", price=65_500.0, usdt_rate=1.0)])
    s = c.get("/api/signal").get_json()
    assert "price_divergence" in s["diagnostics"]["gate_keys"]
    assert any("binance 65,000.00 USDT" in r and "coinbase 65,500.00 USD" in r for r in s["no_trade_reasons"])
    c2 = make_client(config, db, [FakeProvider("binance", price=65_000.0),
                                  FakeProvider("coinbase", symbol="BTC-USD", quote="USD", fail=True)])
    s = c2.get("/api/signal").get_json()
    assert "price_divergence" not in s["diagnostics"]["gate_keys"]
    assert any(w.startswith("Price not cross-checked") for w in s["data_warnings"])


# ------------------------------------------------------------------ alerts: no spam
def test_unavailable_cross_check_alert_respects_the_cooldown(db):
    unavailable = {"status": "unavailable", "reason": "coinbase: timeout after 10s", "reference_configured": True}
    sig = {"id": 1, "signal": "NO TRADE", "setup": "HOLD", "score": 0.0, "engine": "signal-1.8",
           "price": {"value": 65_000.0, "cross_check": unavailable}}
    svc, data, clock, settings = make_alerts(db, signal=sig, health=[], price=price(65_000))
    rule(svc, "source_failure")
    titles = lambda alerts: [a["title"] for a in alerts]                                   # noqa: E731
    assert titles(svc.evaluate()) == ["Price not cross-checked"]
    for _ in range(5):                                                   # every minute for 5 minutes: no repeat
        clock.advance(60_000)
        assert svc.evaluate() == []
    clock.advance(int(settings["cooldown_min"]) * 60_000)                # after the cooldown it may repeat
    assert titles(svc.evaluate()) == ["Price not cross-checked"]
    data["signal"] = {**sig, "price": {"cross_check": {**unavailable, "reference_configured": False,
                                                       "reason": "no second price source configured"}}}
    clock.advance(10 * int(settings["cooldown_min"]) * 60_000)
    assert svc.evaluate() == []                                          # a single-source setup is not a failure


# ------------------------------------------------------------------ USDT -> USD conversion (batch 2 follow-up)
def test_usdt_price_is_converted_to_usd_before_comparing(config, db):
    """Raw USDT vs USD prices 0.6% apart, but USDT trades at 0.995 USD: converted, the gap is only 0.10%."""
    svc, p2 = market(config, db, ref=64_610.0, usdt_rate=0.995)
    xc = svc.cross_check(svc.get_price())
    assert xc["status"] == "checked" and p2.rate_calls == 1
    assert xc["raw_diff_pct"] == pytest.approx(0.6, abs=0.001)
    assert xc["diff_pct"] == pytest.approx(0.1005, abs=0.001)               # 65,000 x 0.995 = 64,675 vs 64,610
    assert xc["conversion"]["pair"] == "USDT-USD" and xc["conversion"]["rate"] == 0.995
    assert xc["conversion"]["source"] == "coinbase" and xc["primary"]["price_usd"] == pytest.approx(64_675.0)
    s = run(price={**PRICE_OK, "price": 65_000.0, "cross_check": xc})
    assert "price_divergence" not in s["diagnostics"]["gate_keys"]          # raw 0.6% alone would have blocked


def test_a_real_gap_after_conversion_blocks_and_the_reason_shows_rate_and_raw_prices(config, db):
    svc, _ = market(config, db, ref=64_500.0, usdt_rate=1.0002)
    xc = svc.cross_check(svc.get_price())
    s = run(price={**PRICE_OK, "price": 65_000.0, "cross_check": xc})
    assert s["signal"] == NO_TRADE and "price_divergence" in s["diagnostics"]["gate_keys"]
    r = next(x for x in s["no_trade_reasons"] if x.startswith("Price sources disagree"))
    assert "after USDT-to-USD conversion" in r and "limit 0.5%" in r
    assert "binance 65,000.00 USDT x 1.0002 USDT-USD = 65,013.00 USD" in r and "coinbase 64,500.00 USD" in r
    assert "raw gap 0.77%" in r


@pytest.mark.parametrize("rate_kwargs, why", [
    ({"rate_fail": True}, "rate unavailable"),
    ({"usdt_rate": None}, "rate unavailable"),
    ({"rate_observed": FIXED_NOW - 1_000 - 20_000}, "20 s"),                  # rate 20 s older than the prices
])
def test_without_a_usable_usdt_rate_the_raw_comparison_is_a_warning_only(config, db, rate_kwargs, why):
    svc, _ = market(config, db, ref=64_500.0, **rate_kwargs)                  # raw gap 0.77%: above the limit
    xc = svc.cross_check(svc.get_price())
    assert xc["status"] == "raw_only" and why in xc["reason"]
    assert xc["raw_diff_pct"] == pytest.approx(0.769, abs=0.001)
    s = run(price={**PRICE_OK, "price": 65_000.0, "cross_check": xc})
    assert "price_divergence" not in s["diagnostics"]["gate_keys"]
    w = next(x for x in s["data_warnings"] if x.startswith("Price cross-check not converted to USD"))
    assert "raw gap 0.77%" in w and "not blocking" in w


def test_same_quote_currencies_need_no_conversion(config, db):
    p1 = FakeProvider("binance", price=65_000.0)
    p2 = FakeProvider("other", price=65_100.0)                                  # both USDT
    svc = MarketDataService(db, config, http=HttpClient(min_interval=0), providers=[p1, p2],
                            overview_provider=FakeOverview(), clock=lambda: FIXED_NOW)
    xc = svc.cross_check(svc.get_price())
    assert xc["status"] == "checked" and xc.get("conversion") is None and p2.rate_calls == 0
    assert xc["diff_pct"] == pytest.approx(0.154, abs=0.001)


# ------------------------------------------------------------------ stablecoin deviation (batch 2 follow-up)
def test_usdt_more_than_1_pct_from_one_is_a_separate_warning_not_a_block(config, db):
    svc, _ = market(config, db, ref=64_025.0, usdt_rate=0.985)              # converted gap ~0.0%: no divergence
    xc = svc.cross_check(svc.get_price())
    assert xc["stablecoin"] == {"pair": "USDT-USD", "rate": 0.985, "deviation_pct": -1.5, "warning": True}
    s = run(price={**PRICE_OK, "price": 65_000.0, "cross_check": xc})
    assert "price_divergence" not in s["diagnostics"]["gate_keys"]          # a deviation alone never blocks
    w = next(x for x in s["data_warnings"] if x.startswith("Stablecoin deviation"))
    assert "USDT-USD is 0.9850" in w and "-1.50% from 1.00" in w
    svc2, _ = market(config, db, ref=64_935.0, usdt_rate=0.999)
    assert svc2.cross_check(svc2.get_price())["stablecoin"]["warning"] is False


def test_stablecoin_deviation_alert_respects_the_cooldown(db):
    xc = {"status": "checked", "diff_pct": 0.02,
          "stablecoin": {"pair": "USDT-USD", "rate": 0.985, "deviation_pct": -1.5, "warning": True}}
    sig = {"id": 1, "signal": "NO TRADE", "setup": "HOLD", "score": 0.0, "engine": "signal-1.8",
           "price": {"value": 65_000.0, "cross_check": xc}}
    svc, data, clock, settings = make_alerts(db, signal=sig, health=[], price=price(65_000))
    rule(svc, "source_failure")
    assert [a["title"] for a in svc.evaluate()] == ["Stablecoin deviation: USDT-USD 0.9850"]
    for _ in range(5):
        clock.advance(60_000)
        assert svc.evaluate() == []
    clock.advance(int(settings["cooldown_min"]) * 60_000)
    assert [a["title"] for a in svc.evaluate()] == ["Stablecoin deviation: USDT-USD 0.9850"]
    data["signal"] = {**sig, "price": {"cross_check": {**xc, "stablecoin": {**xc["stablecoin"], "warning": False}}}}
    clock.advance(10 * int(settings["cooldown_min"]) * 60_000)
    assert svc.evaluate() == []


def test_coinbase_usdt_usd_ticker_is_parsed():
    from services.market_data import CoinbaseProvider

    class Http:
        def get_json(self, url, params=None, headers=None):
            assert url.endswith("/products/USDT-USD/ticker")
            return {"price": "0.99973", "time": "2026-10-30T21:12:51.123Z"}, 18.0
    q = CoinbaseProvider(Http()).fetch_rate("USDT-USD")
    assert q["rate"] == 0.99973 and q["observed_at"] == 1793394771123 and q["latency_ms"] == 18.0
