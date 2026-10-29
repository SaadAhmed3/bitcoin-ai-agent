"""Live connectivity check for every market data source.

Run from the project folder:  python scripts/check_sources.py
Uses real network calls (unlike the unit tests). Prints one line per source.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config  # noqa: E402
from services.http_client import HttpClient, ProviderError  # noqa: E402
from services.market_data import BinanceProvider, CoinbaseProvider, CoinGeckoProvider, validate_candles  # noqa: E402
from database import now_ms  # noqa: E402
from services.news_collector import FEEDS, parse_feed  # noqa: E402
from services.news_analyzer import analyse  # noqa: E402


def main() -> int:
    cfg = Config()
    http = HttpClient(timeout=cfg.request_timeout, retries=1, backoff=0.5, user_agent=cfg.user_agent)
    failures = 0
    for p in (BinanceProvider(http), CoinbaseProvider(http)):
        try:
            q = p.fetch_price()
            rows, _ = p.fetch_candles("1h", 50)
            closed, _ = validate_candles(rows, "1h", now_ms())
            print(f"[OK]   {p.name:9} price {q['price']:>12,.2f} {p.quote}  | {len(closed)} closed 1h candles")
        except ProviderError as exc:
            failures += 1
            print(f"[FAIL] {p.name:9} {exc}")
    try:
        o = CoinGeckoProvider(http, cfg.coingecko_demo_key).fetch_overview()
        dom = f"{o['btc_dominance_pct']:.1f}%" if o["btc_dominance_pct"] else "n/a"
        print(f"[OK]   coingecko market cap ${o['market_cap_usd'] / 1e12:.2f}T | dominance {dom}")
    except ProviderError as exc:
        print(f"[WARN] coingecko {exc}  (optional source)")
    print()
    all_items, feed_fail = [], 0
    for f in FEEDS:
        try:
            content, _ = http.get_bytes(f["url"])
            items, warns = parse_feed(content, f, now_ms())
            all_items += items
            dated = sum(1 for i in items if i["published_at"])
            note = f"  ({'; '.join(warns)})" if warns else ""
            print(f"[OK]   rss:{f['id']:15} {len(items):3} items, {dated} with publish time{note}")
        except ProviderError as exc:
            feed_fail += 1
            print(f"[FAIL] rss:{f['id']:15} {exc}")
    if all_items:
        events, summary = analyse(all_items, now_ms())
        print(f"\n       {len(all_items)} articles -> {len(events)} distinct events after de-duplication")
        print(f"       news bias: {summary['bias_label']} ({summary['bias_score']:+.2f}), "
              f"counts {summary['counts']}")
    if failures == 2:
        print("\nNo exchange reachable. Check internet, firewall/VPN, or regional blocks (HTTP 451).")
    if feed_fail == len(FEEDS):
        print("\nNo RSS feed reachable. News panels will stay empty.")
    return 1 if failures == 2 else 0


if __name__ == "__main__":
    raise SystemExit(main())
