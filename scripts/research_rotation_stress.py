"""Rotation survivorship stress test (reported only), run ONCE after PREREGISTRATION_ROTATION_STRESS.md is committed.

Usage (project folder, .venv active):
    python scripts\\research_rotation_stress.py

Downloads any missing daily candles of the fixed universe from Binance's public klines (no key; the database is backed
up first), runs Rotation's rule over it on the original design window and writes data/rotation_stress_design.json/.md.
Refuses when: the pre-registration is not committed, the output already exists, or the stored Rotation design result
is missing. Never writes a strategy result: Rotation's own result and PASS verdict stay unchanged.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from database import Database  # noqa: E402
from services import rotation_stress as rs  # noqa: E402
from services.asset_candles import DEFAULT_START  # noqa: E402
from services.history import download  # noqa: E402
from services.http_client import HttpClient, ProviderError  # noqa: E402
from services.strategies import rotation as rot  # noqa: E402
from services.strategies.base import config_hash  # noqa: E402

PREREG = ROOT / "PREREGISTRATION_ROTATION_STRESS.md"
DATA_DIR = ROOT / "data"


def prereg_committed(path: Path) -> bool:
    try:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", path.name], cwd=ROOT, capture_output=True)
        clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", path.name], cwd=ROOT, capture_output=True)
    except OSError:
        return False
    return tracked.returncode == 0 and clean.returncode == 0


def open_db() -> Database:
    return Database(Config().db_path)


def backup(db_path: str) -> Path:
    src = Path(db_path).resolve()
    dst = src.with_name(src.name + datetime.now(timezone.utc).strftime(".backup-%Y%m%dT%H%M%SZ-pre-rotation-stress"))
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    a.backup(b)
    b.close()
    a.close()
    return dst


def fetch_missing(db: Database, http) -> list[str]:
    """Download the universe symbols that have no stored daily candles yet; returns the ones that failed."""
    failed = []
    for sym in rs.UNIVERSE:
        if db.get_candles_range("binance", sym, "1d"):
            continue
        try:
            r = download(db, http, "1d", DEFAULT_START, symbol=sym)
            print(f"[OK] {sym}: {r['stored_in_range']} daily candles")
        except ProviderError as exc:
            print(f"[N/A] {sym}: {exc}")
            failed.append(sym)
    return failed


def main(argv: list[str] | None = None, http=None) -> int:
    if not prereg_committed(PREREG):
        print(f"{PREREG.name} must exist and be committed before the run. Nothing was run.")
        return 2
    out_json, out_md = DATA_DIR / "rotation_stress_design.json", DATA_DIR / "rotation_stress_design.md"
    if out_json.exists() or out_md.exists():
        print(f"{out_json.name} already exists: the stress test runs once. Nothing was run.")
        return 2
    db = open_db()
    h = config_hash(rot.validate_config({}))
    stored = db.strategy_backtests("rotation", h)
    if not stored:
        print("No stored Rotation design result to compare with. Nothing was run.")
        return 2
    original = stored[0]["result"]
    if http is None:
        print(f"Database backed up to {backup(Config().db_path).name}")
        cfg = Config()
        http = HttpClient(timeout=cfg.request_timeout, retries=3, backoff=1.0, min_interval=0.3, user_agent=cfg.user_agent)
    fetch_missing(db, http)
    rows = {s: db.get_candles_range("binance", s, "1d") for s in rs.UNIVERSE}
    result = rs.study(rows, rs.UNIVERSE, original)
    with open(out_json, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(result, indent=1, default=float))
    md = rs.markdown(result)
    with open(out_md, "x", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    print(f"Saved {out_json} and {out_md.name}. Rotation's own result and verdict are unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
