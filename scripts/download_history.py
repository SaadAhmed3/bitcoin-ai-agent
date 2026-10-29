"""Download historical BTC/USDT candles (1h, 4h, 1d) from Binance into the local database.

Usage (project folder, .venv active):
    python scripts\\download_history.py            # last 4.5 years (enough for a 4-year backtest + warm-up)
    python scripts\\download_history.py --years 6

Public endpoint, no API key. About 70 requests for 4.5 years; takes 1-3 minutes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config, ensure_dirs  # noqa: E402
from database import Database  # noqa: E402
from services.history import download_all  # noqa: E402
from services.http_client import HttpClient, ProviderError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=4.5)
    args = ap.parse_args()
    cfg = Config()
    ensure_dirs(cfg)
    db = Database(cfg.db_path)
    db.init()
    http = HttpClient(timeout=cfg.request_timeout, retries=3, backoff=1.0, min_interval=0.3, user_agent=cfg.user_agent)
    try:
        results = download_all(db, http, args.years, progress=print)
    except ProviderError as exc:
        print(f"[FAIL] {exc}")
        return 1
    for r in results:
        print(f"[OK] {r['timeframe']}: {r['stored_in_range']} candles stored, {r['gaps']} missing, {r['pages']} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
