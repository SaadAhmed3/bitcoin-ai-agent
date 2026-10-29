"""0.9.1 UI polish: views and hash routes, the overview signal card, folds, chart overlays, freshness, mobile layout,
placeholders and contrast. See tests/e2e/conftest.py for the fixture app (fake providers, no network)."""
from __future__ import annotations

import re

import pytest

from e2e.conftest import VIEWS, Page, wait_dashboard_ready, wait_view_ready
from e2e.e2e_helpers import contrast, no_horizontal_scroll, placeholder_problems

pytestmark = pytest.mark.e2e
smoke = pytest.mark.smoke

VIEW_TITLES = {"overview": "Overview", "market": "Market & chart", "news": "News & calendar", "signal": "Signal details",
               "research": "Research (backtest)", "paper": "Risk & paper", "alerts": "Alerts", "assistant": "Ask the data",
               "system": "System & sources"}
VIEW_SECTIONS = {"overview": ["#overview", "#sigSummary", "#chart", "#ovExtra", "#watchlist"],
                 "market": ["#chart", "#analysis", "#mtf"], "news": ["#news", "#calendar"],
                 "signal": ["#signal", "#sighist"], "research": ["#backtest"], "paper": ["#risk"],
                 "alerts": ["#alerts"], "assistant": ["#assistant"], "system": ["#sources", "#system"]}
ALL_SECTIONS = sorted({s for v in VIEW_SECTIONS.values() for s in v})


def assert_view(page, view):
    assert page.locator("main").get_attribute("data-view") == view
    assert page.locator(f"#viewNav a[data-view='{view}']").get_attribute("aria-current") == "page"
    assert page.locator("#viewNav a[aria-current='page']").count() == 1
    assert VIEW_TITLES[view] in page.title()
    for sel in ALL_SECTIONS:
        assert page.locator(sel).is_visible() == (sel in VIEW_SECTIONS[view]), (view, sel)


def settle_scroll(page):
    page.wait_for_function("""() => new Promise((ok) => { let last = -1, same = 0;
        const t = setInterval(() => { same = scrollY === last ? same + 1 : 0; last = scrollY;
                                      if (same >= 4) { clearInterval(t); ok(true); } }, 100); })""")


# ------------------------------------------------------------------ views
@smoke
@pytest.mark.parametrize("view", VIEWS)
def test_each_view_opens_from_the_sidebar_and_from_its_hash(ui, view):
    page = ui.goto("/")
    assert_view(page, "overview")
    ui.view(view)
    assert_view(page, view)
    assert page.url.endswith(f"#/{view}")
    page.goto(ui.base + "/")                                   # a fresh load straight onto the hash route
    page.goto(ui.base + f"/#/{view}")
    page.reload()
    wait_view_ready(page, view)
    assert_view(page, view)
    assert ui.errors == []


OLD_ANCHORS = {"#backtest": "research", "#news": "news", "#signal": "signal", "#risk": "paper", "#sources": "system",
               "#mtf": "market", "#analysis": "market", "#calendar": "news", "#watchlist": "overview", "#chart": "overview",
               "#alerts": "alerts", "#assistant": "assistant", "#sighist": "signal", "#overview": "overview"}


@smoke
@pytest.mark.parametrize("anchor,view", OLD_ANCHORS.items())
def test_old_anchors_open_the_view_that_contains_the_section(ui, anchor, view):
    page = ui.goto("/" + anchor)
    assert_view(page, view)
    settle_scroll(page)
    box = page.locator(anchor).bounding_box()
    bar = page.locator(".topbar").bounding_box()
    assert box["y"] >= bar["y"] + bar["height"] - 1 and box["y"] < 900, (anchor, box)


@smoke
def test_back_and_forward_buttons_move_between_views(ui):
    page = ui.goto("/")
    for v in ("market", "research", "alerts"):
        ui.view(v)
    page.go_back(); wait_view_ready(page, "research"); assert_view(page, "research")
    page.go_back(); wait_view_ready(page, "market"); assert_view(page, "market")
    page.go_forward(); wait_view_ready(page, "research"); assert_view(page, "research")
    page.go_back(); page.go_back(); wait_view_ready(page, "overview"); assert_view(page, "overview")


@smoke
def test_mobile_menu_opens_and_closes(mobile):
    page = mobile.goto("/")
    btn = page.locator("#menuBtn")
    side = page.locator("#sidebar")
    assert btn.is_visible() and btn.get_attribute("aria-expanded") == "false"
    assert side.bounding_box()["x"] + side.bounding_box()["width"] <= 1           # off-canvas
    btn.click()
    page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().x >= 0")
    assert btn.get_attribute("aria-expanded") == "true" and btn.get_attribute("aria-label") == "Close menu"
    page.click("#viewNav a[data-view='news']")                                   # choosing a view closes it
    wait_view_ready(page, "news")
    assert btn.get_attribute("aria-expanded") == "false"
    page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().right <= 1")
    btn.click()
    page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().x >= 0")
    page.keyboard.press("Escape")                                                # Escape closes it too
    assert btn.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.id") == "menuBtn"
    assert mobile.errors == []


# ------------------------------------------------------------------ overview signal card / signal view
def test_overview_signal_card_shows_the_plain_reason_line(ui):
    page = ui.goto("/")
    page.wait_for_selector("#sigSummary[data-state='ready']")
    assert page.locator("#sumBadge").text_content() == "NO TRADE"
    assert "notrade" in page.locator("#sumBadge").get_attribute("class")
    reason = page.locator("#sumReason").text_content()
    assert reason == "No proven edge yet: no validation backtest has been run."     # from the first NO TRADE reason
    facts = page.locator("#sumFacts").text_content()
    for word in ("Setup", "HOLD", "not a probability", "BTC price", "$65,000.00", "fake1", "Generated", "ago"):
        assert word in facts, word
    link = page.locator("#sumLink")
    assert link.get_attribute("href") == "#/signal" and "See full reasoning" in link.text_content()
    link.click()
    wait_view_ready(page, "signal")
    assert_view(page, "signal")


def test_signal_details_are_folded_and_expand_and_remember(ui):
    page = ui.goto("/#/signal")
    folds = ["fdEvidence", "fdComponents", "fdPlan", "fdRisk", "fdNews", "fdDiag", "fdWarn"]
    for f in folds:
        assert page.locator(f"#{f}").get_attribute("open") is None, f              # closed by default
    assert not page.locator("#sigFor").is_visible()
    page.click("#fdEvidence > summary")
    assert page.locator("#sigFor").is_visible() and page.locator("#sigFor li").count() >= 1
    page.click("#fdComponents > summary")
    assert page.locator("#sigComp tbody tr").first.is_visible()
    page.reload()
    wait_view_ready(page, "signal")
    assert page.locator("#fdEvidence").get_attribute("open") is not None             # remembered (localStorage)
    assert page.locator("#fdComponents").get_attribute("open") is not None
    assert page.locator("#fdRisk").get_attribute("open") is None
    page.click("#fdEvidence > summary")
    page.click("#fdComponents > summary")                                           # leave the defaults behind


def test_research_view_summary_first_then_folded_details(ui):
    page = ui.goto("/#/research")
    page.wait_for_selector("#btResult .bt-tiles")
    tiles = page.locator("#btResult .tile")
    assert tiles.count() == 4
    t = tiles.all_text_contents()
    assert "Net return" in t[0] and "buy & hold +180.40%" in t[0]
    assert "Max drawdown" in t[1] and "Trades" in t[2] and "6" in t[2]
    assert re.search(r"Criteria passed\s*4/7", t[3])
    order = page.evaluate("""() => [...document.querySelectorAll('#btResult > *')].map(e => e.id || e.className)""")
    assert order.index("bt-tiles") < order.index("btChart") < order.index("fdBtCriteria")
    for f in ("fdBtCriteria", "fdBtFunnel", "fdBtWindows", "fdBtTrades", "fdBtLimits", "fdBtSettings"):
        assert page.locator(f"#{f}").get_attribute("open") is None, f
    page.click("#fdBtFunnel > summary")
    assert page.locator("#btResult table.funnel").is_visible()
    page.click("#fdBtFunnel > summary")
    assert page.locator("details.help").count() == 1 and "holdout" in page.locator("details.help").text_content()


def test_research_empty_state_has_one_call_to_action(browser, fresh_server):
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/research")
        page.wait_for_selector("#btResult .cta")
        assert "No backtest yet. Download history, then run a 4-year full/standard backtest (about 8 min)." \
            in " ".join(page.locator("#btResult .cta p").text_content().split())
        assert page.locator("#btResult button").count() == 1 and page.locator("#btCta").is_visible()
        assert p.errors == []
    finally:
        p.context.close()


# ------------------------------------------------------------------ placeholders, console, layout
def visit_every_view(p: Page, open_details: bool = True) -> dict[str, list[str]]:
    page = p.goto("/")
    found = {}
    for view in VIEWS:
        p.view(view)
        if view == "research":
            page.wait_for_selector("#btResult .verdict, #btResult .cta")
        page.wait_for_timeout(400)
        if open_details:
            page.evaluate("() => document.querySelectorAll('main details').forEach((d) => { d.open = true; })")
        found[view] = placeholder_problems(page)
        if open_details:
            page.evaluate("() => document.querySelectorAll('main details').forEach((d) => { d.open = false; })")
    return found


def test_no_placeholder_text_in_any_view_with_seeded_data(ui):
    found = visit_every_view(ui)
    assert {v: f for v, f in found.items() if f} == {}
    assert ui.errors == []


def test_no_placeholder_text_or_console_errors_in_any_view_with_an_empty_database(browser, fresh_server):
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        found = visit_every_view(p)
        assert {v: f for v, f in found.items() if f} == {}
        assert p.errors == []
    finally:
        p.context.close()


@smoke
def test_no_console_errors_in_any_view(ui):
    visit_every_view(ui, open_details=False)
    ui.page.wait_for_timeout(500)
    assert ui.errors == []


@smoke
@pytest.mark.parametrize("view", VIEWS)
def test_no_horizontal_scroll_at_390_in_every_view(mobile, view):
    page = mobile.goto(f"/#/{view}")
    page.wait_for_timeout(400)
    page.evaluate("() => document.querySelectorAll('main details').forEach((d) => { d.open = true; })")
    assert no_horizontal_scroll(page) == [], view
    assert mobile.errors == []


@smoke
def test_wide_tables_become_stacked_cards_on_a_phone(mobile):
    page = mobile.goto("/#/market")
    page.wait_for_selector("#mtfTable tbody td[data-label]")
    td = page.locator("#mtfTable tbody tr").first.locator("td").nth(1)
    assert td.get_attribute("data-label") == "Close"
    assert page.evaluate("getComputedStyle(document.querySelector('#mtfTable tbody tr')).display") == "block"
    assert page.evaluate("getComputedStyle(document.querySelector('#mtfTable thead')).position") == "absolute"
    assert page.locator("#mtfTable thead th").count() == 9                       # still a semantic table
    assert page.evaluate("""getComputedStyle(document.querySelector('#mtfTable tbody td[data-label]'), '::before').content""") != "none"


# ------------------------------------------------------------------ chart overlays
def trace_names(page):
    return page.evaluate("() => (document.getElementById('chartBox').data || []).map((t) => t.name || '')")


def test_chart_overlays_draw_and_toggle_and_are_remembered(ui):
    page = ui.goto("/")
    names = trace_names(page)
    assert any(n.startswith("Resistance") or n.startswith("Support") for n in names), names
    assert "Alert below $62,000" in names
    assert any(n.startswith("Signal: ") for n in names)
    assert page.evaluate("() => (document.getElementById('chartBox').layout.shapes || []).filter((s) => s.type === 'rect').length") >= 1
    assert any("touch" in n for n in names)                                     # hover labels show the touches
    page.uncheck("#overlayToggles input[data-ov='levels']")
    page.uncheck("#overlayToggles input[data-ov='markers']")
    names = trace_names(page)
    assert not any(n.startswith(("Resistance", "Support", "Signal: ")) for n in names) and "Alert below $62,000" in names
    page.reload()
    wait_dashboard_ready(page)
    assert not page.locator("#overlayToggles input[data-ov='levels']").is_checked()     # remembered
    assert "levels" not in page.locator("#chartBox").get_attribute("data-overlays")
    page.check("#overlayToggles input[data-ov='levels']")
    page.check("#overlayToggles input[data-ov='markers']")
    assert any(n.startswith("Resistance") or n.startswith("Support") for n in trace_names(page))


def test_chart_overlays_are_off_by_default_on_a_phone(mobile):
    page = mobile.goto("/")
    for ov in ("levels", "alerts", "markers"):
        assert not page.locator(f"#overlayToggles input[data-ov='{ov}']").is_checked(), ov
    assert page.locator("#overlayToggles input[data-ov='ema']").is_checked()


# ------------------------------------------------------------------ freshness
@smoke
def test_freshness_text_updates_every_second(ui):
    page = ui.goto("/")
    f = page.locator("#freshness")
    pat = r"Updated \d+ (s|min) ago · next refresh in \d+ s"
    page.wait_for_function("() => /Updated/.test(document.getElementById('freshness').textContent)")
    first = f.text_content()
    assert re.fullmatch(pat, first), first
    page.wait_for_function("(t) => document.getElementById('freshness').textContent !== t", arg=first, timeout=5000)
    assert re.fullmatch(pat, f.text_content())
    assert f.get_attribute("href") == "#/system"


def test_freshness_turns_amber_when_a_source_fails_and_links_to_system(ui, live_server):
    live_server.db.record_health("rss:broken-test", False, error="HTTP 500")
    try:
        page = ui.goto("/")
        page.wait_for_function("() => document.getElementById('freshness').classList.contains('warn')")
        f = page.locator("#freshness")
        assert "rss:broken-test" in f.get_attribute("title")
        f.click()
        wait_view_ready(page, "system")
        assert "rss:broken-test" in page.locator("#srcTable").text_content()
    finally:
        live_server.db.record_health("rss:broken-test", True, latency_ms=1.0)


# ------------------------------------------------------------------ visual consistency
@smoke
def test_every_card_has_a_title_and_a_kind_label(ui):
    page = ui.goto("/")
    missing = []
    for view in VIEWS:
        ui.view(view)
        missing += page.evaluate("""() => [...document.querySelectorAll('main .card')].filter((c) => c.offsetParent && !c.closest('.kpis'))
            .filter((c) => !c.querySelector('.card-head h2')).map((c) => c.id || c.className)""")
    assert missing == []
    kinds = page.evaluate("() => [...new Set([...document.querySelectorAll('main .tag')].map((t) => t.className))]")
    for k in ("tag live", "tag calc", "tag model", "tag hyp"):
        assert k in kinds, kinds


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_contrast_is_at_least_4_5_to_1(ui, theme):
    page = ui.goto("/")
    page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
    checks = {"#viewTitle": "overview", "#sumReason": "overview", "#sumBadge": "overview", "#changeMeta": "overview",
              "#chartMeta": "overview", ".tag.live": "overview", ".tag.calc": "overview", ".tag.model": "overview",
              "#viewNav a[aria-current='page']": "overview", "#sigGates h4": "signal", ".tag.hyp": "research",
              ".sim-banner": "paper", "#freshness": "overview"}
    low = {}
    for sel, view in checks.items():
        if page.locator("main").get_attribute("data-view") != view:
            ui.view(view)
            page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
        ratio = contrast(page, sel)
        if ratio is None or ratio < 4.5:
            low[sel] = ratio
    assert low == {}, (theme, low)
