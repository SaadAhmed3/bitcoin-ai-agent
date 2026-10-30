"""Runs history downloads and backtests in a background thread and records the validation result."""
from __future__ import annotations

import threading
import traceback
from dataclasses import asdict, replace
from typing import Callable

from database import Database, now_ms
from services.backtesting import PERIODS, BacktestConfig, config_hash, hypothesis_hash, run_backtest
from services.history import download_all
from services.http_client import HttpClient
from services.signal_engine import DAY_MS, ENTRY_MODES, FORWARD_OBSERVATION_DAYS

PROVIDER, SYMBOL = "binance", "BTCUSDT"
WARMUP_YEARS = 1.5   # the daily timeframe needs ~500 closed candles before the first decision
REPEAT_BADGE = "HOLDOUT ALREADY USED - not valid for validation"


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
        return out

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
        cfg = cfg or BacktestConfig(years=years, period=period)
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
            if cfg.period == "holdout":
                # Every holdout run is recorded; only the FIRST run of a hypothesis can change the validation.
                hyp = hypothesis_hash(engine_cfg, cfg)
                holdout = self.db.register_holdout_run(
                    self.clock(), hyp, result["config_hash"], result["engine"],
                    {"engine": asdict(engine_cfg), "backtest": {k: v for k, v in asdict(cfg).items() if k != "criteria"},
                     "criteria": asdict(cfg.criteria)},
                    {"verdict": result["verdict"], "all_criteria_passed": bool(result["validated"]),
                     "criteria_passed": criteria_passed, "criteria_total": len(result["criteria"]),
                     "strategy": {k: result["strategy"].get(k) for k in ("trades", "net_return_pct", "max_drawdown_pct",
                                                                         "profit_factor")}},
                    None)
                result["hypothesis_hash"] = hyp
                result["holdout"] = {**holdout, "hypothesis_hash": hyp}
                if not holdout["counts_for_validation"]:
                    result["validated"] = False
                    result["period_badge"] = REPEAT_BADGE
                    result["verdict"] = (f"{REPEAT_BADGE}: holdout run #{holdout['first_run_id']} already tested these "
                                         f"rules. Stored for review, not valid for validation. {criteria_passed} of "
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
            # Only the first holdout run of a hypothesis becomes the validation record the NO TRADE gate reads.
            if holdout and holdout["counts_for_validation"]:
                self.db.set_setting("strategy_validation", {
                    "validated": bool(result["validated"]), "run_id": run_id, "as_of": self.clock(),
                    "engine": result["engine"], "config_hash": result["config_hash"],
                    "entry_mode": result["entry_mode"], "period_kind": "holdout",
                    "counts_for_validation": True, "holdout_run_id": holdout["id"],
                    "hypothesis_hash": result["hypothesis_hash"],
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


def effective_validation(stored: dict | None, engine_cfg) -> dict:
    """A validation only counts for the exact engine version + settings it was produced with."""
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
    if stored.get("validated") and stored.get("as_of"):
        # The NO TRADE gate stays on for a forward-observation period after a validation (see signal_engine).
        return {**stored, "observation_until": int(stored["as_of"]) + FORWARD_OBSERVATION_DAYS * DAY_MS}
    return stored
