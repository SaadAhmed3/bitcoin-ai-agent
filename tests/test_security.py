"""Phase 9 security review: one test (or more) per finding. No network: every provider is a fake."""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from conftest import FIXED_NOW, local_client
from test_phase8 import make_app

from services import security
from services.security import CSRF_HEADER, CSRF_VALUE, MAX_BODY_BYTES, RateLimiter

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "CG-unit-test-secret-9f3a2b7c"


def raw_client(app):
    """A client that does NOT send the app's CSRF header (like a request from another website)."""
    return app.test_client()


def strict_json(resp):
    """Parse a response as strict JSON: NaN/Infinity are not valid JSON and must never be sent."""
    def bad(token):
        raise AssertionError(f"non-finite number {token} in {resp.request.path}")
    return json.loads(resp.get_data(as_text=True), parse_constant=bad)


# ------------------------------------------------------------------ network exposure
def test_bind_defaults_to_loopback_and_needs_explicit_flag():
    assert security.resolve_bind("127.0.0.1", False, False) == ("127.0.0.1", False, [])
    host, debug, warns = security.resolve_bind("0.0.0.0", True, False)
    assert host == "127.0.0.1" and "APP_ALLOW_REMOTE" in warns[0]
    host, debug, warns = security.resolve_bind("0.0.0.0", True, True)
    assert host == "0.0.0.0" and debug is False                   # never the debugger on the network
    assert any("reachable from your network" in w for w in warns) and any("APP_DEBUG ignored" in w for w in warns)


def test_config_defaults_are_safe(monkeypatch):
    for k in ("APP_HOST", "APP_DEBUG", "APP_ALLOW_REMOTE"):
        monkeypatch.delenv(k, raising=False)
    from config import Config
    c = Config()
    assert c.host == "127.0.0.1" and c.debug is False and c.allow_remote is False


def test_ollama_url_warning_only_for_remote_hosts():
    assert security.ollama_url_warning("http://127.0.0.1:11434") is None
    assert security.ollama_url_warning("http://localhost:11434") is None
    assert "not this computer" in security.ollama_url_warning("http://192.168.1.20:11434")


# ------------------------------------------------------------------ DNS rebinding / CSRF
def test_foreign_host_header_is_rejected(config, db):
    _, app = make_app(config, db)
    c = local_client(app)
    assert c.get("/api/health", base_url="http://evil.example").status_code == 400
    assert c.get("/", base_url="http://evil.example:5000").status_code == 400
    assert c.get("/api/health", base_url=f"http://127.0.0.1:{config.port + 1}").status_code == 400
    for ok in ("http://localhost", f"http://127.0.0.1:{config.port}", f"http://localhost:{config.port}"):
        assert c.get("/api/health", base_url=ok).status_code == 200


def test_split_host_handles_ipv6_and_bad_ports():
    assert security.split_host("[::1]:5050") == ("::1", 5050)
    assert security.split_host("127.0.0.1") == ("127.0.0.1", None)
    assert security.split_host("localhost:abc") == ("localhost", -1)
    assert not security.host_allowed("localhost:abc", 5050, set())


def test_cross_origin_post_is_rejected(config, db):
    _, app = make_app(config, db)
    c = raw_client(app)
    before = c.get("/api/alerts").get_json()["unread"]
    # A malicious page: the browser adds its Origin. No custom header (that would need a CORS preflight).
    r = c.post("/api/alerts/test", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and "Cross-origin" in r.get_json()["error"]
    assert c.post("/api/paper/reset", json={}, headers={"Origin": "null"}).status_code == 403
    assert c.post("/api/alerts/test", headers={"Referer": "https://evil.example/page"}).status_code == 403
    assert c.post("/api/alerts/test").status_code == 403                       # no Origin, no Referer, no header
    assert c.put("/api/settings/general", json={"theme": "light"},
                 headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.delete("/api/alerts/rules/1", headers={"Origin": "https://evil.example"}).status_code == 403
    # Another app on a different local port is a different origin too.
    assert c.post("/api/alerts/test", headers={"Origin": f"http://127.0.0.1:{config.port + 1}"}).status_code == 403
    assert c.get("/api/alerts").get_json()["unread"] == before                 # nothing was created


def test_same_origin_and_app_header_are_accepted(config, db):
    _, app = make_app(config, db)
    c = raw_client(app)
    assert c.post("/api/alerts/test", headers={"Origin": f"http://127.0.0.1:{config.port}"}).status_code == 201
    assert c.post("/api/alerts/test", headers={"Referer": "http://localhost/"}).status_code == 201
    assert c.post("/api/alerts/test", headers={CSRF_HEADER: CSRF_VALUE}).status_code == 201
    # The header does not help a cross-site page: its Origin is still checked first.
    assert c.post("/api/alerts/test", headers={CSRF_HEADER: CSRF_VALUE, "Origin": "https://evil.example"}).status_code == 403


def test_app_javascript_sends_the_csrf_header():
    for name in ("app.js", "settings.js"):
        src = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
        assert f'"{CSRF_HEADER}": "{CSRF_VALUE}"' in src
        assert "window.fetch(" in src and src.count("window.fetch(") == 1        # every call goes through the wrapper


# ------------------------------------------------------------------ headers / XSS
@pytest.mark.parametrize("path", ["/", "/settings", "/api/health", "/vendor/plotly.min.js", "/static/js/app.js"])
def test_security_headers_on_every_response(config, db, path):
    c, _ = make_app(config, db)
    r = c.get(path)
    assert r.status_code == 200
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "'unsafe-eval'" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_pages_have_no_inline_executable_script(config, db):
    c, _ = make_app(config, db)
    for path in ("/", "/settings"):
        html = c.get(path).get_data(as_text=True)
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, tag   # CSP would block it anyway
        assert not re.search(r"\son[a-z]+=", html), "inline event handler in " + path


def test_page_config_json_is_escaped(config, db):
    """Values interpolated into the pages cannot close the <script> tag or inject markup."""
    from flask import render_template
    _, app = make_app(config, db)
    evil = "</script><script>alert(1)</script>"
    with app.test_request_context("/"):
        pages = [render_template("index.html", version=evil, timeframes=["1h"], display_tz=evil, theme=evil),
                 render_template("settings.html", version=evil, display_tz=evil, theme=evil, signal_warning=evil)]
    for html in pages:
        assert "<script>alert(1)" not in html
        assert "\\u003c/script\\u003e" in html and "&lt;/script&gt;" in html


def test_news_title_with_script_is_stored_as_text_and_escaped_in_export(config, db):
    c, app = make_app(config, db)
    title = '<script>window.__xss=1</script> Bitcoin ETF inflows <img src=x onerror=alert(1)>'
    db.insert_news([{"id": "x1", "feed": "t", "publisher": "Test", "title": title, "link": "https://a.test/x",
                     "published_at": FIXED_NOW - 3_600_000, "collected_at": FIXED_NOW - 3_600_000}])
    r = c.get("/api/news?hours=168")
    assert r.status_code == 200 and r.mimetype == "application/json"
    assert any(e["title"].startswith("<script>") for e in r.get_json()["events"])   # data, not markup
    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "${esc(e.title)}" in js                                                # rendered escaped


def test_feed_items_with_script_links_are_dropped(db):
    from services.news_collector import NewsCollector
    xml = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
           b'<item><title>Bitcoin ETF inflows hit a record</title><link>javascript:alert(document.cookie)</link></item>'
           b'<item><title>Bitcoin miners sell reserves</title><link>data:text/html,&lt;script&gt;x&lt;/script&gt;</link></item>'
           b'<item><title>Bitcoin hashrate at all-time high</title><link>https://a.test/ok</link></item></channel></rss>')
    col = NewsCollector(db, http=None, feeds=[{"id": "t", "name": "T", "url": "https://t.test"}], clock=lambda: FIXED_NOW,
                        fetcher=lambda f: (xml, 1.0))
    col.refresh(force=True)
    links = [n["link"] for n in db.get_news(0)]
    assert links == ["https://a.test/ok"]


def test_dashboard_links_from_data_go_through_safe_url():
    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "const safeUrl" in js
    found = list(re.finditer(r'href="\$\{([^}]*)\}"', js))
    assert len(found) >= 6
    for m in found:
        expr = m.group(1)
        assert "safeUrl(" in expr or expr in ("esc(s.link)",) or "/api/export/" in js[m.start() - 40:m.start()], expr
    # the one exception is already guarded by an explicit https? test on the same line
    assert re.search(r"s\.link && /\^https\?:", js)


# ------------------------------------------------------------------ input validation
def test_malformed_and_non_object_json_is_400(config, db):
    c, _ = make_app(config, db)
    for body in ("{not json", "[1, 2]", '"text"', "42", "\x00\xff"):
        r = c.post("/api/risk/size", data=body, content_type="application/json")
        assert r.status_code == 400, body
        assert "error" in r.get_json()


def test_large_body_is_400_not_500(config, db):
    c, _ = make_app(config, db)
    big = json.dumps({"message": "a" * (MAX_BODY_BYTES + 10)})
    r = c.post("/api/assistant/chat", data=big, content_type="application/json")
    assert r.status_code == 400 and "too large" in r.get_json()["error"]


@pytest.mark.parametrize("field", ["usd", "stop", "target"])
@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "1e400"])
def test_paper_order_rejects_non_finite_numbers(config, db, field, bad):
    c, app = make_app(config, db)
    body = {"side": "BUY", "usd": 100, field: bad}
    r = c.post("/api/paper/order", json=body)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert app.config["BTC_PAPER"].state(None)["orders"] == []


@pytest.mark.parametrize("bad", ["nan", "inf", "1e400"])
def test_risk_size_rejects_non_finite_numbers(config, db, bad):
    c, _ = make_app(config, db)
    for field in ("portfolio_value", "entry", "stop"):
        body = {"portfolio_value": 10_000, "entry": 60_000, "stop": 58_000, field: bad}
        r = c.post("/api/risk/size", json=body)
        assert r.status_code == 400, (field, r.get_data(as_text=True))


def test_risk_size_settings_must_be_an_object(config, db):
    c, _ = make_app(config, db)
    for settings in ([1, 2], "x", 5):
        r = c.post("/api/risk/size", json={"portfolio_value": 1000, "entry": 100, "stop": 90, "settings": settings})
        assert r.status_code == 400


def test_query_args_out_of_range_are_400(config, db):
    c, _ = make_app(config, db)
    for url in ("/api/candles?tf=1h&limit=abc", "/api/candles?tf=7m", "/api/news?hours=x", "/api/signal/history?limit=1.5",
                "/api/alerts?unread=2", "/api/alerts?limit=0", "/api/assistant/history?limit=zz",
                "/api/export/nope", "/api/export/news?format=xml", "/api/export/news?hours=999",
                "/api/export/backtest?run=abc", "/api/export/candles?tf=1h&from=garbage"):
        r = c.get(url)
        assert r.status_code == 400, url


BODIES = [{}, {"x": 1}, {"message": ["a"]}, {"message": "a" * 5000}, {"side": "SELL", "fraction": "nan"},
          {"side": "BUY", "usd": "nan"}, {"side": "BUY", "usd": {"a": 1}}, {"side": ["BUY"]},
          {"start_balance": "inf"}, {"start_balance": [1]}, {"settings": [1]}, {"settings": {"risk_pct": "nan"}},
          {"portfolio_value": "1e400", "entry": "nan", "stop": 1}, {"weights": "x"}, {"weights": {"news": "nan"}},
          {"reset": 1, "weights": []}, {"kind": "price_above", "params": {"price": "nan"}}, {"enabled": "yes"},
          {"theme": ["dark"]}, {"symbols": "x" * 500}, {"risk_pct": "inf"}, {"risk_pct": None}]
BACKTEST_BODIES = [{"years": "nan"}, {"years": "inf"}, {"years": [4]}, {"years": 4, "entry_mode": "zz"},
                   {"years": 4, "entry_mode": ["standard"]}, {"years": 4, "period": ["full"]},
                   {"years": 4, "period": {"a": 1}}, {"years": 99}]


def test_every_route_survives_junk_input_without_500(config, db):
    """Fuzz every registered route with malformed bodies and query args: 4xx is fine, 500 and NaN are not."""
    c, app = make_app(config, db)
    junk_args = "?limit=abc&tf=zz&hours=-1&unread=2&format=xml&run=abc&from=junk&to=junk&ai=2&force=9&category=%3Cb%3E"
    seen = 0
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        url = re.sub(r"<int:[^>]+>", "1", rule.rule)
        url = re.sub(r"<[^>]+>", "zzz", url)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            if method == "GET":
                for u in (url, url + junk_args):
                    r = c.get(u)
                    assert r.status_code < 500, (u, r.get_data(as_text=True)[:300])
                    if r.mimetype == "application/json":
                        strict_json(r)
                    seen += 1
                continue
            bodies = BACKTEST_BODIES if rule.endpoint == "backtest_run" else BODIES   # never start a real backtest
            for body in bodies:
                r = c.open(url, method=method, json=body)
                assert r.status_code < 500, (method, url, body, r.get_data(as_text=True)[:300])
                strict_json(r)
                seen += 1
    assert seen > 300
    assert app.config["BTC_BACKTESTS"].status()["state"] in ("idle", None)


# ------------------------------------------------------------------ rate limits
def test_rate_limiter_window_and_retry_after():
    t = [0.0]
    lim = RateLimiter({"x": (2, 60)}, clock=lambda: t[0])
    assert lim.check("x") is None and lim.check("x") is None
    wait = lim.check("x")
    assert wait is not None and 59 <= wait <= 60
    t[0] = 61
    assert lim.check("x") is None
    assert lim.check("unlisted") is None


def test_expensive_endpoints_are_rate_limited(config, db):
    c, _ = make_app(config, db)
    for name in ("news_refresh", "export", "assistant_chat", "assistant_explain", "backtest_run", "alert_test",
                 "paper_order", "settings_put"):
        assert name in security.RATE_LIMITS
    n, _ = security.RATE_LIMITS["alert_test"]
    codes = [c.post("/api/alerts/test").status_code for _ in range(n + 1)]
    assert codes[:n] == [201] * n and codes[-1] == 429
    r = c.post("/api/alerts/test")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    n, _ = security.RATE_LIMITS["news_refresh"]
    codes = [c.post("/api/news/refresh").status_code for _ in range(n + 1)]
    assert codes[-1] == 429 and 429 not in codes[:n]


# ------------------------------------------------------------------ errors
def test_unhandled_error_hides_the_trace_and_logs_it(config, db, caplog):
    _, app = make_app(config, db)

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    c = local_client(app)
    r = c.get("/api/boom")
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert "secret internal detail" not in body and "Traceback" not in body and "Internal error" in body
    assert any("Unhandled error" in rec.message for rec in caplog.records)
    assert c.get("/api/nope").status_code == 404 and c.get("/api/nope").get_json()["error"]
    assert c.delete("/api/health").status_code == 405


# ------------------------------------------------------------------ secrets
def test_api_key_never_leaves_the_server(config, db):
    config.coingecko_demo_key = FAKE_KEY
    c, app = make_app(config, db)
    c.post("/api/alerts/test")
    c.post("/api/assistant/chat", json={"message": "What is the data source health?"})
    seen = []
    for url in ("/", "/settings", "/api/settings", "/api/sources", "/api/health", "/api/assistant/status",
                "/api/assistant/history", "/api/signal", "/api/alerts"):
        seen.append(c.get(url).get_data(as_text=True))
    for ds in ("signals", "news", "paper_orders", "alerts", "chat"):
        for fmt in ("csv", "json"):
            seen.append(c.get(f"/api/export/{ds}?format={fmt}").get_data(as_text=True))
    assert not any(FAKE_KEY in s for s in seen)
    assert c.get("/api/settings").get_json()["market"]["coingecko_demo_key"]["value"] == "set"
    # ...and nothing in the database either.
    with sqlite3.connect(config.db_path) as conn:
        dump = "\n".join(conn.iterdump())
    assert FAKE_KEY not in dump


def test_env_files_are_ignored_and_example_has_no_values():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert ".env" in ignore and "data/*.sqlite3*" in ignore
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^COINGECKO_DEMO_API_KEY=\s*$", example, re.M)


KEY_PATTERNS = re.compile(r"(?:CG-[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}"
                          r"|xox[bap]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
                          r"|(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"])", re.I)


def test_no_key_like_strings_in_the_source_tree():
    hits = []
    for p in ROOT.rglob("*"):
        if any(part in (".venv", "venv", ".git", "__pycache__", ".pytest_cache", "data") for part in p.parts):
            continue
        if p.is_file() and p.suffix in (".py", ".js", ".html", ".css", ".md", ".txt", ".ini", ".example", ".json", ".cfg"):
            if p.name == "test_security.py":
                continue
            for m in KEY_PATTERNS.finditer(p.read_text(encoding="utf-8", errors="ignore")):
                hits.append(f"{p.relative_to(ROOT)}: {m.group(0)[:40]}")
    assert hits == []


# ------------------------------------------------------------------ outbound requests / trading safety
ALLOWED_HOSTS = {"api.binance.com", "data-api.binance.vision", "api.exchange.coinbase.com", "api.coingecko.com",
                 "www.coindesk.com", "cointelegraph.com", "decrypt.co", "bitcoinmagazine.com", "www.theblock.co",
                 "news.google.com", "www.federalreserve.gov", "www.bls.gov", "127.0.0.1",
                 # display-only links (never fetched)
                 "www.binance.com", "exchange.coinbase.com"}


def test_outbound_hosts_are_the_documented_ones():
    hosts = set()
    for p in list((ROOT / "services").glob("*.py")) + [ROOT / "config.py"]:
        hosts |= set(re.findall(r"https?://([a-zA-Z0-9.-]+)", p.read_text(encoding="utf-8")))
    assert hosts <= ALLOWED_HOSTS, hosts - ALLOWED_HOSTS


def test_every_outbound_call_has_a_timeout():
    src = (ROOT / "services" / "http_client.py").read_text(encoding="utf-8")
    assert "timeout=self.timeout" in src
    ol = (ROOT / "services" / "ollama_service.py").read_text(encoding="utf-8")
    assert ol.count("self.http_get(") == ol.count("timeout=STATUS_TIMEOUT_SECONDS") >= 1
    assert "self.http_post(f\"{self.url}/api/chat\", timeout=self.timeout" in ol
    for p in (ROOT / "services").glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for call in re.findall(r"requests\.(?:get|post|put|delete|request)\([^)]*\)", text):
            assert "timeout" in call, f"{p.name}: {call}"
        assert "urlopen(" not in text, p.name


def test_no_real_trading_code_paths():
    forbidden = re.compile(r"api/v3/order|/sapi/|fapi\.|dapi\.|X-MBX-APIKEY|hmac\.new|withdraw\w*\(|ccxt|"
                           r"python-binance|leverage\s*=|/orders\b", re.I)
    for p in list(ROOT.glob("*.py")) + list((ROOT / "services").glob("*.py")) + list((ROOT / "database").glob("*.py")):
        text = p.read_text(encoding="utf-8")
        assert not forbidden.search(text), f"{p.name}: {forbidden.search(text).group(0)}"
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "ccxt" not in req and "python-binance" not in req


def test_paper_db_check_constraint_blocks_non_simulated_orders(db):
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute("INSERT INTO paper_orders (created_at, side, qty, price, fee, reason, simulated) "
                         "VALUES (?, 'BUY', 1, 1, 0, 'x', 0)", (FIXED_NOW,))


# ------------------------------------------------------------------ dependencies
def test_requirements_are_pinned():
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            assert re.fullmatch(r"[A-Za-z0-9_.\-\[\]]+==[0-9][A-Za-z0-9.]*", line), line
