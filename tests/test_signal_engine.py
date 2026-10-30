"""Signal engine tests with hand-built analysis inputs (no network, no randomness)."""
import json
from copy import deepcopy

import pytest
from conftest import FIXED_NOW, FakeProvider, make_rows

from services.signal_engine import (BUY, HOLD, NO_TRADE, SELL, STRONG_BUY, EngineConfig, build_buy_plan,
                                    generate_signal, validate_weights)

VALIDATED = {"validated": True, "as_of": FIXED_NOW, "note": "test"}


def tf(price=80_000.0, trend="uptrend", stack="bull", rsi=60.0, macd=50.0, adx=30.0, pdi=30.0, mdi=15.0,
       vol_ratio=1.2, up=True, pb=0.7, atr=800.0, regime="normal", breakout="inside_range", vol_conf=False,
       support=(78_500.0, 3), resistance=(84_000.0, 2), bars=500, div=None):
    e = {"bull": (price * 0.99, price * 0.98, price * 0.96), "bear": (price * 1.01, price * 1.02, price * 1.04),
         "mixed": (price * 0.99, price * 1.01, price * 0.97)}[stack]
    e200 = price * (0.9 if trend == "uptrend" else 1.1)
    return {
        "status": "ok", "bars": bars, "price": price,
        "indicators": {"rsi14": rsi, "macd_hist": macd, "ema9": e[0], "ema21": e[1], "ema50": e[2], "ema200": e200,
                       "sma50": price * (0.97 if trend == "uptrend" else 1.03), "sma200": price * (0.92 if trend == "uptrend" else 1.08),
                       "adx": adx, "plus_di": pdi, "minus_di": mdi, "vol_ratio": vol_ratio, "last_candle_up": up,
                       "bb_pct_b": pb, "atr14": atr, "atr_pct": atr / price * 100},
        "structure": {"trend": trend, "score": {"uptrend": 1.0, "downtrend": -1.0}.get(trend, 0.0)},
        "levels": {"support": [{"price": support[0], "touches": support[1]}] if support else [],
                   "resistance": [{"price": resistance[0], "touches": resistance[1]}] if resistance else []},
        "breakout": {"state": breakout, "volume_confirmed": vol_conf},
        "volatility": {"regime": regime, "percentile": 50.0},
        "divergence": div or {"bullish": False, "bearish": False},
    }


def bullish():
    return {"1h": tf(rsi=58), "4h": tf(), "1d": tf(atr=2500, support=(72_000.0, 4), resistance=(95_000.0, 2))}


def bearish():
    b = dict(trend="downtrend", stack="bear", rsi=38, macd=-60, pdi=12, mdi=32, up=False, pb=0.25,
             support=(76_000.0, 2), resistance=(81_500.0, 3))
    return {"1h": tf(**{**b, "rsi": 40}), "4h": tf(**b), "1d": tf(**{**b, "atr": 2500})}


META_OK = {t: {"is_stale": False, "gaps": 0, "warnings": []} for t in ("1h", "4h", "1d")}
PRICE_OK = {"price": 80_000.0, "provider": "binance", "quote": "USDT", "observed_at": FIXED_NOW, "is_stale": False,
            "status": "fresh", "warnings": []}
NEWS_NEUTRAL = {"bias_label": "insufficient_news", "bias_score": 0, "weight_total": 0}


def run(analyses=None, meta=None, price=PRICE_OK, news=NEWS_NEUTRAL, events=None, cfg=None, validation=VALIDATED):
    return generate_signal(analyses or bullish(), meta or deepcopy(META_OK), price, news, events or [],
                           cfg or EngineConfig(), validation, FIXED_NOW)


# ------------------------------------------------------------------ setups
def test_bullish_alignment_gives_buy_setup_with_plan():
    s = run()
    assert s["setup"] in (BUY, STRONG_BUY) and s["signal"] == s["setup"] and s["actionable"]
    p = s["plan"]
    assert p["stop"] < p["entry_zone"][0] <= p["entry_zone"][1] <= 80_000
    assert p["targets"][0]["price"] > p["entry_reference"] and p["rr_t1"] >= 1.5
    assert s["evidence_for"] and s["invalidation"]
    json.dumps(s, allow_nan=False)


def test_bearish_alignment_gives_sell_without_entry_plan():
    s = run(bearish())
    assert s["setup"] == SELL and s["signal"] == SELL and s["plan"] is None
    assert any("resistance" in i for i in s["invalidation"])


def test_mixed_evidence_is_hold():
    a = bullish()
    a["4h"] = tf(trend="range / transition", stack="mixed", rsi=50, macd=-1, adx=15, vol_ratio=0.8, pb=0.5)
    a["1h"] = tf(trend="range / transition", stack="mixed", rsi=49, macd=-1, adx=12)
    a["1d"] = tf(trend="range / transition", stack="mixed", rsi=51, atr=2500)
    s = run(a)
    assert s["setup"] == HOLD and s["signal"] == HOLD and not s["actionable"]


def test_score_is_weighted_sum_of_components():
    s = run()
    total = sum(c["contribution"] for c in s["components"] if c["contribution"] is not None)
    assert s["score"] == pytest.approx(total, abs=1e-3)
    assert sum(c["weight"] for c in s["components"]) == pytest.approx(1.0, abs=1e-3)


def test_missing_news_redistributes_weight():
    s = run()
    news = [c for c in s["components"] if c["name"] == "news"][0]
    assert news["score"] is None and news["weight"] == 0


def test_news_does_not_move_the_score():
    # signal-1.8 (owner's decision, deliberate change from "news moves the score"): news is not part of the score,
    # as in every backtest; it can only block (tests/test_news_blocking.py).
    base = run()["score"]
    bull_news = {"bias_label": "bullish", "bias_score": 0.6, "weight_total": 5, "directional_active": 4}
    bear_news = {"bias_label": "bearish", "bias_score": -0.6, "weight_total": 5, "directional_active": 4}
    assert run(news=bull_news)["score"] == base == run(news=bear_news)["score"]


# ------------------------------------------------------------------ trade plan
def test_plan_costs_reduce_reward_risk():
    a = tf()
    cheap = build_buy_plan(a, EngineConfig(fee_pct=0.0, slippage_pct=0.0))
    costly = build_buy_plan(a, EngineConfig(fee_pct=0.5, slippage_pct=0.5))
    assert costly["rr_t1"] < cheap["rr_t1"]


def test_plan_rr_math():
    a = tf(price=100.0, atr=2.0, support=(97.0, 2), resistance=(110.0, 2))
    p = build_buy_plan(a, EngineConfig(fee_pct=0.0, slippage_pct=0.0))
    # entry zone [max(100-1.5, 97), 100] = [98.5, 100] -> entry 99.25
    # stop: support 97 is not below 98.5 within 3 ATR? 97 < 98.5 and 99.25-97 = 2.25 <= 6 -> stop = 97 - 1 = 96
    assert p["entry_reference"] == pytest.approx(99.25)
    assert p["stop"] == pytest.approx(96.0)
    # T1 = resistance 110. Primary R/R buys at the live price 100 (top of zone): reward 10, risk 4.
    assert p["targets"][0]["price"] == 110.0 and p["rr_t1"] == pytest.approx(10 / 4, abs=0.01)
    # Mid-zone fill (99.25): reward 10.75, risk 3.25.
    assert p["targets"][0]["rr_mid_zone"] == pytest.approx(10.75 / 3.25, abs=0.01)


def test_plan_without_levels_uses_atr():
    p = build_buy_plan(tf(price=100.0, atr=2.0, support=None, resistance=None), EngineConfig())
    assert "ATR" in p["stop_basis"] and "ATR" in p["targets"][0]["basis"]


def test_low_reward_risk_downgrades_buy_to_hold():
    a = bullish()
    a["4h"] = tf(resistance=(80_900.0, 2))  # resistance just above price -> tiny reward
    a["4h"]["levels"]["resistance"] = [{"price": 80_900.0, "touches": 2}]
    s = run(a, cfg=EngineConfig(min_rr=5.0))
    assert s["setup"] == HOLD and any("Reward/risk" in n for n in s["setup_notes"])


# ------------------------------------------------------------------ NO TRADE gates
def _gate(s, text):
    assert s["signal"] == NO_TRADE, s["signal"]
    assert any(text in g for g in s["no_trade_reasons"]), s["no_trade_reasons"]


def test_gate_unvalidated_strategy_but_setup_still_shown():
    s = run(validation=None)
    _gate(s, "Not validated: no backtest has been run yet")
    assert s["setup"] in (BUY, STRONG_BUY) and s["plan_status"] == "research only - not actionable"


def test_gate_can_be_disabled_in_config():
    s = run(validation=None, cfg=EngineConfig(require_validated_edge=False))
    assert s["signal"] in (BUY, STRONG_BUY)


def test_gate_stale_price():
    _gate(run(price={**PRICE_OK, "is_stale": True, "age_seconds": 900}), "stale")


def test_gate_no_price():
    _gate(run(price={"price": None, "status": "unavailable"}), "No current BTC price")


def test_gate_stale_candles_and_gaps():
    meta = deepcopy(META_OK)
    meta["4h"]["is_stale"] = True
    _gate(run(meta=meta), "4h candles are stale")
    meta = deepcopy(META_OK)
    meta["1d"]["gaps"] = 12
    _gate(run(meta=meta), "missing candles")


def test_gate_stale_or_gappy_1h_series():
    """Batch 2 (signal-1.8): 1h drives 40% of momentum and half of overextension; before, a stale or gappy 1h
    series fed the signal silently (only 4h and 1d were checked)."""
    meta = deepcopy(META_OK)
    meta["1h"]["is_stale"] = True
    s = run(meta=meta)
    assert s["signal"] == NO_TRADE and "stale_candles" in s["diagnostics"]["gate_keys"]
    assert any(r.startswith("1h candles are stale") and "momentum" in r for r in s["no_trade_reasons"])
    meta = deepcopy(META_OK)
    meta["1h"]["gaps"] = 6
    s = run(meta=meta)
    assert s["signal"] == NO_TRADE and "gaps" in s["diagnostics"]["gate_keys"]
    assert any(r.startswith("1h series has 6 missing candles") for r in s["no_trade_reasons"])
    meta["1h"]["gaps"] = 5                                         # the limit itself (max_gaps = 5) still passes
    assert "gaps" not in run(meta=meta)["diagnostics"]["gate_keys"]


def test_engine_version_is_signal_1_8():
    from services.signal_engine import ENGINE_VERSION
    assert ENGINE_VERSION == "signal-1.8"


def test_gate_insufficient_primary_data():
    a = bullish()
    a["4h"]["bars"] = 40
    _gate(run(a), "too little data")
    a = bullish()
    a["4h"] = {"status": "insufficient_data", "bars": 5}
    _gate(run(a), "too little data")


def test_gate_extreme_volatility():
    a = bullish()
    a["1h"]["volatility"]["regime"] = "extreme"
    _gate(run(a), "volatility is extreme")


def test_gate_contradictory_evidence():
    a = bullish()
    # strong bullish trend, but momentum/volume/overextension strongly bearish
    a["4h"] = tf(rsi=78, macd=-80, pdi=10, mdi=35, vol_ratio=2.0, up=False, pb=1.2)
    a["1h"] = tf(rsi=80, macd=-60, pdi=10, mdi=35, pb=1.3)
    s = run(a, cfg=EngineConfig(buy_threshold=0.05, strong_threshold=0.9, min_agreement=0.9))
    assert abs(s["score"]) >= 0.05 and s["agreement"] < 0.9
    _gate(s, "contradictory")


def test_gate_counter_trend():
    a = bullish()
    a["1d"] = tf(trend="downtrend", stack="bear", rsi=35, macd=-100, pdi=10, mdi=30, atr=2500)
    s = run(a, cfg=EngineConfig(buy_threshold=0.05))
    assert s["setup"] in (BUY, STRONG_BUY)
    _gate(s, "fights the daily downtrend")


def test_gate_fresh_high_impact_news_against_setup():
    ev = [{"title": "Major exchange hacked, $500M drained", "link": "https://x", "publisher": "X",
           "sentiment": "bearish", "impact": "high", "freshness": "new"}]
    s = run(events=ev)
    _gate(s, "Fresh high-impact bearish news")


def test_old_news_against_setup_does_not_gate():
    ev = [{"title": "Old hack", "link": "https://x", "publisher": "X", "sentiment": "bearish",
           "impact": "high", "freshness": "recent"}]
    assert run(events=ev)["signal"] != NO_TRADE


def test_every_signal_has_required_fields():
    for s in (run(), run(bearish()), run(validation=None)):
        for key in ("signal", "setup", "score", "generated_at", "horizon", "price", "components", "evidence_for",
                    "evidence_against", "no_trade_reasons", "invalidation", "would_change", "risk",
                    "key_levels", "confidence", "data_warnings", "news"):
            assert key in s
        assert "not a probability" in s["confidence"]


# ------------------------------------------------------------------ config validation
def test_validate_weights_normalises_and_rejects_bad_input():
    w = validate_weights({"news": 0, "trend": 2})
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-3) and w["news"] == 0
    for bad in ({"magic": 1}, {"trend": -1}, {"trend": "x"}, {}, {"trend": 0, "momentum": 0}):
        with pytest.raises(ValueError):
            validate_weights(bad)


def test_zero_weight_component_is_ignored():
    w = validate_weights({"news": 0})
    s = run(news={"bias_label": "bearish", "bias_score": -0.9, "weight_total": 9, "directional_active": 5},
            cfg=EngineConfig(weights=w))
    news = [c for c in s["components"] if c["name"] == "news"][0]
    assert news["weight"] == 0 and news["contribution"] in (0, None, 0.0)


# ------------------------------------------------------------------ end-to-end with real indicators
def test_engine_runs_on_real_indicator_output():
    from services.market_data import rows_to_frame
    from services.technical_analysis import analyze
    analyses = {t: analyze(rows_to_frame(make_rows(500, t, seed=3)), t) for t in ("1h", "4h", "1d")}
    s = run(analyses)
    json.dumps(s, allow_nan=False)
    assert s["signal"] in (STRONG_BUY, BUY, HOLD, SELL, NO_TRADE)
    assert len(s["components"]) == 6


# ================================================================ 5.1 regressions (live review, 29 Oct 2026)
import pandas as pd

from services.signal_engine import momentum_tf, score_news, score_trend
from services.technical_analysis import market_structure, support_resistance


def test_levels_do_not_chain_into_a_wide_band():
    # Live 4h swings reported in the review: lows 79,221/79,369/79,506 and highs up to 85,650,
    # spaced so each is within 0.5 ATR of the previous one (the old chaining bug).
    price, atr_v = 79_862.0, 806.0
    lows = [(i, p) for i, p in enumerate([79_221, 79_369, 79_506, 77_652])]
    highs = [(10 + i, p) for i, p in enumerate([80_100, 80_480, 80_845, 81_034, 81_420, 81_823, 82_061,
                                                 82_430, 82_800, 83_200, 83_600, 84_000, 84_400, 84_898, 85_300, 85_650])]
    df = pd.DataFrame({"close": [price] * 40})
    lv = support_resistance(df, highs, lows, atr_v)
    for l in lv["support"] + lv["resistance"]:
        assert l["high"] - l["low"] <= atr_v + 1e-6, l
    assert all(l["high"] < price for l in lv["support"])
    assert all(l["low"] > price for l in lv["resistance"])
    # The real range floor (~79.2k-79.5k) is the nearest support, not 77.6k.
    assert 79_200 <= lv["support"][0]["price"] <= 79_600
    # Nearest resistance is just above price, not an average of a 6k-wide band.
    assert lv["resistance"][0]["price"] < 81_000


def test_level_straddling_price_is_current_zone_not_support():
    df = pd.DataFrame({"close": [100.0]})
    lv = support_resistance(df, [(0, 100.4)], [(1, 99.7)], atr_value=2.0)
    assert lv["current_zone"] and not lv["support"] and not lv["resistance"]


def test_one_higher_high_inside_lower_highs_is_not_an_uptrend():
    highs = [(i, p) for i, p in enumerate([84_898, 82_061, 81_034, 80_845, 81_823])]
    lows = [(i, p) for i, p in enumerate([79_800, 79_221, 79_369, 79_506])]
    st = market_structure(highs, lows)
    assert st["last_high_step"] == "higher_high"  # last pair only, display field
    assert st["trend"] == "range / transition"   # but the sequence is not an uptrend
    assert -0.6 < st["score"] < 0.6


def test_clear_trends_still_detected():
    up = market_structure([(i, 100 + i) for i in range(4)], [(i, 90 + i) for i in range(4)])
    down = market_structure([(i, 100 - i) for i in range(4)], [(i, 90 - i) for i in range(4)])
    assert (up["trend"], up["score"], down["trend"], down["score"]) == ("uptrend", 1.0, "downtrend", -1.0)


def test_flat_macd_histogram_counts_near_zero():
    a = tf(macd=-0.01, rsi=50, adx=10)
    v, why = momentum_tf(a, "4h")
    assert abs(v) < 0.05
    big, _ = momentum_tf(tf(macd=-500, rsi=50, adx=10), "4h")
    assert big == pytest.approx(-0.7 / 3, abs=1e-6)  # full MACD part (-0.7) averaged with RSI 0 and ADX 0


def test_balanced_di_is_not_sellers_in_control():
    v, why = momentum_tf(tf(rsi=50, macd=0.0, adx=22, pdi=24.4, mdi=26.9), "1h")
    assert abs(v) < 0.1
    assert any("roughly balanced" in t for t, _ in why)


def test_evidence_lists_include_facts_from_cancelled_trend():
    a = bullish()
    a["4h"] = tf(trend="downtrend", stack="bear", rsi=42, macd=-40, pdi=15, mdi=28, up=False)
    a["4h"]["indicators"]["ema200"] = 80_000 * 1.05  # 4h close below 4h EMA200
    s = run(a)
    trend = [c for c in s["components"] if c["name"] == "trend"][0]
    assert abs(trend["score"]) < 0.3                          # 1d bullish and 4h bearish roughly cancel
    assert any(e.startswith("trend: 1d") for e in s["evidence_bullish"])
    assert any(e.startswith("trend: 4h") for e in s["evidence_bearish"])
    assert trend["reasons"][0].startswith("Net trend")


def test_directional_weight_reported():
    a = bullish()
    a["4h"] = tf(trend="range / transition", stack="mixed", rsi=50, macd=0.0, adx=12, vol_ratio=0.8)
    a["1h"] = tf(trend="range / transition", stack="mixed", rsi=50, macd=0.0, adx=12)
    s = run(a)
    assert 0 <= s["directional_weight"] <= 1 and "price_basis" in s


def test_news_text_says_coverage_not_confidence():
    c = score_news({"bias_label": "bearish", "bias_score": -0.22, "weight_total": 9, "directional_active": 19})
    assert "coverage" in c.reasons[0] and "not a confidence" in c.reasons[0]


def test_live_case_momentum_is_moderate_not_extreme():
    # Reviewer's live values: 4h RSI 41.7, MACD hist -0.01, ADX 22 sellers; 1h DI 24.4 vs 26.9.
    a4 = tf(rsi=41.7, macd=-0.01, adx=22, pdi=18, mdi=27, atr=806)
    a1 = tf(rsi=45, macd=-5, adx=22, pdi=24.4, mdi=26.9, atr=300)
    from services.signal_engine import score_momentum
    m = score_momentum(a4, a1).score
    assert -0.45 < m < -0.1   # was -0.54 with all-or-nothing MACD/ADX


def test_adx_wording_matches_polarity():
    for pdi, mdi, word, pol in ((26, 25, "roughly balanced", 0), (28, 24, "slight edge to buyers", 1),
                                (35, 15, "buyers in control", 1), (15, 35, "sellers in control", -1)):
        _, why = momentum_tf(tf(rsi=50, macd=0.0, adx=45, pdi=pdi, mdi=mdi), "4h")
        fact = [f for f in why if "ADX" in f[0]][0]
        assert word in fact[0] and fact[1] == pol, (pdi, mdi, fact)


# ================================================================ 5.2 regressions (second live recheck)
from services.signal_engine import live_levels, trend_tf


def test_macd_scale_does_not_saturate_on_moderate_histogram():
    # Live 1h: hist -84.9 with ATR 386 (0.22 ATR) used to read "strongly below" (-0.7).
    _, why = momentum_tf(tf(rsi=50, macd=-84.9, adx=10, atr=386), "1h")
    fact = [f for f in why if "MACD" in f[0]][0]
    assert "strongly" not in fact[0] and fact[1] == -1
    v, _ = momentum_tf(tf(rsi=50, macd=-84.9, adx=10, atr=386), "1h")
    assert -0.15 < v < -0.05   # macd part about -0.31, averaged with RSI 0 and ADX 0


def test_structure_reason_shows_both_sequences_with_steps():
    a = tf(trend="downtrend", atr=1965)
    a["structure"] = {"trend": "downtrend", "score": -0.667,
                      "high_sequence": [87_396, 87_220, 84_352, 84_898],
                      "low_sequence": [82_563, 80_394, 79_800, 79_221]}
    _, why = trend_tf(a, "1d")
    text = why[0][0]
    assert "highs 87,396 ↓ 87,220 ↓ 84,352 ↑ 84,898" in text
    assert "lows 82,563 ↓ 80,394 ↓ 79,800 ↓ 79,221" in text


def test_levels_resorted_against_live_price_and_minor_flagged():
    # Closed-candle basis 81,344; live 80,171. The 'support' at 80,394 (built against the close) is above
    # the live price, so for the signal it must be resistance.
    a = tf(price=81_344.0, atr=804)
    a["levels"] = {"support": [{"price": 80_394.0, "low": 80_394.0, "high": 80_394.0, "touches": 1},
                               {"price": 79_800.0, "low": 79_800.0, "high": 79_800.0, "touches": 1},
                               {"price": 79_365.0, "low": 79_221.0, "high": 79_506.0, "touches": 3}],
                   "resistance": [{"price": 80_714.0, "low": 80_394.0, "high": 81_034.0, "touches": 8}],
                   "current_zone": None}
    lv = live_levels(a, 80_171.0)
    assert lv["resistance"][0]["price"] == 80_394.0 and lv["zone"] is None
    assert all(l["high"] < 80_171 for l in lv["support"])
    assert lv["major_support"]["price"] == 79_365.0          # the 3-touch floor, not the 1-touch 79,800
    assert lv["support"][0]["minor"] is True


def test_plan_uses_live_price_and_major_support():
    a = tf(price=79_862.0, atr=804)
    a["levels"] = {"support": [{"price": 79_800.0, "low": 79_800.0, "high": 79_800.0, "touches": 1},
                               {"price": 79_365.0, "low": 79_221.0, "high": 79_506.0, "touches": 3}],
                   "resistance": [{"price": 80_714.0, "low": 80_394.0, "high": 81_034.0, "touches": 8},
                                  {"price": 82_059.0, "low": 81_823.0, "high": 82_295.0, "touches": 3}],
                   "current_zone": None}
    p = build_buy_plan(a, EngineConfig(), price=80_171.0)
    assert p["entry_zone"][1] == 80_171.0                     # built from the live price
    assert p["entry_zone"][0] >= 79_365.0 and p["entry_zone"][0] != 79_800.0   # not floored by the 1-touch level
    assert "79,365" in p["stop_basis"] and "3 touches" in p["stop_basis"]


def test_signal_bear_trigger_and_downside_use_major_support():
    a = bullish()
    a["4h"]["levels"] = {"support": [{"price": 79_800.0, "low": 79_800.0, "high": 79_800.0, "touches": 1},
                                     {"price": 79_365.0, "low": 79_221.0, "high": 79_506.0, "touches": 3}],
                         "resistance": [{"price": 81_000.0, "low": 80_800.0, "high": 81_200.0, "touches": 4}],
                         "current_zone": None}
    a["4h"]["structure"]["score"] = 0.0
    a["4h"]["indicators"]["rsi14"] = 50
    s = run(a, price={**PRICE_OK, "price": 80_171.0})
    assert s["key_levels"]["basis"] == "live price"
    assert s["risk"]["downside_to_support_pct"] == pytest.approx((79_365 / 80_171 - 1) * 100, abs=0.01)
    if s["setup"] == HOLD:
        assert any("79,365" in c for c in s["would_change"])


# ================================================================ 5.3 regressions (third live recheck)
def test_target_uses_lower_edge_of_resistance_zone():
    a = tf(price=100.0, atr=2.0, support=(97.0, 2), resistance=None)
    a["levels"]["resistance"] = [{"price": 106.0, "low": 104.5, "high": 107.5, "touches": 8}]
    p = build_buy_plan(a, EngineConfig(fee_pct=0, slippage_pct=0))
    assert p["targets"][0]["price"] == 104.5 and "lower edge" in p["targets"][0]["basis"]


def test_rr_now_is_worse_than_mid_zone_and_drives_downgrade():
    a = tf(price=100.0, atr=2.0, support=(97.0, 2), resistance=None)
    a["levels"]["resistance"] = [{"price": 105.0, "low": 105.0, "high": 105.0, "touches": 3}]
    p = build_buy_plan(a, EngineConfig(fee_pct=0, slippage_pct=0))
    t1 = p["targets"][0]
    assert t1["rr_after_costs"] < t1["rr_mid_zone"] and p["rr_t1"] == t1["rr_after_costs"]


def test_live_plan_case_from_review():
    # Live: price 80,123; 4h ATR ~804; floor 79,221-79,506 (3 touches); minor 79,800; resistance 80,394-81,034 (8).
    a = tf(price=79_862.0, atr=804)
    a["levels"] = {"support": [{"price": 79_800.0, "low": 79_800.0, "high": 79_800.0, "touches": 1},
                               {"price": 79_365.0, "low": 79_221.0, "high": 79_506.0, "touches": 3}],
                   "resistance": [{"price": 80_714.0, "low": 80_394.0, "high": 81_034.0, "touches": 8},
                                  {"price": 81_200.0, "low": 81_200.0, "high": 81_200.0, "touches": 1},
                                  {"price": 82_059.0, "low": 81_823.0, "high": 82_295.0, "touches": 3}],
                   "current_zone": None}
    p = build_buy_plan(a, EngineConfig(), price=80_123.0)
    assert p["entry_zone"][1] == 80_123.0
    # 5.4: T1 is the nearest MAJOR barrier however close - the 8-touch zone at 80,394 - not the 1-touch 81,200.
    assert p["targets"][0]["price"] == 80_394.0 and "major resistance 80,394-81,034 (8 touches)" in p["targets"][0]["basis"]
    assert p["t1_distance_atr"] < 1
    assert p["targets"][1]["price"] == 81_823.0   # next major barrier, not the minor 81,200
    assert p["rr_t1"] < 1.5      # a buy here would be downgraded to HOLD


def test_analysis_structure_fields_are_not_trend_lookalikes():
    st = market_structure([(0, 10), (1, 9), (2, 8), (3, 8.5)], [(0, 5), (1, 4), (2, 3), (3, 2)])
    assert "highs" not in st and "lows" not in st
    assert st["last_high_step"] == "higher_high" and st["trend"] == "downtrend"


def test_analysis_levels_declare_their_basis():
    df = pd.DataFrame({"close": [100.0]})
    lv = support_resistance(df, [(0, 103.0)], [(0, 97.0)], atr_value=2.0)
    assert lv["basis"] == "last closed candle" and lv["basis_price"] == 100.0



# ================================================================ 5.4 regression (plan ignored the nearest major barrier)
def test_minor_level_before_t1_is_listed_as_obstacle_and_grammar():
    a = tf(price=100.0, atr=2.0, support=(97.0, 2), resistance=None)
    a["levels"]["resistance"] = [{"price": 101.0, "low": 101.0, "high": 101.0, "touches": 1},
                                 {"price": 106.0, "low": 105.0, "high": 107.0, "touches": 4}]
    p = build_buy_plan(a, EngineConfig(fee_pct=0, slippage_pct=0))
    assert p["targets"][0]["price"] == 105.0
    assert p["obstacles_before_t1"] == ["minor resistance 101 (1 touch)"]


def test_downgrade_note_names_the_close_barrier():
    a = bullish()
    a["4h"]["levels"]["resistance"] = [{"price": 80_300.0, "low": 80_200.0, "high": 80_400.0, "touches": 6}]
    s = run(a)
    assert s["setup"] == HOLD
    assert any("first barrier" in n and "80,200-80,400 (6 touches)" in n for n in s["setup_notes"])


def test_no_major_resistance_falls_back_to_projection_or_minor():
    p = build_buy_plan(tf(price=100.0, atr=2.0, support=(97.0, 2), resistance=None), EngineConfig())
    assert "projection" in p["targets"][0]["basis"]



# ================================================================ 6.0 plan display (from the 5.4 review)
def test_room_note_when_target_inside_cost_band_and_obstacles_before_t2():
    a = tf(price=80_294.0, atr=804)
    a["levels"] = {"support": [{"price": 79_365.0, "low": 79_221.0, "high": 79_506.0, "touches": 3}],
                   "resistance": [{"price": 80_714.0, "low": 80_393.56, "high": 81_033.69, "touches": 8},
                                  {"price": 81_200.0, "low": 81_200.0, "high": 81_200.0, "touches": 1},
                                  {"price": 82_059.0, "low": 81_823.0, "high": 82_295.0, "touches": 3}],
                   "current_zone": None}
    p = build_buy_plan(a, EngineConfig(), price=80_294.0)
    assert p["targets"][0]["rr_after_costs"] <= 0 and p["room_note"].startswith("No room before resistance")
    assert p["obstacles_before_t2"] == ["minor resistance 81,200 (1 touch)"]


def test_risk_reports_room_to_resistance():
    a = bullish()
    a["4h"]["levels"]["resistance"] = [{"price": 80_500.0, "low": 80_400.0, "high": 80_600.0, "touches": 5}]
    s = run(a)
    assert s["risk"]["room_to_resistance_pct"] == pytest.approx((80_400 / 80_000 - 1) * 100, abs=0.01)
    assert s["risk"]["room_to_resistance_atr"] == pytest.approx(400 / 800, abs=0.01)



# ================================================================ 6.1: validation reason quotes the backtest
from services.signal_engine import validation_reason


def test_validation_reason_texts():
    assert validation_reason(None).startswith("Not validated: no backtest")
    # 0.9.2 follow-up (deliberate): a pre-6.2 (= full-period) record cannot validate, so its failed criteria are not
    # the reason; a holdout record still lists them.
    r = validation_reason({"validated": False, "run_id": 1, "engine": "signal-1.5",
                           "failed": ["Enough trades (6 trades (need >= 30))"]})
    assert "Run #1" in r and "cannot validate" in r and "no holdout run yet" in r
    h = validation_reason({"validated": False, "run_id": 2, "engine": "signal-1.7", "period_kind": "holdout",
                           "failed": ["Enough trades (9 trades (need >= 15))"]})
    assert "run #2" in h and "Enough trades" in h
    assert "different settings" in validation_reason({"validated": False, "stale": True})


def test_diagnostics_record_raw_setup_and_downgrade():
    a = bullish()
    a["4h"]["levels"]["resistance"] = [{"price": 80_300.0, "low": 80_200.0, "high": 80_400.0, "touches": 6}]
    s = run(a)
    d = s["diagnostics"]
    assert d["raw_setup"] in (BUY, STRONG_BUY) and s["setup"] == HOLD and d["downgrade"] == "low_rr"
    assert d["rr_t1"] is not None and d["rr_t1"] < 1.5
