"""Strategy 1: DCA (dollar-cost averaging) - long-term BTC accumulation on a fixed schedule.

Research only, simulated only: nothing is ever bought. DCA does not time the market and is not a short-term buy/sell
prediction; its signal only says whether today is a scheduled buy day and whether the data can be trusted:
  ACCUMULATE  the next daily open (00:00 UTC) is a scheduled buy day and the data is fresh and complete
  WAIT        not a scheduled buy day (the next one is named)
  PAUSE       data stale, missing candles or no price: no simulated buy until it is trustworthy

Simulation (closed daily candles only, no look-ahead): a buy happens at the OPEN of a scheduled day, a price known
before the day trades: fill = open x (1 + slippage); fee = amount x fee; BTC = (amount - fee) / fill. A scheduled day
without a stored candle is a MISSED buy (no invented price). The portfolio is measured on the whole budget (amount x
scheduled buys): equity = cash not yet invested + BTC x close x (1 - fee - slippage) (liquidation value). DCA never
sells, so realized P&L is 0. Lump sum = the same budget bought at the first day's open with the same costs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from services.backtesting import HOLDOUT_START
from services.strategies.base import DataQuality, StrategyResult, config_hash

STRATEGY_ID = "dca"
VERSION = "dca-1"
PREREGISTRATION = "PREREGISTRATION_DCA.md"
EVAL_TIMEFRAMES = ("1d",)
BACKTEST_TIMEFRAMES = ("1d",)
FORWARD_TIMEFRAME = "1d"
DAY = 86_400_000
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

DEFAULTS: dict[str, Any] = {"amount_usd": 100.0, "interval": "weekly", "weekday": 0, "month_day": 1,
                            "fee_pct": 0.10, "slippage_pct": 0.05, "horizon_days": 365}
FIELDS: dict[str, dict] = {
    "amount_usd": {"kind": "float", "lo": 10, "hi": 100_000, "help": "Simulated amount per scheduled buy (USD)"},
    "interval": {"kind": "choice", "choices": ("daily", "weekly", "monthly"), "help": "How often to buy"},
    "weekday": {"kind": "int", "lo": 0, "hi": 6, "help": "Weekly buys: 0 = Monday ... 6 = Sunday (UTC)"},
    "month_day": {"kind": "int", "lo": 1, "hi": 28, "help": "Monthly buys: day of the month (UTC)"},
    "fee_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Fee per buy (%)"},
    "slippage_pct": {"kind": "float", "lo": 0, "hi": 1, "help": "Estimated slippage per buy (%)"},
    "horizon_days": {"kind": "int", "lo": 30, "hi": 730, "help": "Comparison window: DCA vs lump sum over this many days"},
}


def validate_config(payload: dict | None) -> dict[str, Any]:
    payload = dict(payload or {})
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown DCA setting(s): {', '.join(sorted(unknown))}")
    out = {}
    for k, default in DEFAULTS.items():
        v, f = payload.get(k, default), FIELDS[k]
        try:
            if f["kind"] == "choice":
                if v not in f["choices"]:
                    raise ValueError
            elif f["kind"] == "int":
                if isinstance(v, bool) or float(v) != int(float(v)):
                    raise ValueError
                v = int(float(v))
            else:
                if isinstance(v, bool):
                    raise ValueError
                v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"'{k}' has the wrong value (expected {f['kind']}"
                             + (f": {', '.join(f['choices'])}" if f["kind"] == "choice" else "") + ")") from None
        if f["kind"] != "choice" and not f["lo"] <= v <= f["hi"]:
            raise ValueError(f"'{k}' must be between {f['lo']} and {f['hi']}")
        if k in ("amount_usd",) and v <= 0:
            raise ValueError("'amount_usd' must be positive")
        out[k] = v
    return out


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def is_buy_day(day_ms: int, cfg: dict) -> bool:
    d = _utc(day_ms)
    if cfg["interval"] == "daily":
        return True
    if cfg["interval"] == "weekly":
        return d.weekday() == cfg["weekday"]
    return d.day == cfg["month_day"]


def scheduled_days(start_day: int, end_day: int, cfg: dict) -> list[int]:
    """Scheduled buy days (00:00 UTC, ms) from start_day to end_day inclusive."""
    first = start_day // DAY * DAY
    return [t for t in range(first, end_day + 1, DAY) if is_buy_day(t, cfg)]


def describe(cfg: dict) -> str:
    when = {"daily": "every day", "weekly": f"every {WEEKDAYS[cfg['weekday']]}",
            "monthly": f"on day {cfg['month_day']} of each month"}[cfg["interval"]]
    return f"${cfg['amount_usd']:,.0f} {when} at the 00:00 UTC open"


def _max_dd(values: list[float]) -> float:
    if not values:
        return 0.0
    v = np.asarray(values, dtype=float)
    peak = np.maximum.accumulate(v)
    return float(np.max((peak - v) / peak)) * 100


def simulate(df: pd.DataFrame, cfg: dict, start_day: int, end_day: int) -> dict[str, Any]:
    """Scheduled buys from start_day to end_day (inclusive, 00:00 UTC ms) on the stored daily candles."""
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    liq = 1 - f - s
    sub = df[(df["open_time"] >= start_day) & (df["open_time"] <= end_day)].sort_values("open_time")
    sched = scheduled_days(start_day, end_day, cfg)
    budget = cfg["amount_usd"] * len(sched)
    have = set(int(x) for x in sub["open_time"])
    missed = [d for d in sched if d not in have]
    buys, equity = [], []
    btc = invested = fees = slip = 0.0
    sched_set = set(sched)
    for row in sub.itertuples(index=False):
        day = int(row.open_time)
        if day in sched_set:
            usd = cfg["amount_usd"]
            fill = float(row.open) * (1 + s)
            fee = usd * f
            got = (usd - fee) / fill
            slip_usd = (usd - fee) - got * float(row.open)
            buys.append({"day": day, "open": float(row.open), "fill_price": fill, "usd": usd, "fee_usd": fee,
                         "slippage_usd": slip_usd, "btc": got})
            btc, invested, fees, slip = btc + got, invested + usd, fees + fee, slip + slip_usd
        equity.append({"day": day, "close": float(row.close), "invested": invested, "btc": btc,
                       "equity": (budget - invested) + btc * float(row.close) * liq})
    last_close = float(sub["close"].iloc[-1]) if len(sub) else None
    value = btc * last_close * liq if last_close is not None else 0.0
    out: dict[str, Any] = {
        "schedule": describe(cfg), "start_day": start_day, "end_day": end_day, "scheduled": len(sched),
        "buys": buys, "missed_buys": missed, "budget_usd": budget, "invested_usd": invested, "btc": btc,
        "avg_price": invested / btc if btc else None, "value_usd": value,
        "unrealized_pnl_usd": value - invested, "realized_pnl_usd": 0.0, "fees_usd": fees, "slippage_usd": slip,
        "equity": equity,
        "return_pct": (equity[-1]["equity"] / budget - 1) * 100 if equity and budget else None,
        "roi_invested_pct": (value / invested - 1) * 100 if invested else None,
        "max_drawdown_pct": _max_dd([e["equity"] for e in equity]),
        "warnings": ([f"{len(missed)} scheduled buy day(s) without a stored daily candle: missing data, buy skipped "
                      "(the money stays as cash)."] if missed else []) + ([] if len(sub) else ["No daily candles."]),
    }
    if len(sub) and budget:
        first = sub.iloc[0]
        lump_btc = budget * (1 - f) / (float(first["open"]) * (1 + s))
        curve = [lump_btc * float(c) * liq for c in sub["close"]]
        out["lump_sum"] = {"day": int(first["open_time"]), "btc": lump_btc, "value_usd": curve[-1],
                           "fees_usd": budget * f, "slippage_usd": budget * (1 - f) - lump_btc * float(first["open"]),
                           "return_pct": (curve[-1] / budget - 1) * 100, "max_drawdown_pct": _max_dd(curve)}
    else:
        out["lump_sum"] = None
    return out


def _pct(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def backtest(rows: list[dict], cfg: dict, holdout_start: int = HOLDOUT_START) -> dict[str, Any]:
    """Pre-registered design backtest (PREREGISTRATION_DCA.md): design candles only (closed before the holdout)."""
    df = pd.DataFrame(rows).sort_values("open_time")
    df = df[df["close_time"] < holdout_start].reset_index(drop=True)
    if df.empty:
        raise ValueError("No daily candles before the holdout start.")
    assert int(df["close_time"].max()) < holdout_start, "design data reaches the holdout"
    first, last = int(df["open_time"].iloc[0]), int(df["open_time"].iloc[-1])
    full = simulate(df, cfg, first, last)
    h = cfg["horizon_days"]
    windows = []
    start = first
    while start + (h - 1) * DAY <= last:
        w = simulate(df, cfg, start, start + (h - 1) * DAY)
        if w["lump_sum"]:
            windows.append({"start": start, "dca_return_pct": w["return_pct"], "dca_max_dd_pct": w["max_drawdown_pct"],
                            "lump_return_pct": w["lump_sum"]["return_pct"],
                            "lump_max_dd_pct": w["lump_sum"]["max_drawdown_pct"]})
        start += 7 * DAY
    col = lambda k: [w[k] for w in windows]  # noqa: E731
    rolling = {
        "windows": len(windows), "horizon_days": h, "step_days": 7,
        "median_dca_max_dd_pct": _pct(col("dca_max_dd_pct"), 50), "median_lump_max_dd_pct": _pct(col("lump_max_dd_pct"), 50),
        "p5_dca_return_pct": _pct(col("dca_return_pct"), 5), "p5_lump_return_pct": _pct(col("lump_return_pct"), 5),
        "mean_dca_return_pct": float(np.mean(col("dca_return_pct"))) if windows else None,
        "mean_lump_return_pct": float(np.mean(col("lump_return_pct"))) if windows else None,
        "dca_beats_lump_share_pct": float(np.mean([w["dca_return_pct"] > w["lump_return_pct"] for w in windows]) * 100)
        if windows else None,
        "rows": windows,
    }
    c1 = rolling["median_dca_max_dd_pct"] is not None and rolling["median_dca_max_dd_pct"] < rolling["median_lump_max_dd_pct"]
    c2 = rolling["p5_dca_return_pct"] is not None and rolling["p5_dca_return_pct"] > rolling["p5_lump_return_pct"]
    criteria = [
        {"id": "C1", "rule": f"median max drawdown over rolling {h}-day windows: DCA < lump sum", "passed": bool(c1),
         "dca": rolling["median_dca_max_dd_pct"], "lump_sum": rolling["median_lump_max_dd_pct"]},
        {"id": "C2", "rule": f"5th-percentile {h}-day return (worst case): DCA > lump sum", "passed": bool(c2),
         "dca": rolling["p5_dca_return_pct"], "lump_sum": rolling["p5_lump_return_pct"]},
    ]
    by_year = []
    for year in sorted({_utc(int(t)).year for t in df["open_time"]}):
        y0 = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        y1 = int(datetime(year, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)
        r = simulate(df, cfg, max(first, y0), min(last, y1))
        if r["lump_sum"]:
            by_year.append({"year": year, "days": len(r["equity"]), "dca_return_pct": r["return_pct"],
                            "dca_max_dd_pct": r["max_drawdown_pct"], "lump_return_pct": r["lump_sum"]["return_pct"],
                            "lump_max_dd_pct": r["lump_sum"]["max_drawdown_pct"], "buys": len(r["buys"])})
    full_slim = {k: v for k, v in full.items() if k not in ("equity", "buys")}
    eq = full["equity"]
    idx = np.linspace(0, len(eq) - 1, min(400, len(eq))).astype(int) if eq else []
    return {
        "strategy": STRATEGY_ID, "version": VERSION, "config": cfg, "config_hash": config_hash(cfg),
        "period": {"kind": "design", "first_day": first, "last_day": last,
                   "last_day_close": int(df["close_time"].iloc[-1]), "holdout_start": holdout_start, "days": len(df)},
        "full": full_slim, "lump_sum": full["lump_sum"], "equity_sample": [eq[i] for i in idx],
        "rolling": rolling, "by_year": by_year, "criteria": criteria,
        "verdict": "PASS" if all(c["passed"] for c in criteria) else "FAIL",
        "walk_forward": "not applicable: DCA has no fitted parameters",
        "metrics": {"net_return_pct": full["return_pct"], "win_rate_pct": None, "profit_factor": None,
                    "max_drawdown_pct": full["max_drawdown_pct"], "trades": len(full["buys"]), "avg_reward_risk": None,
                    "costs_usd": full["fees_usd"] + full["slippage_usd"],
                    "buy_hold_return_pct": full["lump_sum"]["return_pct"] if full["lump_sum"] else None},
        "limitations": [
            "Design data only (before 2025-10-29): evidence about the past, not validation.",
            "One asset, one price source (Binance BTCUSDT daily); fills at the daily open with fixed costs.",
            "Rolling windows overlap heavily (7-day steps), so they are not independent samples.",
            "Win rate and profit factor do not apply: DCA never sells.",
        ],
    }


def evaluate(df: pd.DataFrame, cfg: dict, now: int, data: DataQuality, regime: str | None = None,
             config_version: int = 1) -> StrategyResult:
    f, s = cfg["fee_pct"], cfg["slippage_pct"]
    base = dict(strategy_id=STRATEGY_ID, strategy_version=VERSION, evaluated_at=now, asset="BTCUSDT", timeframe="1d",
                strength=None, entry_zone=None, stop=None, targets=[], reward_risk=None, fees_pct=f, slippage_pct=s,
                data=data, regime=regime, config_version=config_version, config_hash=config_hash(cfg))
    note = "DCA is a schedule, not a prediction: it does not time the market."
    sched = f"Schedule: {describe(cfg)}."
    if df is None or df.empty or data.last_candle_close is None:
        return StrategyResult(**base, signal="PAUSE", supporting=[sched], contradictory=[],
                              explanation=f"No daily candles. {note}", rejection="No price data: no simulated buy.",
                              labels=["CALCULATED", "SIMULATED"])
    problems = []
    if data.is_stale:
        problems.append("daily data is stale")
    if data.missing_candles:
        problems.append(f"{data.missing_candles} missing daily candle(s)")
    next_open = int(df["close_time"].iloc[-1]) + 1
    last_close = float(df["close"].iloc[-1])
    nxt = scheduled_days(next_open, next_open + 62 * DAY, cfg)
    nxt_txt = _utc(nxt[0]).strftime("%Y-%m-%d") if nxt else "none within two months"
    supporting = [sched, f"Last daily close ${last_close:,.2f} ({_utc(next_open - 1).strftime('%Y-%m-%d')})."]
    labels = (["LIVE DATA"] if not data.is_stale else []) + ["CALCULATED", "SIMULATED"]
    if problems:
        return StrategyResult(**base, signal="PAUSE", supporting=supporting, contradictory=problems,
                              explanation=f"Paused: {', '.join(problems)}. {note}",
                              rejection=f"No simulated buy while {', '.join(problems)}.", labels=labels)
    if is_buy_day(next_open, cfg):
        return StrategyResult(**base, signal="ACCUMULATE", supporting=supporting, contradictory=[],
                              explanation=f"Scheduled buy day: ${cfg['amount_usd']:,.0f} at the open of "
                                          f"{_utc(next_open).strftime('%Y-%m-%d')} 00:00 UTC (simulated, never executed; "
                                          f"costs {f + s:.2f}%). {note}", rejection=None, labels=labels)
    return StrategyResult(**base, signal="WAIT", supporting=supporting, contradictory=[],
                          explanation=f"Not a scheduled buy day. Next scheduled buy: {nxt_txt}. {note}",
                          rejection="Not a scheduled buy day.", labels=labels)


def markdown(r: dict) -> str:
    """The design backtest as a short report (data/strategy_dca_design.md)."""
    def iso(ms):
        return _utc(ms).strftime("%Y-%m-%d")

    def p(v):
        return "n/a" if v is None else f"{v:+.2f}%"

    full, lump, rol = r["full"], r["lump_sum"], r["rolling"]
    lines = [
        "# DCA design backtest (design-data evidence only)", "",
        f"Pre-registered in `{PREREGISTRATION}` (config hash `{r['config_hash']}`), run once. Daily candles "
        f"{iso(r['period']['first_day'])} to {iso(r['period']['last_day'])} ({r['period']['days']} days), all closed "
        f"before the holdout start {iso(r['period']['holdout_start'])}. Simulated only. {describe(r['config'])}.", "",
        "## Whole design period", "",
        "| | DCA | Lump sum (same budget, day 1) |", "|---|---|---|",
        f"| Budget | ${full['budget_usd']:,.0f} | ${full['budget_usd']:,.0f} |",
        f"| Return on the budget | {p(full['return_pct'])} | {p(lump['return_pct'])} |",
        f"| Max drawdown | {full['max_drawdown_pct']:.2f}% | {lump['max_drawdown_pct']:.2f}% |",
        f"| BTC bought | {full['btc']:.6f} ({r['metrics']['trades']} buys) | {lump['btc']:.6f} |",
        f"| Average price paid (incl. costs) | ${full['avg_price']:,.2f} | |",
        f"| Fees / slippage | ${full['fees_usd']:,.2f} / ${full['slippage_usd']:,.2f} | ${lump['fees_usd']:,.2f} / ${lump['slippage_usd']:,.2f} |",
        f"| Unrealized / realized P&L | ${full['unrealized_pnl_usd']:,.2f} / $0.00 (never sells) | |", "",
        f"Missed buys (no stored candle): {len(full['missed_buys'])}.", "",
        f"## Pre-registered criteria: {r['verdict']} (design data, not validation)", "",
        "| # | Rule | DCA | Lump sum | Passed |", "|---|---|---|---|---|",
    ]
    for c in r["criteria"]:
        lines.append(f"| {c['id']} | {c['rule']} | {c['dca']:.2f}% | {c['lump_sum']:.2f}% | {'yes' if c['passed'] else 'no'} |")
    lines += ["", f"{rol['windows']} rolling {rol['horizon_days']}-day windows (7-day steps, overlapping): mean return "
              f"DCA {p(rol['mean_dca_return_pct'])} vs lump sum {p(rol['mean_lump_return_pct'])}; DCA beat lump sum in "
              f"{rol['dca_beats_lump_share_pct']:.1f}% of windows.", "",
              "## Calendar years", "", "| Year | Days | DCA return | DCA max DD | Lump return | Lump max DD |",
              "|---|---|---|---|---|---|"]
    for y in r["by_year"]:
        lines.append(f"| {y['year']} | {y['days']} | {p(y['dca_return_pct'])} | {y['dca_max_dd_pct']:.2f}% | "
                     f"{p(y['lump_return_pct'])} | {y['lump_max_dd_pct']:.2f}% |")
    lines += ["", "## Limitations", ""] + [f"- {x}" for x in r["limitations"]] + [f"- Walk-forward: {r['walk_forward']}.", ""]
    return "\n".join(lines)


def evaluate_frames(frames: dict, cfg: dict, now: int, data: DataQuality, regime: str | None = None,
                    config_version: int = 1) -> StrategyResult:
    return evaluate(frames.get("1d"), cfg, now, data, regime, config_version)


# --------------------------------------------------------------------------- forward test (strategy_forward.py)
def forward_state(events: list[dict]) -> dict | None:
    marks = [e for e in events if e["kind"] == "mark"]
    return dict(marks[-1]["detail"]) if marks else None


def forward_step(cfg: dict, ctx: dict) -> tuple[list[dict], dict]:
    """One closed day: a scheduled buy at its open, then the mark at its close (budget = the 365-day schedule)."""
    r, t = ctx["rows"][ctx["k"]], ctx["test"]
    start, day = t["start_day"], int(r["open_time"])
    f, s = cfg["fee_pct"] / 100, cfg["slippage_pct"] / 100
    st = ctx["state"] or {"btc": 0.0, "invested": 0.0, "lump_btc": None,
                          "budget": cfg["amount_usd"] * len(scheduled_days(start, start + 364 * DAY, cfg))}
    btc, invested, budget, lump_btc = st["btc"], st["invested"], st["budget"], st["lump_btc"]
    if lump_btc is None:
        lump_btc = budget * (1 - f) / (float(r["open"]) * (1 + s))
    out = []
    if is_buy_day(day, cfg):
        usd = cfg["amount_usd"]
        fill = float(r["open"]) * (1 + s)
        got = (usd - usd * f) / fill
        btc, invested = btc + got, invested + usd
        out.append({"kind": "buy", "detail": {"open": float(r["open"]), "fill_price": fill, "usd": usd,
                                              "fee_usd": usd * f, "btc": got}})
    close = float(r["close"])
    mark = {"close": close, "btc": btc, "invested": invested, "budget": budget, "lump_btc": lump_btc,
            "dca_equity": (budget - invested) + btc * close * (1 - f - s), "lump_equity": lump_btc * close * (1 - f - s)}
    out.append({"kind": "mark", "detail": mark})
    return out, mark


def forward_summary(events: list[dict], cfg: dict, done: bool) -> dict:
    marks = [e for e in events if e["kind"] == "mark"]
    out = {"buys": sum(1 for e in events if e["kind"] == "buy"), "verdict": None, "criteria": [],
           "dca_return_pct": None, "lump_return_pct": None, "dca_max_dd_pct": None, "lump_max_dd_pct": None}
    if marks:
        budget = marks[-1]["detail"]["budget"]
        dca_eq = [m["detail"]["dca_equity"] for m in marks]
        lump_eq = [m["detail"]["lump_equity"] for m in marks]
        out.update(dca_return_pct=(dca_eq[-1] / budget - 1) * 100, lump_return_pct=(lump_eq[-1] / budget - 1) * 100,
                   dca_max_dd_pct=_max_dd(dca_eq), lump_max_dd_pct=_max_dd(lump_eq))
    if done and marks:
        passed = out["dca_max_dd_pct"] < out["lump_max_dd_pct"]
        out["criteria"] = [{"id": "F1", "rule": "max drawdown over the 365 forward days: DCA < lump sum",
                            "passed": passed, "dca": out["dca_max_dd_pct"], "lump_sum": out["lump_max_dd_pct"]}]
        out["verdict"] = "PASS" if passed else "FAIL"
    return out
