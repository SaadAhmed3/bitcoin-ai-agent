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
        assert cards.count() == 6
        titles = [t.strip() for t in page.locator("#stratCards .strat-card h3").all_text_contents()]
        assert titles == ["DCA (dollar-cost averaging)", "Trend following", "Range trading", "Breakout trading",
                          "Breakout-0.5R (variant)", "Narrative / sector rotation"]
        variant = page.locator("#strat-breakout_05r").text_content().lower()
        assert "chosen after design results: forward test only, no design verdict" in variant and "failed" not in variant
        dca = page.locator("#strat-dca")
        assert dca.locator(".strat-signal").text_content() in ("ACCUMULATE", "WAIT", "PAUSE")
        badges = dca.locator(".tag").all_text_contents()                  # one combined status, not four badges
        assert len(badges) == 1 and (badges[0] == "Not validated" or badges[0].endswith(" on design data · not validated"))
        assert "Live data · calculated · simulated" in dca.locator(".strat-kinds").text_content()
        assert "not a prediction" in dca.text_content()
        assert "round 5" in page.locator("#strat-rotation").text_content()   # trend, range, breakout: live
        assert page.locator("#strat-breakout .strat-signal").text_content() in (
            "BUY SETUP", "HOLD", "NO TRADE", "EXIT / REDUCE EXPOSURE")
        table = page.locator("#stratCompare")
        assert table.locator("thead th").all_text_contents() == ["Metric", "DCA", "Trend", "Range", "Breakout",
                                                                 "Breakout-0.5R (forward only)", "Rotation"]
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
            verdict = {"PASS": "PASSED", "FAIL": "FAILED", "INCONCLUSIVE": "INCONCLUSIVE"}[
                fresh_server.db.strategy_backtests("dca")[0]["result"]["verdict"]]
            assert page.locator("#strat-dca .tag").all_text_contents() == [f"{verdict} on design data · not validated"]
            assert p.errors == []
        finally:
            p.context.close()


def test_trend_card_and_details_round_2(browser, fresh_server):
    from services.strategies import trend
    lab = fresh_server.app.config["BTC_STRATEGY_LAB"]
    h = lab.config("trend")["hash"]
    fresh_server.db.strategy_backtest_add("trend", 1, h, "design", "x", {
        "verdict": "INCONCLUSIVE", "sample_warning": "Only 12 trades (fewer than 30): too few to judge; the verdict is INCONCLUSIVE.",
        "period": {"first_day": 1_700_000_000_000, "last_day": 1_760_000_000_000, "holdout_start": 1_761_696_000_000},
        "config": trend.validate_config({}),
        "metrics": {"net_return_pct": -3.2, "win_rate_pct": 41.0, "profit_factor": 0.9, "max_drawdown_pct": 6.5,
                    "trades": 12, "expectancy_r": -0.05, "avg_reward_risk": 2.0, "costs_usd": None, "costs_pct": 1.2,
                    "buy_hold_return_pct": 80.0},
        "criteria": [{"id": "C1", "rule": "expectancy >= +0.15R", "value": -0.05, "passed": False}],
        "buy_and_hold": {"net_return_pct": 80.0, "max_drawdown_pct": 55.0},
        "h2_rule": {"net_return_pct": 40.0, "max_drawdown_pct": 30.0, "entries": 3},
        "random_entries": {"runs": 1000, "beaten_pct": 35.0, "median_return_pct": -1.0},
        "walk_forward": [{"start": 1_700_000_000_000, "end": 1_715_000_000_000, "trades": 3, "return_pct": 1.0, "sum_r": 0.5}],
        "sensitivity": [{"adx_min": 20.0, "target_r": 2.0, "trades": 12, "expectancy_r": -0.05, "profit_factor": 0.9,
                         "max_drawdown_pct": 6.5, "net_return_pct": -3.2, "is_default": True}]}, now=1)
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/strategies")
        page.wait_for_selector("#stratLab[data-state='ready']")
        card = page.locator("#strat-trend")
        assert card.locator(".strat-signal").text_content() in ("BUY SETUP", "HOLD", "NO TRADE", "EXIT / REDUCE EXPOSURE")
        assert card.locator(".tag").all_text_contents() == ["INCONCLUSIVE on design data · not validated"]
        assert "4h" in card.text_content() and "differently from H2" in card.text_content()
        page.locator("#stratDetailPick").select_option("trend")
        page.wait_for_function("() => document.getElementById('stratDetailTitle').textContent.startsWith('Trend following')")
        bt = page.locator("#stratBacktest").text_content()
        for words in ("INCONCLUSIVE", "fewer than 30", "Buy & hold", "H2 rule", "random", "Walk-forward", "Sensitivity"):
            assert words in bt, words
        assert page.locator("#stratCfg-trend").count() == 1 and page.locator("#stratCfg-dca").count() == 0
        assert p.errors == []
    finally:
        p.context.close()


def test_a_failed_strategy_says_so_on_its_card_and_in_the_comparison(browser, fresh_server):
    lab = fresh_server.app.config["BTC_STRATEGY_LAB"]
    fresh_server.db.strategy_backtest_add("trend", 1, lab.config("trend")["hash"], "design", "x",
                                          {"verdict": "FAIL", "metrics": {"trades": 94, "buy_hold_return_pct": 217.05}}, now=1)
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/strategies")
        page.wait_for_selector("#stratLab[data-state='ready']")
        card = page.locator("#strat-trend")
        assert card.locator(".tag").all_text_contents() == ["FAILED on design data · not validated"]
        assert card.locator(".strat-design-warning").text_content() == "Failed on design data: research only"
        row = page.locator("#stratCompare tbody tr", has_text="Validation status")
        assert "FAILED on design data · not validated" in row.text_content()
        bh = page.locator("#stratCompare tbody tr", has_text="Buy-and-hold comparison").text_content()
        assert "(buy & hold, same window)" in bh
        assert p.errors == []
    finally:
        p.context.close()


def test_range_card_and_details_round_3(browser, fresh_server):
    from services.strategies import range as rng
    lab = fresh_server.app.config["BTC_STRATEGY_LAB"]
    fresh_server.db.strategy_backtest_add("range", 1, lab.config("range")["hash"], "design", "x", {
        "verdict": "FAIL", "sample_warning": None,
        "period": {"first_day": 1_700_000_000_000, "last_day": 1_760_000_000_000, "holdout_start": 1_761_696_000_000},
        "config": rng.validate_config({}), "regime_share_pct": 21.5, "valid_range_share_pct": 3.2,
        "metrics": {"net_return_pct": -2.0, "win_rate_pct": 50.0, "profit_factor": 0.9, "max_drawdown_pct": 4.0,
                    "trades": 40, "expectancy_r": -0.02, "avg_reward_risk": 1.4, "costs_usd": None, "costs_pct": 2.0,
                    "buy_hold_return_pct": 200.0},
        "criteria": [{"id": "C1", "rule": "expectancy >= +0.15R", "value": -0.02, "passed": False}],
        "buy_and_hold": {"net_return_pct": 200.0, "max_drawdown_pct": 77.0},
        "random_entries": {"runs": 1000, "beaten_pct": 60.0, "median_return_pct": -1.0},
        "walk_forward": [{"start": 1_700_000_000_000, "end": 1_715_000_000_000, "trades": 5, "return_pct": 0.5, "sum_r": 0.4}],
        "sensitivity": [{"lookback": 50, "adx_max": 20.0, "target": "mid", "trades": 40, "expectancy_r": -0.02,
                         "profit_factor": 0.9, "max_drawdown_pct": 4.0, "net_return_pct": -2.0, "is_default": True},
                        {"lookback": 50, "adx_max": 20.0, "target": "top", "trades": 38, "expectancy_r": 0.01,
                         "profit_factor": 1.0, "max_drawdown_pct": 5.0, "net_return_pct": -1.0, "is_default": False}]}, now=1)
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/strategies")
        page.wait_for_selector("#stratLab[data-state='ready']")
        card = page.locator("#strat-range")
        assert card.locator(".tag").all_text_contents() == ["FAILED on design data · not validated"]
        assert card.locator(".strat-design-warning").text_content() == "Failed on design data: research only"
        page.locator("#stratDetailPick").select_option("range")
        page.wait_for_function("() => document.getElementById('stratDetailTitle').textContent.startsWith('Range trading')")
        bt = page.locator("#stratBacktest").text_content()
        for words in ("FAIL", "Buy & hold", "random", "Walk-forward", "Sensitivity", "Lookback", "top", "SIDEWAYS"):
            assert words in bt, words
        assert "H2 rule" not in bt                                         # only trend compares with H2
        assert p.errors == []
    finally:
        p.context.close()
