import pytest
import requests

from services.http_client import HttpClient, ProviderError


class Resp:
    def __init__(self, status, payload=None, headers=None, bad_json=False, content=b""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self._bad = bad_json
        self.content = content

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return self._payload


class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(script, retries=2):
    s = FakeSession(script)
    return HttpClient(retries=retries, backoff=0, min_interval=0, session=s, sleep=lambda _: None), s


def test_success_returns_json_and_latency():
    c, s = client([Resp(200, {"ok": 1})])
    data, latency = c.get_json("https://example.test/x")
    assert data == {"ok": 1} and latency >= 0 and s.calls == 1


def test_timeout_is_retried_then_succeeds():
    c, s = client([requests.Timeout(), Resp(200, {"ok": 1})])
    assert c.get_json("https://example.test/x")[0] == {"ok": 1}
    assert s.calls == 2


def test_gives_up_after_retries():
    c, s = client([requests.Timeout()] * 3, retries=2)
    with pytest.raises(ProviderError, match="timeout"):
        c.get_json("https://example.test/x")
    assert s.calls == 3


def test_rate_limit_429_is_retried():
    c, s = client([Resp(429, headers={"Retry-After": "1"}), Resp(200, [1])])
    assert c.get_json("https://example.test/x")[0] == [1]


def test_geo_block_is_not_retried():
    c, s = client([Resp(451), Resp(200, {})])
    with pytest.raises(ProviderError, match="451"):
        c.get_json("https://example.test/x")
    assert s.calls == 1


def test_invalid_json_raises():
    c, _ = client([Resp(200, bad_json=True)])
    with pytest.raises(ProviderError, match="invalid JSON"):
        c.get_json("https://example.test/x")


def test_get_bytes_returns_body_and_sends_xml_accept():
    seen = {}

    class S(FakeSession):
        def get(self, url, params=None, headers=None, timeout=None):
            seen.update(headers or {})
            return super().get(url, params, headers, timeout)

    s = S([Resp(200, content=b"<rss/>")])
    c = HttpClient(retries=0, min_interval=0, session=s, sleep=lambda _: None)
    body, _ = c.get_bytes("https://example.test/feed")
    assert body == b"<rss/>" and "rss+xml" in seen["Accept"]


def test_get_bytes_rejects_huge_body():
    c, _ = client([Resp(200, content=b"x" * 20)], retries=0)
    with pytest.raises(ProviderError, match="too large"):
        c.get_bytes("https://example.test/feed", max_bytes=10)
