"""Strategy Lab service: five independent research strategies side by side (round 1: DCA implemented).

Separate from the live signal engine (signal-1.8): its own versioned configurations (strategy_configs), its own daily
signal records (strategy_signals), one pre-registered design backtest per strategy and configuration
(strategy_backtests, written only by scripts/research_strategy.py) and its own forward tracker (strategy_forward.py).
A configuration that differs from the pre-registered one is "NOT VALIDATED (settings changed)" and inherits nothing.
Strategies are never ranked: the comparison table only shows each one's own numbers, N/A when they do not exist.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from database import now_ms
import pandas as pd

from services import asset_candles
from services.strategies import ENGINES, dca_metrics
from services.strategies.base import (FORWARD_PASS, NOT_VALIDATED, STRATEGIES, config_hash, data_quality,
                                      validation_status)

log = logging.getLogger(__name__)
DAY = 86_400_000
NEWS_HOURS = 72
NEWS_NOTE = ("Context only: headlines that name an asset, shown next to the ranking. News is never used by the rule "
             "and never part of the backtest.")

# Fingerprint re-bases (append-only, strategy_forward_rebases). Round 2 made the forward tracker generic and narrowed
# each strategy's fingerprint to its own module + the tracker core, so later rounds no longer disturb earlier
# strategies. DCA had been registered with the round-1 scheme but had not processed a single day: re-based once, at
# app start, only while that is still true (the database refuses it afterwards).
KNOWN_REBASES = [
    ("dca", "f626cffd73fb1e10", "f576a36a9cb5fdeacb3a99a6e63c0915b0817a4b5295000e8f85d6bc9cd5d4f6",
     "round 2: generic forward tracker; fingerprint = strategy module + tracker core (re-based before the first day)"),
]


def apply_rebases(db, now: int) -> int:
    from services.strategy_forward import strategy_fingerprint
    added = 0
    for sid, cfg_hash, old_fp, reason in KNOWN_REBASES:
        t = db.strategy_forward_test(sid, cfg_hash)
        if (t is None or t["fingerprint"] != old_fp or db.strategy_forward_rebases(t["id"])
                or db.strategy_forward_events(t["id"])):
            continue
        db.strategy_forward_rebase(t["id"], old_fp, strategy_fingerprint(sid), reason, now)
        added += 1
    return added
METRICS = [("Net return", "net_return_pct", "signed_pct"), ("Win rate", "win_rate_pct", "pct"),
           ("Profit factor", "profit_factor", "num"), ("Maximum drawdown", "max_drawdown_pct", "pct"),
           ("Number of trades", "trades", "int"), ("Average reward/risk", "avg_reward_risk", "num"),
           ("Fees and slippage", "costs_usd", "usd"), ("Buy-and-hold comparison", "buy_hold_return_pct", "signed_pct")]
# What "buy-and-hold" means for each strategy's backtest (the comparison row says it in every cell).
BUY_HOLD_BASIS = {"dca": "lump sum, same budget", "trend": "buy & hold, same window", "range": "buy & hold, same window",
                  "breakout": "buy & hold, same window", "rotation": "BTC buy & hold, same window"}
FAILED_NOTE = "Failed on design data: research only"
COMPARE_NOTE = ("Strategies are not ranked: each column shows that strategy's own pre-registered design backtest "
                "(design data before 2025-10-29, hypothetical) or N/A. A higher return with a deeper drawdown is not "
                "better by default, and design results never validate a strategy.")


def _fmt(v: Any, kind: str) -> str:
    if v is None:
        return "N/A"
    if kind == "signed_pct":
        return f"{v:+.2f}%"
    if kind == "pct":
        return f"{v:.2f}%"
    if kind == "int":
        return f"{int(v)}"
    if kind == "usd":
        return f"${v:,.2f}"
    return f"{v:.2f}"


class StrategyLab:
    def __init__(self, db, market, forward, clock: Callable[[], int] = now_ms,
                 regime_fn: Callable[[], str | None] | None = None):
        self.db, self.market, self.forward, self.clock = db, market, forward, clock
        self.regime_fn = regime_fn or (lambda: None)
        self._supplement: dict = {}

    # ---------------------------------------------------------------- configuration (versioned, append-only)
    def _engine(self, sid: str):
        if sid not in {s["id"] for s in STRATEGIES}:
            raise LookupError(f"Unknown strategy '{sid}'")
        if sid not in ENGINES:
            raise LookupError(f"Strategy '{sid}' is not implemented yet")
        return ENGINES[sid]

    def ensure_defaults(self) -> None:
        apply_rebases(self.db, self.clock())
        for sid, eng in ENGINES.items():
            if not self.db.strategy_configs(sid):
                cfg = eng.validate_config({})
                self.db.strategy_config_add(sid, cfg, config_hash(cfg), f"pre-registered defaults ({eng.PREREGISTRATION})",
                                            self.clock())

    def config(self, sid: str) -> dict:
        eng = self._engine(sid)
        self.ensure_defaults()
        cur = self.db.strategy_configs(sid)[0]
        prereg = config_hash(eng.validate_config({}))
        return {"strategy_id": sid, "version": cur["version"], "config": cur["config"], "hash": cur["config_hash"],
                "preregistered_hash": prereg, "settings_changed": cur["config_hash"] != prereg,
                "reason": cur["reason"], "defaults": eng.DEFAULTS, "fields": eng.FIELDS,
                "preregistration": eng.PREREGISTRATION, "order": list(eng.DEFAULTS)}

    def update_config(self, sid: str, payload: dict) -> dict:
        eng = self._engine(sid)
        if not isinstance(payload, dict):
            raise ValueError("Send a JSON object of settings")
        cur = self.config(sid)
        cfg = eng.validate_config({**cur["config"], **payload})
        if config_hash(cfg) != cur["hash"]:
            self.db.strategy_config_add(sid, cfg, config_hash(cfg), "changed in Strategy Lab", self.clock())
        return self.config(sid)

    def reset_config(self, sid: str) -> dict:
        eng = self._engine(sid)
        cur = self.config(sid)
        cfg = eng.validate_config({})
        if config_hash(cfg) != cur["hash"]:
            self.db.strategy_config_add(sid, cfg, config_hash(cfg), "reset to the pre-registered defaults", self.clock())
        return self.config(sid)

    # ---------------------------------------------------------------- evaluation and status
    def backtest_for(self, sid: str, cfg_hash: str) -> dict | None:
        rows = self.db.strategy_backtests(sid, cfg_hash)
        return {**rows[0]["result"], "id": rows[0]["id"], "created_at": rows[0]["created_at"]} if rows else None

    def forward_status(self, sid: str) -> dict:
        eng = self._engine(sid)
        return self.forward.status(sid, config_hash(eng.validate_config({})))

    def validation(self, sid: str, cfg: dict) -> dict:
        eng = self._engine(sid)
        fwd = self.forward_status(sid) if not cfg["settings_changed"] else None
        if getattr(eng, "FORWARD_ONLY", False):         # chosen after design results: never a design verdict
            out = validation_status(cfg["preregistered_hash"], cfg["hash"], None, fwd)
            label = eng.LABEL[0].upper() + eng.LABEL[1:]
            return {**out, "label": f"{label} · {out['label'][0].lower() + out['label'][1:]}",
                    "design_verdict": None, "forward_only": True}
        bt = self.backtest_for(sid, cfg["hash"])
        return validation_status(cfg["preregistered_hash"], cfg["hash"], bt, fwd)

    def evaluate(self, sid: str) -> dict:
        eng = self._engine(sid)
        cfg = self.config(sid)
        now = self.clock()
        results = {tf: self.market.get_candles(tf) for tf in eng.EVAL_TIMEFRAMES}
        main = results[eng.EVAL_TIMEFRAMES[0]]
        frames = {tf: res.df for tf, res in results.items()}
        frames.update(self.extra_frames(eng, now))
        try:
            regime = self.regime_fn()
        except Exception:                      # context only: never blocks the strategy
            log.exception("Regime context failed")
            regime = None
        r = eng.evaluate_frames(frames, cfg["config"], now, data_quality(main, now), regime, cfg["version"]).to_dict()
        if hasattr(eng, "NEWS_WORDS"):
            r["news_context"] = self.news_context(eng, now)
        status = self.validation(sid, cfg)
        if status["status"] != FORWARD_PASS:
            r["labels"] = r["labels"] + ["NOT VALIDATED"]
        # every live result of a strategy that failed its pre-registered design criteria says so
        r["design_warning"] = (FAILED_NOTE if status.get("design_verdict") == "FAIL" else
                               eng.LABEL[0].upper() + eng.LABEL[1:] if getattr(eng, "FORWARD_ONLY", False) else None)
        return r

    def extra_frames(self, eng, now: int) -> dict:
        """Stored closed candles of the other assets a strategy ranks ("1d:<SYMBOL>"), e.g. rotation's ETH/SOL/BNB."""
        return asset_candles.frames(self.db, getattr(eng, "EXTRA_FRAMES", ()), now)

    def news_context(self, eng, now: int, limit: int = 8) -> dict:
        """Recent stored headlines that name one of the strategy's assets: shown, never used by the rule."""
        items = []
        for n in self.db.get_news(now - NEWS_HOURS * 3_600_000, 300):
            assets = eng.news_assets(n["title"])
            if assets:
                items.append({"title": n["title"], "publisher": n["publisher"], "link": n["link"],
                              "published_at": n.get("published_at") or n["collected_at"], "assets": assets})
            if len(items) >= limit:
                break
        return {"note": NEWS_NOTE, "hours": NEWS_HOURS, "items": items}

    def card(self, meta: dict) -> dict:
        sid = meta["id"]
        base = {**meta, "planned_round": meta["round"], "implemented": sid in ENGINES}
        if sid not in ENGINES:
            return {**base, "result": None, "config": None, "backtest": None,
                    "validation": {"status": NOT_VALIDATED, "detail": f"Not implemented yet: planned for round {meta['round']}."}}
        cfg = self.config(sid)
        try:
            result, error = self.evaluate(sid), None
        except Exception as exc:               # a bug or bad data must never break the whole page
            log.exception("Strategy %s failed", sid)
            result, error = None, str(exc)
        bt = None if getattr(self._engine(sid), "FORWARD_ONLY", False) else self.backtest_for(sid, cfg["hash"])
        return {**base, "result": result, "error": error, "config": {k: cfg[k] for k in ("version", "hash", "settings_changed")},
                "backtest": None if bt is None else {"verdict": bt.get("verdict"), "metrics": bt.get("metrics")},
                "validation": self.validation(sid, cfg)}

    def cards(self) -> list[dict]:
        return [self.card(m) for m in STRATEGIES]

    def comparison(self, cards: list[dict]) -> dict:
        rows = []
        for label, key, kind in METRICS:
            vals = {}
            for c in cards:
                m = (c.get("backtest") or {}).get("metrics") or {}
                if key == "costs_usd" and m.get("costs_usd") is None and m.get("costs_pct") is not None:
                    vals[c["id"]] = f"{m['costs_pct']:.2f}% of the account"      # trade strategies: % of the account
                elif key == "buy_hold_return_pct" and m.get(key) is not None:
                    vals[c["id"]] = f"{_fmt(m[key], kind)} ({BUY_HOLD_BASIS.get(c['id'], 'buy & hold')})"
                else:
                    vals[c["id"]] = _fmt(m.get(key), kind)
            rows.append({"metric": label, "values": vals})
        rows.append({"metric": "Validation status",
                     "values": {c["id"]: c["validation"]["label"] if c["implemented"] else "N/A" for c in cards}})
        return {"rows": rows, "note": COMPARE_NOTE}

    def detail(self, sid: str) -> dict:
        meta = next((m for m in STRATEGIES if m["id"] == sid), None)
        if meta is None:
            raise LookupError(f"Unknown strategy '{sid}'")
        card = self.card(meta)
        if not card["implemented"]:
            return card
        cfg = self.config(sid)
        bt = card["backtest"] and self.backtest_for(sid, cfg["hash"])
        extra = {}
        if sid == "dca":
            extra = {"backtest_supplement": self._dca_supplement(bt), "forward_deposit_neutral": self._dca_forward_extra()}
        return {**card, **extra, "backtest": bt, "forward": self.forward_status(sid),
                "history": [{"day": h["day"], "signal": h["signal"], "config_version": h["config_version"],
                             "explanation": h["result"].get("explanation")} for h in self.db.strategy_signals(sid, 60)],
                "config_history": [{"version": v["version"], "config_hash": v["config_hash"], "reason": v["reason"],
                                    "created_at": v["created_at"], "config": v["config"]} for v in self.db.strategy_configs(sid)]}

    # ---------------------------------------------------------------- DCA: deposit-neutral, reported only
    def _dca_supplement(self, bt: dict | None) -> dict | None:
        """Time-weighted and return-on-invested views of the LOCKED study's own period and configuration, from the
        stored design candles. Reported only (addendum 2026-11-01): the stored study and its verdict are untouched."""
        if not bt or not bt.get("period") or not bt.get("config"):
            return None
        key = (bt["id"], bt["period"]["first_day"], bt["period"]["last_day"])
        if key not in self._supplement:
            eng = ENGINES["dca"]
            cfg = eng.validate_config(bt["config"])
            rows = self.db.get_candles_range("binance", "BTCUSDT", "1d", bt["period"]["first_day"], bt["period"]["last_day"])
            df = pd.DataFrame(rows)
            df = df[df["close_time"] < bt["period"]["holdout_start"]] if not df.empty else df
            if df.empty:
                return None
            m = dca_metrics.deposit_neutral(eng.simulate(df, cfg, bt["period"]["first_day"], bt["period"]["last_day"]), cfg)
            self._supplement = {key: {**{k: v for k, v in m.items() if k not in ("twr_index", "roi_series")},
                                      "twr_index": dca_metrics.thin(m["twr_index"]),
                                      "roi_series": dca_metrics.thin(m["roi_series"]),
                                      "budget_basis_max_drawdown_pct": bt["metrics"]["max_drawdown_pct"],
                                      "period": {"first_day": bt["period"]["first_day"], "last_day": bt["period"]["last_day"]}}}
        return self._supplement[key]

    def _dca_forward_extra(self) -> dict | None:
        eng = ENGINES["dca"]
        cfg = eng.validate_config({})
        t = self.db.strategy_forward_test("dca", config_hash(cfg))
        if t is None:
            return None
        events = self.db.strategy_forward_events(t["id"])
        if not events:
            return None
        m = dca_metrics.from_forward_events(events, cfg)
        return {k: v for k, v in m.items() if k not in ("twr_index", "roi_series")}

    # ---------------------------------------------------------------- background (monitor)
    def record_daily(self) -> int:
        """Store each implemented strategy's first evaluation of the UTC day (append-only history)."""
        added, day = 0, self.clock() // DAY * DAY
        for sid in ENGINES:
            r = self.evaluate(sid)
            added += self.db.strategy_signal_add(sid, r["config_version"], r["config_hash"], day, r["signal"], r,
                                                 self.clock())
        return added

    def forward_job(self) -> int:
        """Register each implemented strategy's PRE-REGISTERED configuration (once) and process new closed days."""
        for sid, eng in ENGINES.items():
            cfg = eng.validate_config({})
            self.forward.ensure_started(sid, cfg, config_hash(cfg))
        return self.forward.process()
