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
               "research": "Research (backtest)", "strategies": "Strategy Lab", "paper": "Risk & paper", "alerts": "Alerts", "assistant": "Ask the data",
               "system": "System & sources"}
VIEW_SECTIONS = {"overview": ["#overview", "#sigSummary", "#chart", "#ovExtra", "#watchlist"],
                 "market": ["#chart", "#analysis", "#mtf"], "news": ["#news", "#calendar"],
                 "signal": ["#signal", "#sighist"], "research": ["#backtest"],
                 "strategies": ["#stratLab", "#stratCompareCard", "#stratDetail"], "paper": ["#risk"],
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


FULL_REASON = "Not validated: no holdout run yet. Run #1 is a full-period run (it includes design data) and cannot validate."


def test_legacy_full_run_shows_badge_reason_and_holdout_count(browser, fresh_server):
    """0.9.2 follow-up: a full-period run stored by 0.9.1 (like the real run #1: no period_badge in its payload)
    shows the FULL PERIOD badge, the Signal view no longer calls it 'the validation backtest', and the Research
    view counts the holdout runs made."""
    from database import now_ms
    from e2e.conftest import seeded_backtest
    a, db = fresh_server.app.config, fresh_server.db
    res = seeded_backtest(a["BTC_SIGNALS"].config())
    res.update({"period_kind": "full", "design_badge": None, "period": {**res["period"], "kind": "full"},
                "verdict": "NOT VALIDATED: no demonstrated edge after costs - the live signal stays NO TRADE."})
    summary = {"verdict": res["verdict"], "period": res["period"], "entry_mode": "standard", "period_kind": "full",
               "strategy": {k: res["strategy"][k] for k in ("trades", "net_return_pct", "max_drawdown_pct",
                                                            "profit_factor", "win_rate_pct")},
               "buy_and_hold": 294.32, "criteria_passed": 4, "criteria_total": 7}
    run_id = db.save_backtest(now_ms(), res["engine"], res["config_hash"], False, summary, res)
    assert run_id == 1
    db.set_setting("strategy_validation", {"validated": False, "run_id": run_id, "as_of": now_ms(), "engine": res["engine"],
                                           "config_hash": res["config_hash"], "entry_mode": "standard",
                                           "period_kind": "full", "period": res["period"], "note": res["verdict"],
                                           "failed": ["Enough trades (6 trades (need >= 30))"]})
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/research")
        page.wait_for_selector("#btResult .verdict.design")
        assert page.locator("#btResult .verdict.design").text_content() == "FULL PERIOD - includes design data, cannot validate"
        assert page.locator("#btResult .verdict.design").is_visible()
        page.wait_for_function("() => document.getElementById('btHoldout').textContent.startsWith('Holdout runs made')")
        assert page.locator("#btHoldout").text_content().startswith("Holdout runs made: 0")

        page = p.goto("/#/signal")
        page.wait_for_selector("#sigGates li")
        reasons = [li.text_content() for li in page.locator("#sigGates li").all()]
        assert FULL_REASON in reasons and not any("validation backtest" in r for r in reasons)
        page = p.goto("/")
        page.wait_for_selector("#sigSummary[data-state='ready']")
        assert page.locator("#sumReason").text_content().startswith("No proven edge yet: no holdout run has been made")

        for _ in range(2):                                   # the same rules tested twice: 1 counts, 1 repeat
            db.register_holdout_run(now_ms(), "h" * 16, res["config_hash"], res["engine"], {}, {}, None)
        page = p.goto("/#/research")
        page.wait_for_function("() => document.getElementById('btHoldout').textContent.startsWith('Holdout runs made: 2')")
        assert "1 counted for validation" in page.locator("#btHoldout").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_research_shows_holdout_status_and_a_new_start_once_it_is_spent(browser, fresh_server):
    """0.9.3: 'Holdout status: untouched / spent on <date> by run #N'; once spent, a new holdout start can be set
    only after the spent period, and the period list shows the new start."""
    import datetime as dt
    from database import now_ms

    def day(ms):
        return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/research")
        page.wait_for_function("() => document.getElementById('btHoldoutStatus').textContent.startsWith('Holdout status')")
        assert page.locator("#btHoldoutStatus").text_content() == \
            "Holdout status: untouched (reserved period from 2025-10-29)"
        assert not page.locator("#btNewHoldout").is_visible()
        assert page.locator("#btPeriod option[value='holdout']").text_content() == "holdout (from 2025-10-29)"
        assert "Only the first holdout run in the current holdout period can lift the NO TRADE gate." in \
            page.locator("#backtest .card-head p").text_content()                       # 0.9.4 subtitle

        spent_at = now_ms() - 3_600_000
        fresh_server.db.register_holdout_run(spent_at, "h" * 16, "c" * 16, "signal-1.7", {}, {}, 7)  # like run #7
        page.reload()                                                    # same hash: a goto would not reload
        page.wait_for_selector("#btNewHoldout:not([hidden])")
        assert page.locator("#btHoldoutStatus").text_content() == f"Holdout status: spent on {day(spent_at)} by run #7"
        # 0.9.7 (deliberate): a new holdout must also start after today (forward data only)
        earliest = day(max((spent_at // 86_400_000 + 1) * 86_400_000, (now_ms() // 86_400_000 + 1) * 86_400_000))
        assert page.locator("#btNewHoldoutDate").get_attribute("min") == earliest
        assert f"on or after {earliest}" in page.locator("#btNewHoldoutText").text_content()

        page.fill("#btNewHoldoutDate", day(spent_at))                    # inside the spent period: refused
        page.click("#btNewHoldoutSet")
        page.wait_for_function("() => document.getElementById('btNewHoldoutMsg').textContent.startsWith('Not set')")
        assert "after the spent period" in page.locator("#btNewHoldoutMsg").text_content()

        page.fill("#btNewHoldoutDate", earliest)
        page.click("#btNewHoldoutSet")
        page.wait_for_function("() => document.getElementById('btNewHoldoutMsg').textContent.startsWith('New holdout period')")
        assert page.locator("#btHoldoutStatus").text_content() == \
            f"Holdout status: untouched (reserved period from {earliest})"
        assert not page.locator("#btNewHoldout").is_visible()
        assert page.locator("#btPeriod option[value='holdout']").text_content() == f"holdout (from {earliest})"
        assert page.locator("#btPeriod option[value='design']").text_content() == f"design (to {earliest})"
        assert [e for e in p.errors if "400" not in e] == []             # the refused date logs one 400
    finally:
        p.context.close()


def test_settings_shows_the_news_weight_notice_once(browser, fresh_server):
    """signal-1.8: a stored news weight above 0 is set to 0 (other weights kept) and the Settings page says so once."""
    fresh_server.db.set_setting("signal_config", {"weights": {"trend": 0.4, "momentum": 0.2, "volume": 0.1,
                                                              "structure": 0.1, "overextension": 0.1, "news": 0.1}})
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/overview")                                   # the dashboard reads settings first
        page.wait_for_selector("#sigSummary[data-state='ready']")
        page = p.goto("/settings")
        page.wait_for_selector("#signal[data-state='ready']")
        notice = page.locator("#signalNotice").text_content()
        assert "news weight (0.1) was set to 0" in notice and "other weights were kept" in notice
        weights = page.locator("#signal input[name='weights']").input_value()
        assert '"news":0' in weights and '"trend":0.4444' in weights
        page.reload()
        page.wait_for_selector("#signal[data-state='ready']")
        assert page.locator("#signalNotice").count() == 0              # one-time
        assert p.errors == []
    finally:
        p.context.close()


def test_research_shows_the_forward_tests_panel(browser, fresh_server):
    """research-1: H1, H2 and buy & hold forward paper tests, with days elapsed of 365 and the rule fingerprint."""
    import datetime as dt
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/research")
        page.wait_for_selector("#forward[data-state='ready']")
        assert page.locator("#fwdProgress").text_content().startswith("Not started yet")
        info = fresh_server.app.config["BTC_FORWARD"].ensure_started()      # what the background monitor does
        start = dt.datetime.fromtimestamp(info["start_day"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        page.reload()
        page.wait_for_function("() => document.getElementById('fwdProgress').textContent.startsWith('Day 0 of 365')")
        assert f"started {start} (UTC)" in page.locator("#fwdProgress").text_content()
        rules = [r.get_attribute("data-rule") for r in page.locator("#fwdBody tr").all()]
        assert rules == ["H1", "H2", "BH"]
        h2 = page.locator("#fwdBody tr[data-rule='H2']").text_content()
        assert "pre-registered forward test" in h2 and "verdict after 365 days" in h2
        # ui-sidebar-forward-table (deliberate): empty cells are a CSS dash labelled "no data yet", explained above
        assert page.locator("#fwdBody tr[data-rule='H2'] .na[aria-label='no data yet']").count() == 5
        first = dt.datetime.fromtimestamp(info["start_day"] / 1000 + 86_400, dt.timezone.utc).strftime("%Y-%m-%d 00:00")
        note = page.locator("#fwdNote").text_content()
        assert note.startswith(f"No completed days yet: first update {first} UTC (") and note.endswith(" your time)")
        assert "failed on design data" in page.locator("#fwdBody tr[data-rule='H1']").text_content()
        assert "unchanged since the start" in page.locator("#fwdFingerprint").text_content()
        assert p.errors == []
    finally:
        p.context.close()


def test_research_shows_a_contaminated_holdout_and_asks_for_forward_data(browser, fresh_server):
    """0.9.7: a full run over the holdout (like run #1) contaminates it; Research says so and only allows a new start
    after today."""
    import datetime as dt
    from database import now_ms
    from services.backtest_service import record_contaminations
    from services.backtesting import HOLDOUT_START

    def day(ms):
        return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    db, seen = fresh_server.db, now_ms() - 86_400_000
    rid = db.save_backtest(seen, "signal-1.7", "abc", False, {"period_kind": "full", "period": {
        "kind": "full", "start": HOLDOUT_START - 1000 * 86_400_000, "end": seen}}, {})
    record_contaminations(db)                                           # what app start-up does
    p = Page(browser, fresh_server.url, viewport={"width": 1280, "height": 900})
    try:
        page = p.goto("/#/research")
        page.wait_for_selector("#btNewHoldout:not([hidden])")
        assert page.locator("#btHoldoutStatus").text_content() == \
            f"Holdout status: contaminated (seen via full run #{rid} on {day(seen)}), cannot validate"
        # the run count must not call a contaminated period "untouched"
        assert page.locator("#btHoldout").text_content() == "Holdout runs made: 0 · period contaminated, cannot validate"
        tomorrow = day((now_ms() // 86_400_000 + 1) * 86_400_000)
        assert page.locator("#btNewHoldoutDate").get_attribute("min") == tomorrow
        assert "Validation now needs forward data" in page.locator("#btNewHoldoutText").text_content()
        page.fill("#btNewHoldoutDate", tomorrow)
        page.click("#btNewHoldoutSet")
        page.wait_for_function("() => document.getElementById('btNewHoldoutMsg').textContent.startsWith('New holdout period')")
        assert page.locator("#btHoldoutStatus").text_content() == f"Holdout status: untouched (reserved period from {tomorrow})"
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
