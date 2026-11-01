"""Strategy charts in the Strategy Lab: Chart tab (live, history, forward) and the comparison charts."""
from __future__ import annotations

import pytest

from e2e.conftest import Page
from e2e.e2e_helpers import no_horizontal_scroll


@pytest.fixture
def lab_server(fresh_server):
    """Synthetic binance design candles and a stored trend result (verdict FAIL), like the research script stores."""
    from functools import partial

    from services.strategies import trend
    from test_strategy_charts import shifted_trend
    df4, df1 = shifted_trend()
    db = fresh_server.db
    db.upsert_candles("binance", "BTCUSDT", "4h", df4.to_dict("records"), 1)
    db.upsert_candles("binance", "BTCUSDT", "1d", df1.to_dict("records"), 1)
    res = partial(trend.backtest, random_runs=20)(df4.to_dict("records"), df1.to_dict("records"), trend.validate_config({}))
    res["verdict"] = "FAIL"
    db.strategy_backtest_add("trend", 1, res["config_hash"], "design", "sha", res, now=1)
    fresh_server.trend_result = res
    return fresh_server


def open_chart_tab(browser, srv, sid, **ctx):
    p = Page(browser, srv.url, **({"viewport": {"width": 1280, "height": 900}} | ctx))
    page = p.goto("/#/strategies")
    page.wait_for_selector("#stratLab[data-state='ready']")
    page.locator("#stratDetailPick").select_option(sid)
    page.wait_for_function(f"() => document.getElementById('stratDetail').dataset.sid === '{sid}'")
    page.locator("#stratTabs button[data-tab='chart']").click()
    page.wait_for_selector("#stratLiveChart .main-svg")
    return p, page


def names(page, box):
    return page.evaluate(f"() => (document.getElementById('{box}').data || []).map((t) => t.name)")


def test_trend_live_chart_shows_its_own_indicators_the_checklist_and_the_banner(browser, lab_server):
    p, page = open_chart_tab(browser, lab_server, "trend")
    try:
        n = names(page, "stratLiveChart")
        assert {"EMA 21", "EMA 50", "EMA 200", "ADX(14)", "Daily EMA 50"} <= set(n)
        assert not {"RSI 14", "VWAP", "Bollinger"} & set(n)                      # only what the rules use
        items = page.locator("#stratChecklist li").all_text_contents()
        assert len(items) >= 6 and all(i[0] in "✓✗" for i in items)
        assert page.locator("#stratLiveBanner").text_content() == "Failed on design data: research only"
        assert "How to read this chart" in page.locator("#stratLiveHow").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_history_markers_are_the_stored_trades_and_a_click_zooms_to_one(browser, lab_server):
    res = lab_server.trend_result
    p, page = open_chart_tab(browser, lab_server, "trend")
    try:
        page.wait_for_function(f"() => document.querySelectorAll('#stratTradeList tbody tr').length === {len(res['trade_list'])}",
                               timeout=60_000)
        entries = page.evaluate("""() => document.getElementById('stratHistChart').data
            .filter((t) => t.name === 'Entries').flatMap((t) => t.x.map((x) => Date.parse(x)))""")
        assert sorted(entries) == sorted(t["entry_time"] for t in res["trade_list"])
        assert {"Equity", "Buy & hold", "Drawdown"} <= set(names(page, "stratHistChart"))
        page.locator("#stratTradeList tbody tr").first.click()
        t0 = res["trade_list"][0]
        page.wait_for_function("""(t) => { const r = document.getElementById('stratHistChart').layout.xaxis.range;
            return r && Date.parse(r[0]) <= t.e && Date.parse(r[1]) >= t.x; }""", arg={"e": t0["entry_time"], "x": t0["exit_time"]})
        fitted = page.evaluate("""() => { const gd = document.getElementById('stratHistChart');
            const y = gd.layout.yaxis.range, px = gd.data.find((t) => t.name === 'Price').y;
            return (y[1] - y[0]) < (Math.max(...px) - Math.min(...px)) / 2; }""")
        assert fitted                                                              # the price axis fits the trade
        assert "verified" in page.locator("#stratHistSource").text_content()
        assert "empty" in page.locator("#stratFwdChart").text_content().lower()       # no forward day yet
        assert p.errors == []
    finally:
        p.context.close()


def test_dca_and_range_live_charts(browser, lab_server):
    p, page = open_chart_tab(browser, lab_server, "dca")
    try:
        assert {"BTC", "Scheduled buy days"} <= set(names(page, "stratLiveChart"))
        page.locator("#stratDetailPick").select_option("range")
        page.wait_for_function("() => (document.getElementById('stratLiveChart').data || []).some((t) => t.name === 'Range high')",
                               timeout=60_000)
        assert {"Range high", "Range low", "Midpoint", "ADX(14)"} <= set(names(page, "stratLiveChart"))
        assert p.errors == []
    finally:
        p.context.close()


def test_comparison_charts_are_hypothetical_and_unranked(browser, lab_server):
    p = Page(browser, lab_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/strategies")
        page.wait_for_selector("#stratCmpEquity .main-svg")
        assert "Trend following" in names(page, "stratCmpEquity")
        assert page.evaluate("() => document.getElementById('stratCmpEquity').data[0].y[0]") == 100
        assert page.evaluate("() => document.getElementById('stratCmpScatter').data[0].x.length") >= 1
        assert "not ranked" in page.locator("#stratCmpHow").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_chart_tab_fits_a_phone(browser, lab_server):
    p, page = open_chart_tab(browser, lab_server, "trend", viewport={"width": 390, "height": 844}, is_mobile=True,
                             has_touch=True)
    try:
        assert no_horizontal_scroll(page) == []
        anns = page.evaluate("() => (document.getElementById('stratLiveChart').layout.annotations || []).map((a) => a.text)")
        assert not any("No setup" in a for a in anns)                              # on a phone: the list below instead
        assert page.locator("#stratChecklist li").count() >= 6
        assert p.errors == []
    finally:
        p.context.close()
