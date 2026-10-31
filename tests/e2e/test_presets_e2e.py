"""Chart presets and the shared NO TRADE text on the Overview and Signal views (UI/text only)."""
from __future__ import annotations

import re

from e2e.conftest import Page

OVS = ["ema", "bb", "vwap", "levels", "alerts", "markers", "regime", "sweeps", "vp"]
PRESETS = {"clean": set(), "trend": {"ema", "levels", "regime"}, "levels": {"levels", "vp", "sweeps"}}


def checked(page) -> set:
    return {ov for ov in OVS if page.locator(f"#overlayToggles input[data-ov='{ov}']").is_checked()}


def test_presets_set_the_toggles_and_are_remembered(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/market")
        sel = page.locator("#chartPreset")
        assert page.locator("#chartPreset").count() == 1
        assert sel.locator("option").all_text_contents() == ["Clean", "Trend", "Levels", "Custom"]
        for name, on in PRESETS.items():
            sel.select_option(name)
            page.wait_for_timeout(300)
            assert checked(page) == on, name
        sel.select_option("trend")
        page.locator("#overlayToggles input[data-ov='bb']").check()            # a manual change -> Custom
        assert sel.input_value() == "custom" and checked(page) == {"ema", "levels", "regime", "bb"}
        sel.select_option("custom")                                             # Custom keeps the current toggles
        assert checked(page) == {"ema", "levels", "regime", "bb"}
        sel.select_option("levels")
        page.reload()
        page.wait_for_selector("#chartBox .main-svg")
        assert page.locator("#chartPreset").input_value() == "levels" and checked(page) == PRESETS["levels"]
        assert p.errors == []
    finally:
        p.context.close()


def test_support_resistance_bands_get_lighter_with_the_volume_profile(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/market")
        alphas = """() => (document.getElementById('chartBox').layout.shapes || [])
            .filter((s) => s.name === 'sr band').map((s) => parseFloat(s.fillcolor.split(',').pop()))"""
        page.locator("#chartPreset").select_option("trend")                    # S/R on, profile off
        page.wait_for_function(f"() => ({alphas})().length > 0")
        assert all(a > 0.1 for a in page.evaluate(alphas))
        page.locator("#overlayToggles input[data-ov='vp']").check()
        page.wait_for_function("() => (document.getElementById('chartBox').data || []).some((t) => t.name === 'Volume profile')")
        a = page.evaluate(alphas)
        assert a and all(x <= 0.06 for x in a)
        assert p.errors == []
    finally:
        p.context.close()


def test_overview_and_signal_show_the_same_no_trade_summary(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/")
        s = page.evaluate("async () => (await (await fetch('/api/signal', {headers: {'X-Requested-With': 'BTCSignalLab'}})).json())")
        assert s["signal"] == "NO TRADE" and s["no_trade_summary"]
        assert page.locator("#sumReason").text_content() == s["no_trade_summary"]
        assert not re.search(r"didn.t pass", page.locator("#sumReason").text_content())
        page = p.view("signal")
        assert page.locator("#sigGates .gate-summary").text_content() == s["no_trade_summary"]
        assert p.errors == []
    finally:
        p.context.close()
