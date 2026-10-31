"""One plain-language summary of why the signal is NO TRADE.

Shared by the Overview, Signal details, the signal-change alert and the assistant, so they never disagree. The full
reasons from the signal engine stay as they are (and are always shown in Signal details); this only words the FIRST
reason for a reader. It never claims a validation failed unless the reason says so.
"""
from __future__ import annotations

import re

PLAIN: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), t) for p, t in [
    (r"^Not validated:.*(contaminated|needs forward data)", "No proven edge yet: validation needs forward data{fwd}"),
    (r"^Not validated: no backtest", "No proven edge yet: no validation backtest has been run"),
    (r"^Not validated: no holdout run yet", "No proven edge yet: no holdout run has been made"),
    (r"^Not validated: the rules changed", "No proven edge for the current rules: they changed since the holdout run"),
    (r"forward observation", "Validated, but still in the forward-observation period"),
    (r"^Not validated:.*found no demonstrated edge", "No proven edge: the validation backtest found no edge after costs"),
    (r"^Not validated:.*(failed|did not pass|FAIL)", "No proven edge: the validation run did not pass"),
    (r"^Not validated", "No proven edge yet: no validation counts for the current rules"),
    (r"^No current BTC price", "No live price, so no decision can be made"),
    (r"price is stale", "The price is stale: waiting for fresh data"),
    (r"too little data|could not be calculated", "Not enough data to judge the market"),
    (r"candles are stale", "Chart data is stale: waiting for fresh candles"),
    (r"missing candles", "Gaps in the price history"),
    (r"^Price sources disagree", "Exchanges disagree on the price: waiting until they agree"),
    (r"volatility is extreme", "Volatility is extreme: too risky to judge"),
    (r"contradictory", "The evidence is mixed: indicators disagree"),
    (r"fights the daily", "The setup fights the daily trend"),
    (r"^(Fresh|Recent) high-impact (bearish|bullish) news", "High-impact news against this setup: letting the market digest it"),
]]


def forward_progress(forward: dict | None) -> str:
    """' (Forward tests running, day N of 365)' from ForwardTests.status()."""
    f = forward or {}
    target = f.get("target_days", 365)
    if f.get("state") == "running":
        return f" (Forward tests running, day {f.get('days_elapsed', 0)} of {target})"
    if f.get("state") == "waiting":
        return f" (Forward tests start {f.get('start_date')}, day 0 of {target})"
    if f.get("state") == "done":
        return f" (Forward tests finished: {target} of {target} days)"
    return " (Forward tests not started yet)"


def plain(reason: str, forward: dict | None = None) -> str:
    for pattern, text in PLAIN:
        if pattern.search(reason):
            return text.format(fwd=forward_progress(forward))
    return re.split(r"(?<=\.)\s", str(reason))[0].rstrip(".")


def summary(reasons: list[str] | None, forward: dict | None = None) -> str | None:
    """'<first reason in plain words> (+N more reasons).' or None when there is no NO TRADE reason."""
    reasons = list(reasons or [])
    if not reasons:
        return None
    more = len(reasons) - 1
    return plain(reasons[0], forward) + (f" (+{more} more reason{'s' if more > 1 else ''})" if more else "") + "."
