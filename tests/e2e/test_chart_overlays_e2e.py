"""Chart overlays on Market & chart: regime shading, sweep markers, the chart's volume profile. Visualisation only."""
from __future__ import annotations

import json
import re

from e2e.conftest import Page

H4 = 4 * 3_600_000
OVERLAYS = ["ema", "bb", "vwap", "levels", "alerts", "markers", "regime", "sweeps", "vp"]
TRACES = "() => (document.getElementById('chartBox').data || []).map((t) => t.name)"


def open_market(browser, srv, **ctx):
    p = Page(browser, srv.url, viewport={"width": 1280, "height": 900}, **ctx)
    page = p.goto("/#/market")
    page.wait_for_selector("#chartBox .main-svg")
    return p, page


def test_three_independent_toggles_next_to_the_existing_ones_and_remembered(browser, live_server):
    p, page = open_market(browser, live_server)
    try:
        for ov in OVERLAYS:                                                   # every control once, none duplicated
            assert page.locator(f"#overlayToggles input[data-ov='{ov}']").count() == 1
        labels = page.locator("#overlayToggles label").all_text_contents()
        assert [x.strip() for x in labels[-3:]] == ["Market regime", "Liquidity sweeps", "Volume profile"]
        assert page.locator("#vpRange").is_hidden()
        page.locator("input[data-ov='regime']").check()
        page.locator("input[data-ov='vp']").check()
        assert page.locator("#vpRange").is_visible()
        page.reload()
        page.wait_for_selector("#chartBox .main-svg")
        assert page.locator("input[data-ov='regime']").is_checked() and page.locator("input[data-ov='vp']").is_checked()
        assert not page.locator("input[data-ov='sweeps']").is_checked()
        assert p.errors == []
    finally:
        p.context.close()


def test_regime_shading_covers_full_4h_periods_below_the_candles(browser, live_server):
    p, page = open_market(browser, live_server)
    try:
        page.locator("input[data-ov='regime']").check()
        page.wait_for_function(f"() => ({TRACES})().includes('Market regime')")
        shapes = page.evaluate("() => document.getElementById('chartBox').layout.shapes.filter((s) => s.name === 'regime')")
        assert shapes, "no regime shading"
        for s in shapes:
            assert s["layer"] == "below" and float(re.findall(r"[\d.]+\)$", s["fillcolor"])[0][:-1]) <= 0.1
            for k in ("x0", "x1"):                                            # whole 4h periods on the 1h chart
                ms = page.evaluate(f"() => Date.parse('{s[k]}')")
                assert ms % H4 == 0
        assert "BTC" in page.evaluate(TRACES)                                  # candles still drawn
        hover = page.evaluate("() => document.getElementById('chartBox').data.find((t) => t.name === 'Market regime').text")
        assert all(re.fullmatch(r".+ · \d+/100 · \d checks?", h) for h in hover)
        # never ahead of the chart: the last shaded / labelled 4h bar ended by the last closed 1h candle
        last_candle = page.evaluate("() => Date.parse(document.getElementById('chartBox').data[0].x.at(-1)) + 3600000")
        assert max(page.evaluate(f"() => Date.parse('{s['x1']}')") for s in shapes) <= last_candle
        page.locator("input[data-ov='regime']").uncheck()
        page.wait_for_function(f"() => !({TRACES})().includes('Market regime')")
        assert p.errors == []
    finally:
        p.context.close()


def test_sweep_markers_sit_on_the_real_candle_extremes_of_stored_events(browser, fresh_server):
    fresh_server.app.config["BTC_ADVANCED"].record()                        # the monitor's job: store the events
    stored = {e["event_key"] for e in fresh_server.db.sweep_events(500)}
    assert stored
    p, page = open_market(browser, fresh_server)
    try:
        page.locator("input[data-ov='sweeps']").check()
        with page.expect_request(lambda r: "/api/advanced/overlays?tf=4h" in r.url):    # refetched for the new tf
            page.locator("#tfSeg button[data-tf='4h']").click()
        page.wait_for_function(f"() => ({TRACES})().some((n) => n.startsWith('Liquidity sweep'))")
        pts = page.evaluate("""() => {
            const gd = document.getElementById('chartBox'), c = gd.data[0];
            return gd.data.filter((t) => t.name.startsWith('Liquidity sweep')).flatMap((t) => t.x.map((x, i) => {
                const k = c.x.indexOf(x);
                return {key: t.customdata[i], y: t.y[i], down: t.marker.symbol[i].startsWith('triangle-down'),
                        high: c.high[k], low: c.low[k], hover: t.hovertext[i], name: t.name};
            }));
        }""")
        assert pts
        for m in pts:
            assert m["key"] in stored                                          # only real stored events
            assert m["y"] == (m["high"] if m["down"] else m["low"])           # at the candle's actual high / low
            assert "Swing level" in m["hover"] and "strength" in m["hover"] and "Detected" in m["hover"]
            if "not confirmed" in m["hover"]:
                assert not m["name"].endswith("confirmed / reclaim")
        lines = page.evaluate("() => document.getElementById('chartBox').layout.shapes.filter((s) => s.name === 'sweep level').length")
        assert lines == len(pts)
        assert p.errors == []
    finally:
        p.context.close()


def test_volume_profile_visible_range_is_debounced_and_keeps_the_volume_bars(browser, live_server):
    p, page = open_market(browser, live_server)
    try:
        page.locator("input[data-ov='vp']").check()
        page.wait_for_function(f"() => ({TRACES})().includes('Volume profile')")
        assert "Volume" in page.evaluate(TRACES)                               # normal volume bars stay
        assert "the last 200 closed 1h candles" in page.locator("#chartNote").text_content()
        page.locator("#vpRange").select_option("visible")
        page.wait_for_function("() => document.getElementById('chartNote').textContent.includes('the visible range')")
        seen = []
        page.on("request", lambda r: seen.append(r.url) if "mode=visible" in r.url else None)
        page.evaluate("""async () => {
            const gd = document.getElementById('chartBox'), x = gd.data[0].x;
            for (const k of [150, 180, 200]) await Plotly.relayout(gd, {'xaxis.range': [x[k], x[x.length - 1]]});
        }""")
        page.wait_for_function("() => document.getElementById('chartNote').textContent.includes('the visible range 100 closed')",
                               timeout=60_000)                                # the debounced answer has been drawn
        p.idle()
        assert len(seen) == 1                                                 # three zooms, one request (300 ms)
        assert "the visible range 100 closed 1h candles" in page.locator("#chartNote").text_content()
        kept = page.evaluate("""() => { const gd = document.getElementById('chartBox');
            return Date.parse(gd.layout.xaxis.range[0]) === Date.parse(gd.data[0].x[200]); }""")
        assert kept                                                           # the redraw kept the zoom
        assert p.errors == []
    finally:
        p.context.close()


def test_fast_timeframe_switches_end_on_the_last_choice_with_its_overlays(browser, live_server):
    p, page = open_market(browser, live_server)
    try:
        page.locator("input[data-ov='regime']").check()
        for tf in ("4h", "15m", "1d", "4h"):                                  # quicker than the answers arrive
            page.locator(f"#tfSeg button[data-tf='{tf}']").click()
        page.wait_for_function(f"() => ({TRACES})().includes('Market regime')")     # the 1d labels may come first
        p.idle()                                                               # every answer, late ones too, handled
        step = page.evaluate("() => { const x = document.getElementById('chartBox').data[0].x; "
                             "return Date.parse(x[1]) - Date.parse(x[0]); }")
        assert step == H4                                                      # 4h candles, not a late 1d / 15m answer
        assert "Market regime" in page.evaluate(TRACES)
        assert p.errors == []
    finally:
        p.context.close()


def test_missing_overlay_data_shows_a_clear_note(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        p.page.route("**/api/advanced/overlays*", lambda r: r.fulfill(status=503, content_type="application/json",
                                                                      body=json.dumps({"error": "source down"})))
        page = p.goto("/#/market")
        page.wait_for_selector("#chartBox .main-svg")
        page.locator("input[data-ov='regime']").check()
        page.locator("input[data-ov='sweeps']").check()
        page.wait_for_function("() => document.getElementById('chartNote').textContent.includes('not available (source down)')")
        note = page.locator("#chartNote").text_content()
        assert "Market regime: not available" in note and "Liquidity sweeps: not available" in note
        assert "BTC" in page.evaluate(TRACES)                                  # the chart itself still works
        p.errors = [e for e in p.errors if "503" not in e]                     # the stubbed failure itself
        assert p.errors == []
    finally:
        p.context.close()
