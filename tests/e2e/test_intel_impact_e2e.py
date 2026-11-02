"""AI summary card, impact panel and accuracy tracker on the Global Intelligence page. Fake sources, Ollama off, no network."""
from __future__ import annotations

import pytest

from e2e.conftest import Page
from e2e.e2e_helpers import no_horizontal_scroll


def open_view(p):
    page = p.goto("/#/intel")
    for sel in ("intelSummaries", "intelTracker", "intelEvents"):
        page.wait_for_selector(f"#{sel}[data-state='ready']")
    return page


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_summary_card_says_unavailable_and_still_shows_a_stored_summary(browser, live_server, theme):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
        card = page.locator("#intelSummaries")
        assert "Context, not part of the signal" in card.text_content()
        assert "AI summary unavailable" in card.text_content()
        cited = card.locator(".intel-summary[data-has-summary='true']").first
        link = cited.locator("a.intel-cite").first
        assert link.get_attribute("href").startswith("https://") and link.get_attribute("rel") == "noopener noreferrer"
        assert "seeded summary point" in cited.text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_impact_panel_shows_labels_factors_and_competing_readings(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        ev = page.locator("#intelEventList .intel-event", has_text="Russia launches missile strikes").first
        ev.locator("details.intel-impact summary").click()
        body = ev.locator(".intel-impact-body[data-state='ready']")
        body.wait_for()
        text = body.text_content()
        for phrase in ("0-24h", "1-7d", "1-3 months", "not a probability", "Severity", "Source credibility", "Verification", "Novelty",
                       "Market relevance", "Already priced in", "Historical reaction", "One reading", "Another reading"):
            assert phrase in text, phrase
        assert body.locator("tr[data-horizon='0-24h'] .impact-label").text_content().strip() == "Uncertain"      # war alone gives no direction
        assert [body.locator("tr[data-horizon]").nth(i).get_attribute("data-horizon") for i in range(3)] == ["0-24h", "1-7d", "1-3m"]
        assert p.errors == []
    finally:
        p.context.close()


def test_tracker_says_small_samples_prove_nothing_and_hides_the_rate(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        card = page.locator("#intelTracker")
        text = card.text_content()
        assert "small samples prove nothing" in text.lower() and "PREREGISTRATION_NEWS_IMPACT.md" in text
        assert card.locator("tr[data-horizon]").count() == 3
        assert [card.locator("tr[data-horizon]").nth(i).get_attribute("data-horizon") for i in range(3)] == ["0-24h", "1-7d", "1-3m"]
        assert "Always Neutral" in text
        assert card.locator("tr[data-horizon='0-24h'] .tracker-rate").text_content().strip() == "Hidden until 30 scored, 20 episodes"
        assert p.errors == []
    finally:
        p.context.close()


def test_hostile_summary_text_stays_text(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = open_view(p)
        assert page.evaluate("window.__hacked") is None
        assert page.locator("#intelSummaries script").count() == 0
        assert p.errors == []
    finally:
        p.context.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_phone_layout(browser, live_server, theme):
    p = Page(browser, live_server.url, viewport={"width": 390, "height": 844})
    try:
        page = open_view(p)
        page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
        ev = page.locator("#intelEventList .intel-event").first
        ev.locator("details.intel-impact summary").click()
        ev.locator(".intel-impact-body[data-state='ready']").wait_for()
        no_horizontal_scroll(page)
        assert page.locator("#intelTracker tr[data-horizon='0-24h'] td").first.get_attribute("data-label")
        assert p.errors == []
    finally:
        p.context.close()
