"""One shared plain NO TRADE summary for Overview, Signal details, alerts and the assistant (UI/text only)."""
from __future__ import annotations

from conftest import FakeProvider
from test_app_and_db import make_client

CONTAMINATED = ("Not validated: the holdout from 2025-10-29 is contaminated (seen via full run #1 on 2026-10-29); "
                "validation now needs forward data (Research -> Forward tests, or a new holdout start after today).")
RUNNING = {"state": "running", "days_elapsed": 12, "target_days": 365, "start_date": "2026-11-01"}
WAITING = {"state": "waiting", "days_elapsed": 0, "target_days": 365, "start_date": "2026-11-01"}


def test_contaminated_holdout_says_validation_needs_forward_data_with_progress():
    from services.no_trade_text import summary
    assert summary([CONTAMINATED], RUNNING) == \
        "No proven edge yet: validation needs forward data (Forward tests running, day 12 of 365)."
    assert summary([CONTAMINATED], WAITING) == \
        "No proven edge yet: validation needs forward data (Forward tests start 2026-11-01, day 0 of 365)."
    assert summary([CONTAMINATED], {"state": "not_started"}) == \
        "No proven edge yet: validation needs forward data (Forward tests not started yet)."


def test_never_claims_a_failed_validation_that_did_not_happen():
    from services.no_trade_text import summary
    texts = [summary([r], RUNNING) for r in (
        CONTAMINATED, "Not validated: no backtest has been run yet - run one from the Backtest & validation panel.",
        "Not validated: no holdout run yet for these rules.", "Not validated: Not counted: its holdout period is contaminated.")]
    assert not any("didn't pass" in t or "did not pass" in t for t in texts)
    assert summary(["Not validated: latest backtest (run #3) found no demonstrated edge after costs."], RUNNING) == \
        "No proven edge: the validation backtest found no edge after costs."


def test_other_reasons_and_extra_count():
    from services.no_trade_text import summary
    assert summary([], RUNNING) is None
    assert summary(["Price sources disagree by 0.8%"], None) == \
        "Exchanges disagree on the price: waiting until they agree."
    assert summary([CONTAMINATED, "Volatility is extreme (ATR% 99th percentile)", "x"], RUNNING) == \
        "No proven edge yet: validation needs forward data (Forward tests running, day 12 of 365) (+2 more reasons)."
    assert summary(["Something new. Second sentence."], None) == "Something new."


def test_signal_api_overview_alert_and_assistant_share_the_text(config, db):
    from services.no_trade_text import summary
    c = make_client(config, db, [FakeProvider("fake1")])
    s = c.get("/api/signal").get_json()
    assert s["signal"] == "NO TRADE" and s["engine"] == "signal-1.8"
    fwd = c.application.config["BTC_FORWARD"].status()
    assert s["no_trade_summary"] == summary(s["no_trade_reasons"], fwd)
    # the signal-change alert carries the same sentence
    alerts = c.application.config["BTC_ALERTS"]
    rule = next(r for r in alerts.list_rules() if r["kind"] == "signal_change")
    alerts._set_state(f"rule:{rule['id']}", {"signal": "HOLD", "setup": "HOLD"})
    alerts.evaluate()
    msg = next(a for a in alerts.list_alerts() if a["kind"] == "signal_change")["message"]
    assert s["no_trade_summary"] in msg
    # the assistant leads with it
    ans = c.post("/api/assistant/chat", json={"message": "Why is the signal NO TRADE?"}).get_json()["answer"]
    assert ans.splitlines()[0].startswith(f"The signal is NO TRADE. In short: {s['no_trade_summary']}")
