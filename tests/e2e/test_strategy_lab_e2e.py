"""Strategy Lab view (round 1): five strategy cards, DCA live, comparison, settings with versioning."""
from __future__ import annotations

from e2e.conftest import Page, wait_view_ready


def test_strategy_lab_shows_five_cards_and_an_honest_comparison(browser, live_server):
    p = Page(browser, live_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/")
        page.get_by_role("link", name="Strategy Lab").click()
        wait_view_ready(page, "strategies")
        cards = page.locator("#stratCards .strat-card")
        assert cards.count() == 5
        titles = [t.strip() for t in page.locator("#stratCards .strat-card h3").all_text_contents()]
        assert titles == ["DCA (dollar-cost averaging)", "Trend following", "Range trading", "Breakout trading",
                          "Narrative / sector rotation"]
        dca = page.locator("#strat-dca")
        assert dca.locator(".strat-signal").text_content() in ("ACCUMULATE", "WAIT", "PAUSE")
        badges = dca.locator(".tag").all_text_contents()                  # one combined status, not four badges
        assert badges == ["Not validated"] or badges == ["Backtested on design data · not validated"]
        assert "Live data · calculated · simulated" in dca.locator(".strat-kinds").text_content()
        assert "not a prediction" in dca.text_content()
        for sid, rnd in (("trend", 2), ("range", 3), ("breakout", 4), ("rotation", 5)):
            assert f"round {rnd}" in page.locator(f"#strat-{sid}").text_content()
        table = page.locator("#stratCompare")
        assert table.locator("thead th").all_text_contents() == ["Metric", "DCA", "Trend", "Range", "Breakout", "Rotation"]
        assert "N/A" in table.text_content() and "not ranked" in page.locator("#stratCompareNote").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_changing_dca_settings_marks_it_not_validated_and_reset_restores(browser, fresh_server):
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/strategies")
        form = page.locator("#stratCfg-dca")
        form.locator("summary").click()
        form.locator("select[name='interval']").select_option("monthly")
        page.once("dialog", lambda d: (p.errors.append("no warning") if "NOT VALIDATED" not in d.message else None, d.accept()))
        form.locator("button[type='submit']").click()
        page.wait_for_function("() => document.querySelector('#strat-dca .strat-status').textContent.includes('settings changed')")
        assert "version 2" in form.text_content()
        form.locator("button[data-reset]").click()
        page.wait_for_function("() => !document.querySelector('#strat-dca .strat-status').textContent.includes('settings changed')")
        assert "version 3" in form.text_content()
        assert page.locator("#stratHistory").count() == 1
        assert p.errors == []
    finally:
        p.context.close()


def test_card_text_is_never_clipped_and_the_dca_status_is_one_badge(browser, fresh_server):
    from services.backtesting import HOLDOUT_START
    from services.strategies import dca
    from test_strategy_dca import daily
    rows = daily(900, start=HOLDOUT_START - 700 * 86_400_000, price=20_000, step=15).to_dict("records")
    h = fresh_server.app.config["BTC_STRATEGY_LAB"].config("dca")["hash"]
    fresh_server.db.strategy_backtest_add("dca", 1, h, "design", "x", dca.backtest(rows, dca.validate_config({})), now=1)
    for width in (1280, 390):
        p = Page(browser, fresh_server.url, viewport={"width": width, "height": 700})
        try:
            page = p.goto("/#/strategies")
            page.wait_for_selector("#stratLab[data-state='ready']")
            clipped = page.evaluate("""() => [...document.querySelectorAll('.strat-card p, .strat-card h3')]
                .filter((e) => e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1
                               || getComputedStyle(e).textOverflow === 'ellipsis').map((e) => e.textContent)""")
            assert clipped == []
            assert page.locator("#strat-dca .tag").all_text_contents() == ["Backtested on design data · not validated"]
            assert p.errors == []
        finally:
            p.context.close()
