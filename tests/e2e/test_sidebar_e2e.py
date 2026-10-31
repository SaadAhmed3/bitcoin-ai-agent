"""UI: collapsible sidebar (icon rail on desktop, drawer on phones) and the Forward tests table.

UI only: the rule/tracker code is not touched, so the forward-test fingerprint must stay "unchanged since the start".
"""
from __future__ import annotations

import datetime as dt

import pytest

from e2e.conftest import Page
from e2e.e2e_helpers import contrast, no_horizontal_scroll

DAY = 86_400_000


def wait_ready(page):
    page.wait_for_selector("main[data-view-ready='1']")


# ------------------------------------------------------------------ desktop: icon rail
def test_sidebar_collapses_to_an_icon_rail_and_remembers_it(browser, fresh_server):
    fresh_server.app.config["BTC_ALERTS"].create_test()                 # one unread alert: the badge must show
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/overview")
        wait_ready(page)
        side, btn = page.locator("#sidebar"), page.locator("#collapseBtn")
        assert side.bounding_box()["width"] > 200 and btn.get_attribute("aria-expanded") == "true"
        assert btn.get_attribute("aria-label") == "Collapse sidebar"
        main_before = page.locator("main").bounding_box()["width"]
        page.keyboard.press("Tab")                                       # the toggle is the first focusable control
        assert page.evaluate("document.activeElement.id") == "collapseBtn"
        assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"   # visible focus
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.querySelector('.shell').classList.contains('collapsed')")
        page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().width < 80")
        assert btn.get_attribute("aria-expanded") == "false" and btn.get_attribute("aria-label") == "Expand sidebar"
        assert page.locator("main").bounding_box()["width"] > main_before + 150      # the content uses the space
        overview = page.get_by_role("link", name="Overview")
        assert overview.is_visible()                                     # icon link keeps its accessible name
        overview.hover()
        tip = page.evaluate("getComputedStyle(document.querySelector('nav a[data-view=overview]'), '::after').content")
        assert tip == '"Overview"'
        page.wait_for_function("() => !document.getElementById('navAlertBadge').hidden")
        badge, rail = page.locator("#navAlertBadge").bounding_box(), side.bounding_box()
        assert badge and badge["x"] + badge["width"] <= rail["x"] + rail["width"] + 1   # visible inside the rail
        page.reload()
        wait_ready(page)
        assert page.evaluate("document.querySelector('.shell').classList.contains('collapsed')")   # remembered
        page.locator("#collapseBtn").click()
        page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().width > 200")
        assert p.errors == []
    finally:
        p.context.close()


def test_settings_page_sidebar_collapses_too(browser, fresh_server):
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/settings", ready=False)
        page.wait_for_selector("#general[data-state='ready']")
        page.locator("#collapseBtn").click()
        page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().width < 80")
        assert page.get_by_role("navigation", name="Settings sections").get_by_role("link", name="Dashboard").is_visible()
        page.goto(fresh_server.url + "/#/overview")                      # same preference on the dashboard
        wait_ready(page)
        assert page.evaluate("document.querySelector('.shell').classList.contains('collapsed')")
        assert p.errors == []
    finally:
        p.context.close()


# ------------------------------------------------------------------ phones: off-canvas drawer with an overlay
@pytest.mark.parametrize("path", ["/#/overview", "/settings"])
def test_mobile_drawer_has_an_overlay_and_closes_on_overlay_click_or_escape(browser, fresh_server, path):
    p = Page(browser, fresh_server.url, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    try:
        page = p.goto(path, ready=False)
        page.wait_for_selector("#menuBtn")
        assert not page.locator("#collapseBtn").is_visible() and page.locator("#navScrim").is_hidden()
        menu, side = page.locator("#menuBtn"), page.locator("#sidebar")
        menu.click()
        page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().x >= 0")
        assert menu.get_attribute("aria-expanded") == "true" and page.locator("#navScrim").is_visible()
        page.mouse.click(370, 400)                                       # on the overlay, outside the drawer
        page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().right <= 1")
        assert menu.get_attribute("aria-expanded") == "false" and page.locator("#navScrim").is_hidden()
        menu.click()
        page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().x >= 0")
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().right <= 1")
        assert page.evaluate("document.activeElement.id") == "menuBtn"
        assert side.get_attribute("class") == "sidebar" and no_horizontal_scroll(page) == []
        assert p.errors == []
    finally:
        p.context.close()


# ------------------------------------------------------------------ Forward tests table
def seed_forward(srv, days_done=5):
    """Forward tests that started `days_done` days ago, plus synthetic Binance daily candles up to yesterday."""
    import numpy as np
    from database import now_ms
    from services.forward_tests import RULE_TEXT, COMMON, current_fingerprint
    db = srv.db
    today = now_ms() // DAY * DAY
    start = today - days_done * DAY
    closes = 30_000 * np.exp(np.cumsum(np.random.default_rng(4).normal(0.002, 0.02, 450)))
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        ot = today - (450 - i) * DAY
        rows.append({"open_time": ot, "open": float(prev), "high": float(max(prev, c)), "low": float(min(prev, c)),
                     "close": float(c), "volume": 1.0, "close_time": ot + DAY - 1})
        prev = c
    db.upsert_candles("binance", "BTCUSDT", "1d", rows, now_ms())
    db.forward_start([{"name": n, "rules": {**RULE_TEXT[n], "common": COMMON}, "fingerprint": current_fingerprint(),
                       "start_day": start} for n in ("H1", "H2", "BH")], now_ms())
    srv.app.config["BTC_FORWARD"].process()
    return start


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_forward_table_layout_badges_and_fingerprint(browser, fresh_server, theme):
    seed_forward(fresh_server)
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    p.context.grant_permissions(["clipboard-read", "clipboard-write"])
    try:
        page = p.goto("/#/research")
        page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
        page.wait_for_function("() => document.getElementById('fwdProgress').textContent.startsWith('Day 5 of 365')")
        heads = [h.text_content() for h in page.locator("#fwdTable thead th").all()]
        assert heads == ["Rule", "Position", "Net return", "Max DD", "Trades", "In market", "Last decision"]
        assert page.locator("#fwdBar").evaluate("b => [b.value, b.max]") == [5, 365]
        assert page.locator("#fwdNote").is_hidden()
        h2 = page.locator("#fwdBody tr[data-rule='H2']")
        assert "prereg" in (h2.get_attribute("class") or "") and "pre-registered test" in h2.text_content()
        assert h2.locator(".role").text_content().startswith("pre-registered forward test")
        bh = page.locator("#fwdBody tr[data-rule='BH']")
        assert bh.locator(".pos-badge").text_content() == "IN" and "in" in bh.locator(".pos-badge").get_attribute("class")
        for cell in page.locator("#fwdBody td.num").all():
            style = cell.evaluate("c => [getComputedStyle(c).textAlign, getComputedStyle(c).whiteSpace]")
            assert style == ["right", "nowrap"]
            lines = cell.evaluate("c => { const r = document.createRange(); r.selectNodeContents(c); "
                                  "return new Set([...r.getClientRects()].map((x) => Math.round(x.top))).size; }")
            assert lines == 1                                              # one line, no wrapping
        fp = page.locator("#fwdFingerprint")
        assert fp.locator(".fp-badge.ok").text_content() == "unchanged since the start"
        from services.forward_tests import current_fingerprint
        page.locator("#fwdCopy").click()
        page.wait_for_function("() => document.getElementById('fwdCopy').textContent === 'Copied'")
        assert page.evaluate("navigator.clipboard.readText()") == current_fingerprint()
        assert contrast(page, "#fwdBody tr[data-rule='BH'] .pos-badge") >= 4.5
        assert contrast(page, "#fwdFingerprint .fp-badge") >= 4.5
        assert p.errors == []
    finally:
        p.context.close()


def test_forward_table_fits_a_phone_without_page_scroll(browser, fresh_server):
    seed_forward(fresh_server)
    p = Page(browser, fresh_server.url, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    try:
        page = p.goto("/#/research")
        page.wait_for_function("() => document.getElementById('fwdProgress').textContent.startsWith('Day 5 of 365')")
        assert no_horizontal_scroll(page) == []
        assert page.locator("#fwdBody tr[data-rule='H2'] td.num").first.get_attribute("data-label") == "Net return"
        assert p.errors == []
    finally:
        p.context.close()
