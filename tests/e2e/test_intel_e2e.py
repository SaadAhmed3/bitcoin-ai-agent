"""Global Intelligence page: context only, event list + filters, macro calendar, other markets. Fake sources, no network."""
from __future__ import annotations

import pytest

from e2e.conftest import Page
from e2e.e2e_helpers import no_horizontal_scroll


def open_view(p):
    page = p.goto("/#/intel")
    page.wait_for_selector("#intelEvents[data-state='ready']")
    page.wait_for_selector("#intelMarkets[data-state='ready']")
    page.wait_for_selector("#intelCalendar[data-state='ready']")
    return page


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_page_shows_label_counts_events_calendar_markets(browser, live_server, theme):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
        assert "Context, not part of the signal" in page.locator("#intelBanner").text_content()
        assert page.locator("#intelCounts .intel-count").count() >= 8
        ev = page.locator("#intelEventList .intel-event").first
        for sel in (".intel-title", ".intel-status", ".intel-meta"):
            assert ev.locator(sel).count() == 1 and ev.locator(sel).text_content().strip()
        text = page.locator("#intelMarkets").text_content()
        for name in ("10-year", "dollar", "VIX", "Brent", "WTI", "S&P 500", "Gold"):
            assert name in text, name
        assert page.locator("#intelMarkets tr[data-series='gold'] .na").text_content().strip() == "Unavailable"
        assert page.locator("#intelMarkets tr[data-series='vix'] .mk-source").text_content().strip().startswith("Cboe")
        cal = page.locator("#intelCalendar").text_content()
        assert "UTC" in cal and "Asia/Karachi" in cal and ("upcoming" in cal.lower() or "released" in cal.lower())
        assert p.errors == []
    finally:
        p.close()


def test_filters_are_views_of_one_event_list(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        total = page.locator("#intelEventList .intel-event").count()
        assert total >= 3
        for group, category in (("war", "War / geopolitics"), ("crypto", None), ("macro", None), ("elections", None)):
            page.click(f"#intelFilters button[data-group='{group}']")
            p.idle()
            assert page.locator(f"#intelFilters button[data-group='{group}']").get_attribute("aria-pressed") == "true"
            shown = page.locator("#intelEventList .intel-event").count()
            assert shown <= total
            if category:
                assert shown >= 1 and all(category in c for c in page.locator("#intelEventList .intel-cat").all_text_contents())
        page.click("#intelFilters button[data-group='']")
        p.idle()
        assert page.locator("#intelEventList .intel-event").count() == total
        assert p.errors == []
    finally:
        p.close()


def test_links_open_safely_and_hostile_text_stays_text(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        links = page.locator("#intelEventList a")
        assert links.count() >= 3
        for i in range(links.count()):
            a = links.nth(i)
            assert a.get_attribute("target") == "_blank" and "noopener" in a.get_attribute("rel")
            assert a.get_attribute("href").startswith("https://")
        assert page.locator("#intelEventList script").count() == 0
        assert "<script>" in page.locator("#intelEventList").text_content()     # shown as text
        assert page.evaluate("window.__hacked === undefined")
    finally:
        p.close()


def test_health_of_intel_sources_shows_in_system(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/system")
        page.wait_for_selector("#intelHealthTable tbody tr td:not(.empty)")
        assert "gdelt" in page.locator("#intelHealthTable").text_content().lower()
        assert p.errors == []
    finally:
        p.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_phone_no_horizontal_scroll(browser, live_server, theme):
    p = Page(browser, live_server.url, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    try:
        page = open_view(p)
        page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
        assert no_horizontal_scroll(page) == []
        assert p.errors == []
    finally:
        p.close()


def test_empty_state_before_the_first_refresh(browser, fresh_server):
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        assert page.locator("#intelEventList .empty").count() == 1
        assert page.locator("#intelEventList .intel-event").count() == 0
        assert p.errors == []
    finally:
        p.close()
