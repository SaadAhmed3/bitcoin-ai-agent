"""Download the daily candles of the rotation assets (ETH, SOL, BNB; Binance USDT pairs) into the local database.

Usage (project folder, .venv active):
    python scripts\\download_assets.py

Public endpoint, no API key. From 2021-05-01 (the first stored BTC daily candle, so all four assets cover the same
dates) to now; BTC's own candles are not touched. An asset without data at a date stays missing (N/A).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config, ensure_dirs  # noqa: E402
from database import Database  # noqa: E402
from services.asset_candles import DEFAULT_START  # noqa: E402
from services.history import download  # noqa: E402
from services.http_client import HttpClient, ProviderError  # noqa: E402
from services.strategies.rotation import ALTS  # noqa: E402


def main() -> int:
    cfg = Config()
    ensure_dirs(cfg)
    db = Database(cfg.db_path)
    db.init()
    http = HttpClient(timeout=cfg.request_timeout, retries=3, backoff=1.0, min_interval=0.3, user_agent=cfg.user_agent)
    for sym in ALTS:
        try:
            r = download(db, http, "1d", DEFAULT_START, symbol=sym)
        except ProviderError as exc:
            print(f"[FAIL] {sym}: {exc}")
            return 1
        print(f"[OK] {sym} 1d: {r['stored_in_range']} candles stored, {r['gaps']} missing, {r['pages']} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
