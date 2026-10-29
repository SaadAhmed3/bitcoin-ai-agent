"""User settings (Phase 8): validated, persisted in the `settings` table, with env overrides and defaults.

Precedence for every value: environment variable (from the process or .env) > settings DB > default.
Env overrides are shown read-only. Secrets (the CoinGecko demo key) stay in .env: only "set"/"not set"
is ever returned by any API or written anywhere.

The Signal and Risk & paper sections are managed by their own services (SignalService.update_config and
PaperTrading.update_settings); this module covers the rest.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SUPPORTED_PROVIDERS = ("binance", "coinbase")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
MAX_WATCHLIST = 10
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


@dataclass(frozen=True)
class Field:
    default: Any
    kind: str                       # "str" | "int" | "float" | "bool" | "choice" | "list"
    help: str
    env: str | None = None          # env var that overrides this value
    restart: bool = False           # True: takes effect after an app restart
    lo: float | None = None
    hi: float | None = None
    choices: tuple = ()
    validator: Callable[[Any], Any] | None = None


def _tz(v: str) -> str:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError(f"'{v}' is not a known time zone (e.g. Asia/Karachi, UTC, Europe/London)") from None
    return v


def _providers(v: Any) -> list[str]:
    items = [p.strip().lower() for p in (v.split(",") if isinstance(v, str) else v or [])]
    items = [p for p in items if p]
    if not items:
        raise ValueError("Provider order needs at least one provider")
    bad = [p for p in items if p not in SUPPORTED_PROVIDERS]
    if bad or len(set(items)) != len(items):
        raise ValueError(f"Providers must be distinct values from: {', '.join(SUPPORTED_PROVIDERS)}")
    return items


def _symbols(v: Any) -> list[str]:
    items = [s.strip().upper() for s in (v.split(",") if isinstance(v, str) else v or [])]
    items = [s for s in items if s]
    if not items:
        raise ValueError("The watchlist needs at least one symbol")
    if len(items) > MAX_WATCHLIST:
        raise ValueError(f"The watchlist holds at most {MAX_WATCHLIST} symbols")
    bad = [s for s in items if not SYMBOL_RE.match(s)]
    if bad or len(set(items)) != len(items):
        raise ValueError(f"Invalid or duplicate symbol(s): {', '.join(bad) or 'duplicates'} (use Binance spot symbols like ETHUSDT)")
    return items


SECTIONS: dict[str, dict[str, Field]] = {
    "general": {
        "display_timezone": Field("Asia/Karachi", "str", "Time zone for local times in the dashboard and exports",
                                  env="DISPLAY_TIMEZONE", validator=_tz),
        "theme": Field("system", "choice", "Dashboard theme", choices=("system", "dark", "light")),
        "refresh_price_s": Field(15, "int", "Price refresh in the dashboard (seconds). Minimum 10: the price is cached 10 s",
                                 lo=10, hi=600),
        "refresh_analysis_s": Field(60, "int", "Indicators and data-source panel refresh (seconds)", lo=30, hi=3600),
        "refresh_news_s": Field(300, "int", "News and signal refresh (seconds). Minimum 300: RSS feeds are polled at most every 10 min",
                                lo=300, hi=3600),
    },
    "market": {
        "provider_order": Field(["binance", "coinbase"], "list", "Price/candle providers, first healthy one wins",
                                env="MARKET_PROVIDERS", restart=True, validator=_providers),
    },
    "alerts": {
        "cooldown_min": Field(30, "int", "Default cooldown per alert rule (minutes)", lo=1, hi=1440),
        "news_cooldown_h": Field(6, "int", "Cooldown per news cluster (hours)", lo=1, hi=72),
        "global_cap_per_hour": Field(20, "int", "At most this many alerts per hour (spam protection)", lo=1, hi=200),
        "hysteresis_pct": Field(0.5, "float", "Price alerts re-arm only after the price moves back this far (%)", lo=0.1, hi=10),
        "big_move_1h_pct": Field(3.0, "float", "Default big-move threshold over 1 hour (%)", lo=0.5, hi=50),
        "big_move_24h_pct": Field(8.0, "float", "Default big-move threshold over 24 hours (%)", lo=1, hi=80),
        "channel_browser": Field(False, "bool", "Browser notifications (also needs permission in the browser)"),
        "channel_desktop": Field(False, "bool", "Windows desktop notifications (needs the optional plyer package)"),
    },
    "assistant": {
        "ai_explanations": Field(False, "bool", "Use the local AI to rewrite explanation answers (slow on CPU; may contain mistakes)"),
        "model": Field("qwen2.5:3b", "str", "Ollama model name (checked against `ollama list` when Ollama runs)",
                       env="OLLAMA_MODEL"),
        "timeout_s": Field(150, "int", "Seconds to wait for the local model", env="OLLAMA_TIMEOUT_SECONDS", lo=30, hi=600),
    },
    "watchlist": {
        "symbols": Field(list(DEFAULT_WATCHLIST), "list", "Up to 10 Binance spot symbols (display only - no signals)",
                         validator=_symbols),
    },
}


class SettingsService:
    def __init__(self, db, model_check: Callable[[str], str | None] | None = None,
                 symbol_check: Callable[[list[str]], list[str]] | None = None,
                 secrets: Callable[[], dict[str, bool]] | None = None, env: Callable[[str], str | None] = os.getenv):
        """model_check(name) -> error text or None; symbol_check(symbols) -> list of unknown symbols."""
        self.db = db
        self.model_check = model_check
        self.symbol_check = symbol_check
        self.secrets = secrets or (lambda: {})
        self.env = env
        self._listeners: list[Callable[[str, dict], None]] = []

    def on_change(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    # ------------------------------------------------------------------ read
    def _stored(self, section: str) -> dict:
        return self.db.get_setting(f"settings:{section}", {}) or {}

    def _coerce(self, f: Field, raw: Any, name: str) -> Any:
        try:
            if f.kind == "bool":
                if isinstance(raw, bool):
                    return raw
                if isinstance(raw, str) and raw.strip().lower() in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                    return raw.strip().lower() in ("1", "true", "yes", "on")
                raise ValueError
            if f.kind == "int":
                if isinstance(raw, bool):
                    raise ValueError
                v = int(float(raw))
                if float(raw) != v:
                    raise ValueError
            elif f.kind == "float":
                if isinstance(raw, bool):
                    raise ValueError
                v = float(raw)
            elif f.kind == "choice":
                v = str(raw)
                if v not in f.choices:
                    raise ValueError(f"'{name}' must be one of: {', '.join(f.choices)}")
            elif f.kind == "list":
                v = raw
            else:
                v = str(raw).strip()
                if not v or len(v) > 200:
                    raise ValueError(f"'{name}' must be 1-200 characters")
        except ValueError as exc:
            raise ValueError(str(exc) or f"'{name}' has the wrong type (expected {f.kind})") from None
        except (TypeError, OverflowError):
            raise ValueError(f"'{name}' has the wrong type (expected {f.kind})") from None
        if f.kind in ("int", "float") and ((f.lo is not None and v < f.lo) or (f.hi is not None and v > f.hi)):
            raise ValueError(f"'{name}' must be between {f.lo:g} and {f.hi:g}")
        if f.validator:
            v = f.validator(v)
        return v

    def effective(self, section: str, key: str) -> tuple[Any, str]:
        """(value, source) with source in env | settings | default."""
        f = SECTIONS[section][key]
        if f.env:
            raw = self.env(f.env)
            if raw is not None and str(raw).strip() != "":
                try:
                    return self._coerce(f, raw, key), "env"
                except ValueError:
                    pass   # an invalid env value is ignored (the field shows its other source)
        stored = self._stored(section)
        if key in stored:
            return stored[key], "settings"
        return (list(f.default) if isinstance(f.default, list) else f.default), "default"

    def get(self, section: str, key: str) -> Any:
        return self.effective(section, key)[0]

    def section(self, section: str) -> dict:
        if section not in SECTIONS:
            raise ValueError(f"Unknown settings section '{section}'")
        out = {}
        for key, f in SECTIONS[section].items():
            value, source = self.effective(section, key)
            out[key] = {"value": value, "default": f.default, "source": source, "read_only": source == "env",
                        "env": f.env, "restart_required": f.restart, "help": f.help, "kind": f.kind,
                        "min": f.lo, "max": f.hi, "choices": list(f.choices)}
        return out

    def all(self) -> dict:
        out = {name: self.section(name) for name in SECTIONS}
        # Secrets: status only. The key itself is never read into any response.
        out["market"]["coingecko_demo_key"] = {"value": "set" if self.secrets().get("coingecko_demo_key") else "not set",
                                               "source": "env", "read_only": True, "env": "COINGECKO_DEMO_API_KEY",
                                               "restart_required": True, "kind": "secret",
                                               "help": "Optional free CoinGecko Demo key. Set it in .env; it is never shown or stored here."}
        return out

    # ------------------------------------------------------------------ write
    def update(self, section: str, payload: Any) -> dict:
        if section not in SECTIONS:
            raise ValueError(f"Unknown settings section '{section}'")
        if not isinstance(payload, dict) or not payload:
            raise ValueError("Send a JSON object with the values to change")
        stored = self._stored(section)
        changes = {}
        for key, raw in payload.items():
            if key not in SECTIONS[section]:
                raise ValueError(f"Unknown setting '{key}' in section '{section}'")
            f = SECTIONS[section][key]
            if f.env and self.effective(section, key)[1] == "env":
                raise ValueError(f"'{key}' is set by the environment variable {f.env} and is read-only here")
            v = self._coerce(f, raw, key)
            if section == "assistant" and key == "model" and self.model_check:
                err = self.model_check(v)
                if err:
                    raise ValueError(err)
            if section == "watchlist" and key == "symbols" and self.symbol_check:
                unknown = self.symbol_check(v)
                if unknown:
                    raise ValueError(f"Not a Binance spot symbol: {', '.join(unknown)}")
            changes[key] = v
        stored.update(changes)
        self.db.set_setting(f"settings:{section}", stored)
        for fn in self._listeners:
            fn(section, changes)
        return self.section(section)

    def reset(self, section: str) -> dict:
        if section not in SECTIONS:
            raise ValueError(f"Unknown settings section '{section}'")
        self.db.set_setting(f"settings:{section}", {})
        for fn in self._listeners:
            fn(section, {k: self.get(section, k) for k in SECTIONS[section]})
        return self.section(section)
