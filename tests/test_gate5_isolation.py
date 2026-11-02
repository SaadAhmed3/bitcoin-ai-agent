"""Global Intelligence must never reach the live signal. Same news input -> identical gate 5 decision, before and after.

Gate 5 reads: FEEDS -> news_items -> NewsService.report(hours=72) -> news_analyzer -> signal_engine.news_blocks.
These tests pin that path. A failure here means STOP: any change to gate 5 is a separate proposal.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from conftest import FIXED_NOW, FakeProvider
from test_app_and_db import NEWS_XML, make_client
from test_phase8 import FakeHttp

from services.forward_tests import current_fingerprint
from services.news_collector import FEEDS, NewsCollector
from services.news_service import NewsService
from services.signal_engine import DEFAULT_WEIGHTS, ENGINE_VERSION, news_blocks
from services.strategy_forward import strategy_fingerprint

ROOT = Path(__file__).resolve().parents[1]
HOUR = 3_600_000

# SHA-256 with line endings normalised, recorded on 2026-11-02 from main at 1c665f6, before any R2 code was written.
FROZEN = {
    "services/signal_engine.py": "345bdce1b1b829e2c988f794385c4f1f5e18d2d4668bc431d84d0f8f72c5bb13",
    "services/technical_analysis.py": "dfbbca3de7d24ed65e7904b6055c248e758b247a83d292eda539d3a9627e73fe",
    "services/backtesting.py": "e3a0481a345401a7c3fc6028cdcf59e7845ef7074cc8f6b473ff8cb2689ee630",
    "services/regime.py": "c70265f88c17281ab588508874fdf47dda9304f4700eb3debf22c54bb8559bcd",
    "services/forward_tests.py": "5e4b8a0ebba56ae03631b37acc114ae63c1cb8d6a1ab69cc32f1f5f6e5b0a450",
    "services/strategy_forward.py": "10311b94993d79289aff2c1b464f311944b225dea631da5402cf7bcac9c1e9e5",
    "services/news_collector.py": "eeffd6163e22b8b5399078f4099b86c9554b57aae06573503b9d8424d8111be7",
    "services/news_analyzer.py": "0602d26a0b79e0681ee317661af8cc0814fa95061285b275203ebb1bbd1f6de6",
    "services/news_service.py": "c303411301e91b9921bd52370c10fae62d36f24ea64017896daf3f99c47e4668",
    "services/signal_service.py": "514162229b750ec4f4f594d776c2908e4f6984a41178b66b8700e520a7a97395",
    "services/paper_trading.py": "052583e2c472a980f62acca689d38193b5f6950839ac4b3574c10c29eb109569",
    "services/alerts.py": "1627c28c8040c4b7103cb243a86aa972d6bba4ff63dbd6648028559d0aae6373",
    "services/assistant.py": "04a0792a43f8415f35c3373e0a55c790dcb40a2811f8c551c9f77074d88ba596",
    "services/ollama_service.py": "c3a000b0c3e9e56084d88dfdb1b67dd951888e63d6d258e77a733713a73ed36e",
    "services/strategies/__init__.py": "9a86fcf20fb4beaecf55d5166bd592875503e60966e0f8dfdb2ff074a71f9b33",
    "services/strategies/base.py": "f27bb672d45ac77609e0874c35c8f6747e9224332d3c5ab5e3a9c7a062f0bb2c",
    "services/strategies/breakout.py": "81a0aaf854af5c4b423875abf96bba18d0bb8488486bb1adc2e248e3d851bb8c",
    "services/strategies/breakout_05r.py": "7093d6ee61c77ffc6a7243055d82d315d06159450dd444a9d92470227c27607a",
    "services/strategies/dca.py": "3864c320070461761d8fcc93b18729adeb8b613daf8998d1d1985669dc434f36",
    "services/strategies/dca_metrics.py": "c9fc976a23c52d7c906c6536129d6c99ae4487f8c3a759925b5fe76c68d034af",
    "services/strategies/range.py": "7bae5932d4df4c7bff53c9c1e56999b14cb17294edb77e2a92faeee53f0135c8",
    "services/strategies/rotation.py": "3fd83dde73ee9045af9f1cb9c6381aa1f63e64004fd190197af2fad478444007",
    "services/strategies/trend.py": "31680688c51727bc9795da2b61a9f4b8020bc9dca89382d3cb6a58c50f07f867",
}
FINGERPRINTS = {"dca": "6f3658518fdbc3db", "trend": "432740ee3f1654a0", "range": "cfac02664cd27ea9",
                "breakout": "ec393d8c89687959", "breakout_05r": "7d38ed28daa9085b", "rotation": "0e3d89b543d38528"}
FEEDS_PINNED = [("cointelegraph", "https://cointelegraph.com/rss"),
                ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"),
                ("theblock", "https://www.theblock.co/rss.xml"), ("decrypt", "https://decrypt.co/feed"),
                ("bitcoinmagazine", "https://bitcoinmagazine.com/feed"),
                ("googlenews", "https://news.google.com/rss/search?q=Bitcoin+OR+Federal+Reserve+OR+inflation&hl=en-US&gl=US&ceid=US:en")]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# ---------------------------------------------------------------- pins
def test_frozen_files_have_no_diff():
    changed = [p for p, h in FROZEN.items() if sha(ROOT / p) != h]
    assert changed == [], f"frozen files changed: {changed}"


def test_every_strategy_module_is_pinned():
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "services" / "strategies").glob("*.py")}
    assert on_disk <= set(FROZEN)


def test_forward_test_fingerprints_unchanged():
    assert current_fingerprint().startswith("5e4b8a0ebba56ae0")                     # H1, H2, BH
    assert {s: strategy_fingerprint(s)[:16] for s in FINGERPRINTS} == FINGERPRINTS


def test_engine_and_news_weight_unchanged():
    assert ENGINE_VERSION == "signal-1.8" and DEFAULT_WEIGHTS["news"] == 0.0


def test_gate5_feed_list_unchanged():
    assert [(f["id"], f["url"]) for f in FEEDS] == FEEDS_PINNED


def test_gate5_decision_text_pinned():
    ev = {"id": "e1", "title": "Exchange X halts withdrawals after a hack", "link": "https://news.example/x",
          "publisher": "Example Wire", "sentiment": "bearish", "verification": "confirmed", "freshness": "new",
          "impact": "high", "age_hours": 1.0}
    assert news_blocks([ev], "bearish") == (
        'Fresh high-impact bearish news (confirmed, 1.0 h ago): "Exchange X halts withdrawals after a hack" - '
        "Example Wire https://news.example/x. News can only block; it is not in the score.", [])
    assert news_blocks([ev], "bullish") == (None, [])
    assert news_blocks([ev], None) == (None, [])


# ---------------------------------------------------------------- no path from Intelligence to the signal
def _imports(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out |= {n.module} | {f"{n.module}.{a.name}" for a in n.names}
    return out


def test_nothing_existing_imports_intelligence():
    offenders = []
    for p in (ROOT / "services").rglob("*.py"):
        if p.name.startswith("intel_"):
            continue
        if any("intel_" in m for m in _imports(p)):
            offenders.append(p.name)
    assert offenders == []
    assert (ROOT / "services" / "intel_service.py").exists()


def test_intelligence_never_writes_news_or_touches_the_signal():
    files = list((ROOT / "services").glob("intel_*.py"))
    assert len(files) >= 4
    for p in files:
        src = p.read_text(encoding="utf-8")
        assert "insert_news" not in src and "news_items" not in src.replace("reads news_items", "")
        assert not any(m.startswith(("services.signal", "services.paper", "services.strategies", "services.forward"))
                       for m in _imports(p)), p.name


# ---------------------------------------------------------------- same input, same decision
BLOCKER = {"id": "x" * 20, "source_id": "gdelt", "source_type": "gdelt", "publisher": "evil.test",
           "title": "Bitcoin exchange hacked, $900 million stolen, withdrawals halted worldwide",
           "link": "https://evil.test/hack", "summary": "", "published_at": FIXED_NOW - HOUR, "collected_at": FIXED_NOW}


def _report(db):
    collector = NewsCollector(db, http=None, feeds=[], clock=lambda: FIXED_NOW, fetcher=lambda f: (b"", 1.0))
    return NewsService(db, collector, price_series=lambda: None, clock=lambda: FIXED_NOW).report(hours=72, refresh=False)


def _decisions(report):
    return {side: news_blocks(report["events"], side) for side in ("bearish", "bullish", None)}


def _stable(report):
    r = json.loads(json.dumps(report, sort_keys=True, default=str))
    r.get("summary", {}).pop("generated_at", None)
    return r


def _news_rows(db):
    with db.connect() as conn:
        return [tuple(r) for r in conn.execute("SELECT * FROM news_items ORDER BY id")]


def test_same_news_input_gives_identical_gate5_decision_after_intelligence_runs(config, db):
    from services.intel_service import IntelService
    c = make_client(config, db, [FakeProvider("fake1")])
    c.post("/api/news/refresh")                                  # NEWS_XML into news_items, the gate 5 input
    assert NEWS_XML and _news_rows(db)
    rows, before = _news_rows(db), _report(db)
    decisions = _decisions(before)
    signal = c.get("/api/signal").get_json()

    intel = IntelService(db, http=FakeHttp({"gdeltproject": {"articles": [
        {"url": BLOCKER["link"], "title": BLOCKER["title"], "domain": "evil.test", "seendate": "20251009T080000Z",
         "sourcecountry": "United States", "language": "English"}]}}), clock=lambda: FIXED_NOW)
    intel.refresh(force=True)
    assert intel.events()["events"], "the pipeline must really have produced events"
    assert any(e["title"] == BLOCKER["title"] for e in intel.events()["events"])

    assert _news_rows(db) == rows                                # gate 5's table is byte-identical
    after = _report(db)
    assert _stable(after) == _stable(before)
    assert _decisions(after) == decisions
    assert not any(BLOCKER["title"] == e["title"] for e in after["events"])
    again = c.get("/api/signal").get_json()
    for key in ("signal", "setup", "score", "no_trade_reasons"):
        assert again[key] == signal[key], key
    assert again["diagnostics"]["gate_keys"] == signal["diagnostics"]["gate_keys"]


def test_gate5_sees_news_rows_but_not_intel_articles(config, db):
    """The control: the same headline DOES block when it is really in news_items, so the test above can fail."""
    from services.intel_service import IntelService
    make_client(config, db, [FakeProvider("fake1")])
    IntelService(db, http=FakeHttp(), clock=lambda: FIXED_NOW).add_articles([dict(BLOCKER)])
    assert not any(e["title"] == BLOCKER["title"] for e in _report(db)["events"])
    db.insert_news([{"id": BLOCKER["id"], "feed": "t", "publisher": "P", "title": BLOCKER["title"], "link": BLOCKER["link"],
                     "summary": "", "published_at": BLOCKER["published_at"], "collected_at": FIXED_NOW}])
    assert any(e["title"] == BLOCKER["title"] for e in _report(db)["events"])
