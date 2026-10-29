"""Re-run the news analyzer on stored (or live) articles and print a review report.

Usage (from the project folder, .venv active):
    python scripts\\review_news.py               # articles already stored in the local database
    python scripts\\review_news.py --live        # fetch all RSS feeds now (nothing is saved)
    python scripts\\review_news.py --hours 24 --top 40

Writes data\\news_review.csv with one row per event (all fields used in the report) so
headlines can be checked in Excel. Opens the database in SQLite read-only mode (mode=ro).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from database import Database, now_ms  # noqa: E402
from services.http_client import HttpClient, ProviderError  # noqa: E402
from services.news_analyzer import HOUR, analyse  # noqa: E402
from services.news_collector import FEEDS, parse_feed  # noqa: E402


def load_items(args, cfg: Config) -> list[dict]:
    if args.live:
        http = HttpClient(timeout=cfg.request_timeout, retries=1, user_agent=cfg.user_agent)
        items = []
        for f in FEEDS:
            try:
                content, _ = http.get_bytes(f["url"])
                items += parse_feed(content, f, now_ms())[0]
            except ProviderError as exc:
                print(f"[FAIL] rss:{f['id']}: {exc}")
        # Same de-duplication by canonical link id as the database does.
        return list({i["id"]: i for i in items}.values())
    if not Path(cfg.db_path).exists():
        return []
    db = Database(cfg.db_path, read_only=True)  # never modifies the database file
    return db.get_news(now_ms() - (args.hours + 36) * HOUR)


def short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="fetch feeds now instead of reading the database")
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--top", type=int, default=30, help="events to list in the main table")
    args = ap.parse_args()
    cfg = Config()
    items = load_items(args, cfg)
    if not items:
        print("No articles found. Run the app (or use --live) to collect news first.")
        return 1

    events, s = analyse(items, now_ms())
    events = [e for e in events if e["age_hours"] <= args.hours]
    multi = [e for e in events if len(e["sources"]) > 1]

    print(f"\n{len(items)} articles -> {len(events)} events in the last {args.hours}h "
          f"(excluded: {s['excluded']})")
    print(f"News bias: {s['bias_label']} ({s['bias_score']:+.3f})  counts {s['counts']}  "
          f"high-impact active {s['high_impact_active']}")

    print(f"\n=== Top {args.top} events (sorted by impact, then age) ===")
    print(f"{'sent':9} {'score':>6} {'imp':6} {'verif':11} {'rel':>4} {'n':>3}  headline")
    for e in events[: args.top]:
        print(f"{e['sentiment']:9} {e['score']:+6.2f} {e['impact']:6} {e['verification']:11} "
              f"{e['relevance']:4.1f} {len(e['sources']):3}  {short(e['title'], 90)}")
        print(f"{'':48}why: {short(e['btc_impact'][0], 110)}  | cats: {', '.join(e['categories'][:3])}")

    print(f"\n=== Merged events ({len(multi)}) - check that every member is the SAME story ===")
    for e in sorted(multi, key=lambda e: -len(e["sources"])):
        tag = f" [{e['signature']}]" if e.get("signature") else ""
        print(f"\n* {len(e['sources'])} articles, {e['independent_sources']} publishers, "
              f"{e['sentiment']} {e['score']:+.2f}{tag}")
        for src in e["sources"][:12]:
            print(f"    - {short(src['publisher'], 18):18} {src['sentiment']:9} {short(src['title'], 95)}")
        if len(e["sources"]) > 12:
            print(f"    … and {len(e['sources']) - 12} more")

    out = ROOT / "data" / "news_review.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "publisher", "sentiment", "score", "impact", "verification", "freshness",
                    "relevance", "categories", "articles", "publishers", "why", "link"])
        for e in events:
            w.writerow([e["title"], e["publisher"], e["sentiment"], e["score"], e["impact"], e["verification"],
                        e["freshness"], e["relevance"], "; ".join(e["categories"]), len(e["sources"]),
                        e["independent_sources"], " | ".join(e["btc_impact"]), e["link"]])
    print(f"\nFull table written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
