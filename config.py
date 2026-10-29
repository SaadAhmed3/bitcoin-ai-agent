"""Central configuration.

All values can be overridden with environment variables or a local `.env` file
(copy `.env.example` to `.env`). Secrets never belong in source code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Timeframe -> seconds. These are the only timeframes the app accepts.
TIMEFRAMES: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # Server
    host: str = field(default_factory=lambda: _env_str("APP_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("APP_PORT", 5000))
    debug: bool = field(default_factory=lambda: _env_bool("APP_DEBUG", False))
    # Phase 9: the app binds to 127.0.0.1 unless remote access is explicitly allowed (it has no login).
    allow_remote: bool = field(default_factory=lambda: _env_bool("APP_ALLOW_REMOTE", False))
    # Extra Host names accepted besides localhost/127.0.0.1/[::1] (comma-separated), e.g. a LAN IP.
    allowed_hosts: list[str] = field(
        default_factory=lambda: [h.strip() for h in _env_str("APP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    )

    # Storage
    db_path: str = field(
        default_factory=lambda: _env_str("DB_PATH", str(BASE_DIR / "data" / "btc_agent.sqlite3"))
    )

    # Networking
    request_timeout: float = field(default_factory=lambda: _env_float("REQUEST_TIMEOUT_SECONDS", 10.0))
    max_retries: int = field(default_factory=lambda: _env_int("REQUEST_MAX_RETRIES", 2))
    retry_backoff: float = field(default_factory=lambda: _env_float("REQUEST_RETRY_BACKOFF", 1.0))
    # Minimum seconds between two requests to the same host (simple client-side rate limit).
    min_request_interval: float = field(default_factory=lambda: _env_float("MIN_REQUEST_INTERVAL", 0.25))
    user_agent: str = "bitcoin-ai-agent/0.3 (local research tool)"

    # Provider order for prices and candles. First healthy provider wins.
    providers: list[str] = field(
        default_factory=lambda: [
            p.strip() for p in _env_str("MARKET_PROVIDERS", "binance,coinbase").split(",") if p.strip()
        ]
    )
    # Optional free CoinGecko Demo key (free registration). Works without a key at lower limits.
    coingecko_demo_key: str = field(default_factory=lambda: _env_str("COINGECKO_DEMO_API_KEY", ""))

    # Caching / freshness
    price_cache_seconds: int = field(default_factory=lambda: _env_int("PRICE_CACHE_SECONDS", 10))
    price_stale_seconds: int = field(default_factory=lambda: _env_int("PRICE_STALE_SECONDS", 120))
    overview_cache_seconds: int = field(default_factory=lambda: _env_int("OVERVIEW_CACHE_SECONDS", 300))
    candle_limit: int = field(default_factory=lambda: _env_int("CANDLE_LIMIT", 500))

    # News
    news_refresh_seconds: int = field(default_factory=lambda: _env_int("NEWS_REFRESH_SECONDS", 600))

    # UI
    display_timezone: str = field(default_factory=lambda: _env_str("DISPLAY_TIMEZONE", "Asia/Karachi"))

    # Local AI assistant (Phase 7, optional). Ollama runs on this machine; keep the URL on localhost so no
    # app data leaves the computer. Without Ollama the assistant answers with deterministic rule-based text.
    ollama_url: str = field(default_factory=lambda: _env_str("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"))
    ollama_model: str = field(default_factory=lambda: _env_str("OLLAMA_MODEL", "qwen2.5:3b"))
    ollama_timeout: float = field(default_factory=lambda: _env_float("OLLAMA_TIMEOUT_SECONDS", 150.0))
    assistant_max_chars: int = field(default_factory=lambda: _env_int("ASSISTANT_MAX_MESSAGE_CHARS", 1000))

    def candle_cache_seconds(self, timeframe: str) -> int:
        """Re-fetch candles at most this often: a fraction of the candle interval, min 30s."""
        return max(30, TIMEFRAMES[timeframe] // 5)


def ensure_dirs(config: Config) -> None:
    Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
