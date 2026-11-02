"""Reaction study on the Global Intelligence page, and market freshness labels. Fake sources and synthetic candles, no network."""
from __future__ import annotations

import pytest

from e2e.conftest import Page
from e2e.e2e_helpers import no_horizontal_scroll


def open_view(p):
    page = p.goto("/#/intel")
    page.wait_for_selector("#intelStudy[data-state='ready']")
    page.wait_for_selector("#intelEvents[data-state='ready']")
    page.wait_for_selector("#intelMarkets[data-state='ready']")
    return page


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_study_is_labelled_and_says_what_it_is_not(browser, live_server, theme):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
        text = page.locator("#intelStudy").text_content()
        assert "Context, not part of the signal" in text
        low = text.lower()
        for phrase in ("correlation", "causation", "overlap", "descriptive", "consensus"):
            assert phrase in low, phrase
        assert page.locator("#intelStudyTable tbody tr[data-category]").count() >= 8
        assert page.locator("#intelStudyReleases tbody tr[data-kind]").count() == 5
        assert "CPI release" in page.locator("#intelStudyReleases").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_horizon_buttons_switch_the_tables(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        hs = page.locator("#intelStudyHorizons button[data-horizon]")
        assert hs.all_text_contents() == ["+1h", "+4h", "+24h", "+7d", "+30d"]
        assert page.locator("#intelStudyHorizons button[aria-pressed='true']").text_content() == "+24h"
        page.click("#intelStudyHorizons button[data-horizon='1h']")
        assert page.locator("#intelStudyHorizons button[data-horizon='1h']").get_attribute("aria-pressed") == "true"
        assert "+1h" in page.locator("#intelStudyHorizonNote").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_events_show_their_time_and_open_their_reaction(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        ev = page.locator("#intelEventList .intel-event", has_text="Federal Reserve issues FOMC statement").first
        assert "First seen" in ev.locator(".intel-time").text_content()
        ev.locator("details.intel-reaction summary").click()
        body = ev.locator("details.intel-reaction .intel-reaction-body[data-state='ready']")
        body.wait_for()
        text = body.text_content()
        for phrase in ("last closed candle", "+1h", "+30d", "Other markets", "Daily"):
            assert phrase in text, phrase
        assert body.locator("tr[data-horizon='1h'] .react-status").text_content().strip() == "Measured"
        assert body.locator("tr[data-horizon='30d'] .react-status").text_content().strip() == "Waiting"
        assert p.errors == []
    finally:
        p.context.close()


def test_hostile_headline_stays_text_in_the_reaction(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        ev = page.locator("#intelEventList .intel-event", has_text="hostile headline").first
        ev.locator("details.intel-reaction summary").click()
        ev.locator(".intel-reaction-body[data-state='ready']").wait_for()
        assert page.evaluate("window.__hacked") is None
        assert ev.locator(".intel-reaction-body script").count() == 0
        assert p.errors == []
    finally:
        p.context.close()


def test_markets_show_cadence_and_flag_what_is_older_than_normal(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        dollar = page.locator("#intelMarkets tr[data-series='usd_broad']")
        assert "Weekly" in dollar.locator(".mk-cadence").text_content()
        assert dollar.locator(".mk-stale").count() == 0
        vix = page.locator("#intelMarkets tr[data-series='vix']")                 # the fake VIX is 12 days old
        assert "normally" in vix.locator(".mk-stale").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_page_says_when_a_study_cannot_be_loaded(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        p.context.route("**/api/intel/reactions", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error":"boom"}'))
        page = p.goto("/#/intel")
        page.wait_for_selector("#intelStudy[data-state='error']")
        assert "boom" in page.locator("#intelStudy").text_content()
        assert page.locator("#intelEvents").count() == 1
    finally:
        p.context.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_phone_no_horizontal_scroll_with_the_study_open(browser, live_server, theme):
    p = Page(browser, live_server.url, viewport={"width": 390, "height": 844})
    try:
        page = open_view(p)
        page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
        ev = page.locator("#intelEventList .intel-event", has_text="Federal Reserve issues FOMC statement").first
        ev.locator("details.intel-reaction summary").click()
        ev.locator(".intel-reaction-body[data-state='ready']").wait_for()
        no_horizontal_scroll(page)
        for sel in ("#intelStudyTable tbody tr[data-category] td:first-child", "#intelStudyReleases tbody tr[data-kind] td:first-child",
                    "#intelMarkets tr[data-series='usd_broad'] td:first-child", ".intel-reaction-body tr[data-horizon='1h'] td:first-child"):
            assert page.locator(sel).first.get_attribute("data-label"), sel
        assert p.errors == []
    finally:
        p.context.close()
