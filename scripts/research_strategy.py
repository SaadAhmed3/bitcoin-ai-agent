"""Strategy Lab: the pre-registered DESIGN backtest of one strategy, run once per strategy and configuration.

Refuses to run when:
  * the strategy is not implemented, is forward-only (a variant chosen after design results), or its
    PREREGISTRATION_<STRATEGY>.md is missing or not committed in git;
  * the strategy's defaults no longer produce the config hash written in that pre-registration;
  * a design backtest of this configuration is already stored (strategy_backtests is append-only and unique), or the
    output files already exist (they are created, never overwritten).
Design data only: candles (the strategy's BACKTEST_TIMEFRAMES) closed before the 2025-10-29 holdout start. Writes data\\strategy_<id>_design.json/.md
and the stored result shown in Strategy Lab.

Usage (from the project folder, .venv active):
    python scripts\\research_strategy.py dca   (or trend)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from database import Database, now_ms  # noqa: E402
from services.strategies import ENGINES  # noqa: E402
from services.strategies.base import config_hash  # noqa: E402

DATA_DIR = ROOT / "data"


def open_db() -> Database:
    db = Database(Config().db_path)
    db.init()
    return db


def load_rows(db: Database, timeframe: str) -> list[dict]:
    return db.get_candles_range("binance", "BTCUSDT", timeframe)


def prereg_committed(path: Path) -> bool:
    """The pre-registration is tracked by git and has no uncommitted changes."""
    try:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", path.name], cwd=ROOT, capture_output=True)
        clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", path.name], cwd=ROOT, capture_output=True)
    except OSError:
        return False
    return tracked.returncode == 0 and clean.returncode == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered design backtest of one Strategy Lab strategy (run once).")
    ap.add_argument("strategy")
    args = ap.parse_args(argv)
    sid = args.strategy
    eng = ENGINES.get(sid)
    if eng is None:
        print(f"Strategy '{sid}' is not implemented (available: {', '.join(ENGINES)}). Nothing was run.")
        return 2
    if getattr(eng, "FORWARD_ONLY", False):
        print(f"'{sid}' is forward-only ({eng.LABEL}): it never gets a design backtest. Nothing was run.")
        return 2
    prereg = ROOT / eng.PREREGISTRATION
    if not prereg.exists() or not prereg_committed(prereg):
        print(f"{eng.PREREGISTRATION} must exist and be committed before the first backtest. Nothing was run.")
        return 2
    text = prereg.read_text(encoding="utf-8")
    cfg = eng.validate_config({})
    h = config_hash(cfg)
    if f"Config hash: `{h}`" not in text:
        print(f"The {sid} defaults (hash {h}) differ from the pre-registered ones. Nothing was run.")
        return 2
    db = open_db()
    if db.strategy_backtests(sid, h):
        print(f"The design backtest of {sid} (config {h}) was already run once. Nothing was run.")
        return 2
    out_json, out_md = DATA_DIR / f"strategy_{sid}_design.json", DATA_DIR / f"strategy_{sid}_design.md"
    if out_json.exists() or out_md.exists():
        print(f"{out_json.name} already exists. Nothing was run.")
        return 2
    versions = [v["version"] for v in db.strategy_configs(sid) if v["config_hash"] == h]
    result = eng.backtest(*[load_rows(db, tf) for tf in eng.BACKTEST_TIMEFRAMES], cfg)
    prereg_sha = hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest()
    db.strategy_backtest_add(sid, min(versions) if versions else 1, h, "design", prereg_sha,
                             {**result, "preregistration_sha256": prereg_sha}, now_ms())
    with open(out_json, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(result, indent=1, default=float))
    md = eng.markdown(result)
    with open(out_md, "x", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    print(f"Saved {out_json} and {out_md.name}; stored in strategy_backtests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
