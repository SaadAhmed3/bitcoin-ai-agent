"""Advanced market analysis on Market & chart: regime, sweeps, volume profile. Display + shadow only."""
from __future__ import annotations

import pytest

from e2e.conftest import Page
from e2e.e2e_helpers import contrast, no_horizontal_scroll

REGIMES = ("TRENDING BULLISH", "TRENDING BEARISH", "SIDEWAYS / RANGING", "HIGH VOLATILITY", "LOW VOLATILITY",
           "TRANSITIONAL / UNCERTAIN")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_market_view_shows_the_three_panels_as_context(browser, live_server, theme):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/market")
        page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
        page.wait_for_selector("#advanced[data-state='ready']", timeout=60_000)   # loaded: a state, not a time
        card = page.locator("#advanced")
        assert card.is_visible()
        assert card.locator("h2").first.text_content() == "Advanced market analysis"
        assert "Context, not part of the score" in card.text_content()
        assert page.locator("#advRegime .adv-label").text_content() in REGIMES
        conf = page.locator("#advRegime .adv-conf")
        assert "/ 100 · " in conf.text_content() and conf.text_content().endswith("checks")   # heuristic + basis
        assert conf.get_attribute("title").startswith("Heuristic, not a probability")
        assert page.locator("#advSweep").text_content().strip()
        vp = page.locator("#advProfile").text_content()
        for k in ("POC", "VAH", "VAL", "$"):
            assert k in vp
        assert "200 closed 4h candles" in vp
        assert contrast(page, "#advRegime .adv-label") >= 4.5
        assert p.errors == []
    finally:
        p.context.close()


def test_volume_profile_overlay_toggles_on_the_chart(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/market")
        names = "() => (document.getElementById('chartBox').data || []).map((t) => t.name)"
        assert "Volume profile" not in page.evaluate(names)                         # off by default
        page.locator("#overlayToggles input[data-ov='vp']").check()
        page.wait_for_function(f"() => ({names})().includes('Volume profile')")
        shapes = page.evaluate("() => document.getElementById('chartBox').layout.shapes.map((s) => s.name || '')")
        assert {"POC", "VAH", "VAL"} <= set(shapes)
        assert "vp" in page.locator("#chartBox").get_attribute("data-overlays")
        page.locator("#advProfileTf").select_option("1d")                           # the 1D profile, on demand
        page.wait_for_function("() => document.getElementById('advProfile').textContent.includes('1d candles')")
        page.locator("#overlayToggles input[data-ov='vp']").uncheck()
        page.wait_for_function(f"() => !({names})().includes('Volume profile')")
        assert p.errors == []
    finally:
        p.context.close()


def test_signal_view_shows_the_context_line_and_the_live_engine_is_unchanged(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/signal")
        ctx = page.locator("#signal .ctx-note")
        page.wait_for_function("() => document.querySelector('#signal .ctx-note').textContent.includes('Regime')")
        assert ctx.text_content().startswith("Context, not part of the score")
        assert "signal-1.8" in page.locator("#signal").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_settings_has_the_shadow_switch_and_weights(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/settings", ready=False)
        page.wait_for_selector("#advanced[data-state='ready']")
        sec = page.locator("#advanced")
        assert "never part of its score" in sec.text_content()
        assert sec.locator("input[name='shadow_enabled']").is_checked()
        assert sec.locator("input[name='weight_regime']").input_value() == "0.1"
        assert sec.locator("input[name='weight_regime']").get_attribute("max") == "0.3"
        assert p.errors == []
    finally:
        p.context.close()


def test_panels_fit_a_phone(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    try:
        page = p.goto("/#/market")
        page.locator("#advanced").scroll_into_view_if_needed()
        assert no_horizontal_scroll(page) == []
        assert p.errors == []
    finally:
        p.context.close()
