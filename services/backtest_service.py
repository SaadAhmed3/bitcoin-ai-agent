"""Runs history downloads and backtests in a background thread and records the validation result."""
from __future__ import annotations

import threading
import traceback
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Callable

from database import Database, now_ms
from services.backtesting import (HOLDOUT_START, PERIODS, BacktestConfig, code_fingerprint, config_hash,
                                  hypothesis_hash, run_backtest)
from services.history import download_all
from services.http_client import HttpClient
from services.signal_engine import DAY_MS, ENTRY_MODES, FORWARD_OBSERVATION_DAYS

PROVIDER, SYMBOL = "binance", "BTCUSDT"
WARMUP_YEARS = 1.5   # the daily timeframe needs ~500 closed candles before the first decision
SPENT_BADGE = "HOLDOUT SPENT - not valid for validation"


class BacktestService:
    def __init__(self, db: Database, engine_config: Callable, http_factory: Callable[[], HttpClient],
                 clock: Callable[[], int] = now_ms, runner: Callable = run_backtest, downloader: Callable = download_all):
        self.db = db
        self.engine_config = engine_config
        self.http_factory = http_factory
        self.clock = clock
        self.runner = runner
        self.downloader = downloader
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = {"state": "idle", "progress": 0.0, "message": "No backtest running.", "run_id": None,
                      "started_at": None, "finished_at": None, "error": None}

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        with self._lock:
            out = dict(self.state)
        out["history"] = {tf: self.db.candle_span(PROVIDER, SYMBOL, tf) for tf in ("1h", "4h", "1d")}
        out["holdout"] = holdout_status(self.db)
        return out

    # ------------------------------------------------------------------ the single-use holdout (0.9.3)
    def set_holdout_start(self, value) -> dict:
        """Start a NEW holdout period. Only allowed once the current one is spent, and only from the day after the
        run that spent it: the new holdout must be forward data that no rule choice has seen."""
        st = holdout_status(self.db)
        if st["status"] != "spent":
            raise ValueError(f"The current holdout (from {_utc_date(st['start'])}) is untouched: use it. A new holdout "
                             "start can only be set after it has been spent.")
        if not isinstance(value, str):
            raise ValueError("start must be a date string YYYY-MM-DD (UTC)")
        try:
            day = datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("start must be a date string YYYY-MM-DD (UTC)") from None
        start = int(day.timestamp() * 1000)
        if start < st["earliest_new_start"]:
            raise ValueError(f"The new holdout must begin after the spent period: on or after "
                             f"{_utc_date(st['earliest_new_start'])} (the holdout from {_utc_date(st['start'])} was "
                             f"{st['label']}).")
        stored = self.db.get_setting("holdout_period") or {}
        previous = list(stored.get("previous") or []) + [{"start": st["start"], "spent_at": st["spent_at"],
                                                           "spent_by_run": st["spent_by_run"]}]
        self.db.set_setting("holdout_period", {"start": start, "set_at": self.clock(), "previous": previous})
        return holdout_status(self.db)

    def _set(self, **kw) -> None:
        with self._lock:
            self.state.update(kw)

    # ------------------------------------------------------------------ control
    def start(self, years: float = 4.0, download: bool = True, cfg: BacktestConfig | None = None,
              background: bool = True, entry_mode: str | None = None, period: str = "full") -> dict:
        try:
            years = float(years)
        except (TypeError, ValueError):
            raise ValueError("years must be a number") from None
        if not 0.5 <= years <= 8:
            raise ValueError("years must be between 0.5 and 8")
        if entry_mode is not None and entry_mode not in ENTRY_MODES:
            raise ValueError(f"entry_mode must be one of: {', '.join(ENTRY_MODES)}")
        if period not in PERIODS:
            raise ValueError(f"period must be one of: {', '.join(PERIODS)}")
        with self._lock:
            if self.state["state"] in ("downloading", "running"):
                raise ValueError("A backtest is already running.")
            self._cancel.clear()
            self.state.update({"state": "starting", "progress": 0.0, "message": "Starting...", "run_id": None,
                               "started_at": self.clock(), "finished_at": None, "error": None})
        cfg = cfg or BacktestConfig(years=years, period=period, holdout_start=current_holdout_start(self.db))
        if background:
            self._thread = threading.Thread(target=self._run, args=(years, download, cfg, entry_mode), daemon=True)
            self._thread.start()
        else:
            self._run(years, download, cfg, entry_mode)
        return self.status()

    def cancel(self) -> dict:
        self._cancel.set()
        self._set(message="Cancelling...")
        return self.status()

    # ------------------------------------------------------------------ work
    def _run(self, years: float, download: bool, cfg: BacktestConfig, entry_mode: str | None = None) -> None:
        try:
            if download:
                self._set(state="downloading", message="Downloading historical candles from Binance...")
                self.downloader(self.db, self.http_factory(), years + WARMUP_YEARS,
                                progress=lambda m: self._set(message=m), clock=self.clock)
            if self._cancel.is_set():
                raise RuntimeError("cancelled")
            self._set(state="running", message="Loading candles...")
            rows = {tf: self.db.get_candles_range(PROVIDER, SYMBOL, tf) for tf in ("1h", "4h", "1d")}
            engine_cfg = self.engine_config()
            if entry_mode is not None:
                engine_cfg = replace(engine_cfg, entry_mode=entry_mode)
            result = self.runner(rows["1h"], rows["4h"], rows["1d"], engine_cfg, cfg,
                                 progress=lambda f, m: self._set(progress=round(f, 3), message=m),
                                 cancelled=self._cancel.is_set, now_ms=self.clock())
            result.setdefault("entry_mode", engine_cfg.entry_mode)
            result.setdefault("period_kind", cfg.period)
            criteria_passed = sum(1 for c in result["criteria"] if c["passed"])
            holdout = None
            fingerprint = code_fingerprint()
            if cfg.period == "holdout":
                # Every holdout run is recorded. The holdout is SINGLE-USE: only the first run in this holdout period,
                # whatever its rule set, can change the validation; it spends the period.
                hyp = hypothesis_hash(engine_cfg, cfg, fingerprint)
                holdout = self.db.register_holdout_run(
                    self.clock(), hyp, result["config_hash"], result["engine"],
                    {"engine": asdict(engine_cfg), "backtest": {k: v for k, v in asdict(cfg).items() if k != "criteria"},
                     "criteria": asdict(cfg.criteria), "code_fingerprint": fingerprint},
                    {"verdict": result["verdict"], "all_criteria_passed": bool(result["validated"]),
                     "criteria_passed": criteria_passed, "criteria_total": len(result["criteria"]),
                     "strategy": {k: result["strategy"].get(k) for k in ("trades", "net_return_pct", "max_drawdown_pct",
                                                                         "profit_factor")}},
                    None, holdout_start=cfg.holdout_start)
                result["hypothesis_hash"] = hyp
                result["holdout"] = {**holdout, "hypothesis_hash": hyp, "holdout_start": cfg.holdout_start}
                if not holdout["counts_for_validation"]:
                    result["validated"] = False
                    result["period_badge"] = SPENT_BADGE
                    result["verdict"] = (f"Stored for review, {spent_label(holdout['spender'])}. {criteria_passed} of "
                                         f"{len(result['criteria'])} criteria passed.")
            else:
                result["validated"] = False   # design and full runs include in-sample data: never a validation
            summary = {"verdict": result["verdict"], "period": result["period"],
                       "entry_mode": result["entry_mode"], "period_kind": result["period_kind"],
                       "strategy": {k: result["strategy"][k] for k in ("trades", "net_return_pct", "max_drawdown_pct",
                                                                        "profit_factor", "win_rate_pct")},
                       "buy_and_hold": result["baselines"]["buy_and_hold"]["net_return_pct"],
                       "criteria_passed": criteria_passed, "criteria_total": len(result["criteria"]),
                       "counts_for_validation": bool(holdout and holdout["counts_for_validation"])}
            run_id = self.db.save_backtest(self.clock(), result["engine"], result["config_hash"],
                                           bool(result["validated"]), summary, result)
            if holdout:
                self.db.link_holdout_run(holdout["id"], run_id)
            # Only the run that spent the holdout period becomes the validation record the NO TRADE gate reads.
            if holdout and holdout["counts_for_validation"]:
                self.db.set_setting("strategy_validation", {
                    "validated": bool(result["validated"]), "run_id": run_id, "as_of": self.clock(),
                    "engine": result["engine"], "config_hash": result["config_hash"],
                    "entry_mode": result["entry_mode"], "period_kind": "holdout",
                    "counts_for_validation": True, "holdout_run_id": holdout["id"],
                    "hypothesis_hash": result["hypothesis_hash"], "code_fingerprint": fingerprint,
                    "holdout_start": cfg.holdout_start,
                    "period": result["period"], "note": result["verdict"],
                    "failed": [f"{c['name']} ({c.get('detail', '')})" for c in result["criteria"] if not c["passed"]]})
            self._set(state="done", progress=1.0, run_id=run_id, finished_at=self.clock(),
                      message=f"Finished: {result['verdict']}")
        except RuntimeError as exc:
            if str(exc) == "cancelled":
                self._set(state="cancelled", message="Backtest cancelled.", finished_at=self.clock())
            else:
                self._fail(exc)
        except Exception as exc:  # report, never crash the app
            self._fail(exc)

    def _fail(self, exc: Exception) -> None:
        traceback.print_exc()
        self._set(state="error", error=str(exc), message=f"Failed: {exc}", finished_at=self.clock())


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def current_holdout_start(db: Database) -> int:
    """Start of the current holdout period: 2025-10-29 until a spent holdout is replaced by a new one."""
    return int((db.get_setting("holdout_period") or {}).get("start") or HOLDOUT_START)


def spent_label(spender: dict) -> str:
    run = f"run #{spender['backtest_run_id']}" if spender.get("backtest_run_id") else f"holdout run #{spender['id']}"
    return f"not valid for validation: holdout already spent on {_utc_date(spender['created_at'])}, {run}"


def holdout_status(db: Database) -> dict:
    """The current holdout period: untouched, or spent on <date> by run #N - then a new start may be set, from the
    day after the spending run (forward data only)."""
    start = current_holdout_start(db)
    st = db.holdout_state(start)
    st["previous"] = (db.get_setting("holdout_period") or {}).get("previous") or []
    if st["status"] == "spent":
        run = st["spent_by_run"] or f"{st['spent_by_holdout_run']} (holdout registry)"
        st["label"] = f"spent on {_utc_date(st['spent_at'])} by run #{run}"
        st["earliest_new_start"] = (max(start, st["spent_at"]) // DAY_MS + 1) * DAY_MS
    else:
        st["label"], st["earliest_new_start"] = "untouched", None
    return st


def current_validation(db: Database, engine_cfg) -> dict:
    """The validation the NO TRADE gate reads: the stored record, checked against the current settings, the current
    rule code and the run that actually spent its holdout period."""
    stored = db.get_setting("strategy_validation")
    spender = None
    if stored and stored.get("validated"):
        spender = db.holdout_state(int(stored.get("holdout_start") or HOLDOUT_START))["spent_by_holdout_run"]
    return effective_validation(stored, engine_cfg, spender_id=spender, fingerprint=code_fingerprint(),
                                check_spender=True)


def effective_validation(stored: dict | None, engine_cfg, spender_id: int | None = None,
                         fingerprint: str | None = None, check_spender: bool = False) -> dict:
    """A validation only counts for the exact engine version + settings it was produced with (and, when given,
    the same rule code and the run that spent the holdout period - see current_validation)."""
    if not stored:
        return {"validated": False, "note": "No backtest has been run yet - run one from the Backtest & validation panel."}
    current = config_hash(engine_cfg)
    if stored.get("config_hash") != current:
        return {**stored, "validated": False, "stale": True,
                "note": "The last backtest was run with different engine settings or version. Re-run it to validate "
                        "the current settings."}
    if stored.get("validated") and not (stored.get("period_kind") == "holdout"
                                        and stored.get("counts_for_validation") is True):
        # Records from before 0.9.2 (full runs, or holdout runs without the registry) can never lift the gate.
        kind = stored.get("period_kind") or "full"
        why = f"only the first holdout run of a set of rules can validate; this record is from a {kind} run"
        return {**stored, "validated": False, "note": f"Not counted: {why}.",
                "failed": [f"Not a first holdout run ({why})"]}
    if stored.get("validated") and fingerprint is not None and stored.get("code_fingerprint") != fingerprint:
        # 0.9.3: the rule code changed (even without an engine version bump), or the record predates fingerprints.
        return {**stored, "validated": False, "stale": True,
                "note": "The rule code changed since this validation (code fingerprint differs): the validated rules "
                        "are not the ones running now. Validating them needs a new holdout period."}
    if stored.get("validated") and check_spender and stored.get("holdout_run_id") != spender_id:
        return {**stored, "validated": False,
                "note": "Not counted: this holdout period was spent by another run; only that run's result counts."}
    if stored.get("validated") and stored.get("as_of"):
        # The NO TRADE gate stays on for a forward-observation period after a validation (see signal_engine).
        return {**stored, "observation_until": int(stored["as_of"]) + FORWARD_OBSERVATION_DAYS * DAY_MS}
    return stored
