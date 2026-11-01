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


def rendered(page, box, tag, timeout=90_000):
    """Deterministic "chart rendered" signal: the chart sets data-rendered to its strategy id (or "compare") only after
    Plotly has drawn it, or after its empty state is shown; it is cleared when a new render starts."""
    page.wait_for_selector(f"#{box}[data-rendered='{tag}']", state="attached", timeout=timeout)


def open_chart_tab(browser, srv, sid, **ctx):
    p = Page(browser, srv.url, **({"viewport": {"width": 1280, "height": 900}} | ctx))
    page = p.goto("/#/strategies")
    page.wait_for_selector("#stratLab[data-state='ready']")
    page.locator("#stratDetailPick").select_option(sid)
    page.wait_for_function(f"() => document.getElementById('stratDetail').dataset.sid === '{sid}'")
    page.locator("#stratTabs button[data-tab='chart']").click()
    rendered(page, "stratLiveChart", sid)
    return p, page


def names(page, box):
    return page.evaluate(f"() => (document.getElementById('{box}').data || []).map((t) => t.name)")


def test_trend_live_chart_shows_its_own_indicators_the_checklist_and_the_banner(browser, lab_server):
    p, page = open_chart_tab(browser, lab_server, "trend")
    try:
        n = names(page, "stratLiveChart")
        assert {"EMA 21", "EMA 50", "EMA 200", "ADX(14)", "Daily EMA 50"} <= set(n)
        assert not {"RSI 14", "VWAP", "Bollinger"} & set(n)                      # only what the rules use
        anns = page.evaluate("() => (document.getElementById('stratLiveChart').layout.annotations || []).map((a) => a.text)")
        assert not any("No setup" in a or "✓" in a or "✗" in a for a in anns)       # the rules are listed once, below
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
        rendered(page, "stratHistChart", "trend")
        assert page.locator("#stratTradeList tbody tr").count() == len(res["trade_list"])
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
        rendered(page, "stratFwdChart", "trend")
        assert "empty" in page.locator("#stratFwdChart").text_content().lower()       # no forward day yet
        assert p.errors == []
    finally:
        p.context.close()


def test_dca_and_range_live_charts(browser, lab_server):
    p, page = open_chart_tab(browser, lab_server, "dca")
    try:
        live = set(names(page, "stratLiveChart"))
        assert {"BTC", "Scheduled buy days (hypothetical)"} <= live                  # no forward buys yet
        assert not {"Scheduled buy days", "Average buy price"} & live
        avg = [n for n in live if n.startswith("Hypothetical average")]
        assert avg == ["Hypothetical average of the scheduled buys in view"]
        page.locator("#stratDetailPick").select_option("range")
        rendered(page, "stratLiveChart", "range")
        assert {"Range high", "Range low", "Midpoint", "ADX(14)"} <= set(names(page, "stratLiveChart"))
        page.locator("#stratDetailPick").select_option("breakout")
        rendered(page, "stratLiveChart", "breakout")
        live = set(names(page, "stratLiveChart"))
        assert {"20-bar high (level)", "10-bar low (trailing exit)", "Volume 1.2× average"} <= live
        assert not {"ADX(14)", "EMA 21", "Range high"} & live                    # only what its rules use
        assert page.locator("#stratChecklist li").count() >= 3
        page.locator("#stratDetailPick").select_option("rotation")
        rendered(page, "stratLiveChart", "rotation")
        live = set(names(page, "stratLiveChart"))
        assert {"28-day return", "BTC", "ETH", "SOL", "BNB"} <= live                 # ranking + normalised prices
        assert "Context only" in page.locator("#stratNews").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_comparison_charts_are_hypothetical_and_unranked(browser, lab_server):
    p = Page(browser, lab_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/strategies")
        rendered(page, "stratCmpEquity", "compare")
        rendered(page, "stratCmpScatter", "compare")
        assert "Trend following" in names(page, "stratCmpEquity")
        assert page.evaluate("() => document.getElementById('stratCmpEquity').data[0].y[0]") == 100
        assert page.evaluate("() => document.getElementById('stratCmpScatter').data[0].x.length") >= 1
        assert "not ranked" in page.locator("#stratCmpHow").text_content()
        inside = page.evaluate("""() => {
            const box = document.querySelector('#stratCmpScatter .bglayer rect.bg').getBoundingClientRect();
            return [...document.querySelectorAll('#stratCmpScatter .textpoint text')].map((t) => {
                const r = t.getBoundingClientRect();
                return r.left >= box.left - 0.5 && r.right <= box.right + 0.5 && r.top >= box.top - 0.5 && r.bottom <= box.bottom + 0.5;
            }); }""")
        assert inside and all(inside)                                              # every label inside the plot area
        assert p.errors == []
    finally:
        p.context.close()


def test_chart_tab_fits_a_phone(browser, lab_server):
    p, page = open_chart_tab(browser, lab_server, "trend", viewport={"width": 390, "height": 844}, is_mobile=True,
                             has_touch=True)
    try:
        for box in ("stratHistChart", "stratFwdChart"):                          # the whole tab drawn, then measured
            rendered(page, box, "trend")
        assert no_horizontal_scroll(page) == []
        anns = page.evaluate("() => (document.getElementById('stratLiveChart').layout.annotations || []).map((a) => a.text)")
        assert not any("No setup" in a for a in anns)                              # on a phone: the list below instead
        assert page.locator("#stratChecklist li").count() >= 6
        assert p.errors == []
    finally:
        p.context.close()


def test_empty_charts_take_little_space(browser, fresh_server):
    p, page = open_chart_tab(browser, fresh_server, "trend")                    # no stored backtest, no forward data
    try:
        for box in ("stratHistChart", "stratFwdChart"):
            rendered(page, box, "trend")
            assert page.locator(f"#{box}").get_attribute("data-kind") == "empty"
            assert page.locator(f"#{box}").bounding_box()["height"] < 120
        assert page.locator("#stratTradeList").is_hidden()
        assert p.errors == []
    finally:
        p.context.close()


def test_the_forward_only_variant_is_labelled_on_its_chart_tab(browser, lab_server):
    p, page = open_chart_tab(browser, lab_server, "breakout_05r")
    try:
        label = "chosen after design results: forward test only, no design verdict"
        assert page.locator("#stratLiveBanner").text_content().lower() == label
        assert "20-bar high (level)" in names(page, "stratLiveChart")
        rendered(page, "stratHistChart", "breakout_05r")
        assert label in page.locator("#stratHistChart").text_content().lower()
        assert page.locator("#stratHistChart").get_attribute("data-kind") == "empty"
        page.locator("#stratTabs button[data-tab='overview']").click()
        assert label in page.locator("#stratBacktest").text_content().lower()
        assert p.errors == []
    finally:
        p.context.close()
