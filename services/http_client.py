"""HTTP client with timeouts, retries with exponential backoff and a per-host rate limit."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

import requests

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ProviderError(Exception):
    """Raised when an external data source cannot deliver usable data."""


class RateLimiter:
    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._sleep = sleep

    def wait(self, host: str) -> None:
        with self._lock:
            now = self._clock()
            last = self._last.get(host)
            if last is not None:
                delta = now - last
                if delta < self.min_interval:
                    self._sleep(self.min_interval - delta)
            self._last[host] = self._clock()


class HttpClient:
    def __init__(self, timeout: float = 10.0, retries: int = 2, backoff: float = 1.0,
                 min_interval: float = 0.25, user_agent: str = "bitcoin-ai-agent",
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self._sleep = sleep
        self.limiter = RateLimiter(min_interval, sleep=sleep)

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> tuple[Any, float]:
        """GET a URL and parse JSON. Returns (data, latency_ms). Raises ProviderError."""
        resp, latency = self._get(url, params, headers)
        try:
            return resp.json(), latency
        except ValueError as exc:
            raise ProviderError(f"{urlparse(url).netloc}: invalid JSON response") from exc

    def get_bytes(self, url: str, params: dict | None = None, headers: dict | None = None,
                  max_bytes: int = 5_000_000) -> tuple[bytes, float]:
        """GET a URL and return the raw body (used for RSS/XML). Raises ProviderError."""
        merged = {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"}
        merged.update(headers or {})
        resp, latency = self._get(url, params, merged)
        body = resp.content
        if len(body) > max_bytes:
            raise ProviderError(f"{urlparse(url).netloc}: response too large ({len(body)} bytes)")
        return body, latency

    def _get(self, url: str, params: dict | None, headers: dict | None):
        host = urlparse(url).netloc
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            self.limiter.wait(host)
            started = time.perf_counter()
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.Timeout:
                last_error = f"timeout after {self.timeout}s"
            except requests.ConnectionError as exc:
                last_error = f"connection error: {exc.__class__.__name__}"
            else:
                latency = (time.perf_counter() - started) * 1000
                if resp.status_code == 200:
                    return resp, latency
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRYABLE_STATUS:
                    # 4xx such as 403/451 (geo-block) will not fix itself on retry.
                    raise ProviderError(f"{host}: {last_error}")
                retry_after = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
                if retry_after and retry_after.isdigit() and attempt < self.retries:
                    self._sleep(min(float(retry_after), 30.0))
                    continue
            if attempt < self.retries:
                self._sleep(self.backoff * (2 ** attempt))
        raise ProviderError(f"{host}: {last_error} (after {self.retries + 1} attempts)")
