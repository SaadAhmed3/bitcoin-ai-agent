"""Playwright UI tests for the dashboard and settings page (Phase 9). See tests/e2e/conftest.py for the fixture app.

Tests marked `smoke` only read: they are safe against the real running app (E2E_BASE_URL).
0.9.1: the dashboard shows one view at a time (hash routes such as /#/paper), so tests open the view that holds the
panel they check first; the assertions themselves are unchanged unless noted."""
from __future__ import annotations

import csv
import io
import json
import re

import pytest

from e2e.conftest import E2E_KEY, VIEWS, XSS_RAW, XSS_TITLE, Page, wait_dashboard_ready
from e2e.e2e_helpers import accessible_name_problems, no_horizontal_scroll

pytestmark = pytest.mark.e2e
smoke = pytest.mark.smoke


def text(page, sel: str) -> str:
    """DOM text (not inner_text, which applies CSS text-transform)."""
    return page.locator(sel).first.text_content().strip()


# ------------------------------------------------------------------ dashboard load
@smoke
def test_dashboard_loads_ready_with_price_chart_and_no_console_errors(ui):
    page = ui.goto("/")
    for sel in ("#sigSummary", "#signal", "#watchlist", "#calendar", "#alerts"):     # loaded by the Overview
        assert page.locator(sel).get_attribute("data-state") == "ready", sel
    ui.view("assistant")                                     # the assistant panel loads when its view is opened
    assert page.locator("#assistant").get_attribute("data-state") == "ready"
    ui.view("overview")
    assert re.fullmatch(r"\$[\d,]+\.\d{2}", text(page, "#price").strip())
    assert page.locator("#chartBox .main-svg").count() >= 1            # Plotly rendered (local vendor route, CSP on)
    assert page.locator("#chartBox .candlestick, #chartBox g.trace").count() >= 1
    assert page.evaluate("typeof Plotly") == "object"
    page.wait_for_timeout(500)
    assert ui.errors == []


# ------------------------------------------------------------------ signal
@smoke
def test_signal_panel_no_trade_reasons_evidence_and_not_a_probability(ui):
    page = ui.goto("/#/signal")
    assert text(page, "#sigBadge") == "NO TRADE"
    assert "NO TRADE because" in text(page, "#sigGates")
    assert page.locator("#sigGates li").count() >= 1
    assert page.locator("#sigFor li").count() >= 1 and page.locator("#sigAgainst li").count() >= 1
    assert page.locator("#sigComp tbody tr").count() >= 5
    body = page.inner_text("body")
    assert "not a probability" in body.lower()
    assert page.locator("#histTable tbody tr").count() >= 1


# ------------------------------------------------------------------ mtf & analysis
@smoke
def test_multi_timeframe_and_analysis_panels_render_values(ui):
    page = ui.goto("/#/market")
    page.wait_for_selector("#mtfTable tbody tr td.mono")
    rows = page.locator("#mtfTable tbody tr")
    assert rows.count() == 5
    assert re.search(r"\$[\d,]+", rows.nth(2).inner_text())
    page.wait_for_selector("#readings li .name")
    assert page.locator("#readings li .name").count() >= 5
    assert "bullish" in text(page, "#tally") and "bearish" in text(page, "#tally")
    assert "ATR 14" in text(page, "#facts")
    assert text(page, "#volRegime") not in ("", "—")


# ------------------------------------------------------------------ news
def test_news_links_open_in_new_tab_with_noopener_and_script_title_is_inert(ui):
    page = ui.goto("/#/news")
    page.wait_for_selector("#newsList li a")
    links = page.locator("#newsList a[href^='http']")
    assert links.count() >= 3
    for i in range(links.count()):
        a = links.nth(i)
        assert a.get_attribute("target") == "_blank"
        assert "noopener" in (a.get_attribute("rel") or "").split()
    # The hostile headline is shown as text and never executed.
    assert page.evaluate("window.__xss") is None
    titles = page.locator("#newsList .news-top a").all_text_contents()
    assert XSS_RAW in titles                                    # raw hostile text (seeded in the DB) shown literally
    assert "window.__xss=1 Bitcoin ETF headline" in titles      # the feed copy had its tags stripped at ingestion
    assert page.locator("#newsList script, #newsList img").count() == 0
    with ui.context.expect_page() as new:                  # opens a new tab (external hosts are stubbed)
        links.first.click()
    assert new.value.url.startswith("https://news.test/")
    new.value.close()


def test_news_empty_state_and_fresh_install_have_no_console_errors(browser, fresh_server):
    """Empty feed + a first start with no backtest yet: honest empty states and a clean console."""
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/news")
        page.wait_for_selector("#newsList li.empty")
        assert "No news collected yet." in text(page, "#newsList")
        assert "Not enough recent" in text(page, "#newsBias")
        p.view("research")
        page.wait_for_selector("#btResult .empty")
        assert "No backtest yet" in text(page, "#btResult")
        assert page.locator("#btExport").get_attribute("hidden") is not None
        page.wait_for_timeout(1000)
        assert p.errors == []
    finally:
        p.context.close()


# ------------------------------------------------------------------ backtest
@smoke
def test_backtest_panel_verdict_greyed_criteria_funnel_selects_and_design_badge(ui, live_server):
    page = ui.goto("/#/research")
    page.wait_for_selector("#btResult .verdict, #btResult .empty")
    opts = lambda sel: page.locator(f"{sel} option").evaluate_all("os => os.map(o => o.value)")
    assert opts("#btEntryMode") == ["standard", "breakout", "pullback"]
    assert opts("#btPeriod") == ["full", "design", "holdout"]
    if getattr(live_server, "external", False) and page.locator("#btResult .verdict").count() == 0:
        assert "No backtest yet" in text(page, "#btResult")          # a fresh database: honest empty state
        return
    if not getattr(live_server, "external", False):
        assert "DESIGN PERIOD" in text(page, "#btResult .verdict.design")
        assert "NOT VALIDATED" in text(page, "#btResult") or "cannot validate" in text(page, "#btResult")
        moot = page.locator("#btResult .criteria li.moot")
        assert moot.count() == 4
        assert "Not meaningful: too few trades to judge." in moot.first.text_content()
        assert float(moot.first.evaluate("e => getComputedStyle(e).opacity")) < 1
    assert page.locator("#btResult .criteria li").count() >= 5
    assert page.locator("#btResult table.funnel").count() == 1
    assert "Raw buy setups" in text(page, "#btResult table.funnel")
    assert page.locator("#btResult .bt-chart .main-svg").count() >= 1


# ------------------------------------------------------------------ risk calculator
def test_risk_calculator_valid_and_invalid_input(ui):
    page = ui.goto("/#/paper")
    page.fill("#rkPortfolio", "10000")
    page.fill("#rkRisk", "1")
    page.fill("#rkEntry", "60000")
    page.fill("#rkStop", "58000")
    page.click("#riskForm button[type=submit]")
    page.wait_for_selector("#riskOut .big")
    assert re.search(r"[\d.]+ BTC", text(page, "#riskOut .big"))
    assert "Max estimated loss" in text(page, "#riskOut")
    page.fill("#rkStop", "61000")                      # stop above entry: rejected by the server
    page.click("#riskForm button[type=submit]")
    page.wait_for_selector("#riskOut .kpi-warn")
    assert "stop must be below" in text(page, "#riskOut .kpi-warn").lower()


# ------------------------------------------------------------------ paper trading
def test_paper_buy_sell_reset_all_simulated(ui):
    page = ui.goto("/#/paper")
    page.wait_for_selector("#paperFacts .fact")
    page.fill("#ppUsd", "1000")
    page.fill("#ppStop", "")
    page.fill("#ppTarget", "")
    page.click("#paperForm button[type=submit]")
    page.wait_for_function("() => document.querySelector('#paperMsg').textContent.includes('BUY')")
    assert text(page, "#paperMsg").startswith("SIMULATED BUY")
    assert "SIM BUY" in text(page, "#paperOrders tbody")
    page.click("#ppSell")
    page.wait_for_function("() => document.querySelector('#paperMsg').textContent.includes('SELL')")
    assert text(page, "#paperMsg").startswith("SIMULATED SELL")
    dialogs = []
    page.once("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.click("#ppReset")
    page.wait_for_function("() => document.querySelector('#paperMsg').textContent.includes('reset')")
    assert "SIMULATED" in dialogs[0]
    assert text(page, "#paperMsg").startswith("SIMULATED account reset")
    page.wait_for_selector("#paperOrders tbody td.empty")
    assert "No simulated orders yet." in text(page, "#paperOrders tbody")
    assert "SIMULATED ACCOUNT" in text(page, ".paper-card .sim-banner")


# ------------------------------------------------------------------ assistant
@smoke
def test_assistant_offline_badge(ui, live_server):
    page = ui.goto("/#/assistant")
    if not getattr(live_server, "external", False):
        assert "Local AI offline" in text(page, "#aiBadge")
    assert text(page, "#aiBadge") != "Checking local AI…"


def test_assistant_rule_answer_with_sources(ui):
    page = ui.goto("/#/assistant")
    page.click("#chatChips button[data-q='Why is the signal NO TRADE?']")
    page.wait_for_selector("#assistant[data-chat='answered']")
    bot = page.locator("#chatLog .msg.bot").last
    assert "Rule-based" in bot.locator(".msg-meta").text_content()
    assert "NO TRADE" in bot.locator(".msg-body").text_content()
    assert re.match(r"Sources \(\d+\)", bot.locator(".msg-src summary").inner_text())
    assert bot.locator(".msg-src li").count() >= 1
    # Typed question about the news: the hostile headline (if quoted) stays text.
    page.fill("#chatInput", "Summarize the news.")
    page.click("#chatSend")
    page.wait_for_function("() => document.querySelectorAll('#chatLog .msg.bot').length >= 2")
    assert page.evaluate("window.__xss") is None and page.locator("#chatLog script, #chatLog img").count() == 0
    hostile = "<img src=x onerror=window.__xss=5> what is the price?"
    page.fill("#chatInput", hostile)
    page.click("#chatSend")
    page.wait_for_function("() => document.querySelectorAll('#chatLog .msg.bot').length >= 3")
    assert hostile in page.locator("#chatLog .msg.user").last.text_content()
    assert page.evaluate("window.__xss") is None and page.locator("#chatLog img").count() == 0
    page.click("#chatClear")
    page.wait_for_selector("#chatLog .empty")


# ------------------------------------------------------------------ alerts
def test_alert_centre_test_alert_ack_and_ack_all(ui):
    page = ui.goto("/#/alerts")
    assert page.evaluate("window.__xss") is None and page.locator("#alList script, #alList img").count() == 0
    seeded = page.locator("#alList li.alert", has_text="XSS")
    assert "<b>XSS</b> alert title" in seeded.locator(".al-head b").text_content()
    assert XSS_TITLE in seeded.text_content()
    assert page.locator("a[href^='javascript:' i], a[href^='data:' i]").count() == 0    # safeUrl() made it inert
    page.click("#alAckAll")
    page.wait_for_function("() => document.querySelector('#bellBadge').hidden")
    page.click("#alTest")
    page.wait_for_selector("#alList li.alert:not(.acked)")
    assert "TEST alert" in text(page, "#alList li.alert:not(.acked)")
    assert page.locator("#bellBadge").is_visible() and text(page, "#bellBadge") == "1"
    page.click("#alTest")
    page.wait_for_function("() => document.querySelector('#bellBadge').textContent === '2'")
    assert page.locator("#bellBtn").get_attribute("aria-label") == "Alerts: 2 unread"
    page.locator("#alList button[data-ack]").first.click()
    page.wait_for_function("() => document.querySelector('#bellBadge').textContent === '1'")
    page.click("#alAckAll")
    page.wait_for_function("() => document.querySelector('#bellBadge').hidden")
    assert page.locator("#alList button[data-ack]").count() == 0
    assert page.locator("#bellBtn").get_attribute("aria-label") == "Alerts: 0 unread"


# ------------------------------------------------------------------ settings
def test_settings_save_reload_persist_reset_and_key_status(ui):
    page = ui.goto("/settings", ready=False)
    page.wait_for_selector("#general[data-state='ready']")
    field = page.locator("#general input[name='refresh_price_s']")
    assert field.input_value() == "15"
    field.fill("30")
    page.click("#general button[type=submit]")
    page.wait_for_function("() => document.querySelector('#general .set-msg').textContent.startsWith('Saved')")
    page.reload()
    page.wait_for_selector("#general[data-state='ready']")
    assert page.locator("#general input[name='refresh_price_s']").input_value() == "30"
    row = page.locator("#general .set-row", has=page.locator("input[name='refresh_price_s']"))
    assert "settings DB" in row.text_content()
    page.click("#general button[data-reset='general']")
    page.wait_for_function("() => document.querySelector('#general .set-msg').textContent === 'Reset to defaults.'")
    assert page.locator("#general input[name='refresh_price_s']").input_value() == "15"
    page.reload()
    page.wait_for_selector("#market[data-state='ready']")
    key_row = page.locator("#market .set-row", has_text="coingecko demo key")
    assert key_row.locator(".mono").inner_text() in ("set", "not set")
    assert key_row.locator(".mono").inner_text() == "set"                  # the fixture app has a (fake) key
    assert E2E_KEY not in page.content()
    assert ui.errors == []


@smoke
def test_settings_key_status_is_only_set_or_not_set(ui):
    page = ui.goto("/settings", ready=False)
    page.wait_for_selector("#market[data-state='ready']")
    key_row = page.locator("#market .set-row", has_text="coingecko demo key")
    assert key_row.locator(".mono").inner_text() in ("set", "not set")
    assert not re.search(r"CG-[A-Za-z0-9]{8,}", page.content())


# ------------------------------------------------------------------ exports
DATASETS = ["signals", "news", "paper_orders", "alerts", "backtest"]
EXPORT_VIEW = {"signals": "signal", "news": "news", "paper_orders": "paper", "alerts": "alerts", "backtest": "research"}


@smoke
@pytest.mark.parametrize("dataset", DATASETS)
def test_export_buttons_download_files_with_expected_name_and_header(ui, live_server, dataset):
    page = ui.goto(f"/#/{EXPORT_VIEW[dataset]}")
    box = page.locator(f".export[data-dataset='{dataset}']")
    if dataset == "backtest":
        page.wait_for_selector("#btResult .verdict, #btResult .empty")
        if getattr(live_server, "external", False) and box.get_attribute("hidden") is not None:
            pytest.skip("no stored backtest in this database, so there is no backtest export button")
    box.locator("a", has_text="CSV").wait_for()
    for fmt in ("CSV", "JSON"):
        with page.expect_download() as dl:
            box.locator("a", has_text=fmt).click()
        d = dl.value
        assert re.fullmatch(rf"btc-signal-lab_{dataset}_\d{{8}}T\d{{6}}Z\.{fmt.lower()}", d.suggested_filename)
        raw = open(d.path(), "rb").read().decode("utf-8")
        if fmt == "CSV":
            header = next(csv.reader(io.StringIO(raw.lstrip("﻿"))))
            assert len(header) >= 3 and all(h and h == h.strip() for h in header), header
            if dataset in ("signals", "alerts", "paper_orders"):
                assert header[0] == "id" or "created_at_utc" in header
        else:
            body = json.loads(raw)
            assert body["metadata"]["dataset"] == dataset and "app_version" in body["metadata"]


# ------------------------------------------------------------------ theme, nav, layout
@smoke
def test_theme_toggle_persists(ui):
    page = ui.goto("/")
    start = page.evaluate("document.documentElement.dataset.theme")
    page.click("#themeBtn")
    flipped = "light" if start == "dark" else "dark"
    assert page.evaluate("document.documentElement.dataset.theme") == flipped
    page.reload()
    wait_dashboard_ready(page)
    assert page.evaluate("document.documentElement.dataset.theme") == flipped
    page.goto(ui.base + "/settings")
    assert page.evaluate("document.documentElement.dataset.theme") == flipped
    page.click("#themeBtn")                                                    # flip back: leave no trace
    assert page.evaluate("document.documentElement.dataset.theme") == start


@smoke
@pytest.mark.parametrize("target", ["#signal", "#backtest", "#risk", "#alerts", "#assistant", "#calendar", "#mtf", "#sources"])
def test_nav_jumps_land_below_the_sticky_bar(ui, target):
    """The sidebar now switches views; OLD deep links (#backtest, ...) open their view and land below the bar."""
    page = ui.goto("/")
    page.evaluate("(t) => { location.hash = t; }", target)
    wait_dashboard_ready(page)
    page.wait_for_function("""() => new Promise((ok) => {                    // wait until smooth scrolling settles
        let last = -1, same = 0;
        const t = setInterval(() => { same = scrollY === last ? same + 1 : 0; last = scrollY;
                                      if (same >= 4) { clearInterval(t); ok(true); } }, 100); })""")
    bar = page.locator(".topbar").bounding_box()
    sec = page.locator(target).bounding_box()
    at_bottom = page.evaluate("() => Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight - 1")
    assert sec["y"] >= bar["y"] + bar["height"] - 1, (target, sec, bar)          # never hidden under the bar
    assert sec["y"] <= bar["y"] + bar["height"] + 40 or at_bottom, (target, sec, bar)


@smoke
@pytest.mark.parametrize("path", ["/", "/settings"])
def test_no_horizontal_scroll_at_390x844(mobile, path):
    page = mobile.goto(path, ready=(path == "/"))
    if path == "/settings":
        page.wait_for_selector("#general[data-state='ready']")
    assert no_horizontal_scroll(page) == [], path


# ------------------------------------------------------------------ accessibility
@smoke
@pytest.mark.parametrize("path", ["/", "/settings"])
def test_every_control_has_an_accessible_name(ui, path):
    page = ui.goto(path, ready=(path == "/"))
    if path == "/settings":
        page.wait_for_selector("#signal[data-state='ready']")
        assert accessible_name_problems(page) == []
    else:
        for view in VIEWS:                                  # controls are spread over the views now
            ui.view(view)
            assert accessible_name_problems(page) == [], view
    for chart in page.locator("#chartBox, #btChart").all():
        assert chart.get_attribute("aria-label")
    assert page.locator("html").get_attribute("lang") == "en"


TAB_TARGETS = {"overview": {"refreshBtn", "themeBtn", "sumLink"}, "signal": {"sigRefresh"}, "research": {"btRun"},
               "paper": {"rkPortfolio", "ppUsd"}, "alerts": {"alTest", "alAckAll"}, "assistant": {"chatInput", "chatSend"}}


@smoke
def test_keyboard_tab_reaches_main_controls(ui):
    """Same controls as before; they are spread over the views now, so each view is tabbed through."""
    page = ui.goto("/")
    for view, want in TAB_TARGETS.items():
        ui.view(view)
        seen: set[str] = set()
        page.locator("body").focus()
        for _ in range(300):
            page.keyboard.press("Tab")
            el = page.evaluate("(() => { const a = document.activeElement; return a ? a.id : '' })()")
            if el:
                seen.add(el)
            if want <= seen:
                break
        assert want - seen == set(), view
    # The keyboard-focused element is visibly outlined (focus ring not removed): Tab to it, as a keyboard user would.
    ui.view("research")
    page.locator("body").focus()
    for _ in range(300):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.id") == "btRun":
            break
    assert page.evaluate("document.activeElement.id") == "btRun"
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
