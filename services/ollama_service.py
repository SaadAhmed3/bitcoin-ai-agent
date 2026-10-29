"""Thin client for a LOCAL Ollama server (Phase 7, optional).

Only two endpoints are used: GET /api/tags (is it running, which models are installed) and
POST /api/chat (non-streaming). Every failure is turned into OllamaError so callers can fall back
to rule-based answers; nothing here raises anything else into a Flask route.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

import requests

STATUS_CACHE_SECONDS = 30
STATUS_TIMEOUT_SECONDS = 2.0
CHAT_OPTIONS = {"temperature": 0.2, "num_ctx": 4096}   # low temperature: stick to the facts
THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.S | re.I)   # reasoning blocks (closed or cut off)


def strip_think(text: str) -> str:
    return THINK_RE.sub("", text or "").strip()


class OllamaError(Exception):
    """Ollama is unreachable, timed out, or returned something unusable."""


def _model_matches(wanted: str, installed: str) -> bool:
    w, i = wanted.strip().lower(), installed.strip().lower()
    return w == i or (":" not in w and i == f"{w}:latest") or (w.endswith(":latest") and i == w[:-7])


class OllamaService:
    def __init__(self, url: str, model: str, timeout: float = 150.0,
                 http_get: Callable[..., Any] = requests.get, http_post: Callable[..., Any] = requests.post,
                 clock: Callable[[], float] = time.monotonic):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.http_get = http_get
        self.http_post = http_post
        self.clock = clock
        self._cache: tuple[float, dict] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ status
    def status(self, force: bool = False) -> dict:
        with self._lock:
            if not force and self._cache and self.clock() - self._cache[0] < STATUS_CACHE_SECONDS:
                return dict(self._cache[1])
        out = {"available": False, "models": [], "model_installed": False, "model": self.model,
               "url": self.url, "error": None}
        try:
            r = self.http_get(f"{self.url}/api/tags", timeout=STATUS_TIMEOUT_SECONDS)
            if getattr(r, "status_code", 200) != 200:
                raise OllamaError(f"Ollama returned HTTP {r.status_code}")
            models = [m.get("name") or m.get("model") for m in (r.json() or {}).get("models", [])]
            models = [m for m in models if m]
            out.update(available=True, models=models, model_installed=any(_model_matches(self.model, m) for m in models))
            if not out["model_installed"]:
                out["error"] = f"Model {self.model} is not installed. Run: ollama pull {self.model}"
        except requests.exceptions.Timeout:
            out["error"] = "Ollama did not answer within 2 s"
        except requests.exceptions.ConnectionError:
            out["error"] = f"Ollama is not running at {self.url}"
        except OllamaError as exc:
            out["error"] = str(exc)
        except Exception as exc:  # malformed JSON or anything unexpected: report, never raise
            out["error"] = f"Unexpected Ollama response: {type(exc).__name__}"
        with self._lock:
            self._cache = (self.clock(), dict(out))
        return out

    def ready(self) -> bool:
        st = self.status()
        return bool(st["available"] and st["model_installed"])

    # ------------------------------------------------------------------ chat
    def chat(self, messages: list[dict]) -> dict:
        """Non-streaming chat. Returns {text, model, duration_ms}; raises OllamaError on any failure."""
        t0 = time.monotonic()
        body = {"model": self.model, "messages": messages, "stream": False, "options": dict(CHAT_OPTIONS)}
        if self.model.lower().startswith("qwen3"):
            body["think"] = False   # qwen3 "thinks" by default: slow on CPU and not needed for a rewrite
        try:
            r = self.http_post(f"{self.url}/api/chat", timeout=self.timeout, json=body)
        except requests.exceptions.Timeout:
            raise OllamaError(f"Ollama timed out after {self.timeout:.0f} s") from None
        except requests.exceptions.ConnectionError:
            raise OllamaError(f"Ollama is not running at {self.url}") from None
        except Exception as exc:
            raise OllamaError(f"Ollama request failed: {type(exc).__name__}") from None
        if getattr(r, "status_code", 200) != 200:
            detail = ""
            try:
                detail = (r.json() or {}).get("error", "")
            except Exception:
                pass
            raise OllamaError(f"Ollama returned HTTP {r.status_code} {detail}".strip())
        try:
            data = r.json()
            text = ((data or {}).get("message") or {}).get("content", "")
        except Exception:
            raise OllamaError("Ollama returned an unreadable response") from None
        text = strip_think(text) if isinstance(text, str) else ""
        if not text:
            raise OllamaError("Ollama returned an empty answer")
        return {"text": text, "model": data.get("model", self.model),
                "duration_ms": int((time.monotonic() - t0) * 1000)}
