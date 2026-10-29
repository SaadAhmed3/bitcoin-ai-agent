"""Security layer for a local, single-user web app (Phase 9).

Threat model: the app runs on the user's own PC. The main remote attacker is a malicious WEBSITE open in the
same browser, which can make the browser send requests to http://127.0.0.1:<port> (CSRF) or point its own domain
at 127.0.0.1 (DNS rebinding). Defences here:

* Host allow-list  - requests whose Host header is not localhost / 127.0.0.1 / [::1] (optionally on the app's port)
                     are rejected with 400 (stops DNS rebinding).
* Same-origin writes - POST/PUT/PATCH/DELETE need an Origin (or, without Origin, a Referer) from an allowed host,
                     or the custom header X-Requested-With: BTCSignalLab that only the app's own JS sends. A
                     cross-site page cannot set that header without a CORS preflight, which the app never approves.
* Security headers  - CSP (scripts only from 'self'), nosniff, frame-ancestors 'none' / X-Frame-Options DENY,
                     Referrer-Policy no-referrer.
* JSON guard        - a request body sent as JSON must parse and be an object, else 400.
* Rate limits       - simple in-memory sliding windows for expensive endpoints (429 with Retry-After).
* Bind guard        - the server binds to 127.0.0.1 unless APP_ALLOW_REMOTE=1 is set explicitly; debug mode is
                     refused on a non-loopback address.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable
from urllib.parse import urlparse

from flask import Flask, jsonify, request

log = logging.getLogger("btc_agent.security")

CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "BTCSignalLab"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY_BYTES = 64 * 1024

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# endpoint name -> (max requests, window seconds). Only expensive or outbound-triggering endpoints.
RATE_LIMITS = {
    "news_refresh": (3, 60),          # fetches every RSS feed
    "export": (30, 60),               # up to 100k rows each
    "assistant_chat": (30, 60),       # AI path has its own single-flight lock
    "assistant_explain": (30, 60),
    "alert_test": (10, 60),
    "paper_order": (60, 60),          # forces a live price fetch
    "backtest_run": (6, 60),          # already single-flight; this stops start/cancel loops
    "settings_put": (60, 60),         # watchlist validation can call Binance
}


def split_host(host: str) -> tuple[str, int | None]:
    """'127.0.0.1:5050' -> ('127.0.0.1', 5050); '[::1]:5050' -> ('::1', 5050)."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        name, _, rest = host[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    else:
        name, _, port = host.partition(":")
    try:
        return name, (int(port) if port else None)
    except ValueError:
        return name, -1


def host_allowed(host: str, port: int | None, extra_hosts: set[str]) -> bool:
    name, p = split_host(host)
    if name not in LOOPBACK_HOSTS and name not in extra_hosts:
        return False
    return p is None or port is None or p == port


def origin_allowed(origin: str, port: int | None, extra_hosts: set[str]) -> bool:
    if not origin or origin == "null":
        return False
    u = urlparse(origin)
    if u.scheme not in ("http", "https") or not u.netloc:
        return False
    return host_allowed(u.netloc, port, extra_hosts)


class RateLimiter:
    def __init__(self, limits: dict[str, tuple[int, int]], clock: Callable[[], float] = time.monotonic):
        self.limits = limits
        self.clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """None if allowed (and counted), else seconds until the next request is allowed."""
        if key not in self.limits:
            return None
        n, window = self.limits[key]
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= n:
                return max(0.0, window - (now - q[0]))
            q.append(now)
        return None


def resolve_bind(host: str, debug: bool, allow_remote: bool) -> tuple[str, bool, list[str]]:
    """Returns (host, debug, warnings). Loopback unless remote access is explicitly allowed; never debug remotely."""
    warnings: list[str] = []
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not allow_remote:
        warnings.append(f"APP_HOST={host} ignored: binding to 127.0.0.1. Set APP_ALLOW_REMOTE=1 to expose the app "
                        "on your network (not recommended: there is no login).")
        host, loopback = "127.0.0.1", True
    if not loopback:
        warnings.append(f"WARNING: the app is reachable from your network on {host}. It has no login; anyone on "
                        "the network can use it. Add the address you browse with to APP_ALLOWED_HOSTS.")
        if debug:
            warnings.append("APP_DEBUG ignored: the Flask debugger is never enabled on a non-loopback address.")
            debug = False
    return host, debug, warnings


def ollama_url_warning(url: str) -> str | None:
    name = (urlparse(url).hostname or "").lower()
    if name in LOOPBACK_HOSTS:
        return None
    return (f"OLLAMA_URL points to {name or url!r}, not this computer. Questions and the fact sheet would leave "
            "your PC. Use http://127.0.0.1:11434 unless you trust that host.")


def install(app: Flask, port_fn: Callable[[], int | None], extra_hosts: set[str] | None = None,
            limiter: RateLimiter | None = None) -> RateLimiter:
    """Register the request guards and response headers on `app`."""
    extra = {h.strip().lower() for h in (extra_hosts or set()) if h.strip()}
    limiter = limiter or RateLimiter(RATE_LIMITS)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES

    def deny(status: int, msg: str):
        log.warning("Blocked %s %s: %s", request.method, request.path, msg)
        return jsonify({"error": msg}), status

    @app.before_request
    def _guards():
        port = port_fn()
        if not host_allowed(request.host, port, extra):
            return deny(400, "Host not allowed. Open the app at http://127.0.0.1:<port>.")
        if request.method in STATE_CHANGING:
            origin = request.headers.get("Origin")
            referer = request.headers.get("Referer")
            if origin is not None:
                ok = origin_allowed(origin, port, extra)
            elif referer:
                ok = origin_allowed(referer, port, extra)
            else:
                ok = request.headers.get(CSRF_HEADER) == CSRF_VALUE
            if not ok:
                return deny(403, "Cross-origin request blocked. Use the dashboard, or send the header "
                                 f"{CSRF_HEADER}: {CSRF_VALUE} from a local script.")
            if request.content_length and request.content_length > MAX_BODY_BYTES:
                return deny(400, f"Request body too large (max {MAX_BODY_BYTES // 1024} KB).")
            if request.get_data(cache=True) and (request.is_json or request.mimetype in ("", "text/plain")):
                body = request.get_json(silent=True, force=True)
                if body is None:
                    return deny(400, "Malformed JSON body.")
                if not isinstance(body, dict):
                    return deny(400, "The JSON body must be an object, e.g. {\"key\": value}.")
        wait = limiter.check(request.endpoint or "")
        if wait is not None:
            resp = jsonify({"error": f"Too many requests to this endpoint. Try again in {int(wait) + 1} s."})
            resp.headers["Retry-After"] = str(int(wait) + 1)
            return resp, 429
        return None

    @app.after_request
    def _headers(resp):
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    return limiter
