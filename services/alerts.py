"""Alerts (Phase 8): user rules, anti-spam, persistent state and a background monitor.

Rule kinds (each enabled/disabled, each with a cooldown):
  price_above / price_below   fire once when the price crosses the threshold; re-arm only after the price
                              moves back by the hysteresis (default 0.5%)
  big_move                    |change| over 1h >= pct_1h (default 3%) or over 24h >= pct_24h (default 8%)
  significant_news            a new event with impact "high" and freshness "new"; once per news CLUSTER
  high_volatility             the 4h or 1h volatility regime BECOMES "extreme" (transition only)
  signal_change               the final signal or setup differs from the last alerted value
  source_failure              a source fails >= 3 times in a row, or the price is stale; a recovery alert follows
  risk_limit                  the SIMULATED paper account hits its daily loss limit or exceeds max exposure

Anti-spam: per-rule cooldown (default 30 min; 6 h per news cluster), identical-message dedupe within the
cooldown, and a global cap of N alerts per hour (default 20) after which ONE "alerts paused" alert is created.
All state lives in SQLite (alert_state), so it survives restarts. Every alert has a UTC timestamp, a reason
and source links where applicable.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from typing import Any, Callable

log = logging.getLogger("btc_agent.alerts")

KINDS = ("price_above", "price_below", "big_move", "significant_news", "high_volatility", "signal_change",
         "source_failure", "risk_limit")
SEVERITIES = ("info", "warning", "critical")
DEFAULT_RULES = [("big_move", {}), ("significant_news", {}), ("high_volatility", {}), ("signal_change", {}),
                 ("source_failure", {}), ("risk_limit", {})]
FAILURES_IN_A_ROW = 3
HOUR_MS = 3_600_000
PROVIDER_LINKS = {"binance": "https://www.binance.com/en/trade/BTC_USDT",
                  "coinbase": "https://exchange.coinbase.com/trade/BTC-USD"}
PAUSED_TITLE = "Alerts paused for spam protection"


def iso(ms: int | None) -> str | None:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ms else None


def _fmt(v: float | None, d: int = 2) -> str:
    return "n/a" if v is None else f"{v:,.{d}f}"


class AlertService:
    """`providers`: zero-argument callables (any may be missing or fail):
    price -> price dict; price_1h_ago -> float|None; news -> news report; volatility -> {"4h": regime, "1h": regime};
    signal -> signal dict; health -> source_health rows; paper -> paper state."""

    def __init__(self, db, settings: Callable[[str], Any], providers: dict[str, Callable[[], Any]],
                 clock: Callable[[], int], notifiers: list[Callable[[dict], None]] | None = None):
        self.db = db
        self.settings = settings            # settings("cooldown_min") -> value from Settings → Alerts
        self.providers = providers
        self.clock = clock
        self.notifiers = notifiers or []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ state (SQLite)
    def _get_state(self, key: str, default: Any = None) -> Any:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM alert_state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def _set_state(self, key: str, value: Any) -> None:
        with self.db.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO alert_state (key, value, updated_at) VALUES (?,?,?)",
                         (key, json.dumps(value), self.clock()))

    # ------------------------------------------------------------------ rules
    @staticmethod
    def _rule_row(r) -> dict:
        d = dict(r)
        d["params"] = json.loads(d["params"] or "{}")
        d["enabled"] = bool(d["enabled"])
        return d

    def list_rules(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM alert_rules ORDER BY id").fetchall()
        return [self._rule_row(r) for r in rows]

    def get_rule(self, rule_id: int) -> dict | None:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        return self._rule_row(r) if r else None

    def _validate(self, kind: str, params: Any, cooldown_s: Any, enabled: Any) -> tuple[dict, int | None, bool]:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")

        def num(key: str, lo: float, hi: float, required: bool = False) -> float | None:
            v = params.get(key)
            if v in (None, ""):
                if required:
                    raise ValueError(f"'{key}' is required for {kind}")
                return None
            if isinstance(v, bool):
                raise ValueError(f"'{key}' must be a number")
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"'{key}' must be a number") from None
            if not lo <= f <= hi:
                raise ValueError(f"'{key}' must be between {lo:g} and {hi:g}")
            return f

        clean: dict = {}
        if kind in ("price_above", "price_below"):
            clean["price"] = num("price", 1, 10_000_000, required=True)
        elif kind == "big_move":
            for key, (lo, hi) in {"pct_1h": (0.5, 50), "pct_24h": (1, 80)}.items():
                v = num(key, lo, hi)
                if v is not None:
                    clean[key] = v
        unknown = set(params) - set(clean) - {"price", "pct_1h", "pct_24h"}
        if unknown:
            raise ValueError(f"Unknown parameter(s) for {kind}: {', '.join(sorted(unknown))}")
        if cooldown_s not in (None, ""):
            if isinstance(cooldown_s, bool):
                raise ValueError("cooldown_s must be a number of seconds")
            try:
                cooldown_s = int(cooldown_s)
            except (TypeError, ValueError):
                raise ValueError("cooldown_s must be a number of seconds") from None
            if not 60 <= cooldown_s <= 7 * 86400:
                raise ValueError("cooldown_s must be between 60 and 604800 seconds")
        else:
            cooldown_s = None
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        return clean, cooldown_s, enabled

    def create_rule(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Send a JSON object")
        params, cooldown, enabled = self._validate(payload.get("kind"), payload.get("params"),
                                                   payload.get("cooldown_s"), payload.get("enabled", True))
        now = self.clock()
        with self.db.connect() as conn:
            cur = conn.execute("INSERT INTO alert_rules (kind, params, enabled, cooldown_s, created_at, updated_at) "
                               "VALUES (?,?,?,?,?,?)", (payload["kind"], json.dumps(params), int(enabled), cooldown, now, now))
            rid = int(cur.lastrowid)
        return self.get_rule(rid)

    def update_rule(self, rule_id: int, payload: Any) -> dict:
        rule = self.get_rule(rule_id)
        if rule is None:
            raise LookupError("Rule not found")
        if not isinstance(payload, dict):
            raise ValueError("Send a JSON object")
        if "kind" in payload and payload["kind"] != rule["kind"]:
            raise ValueError("The kind of a rule cannot be changed; create a new rule")
        params = payload.get("params", rule["params"])
        params, cooldown, enabled = self._validate(rule["kind"], params, payload.get("cooldown_s", rule["cooldown_s"]),
                                                   payload.get("enabled", rule["enabled"]))
        with self.db.connect() as conn:
            conn.execute("UPDATE alert_rules SET params=?, enabled=?, cooldown_s=?, updated_at=? WHERE id=?",
                         (json.dumps(params), int(enabled), cooldown, self.clock(), rule_id))
        if params != rule["params"]:
            self._set_state(f"rule:{rule_id}", {})     # new threshold: start armed again
        return self.get_rule(rule_id)

    def delete_rule(self, rule_id: int) -> bool:
        with self.db.connect() as conn:
            n = conn.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,)).rowcount
            conn.execute("DELETE FROM alert_state WHERE key LIKE ? OR key LIKE ?", (f"rule:{rule_id}%", f"%:{rule_id}:%"))
        return n > 0

    def seed_defaults(self) -> None:
        """Create the default (non-price) rules once; the user can disable or delete them."""
        if self._get_state("defaults_seeded"):
            return
        for kind, params in DEFAULT_RULES:
            self.create_rule({"kind": kind, "params": params})
        self._set_state("defaults_seeded", True)

    # ------------------------------------------------------------------ alerts
    @staticmethod
    def _alert_row(r) -> dict:
        d = dict(r)
        d["links"] = json.loads(d.pop("links_json") or "[]")
        d["created_at_iso"] = iso(d["created_at"])
        d["acknowledged_at_iso"] = iso(d["acknowledged_at"])
        return d

    def list_alerts(self, unread: bool = False, limit: int = 100) -> list[dict]:
        q = "SELECT * FROM alerts" + (" WHERE acknowledged_at IS NULL" if unread else "") + " ORDER BY id DESC LIMIT ?"
        with self.db.connect() as conn:
            rows = conn.execute(q, (limit,)).fetchall()
        return [self._alert_row(r) for r in rows]

    def unread_count(self) -> int:
        with self.db.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE acknowledged_at IS NULL").fetchone()["n"])

    def ack(self, alert_id: int) -> bool:
        with self.db.connect() as conn:
            return conn.execute("UPDATE alerts SET acknowledged_at=? WHERE id=? AND acknowledged_at IS NULL",
                                (self.clock(), alert_id)).rowcount > 0 or \
                conn.execute("SELECT 1 FROM alerts WHERE id=?", (alert_id,)).fetchone() is not None

    def ack_all(self) -> int:
        with self.db.connect() as conn:
            return conn.execute("UPDATE alerts SET acknowledged_at=? WHERE acknowledged_at IS NULL", (self.clock(),)).rowcount

    def delete_alerts(self, kind: str | None = None) -> int:
        with self.db.connect() as conn:
            if kind:
                return conn.execute("DELETE FROM alerts WHERE kind=?", (kind,)).rowcount
            return conn.execute("DELETE FROM alerts").rowcount

    def create_test(self) -> dict:
        return self._store(None, "test", "info", "TEST alert - not a real market event",
                           "This is a test alert created from the alert centre. Nothing happened in the market.",
                           "Requested by the user (POST /api/alerts/test)", None, None, "app", ["/#alerts"])

    # ------------------------------------------------------------------ emitting (anti-spam)
    def _store(self, rule_id, kind, severity, title, message, reason, value, threshold, source, links) -> dict:
        now = self.clock()
        with self.db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO alerts (rule_id, kind, severity, title, message, reason, value, threshold, source,
                                       links_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (rule_id, kind, severity, title, message, reason, value, threshold, source, json.dumps(links or []), now))
            row = conn.execute("SELECT * FROM alerts WHERE id=?", (cur.lastrowid,)).fetchone()
        alert = self._alert_row(row)
        for notify in self.notifiers:
            try:
                notify(alert)
            except Exception:   # a notification channel must never break alerting
                log.exception("Alert notification failed")
        return alert

    def _cap_reached(self, now: int) -> bool:
        cap = int(self.settings("global_cap_per_hour") or 20)
        with self.db.connect() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE created_at > ? AND kind NOT IN ('system', 'test')",
                             (now - HOUR_MS,)).fetchone()["n"]
        if n < cap:
            return False
        if (self._get_state("paused_at") or 0) <= now - HOUR_MS:
            self._set_state("paused_at", now)
            self._store(None, "system", "warning", PAUSED_TITLE,
                        f"{cap} alerts in the last hour. Further alerts are suppressed until the hourly count drops.",
                        f"Global cap of {cap} alerts per hour reached", float(n), float(cap), "app", ["/#alerts"])
        return True

    def emit(self, rule: dict, severity: str, title: str, message: str, reason: str, value: float | None,
             threshold: float | None, source: str, links: list[str], cooldown_key: str | None = None,
             cooldown_s: int | None = None) -> dict | None:
        """Store an alert unless a cooldown, the dedupe or the global cap suppresses it. Returns it or None."""
        now = self.clock()
        key = cooldown_key or f"cooldown:{rule['id']}"
        cd = cooldown_s if cooldown_s is not None else (rule.get("cooldown_s") or int(self.settings("cooldown_min") or 30) * 60)
        last = self._get_state(key) or {}
        if last.get("at") and now - last["at"] < cd * 1000:
            return None                                                    # cooldown
        dedupe = self._get_state(f"lastmsg:{rule['id']}") or {}
        if dedupe.get("message") == message and now - dedupe.get("at", 0) < cd * 1000:
            return None                                                    # identical message within cooldown
        if self._cap_reached(now):
            return None
        alert = self._store(rule["id"], rule["kind"], severity, title, message, reason, value, threshold, source, links)
        self._set_state(key, {"at": now})
        self._set_state(f"lastmsg:{rule['id']}", {"message": message, "at": now})
        return alert

    # ------------------------------------------------------------------ evaluation
    def _get(self, name: str) -> Any:
        fn = self.providers.get(name)
        return fn() if fn else None

    def evaluate(self) -> list[dict]:
        """Run every enabled rule once. A failing rule is logged and skipped; it never stops the others."""
        created: list[dict] = []
        with self._lock:
            cache: dict[str, Any] = {}

            def data(name: str) -> Any:
                if name not in cache:
                    cache[name] = self._get(name)
                return cache[name]

            for rule in self.list_rules():
                if not rule["enabled"]:
                    continue
                try:
                    out = getattr(self, f"_check_{rule['kind']}")(rule, data)
                    created += [a for a in (out or []) if a]
                except Exception:
                    log.exception("Alert rule %s (%s) failed", rule["id"], rule["kind"])
        return created

    def _price(self, data) -> dict | None:
        p = data("price") or {}
        return p if p.get("price") is not None else None

    def _check_price_cross(self, rule: dict, data, above: bool) -> list:
        p = self._price(data)
        if not p:
            return []
        thr, price = float(rule["params"]["price"]), float(p["price"])
        h = float(self.settings("hysteresis_pct") or 0.5) / 100
        state = self._get_state(f"rule:{rule['id']}") or {}
        armed = state.get("armed", True)
        out = []
        crossed = price >= thr if above else price <= thr
        if armed and crossed:
            word = "above" if above else "below"
            provider = p.get("provider", "?")
            a = self.emit(rule, "info", f"BTC {word} {_fmt(thr)} {p.get('quote', 'USDT')}",
                          f"BTC is {_fmt(price)} {p.get('quote', 'USDT')}, {word} your alert level {_fmt(thr)}.",
                          f"Price {_fmt(price)} crossed {word} {_fmt(thr)} ({provider}, observed {iso(p.get('observed_at'))})",
                          price, thr, provider, [PROVIDER_LINKS.get(provider, "/#overview")])
            if a:
                out.append(a)
                armed = False
        elif not armed:
            back = price <= thr * (1 - h) if above else price >= thr * (1 + h)
            if back:
                armed = True                 # moved back past the hysteresis band: can fire again
        self._set_state(f"rule:{rule['id']}", {"armed": armed, "last_price": price})
        return out

    def _check_price_above(self, rule, data):
        return self._check_price_cross(rule, data, True)

    def _check_price_below(self, rule, data):
        return self._check_price_cross(rule, data, False)

    def _check_big_move(self, rule: dict, data) -> list:
        p = self._price(data)
        if not p:
            return []
        out, state = [], self._get_state(f"rule:{rule['id']}") or {}
        changes = {}
        ago = data("price_1h_ago")
        if ago:
            changes["1h"] = (float(p["price"]) / float(ago) - 1) * 100
        if p.get("change_24h_pct") is not None:
            changes["24h"] = float(p["change_24h_pct"])
        limits = {"1h": float(rule["params"].get("pct_1h") or self.settings("big_move_1h_pct") or 3),
                  "24h": float(rule["params"].get("pct_24h") or self.settings("big_move_24h_pct") or 8)}
        for window, change in changes.items():
            breach = abs(change) >= limits[window]
            if breach and not state.get(window):
                direction = "up" if change > 0 else "down"
                a = self.emit(rule, "warning", f"Big move: BTC {direction} {abs(change):.1f}% in {window}",
                              f"BTC moved {change:+.2f}% over {window} (threshold {limits[window]:g}%). Price now {_fmt(p['price'])}.",
                              f"|{window} change| {abs(change):.2f}% >= {limits[window]:g}% ({p.get('provider')}, "
                              f"observed {iso(p.get('observed_at'))})", round(change, 3), limits[window],
                              p.get("provider", "?"), [PROVIDER_LINKS.get(p.get("provider"), "/#overview")],
                              cooldown_key=f"cooldown:{rule['id']}:{window}")
                if a:
                    out.append(a)
                    state[window] = True
            elif not breach:
                state[window] = False
        self._set_state(f"rule:{rule['id']}", state)
        return out

    def _check_significant_news(self, rule: dict, data) -> list:
        report = data("news") or {}
        out = []
        cd = int(self.settings("news_cooldown_h") or 6) * 3600
        for e in report.get("events") or []:
            if e.get("impact") != "high" or e.get("freshness") != "new" or not e.get("id"):
                continue
            key = f"news:{rule['id']}:{e['id']}"           # one alert per CLUSTER, never per publisher
            if self._get_state(key):
                continue
            links = [s.get("link") for s in (e.get("sources") or []) if s.get("link")][:3] or [e.get("link") or "/#news"]
            a = self.emit(rule, "warning", f"High-impact news: {str(e.get('title'))[:120]}",
                          f"{e.get('publisher')}: \"{e.get('title')}\" - sentiment {e.get('sentiment')}, impact high, "
                          f"{e.get('independent_sources', 1)} publisher(s).",
                          f"New high-impact news event (published {iso(e.get('published_at'))})", None, None,
                          f"news: {e.get('publisher')}", links, cooldown_key=f"newscd:{rule['id']}:{e['id']}", cooldown_s=cd)
            if a:
                out.append(a)
                self._set_state(key, {"at": self.clock()})
        return out

    def _check_high_volatility(self, rule: dict, data) -> list:
        vol = data("volatility") or {}
        state = self._get_state(f"rule:{rule['id']}") or {}
        out = []
        for tf in ("4h", "1h"):
            regime = vol.get(tf)
            if regime is None:
                continue
            if regime == "extreme" and state.get(tf) != "extreme":
                a = self.emit(rule, "warning", f"Extreme volatility on {tf}",
                              f"The {tf} volatility regime became EXTREME (ATR in the top 5% of its recent range). "
                              "The signal engine blocks new setups while volatility is extreme.",
                              f"{tf} volatility regime changed from {state.get(tf) or 'unknown'} to extreme",
                              None, None, "technical analysis", ["/#analysis"], cooldown_key=f"cooldown:{rule['id']}:{tf}")
                if a:
                    out.append(a)
                    state[tf] = regime
            else:
                state[tf] = regime
        self._set_state(f"rule:{rule['id']}", state)
        return out

    def _check_signal_change(self, rule: dict, data) -> list:
        sig = data("signal") or {}
        if not sig.get("signal"):
            return []
        state = self._get_state(f"rule:{rule['id']}") or {}
        cur = {"signal": sig["signal"], "setup": sig.get("setup")}
        if not state:
            self._set_state(f"rule:{rule['id']}", cur)       # first look: remember, don't alert
            return []
        if cur == {"signal": state.get("signal"), "setup": state.get("setup")}:
            return []
        links = [f"/api/signal/{sig['id']}"] if sig.get("id") else ["/#signal"]
        a = self.emit(rule, "info", f"Signal changed: {cur['signal']} ({cur['setup']})",
                      f"Signal {state.get('signal')} / {state.get('setup')} -> {cur['signal']} / {cur['setup']}, "
                      f"score {sig.get('score')}. Research signal, not financial advice.",
                      f"Final signal or setup differs from the last alerted value (engine {sig.get('engine')})",
                      sig.get("score"), None, "signal engine", links)
        if a:
            self._set_state(f"rule:{rule['id']}", cur)
            return [a]
        return []

    def _check_source_failure(self, rule: dict, data) -> list:
        out = []
        for h in data("health") or []:
            src = h.get("source")
            key = f"src:{rule['id']}:{src}"
            alerted = self._get_state(key)
            fails = int(h.get("consecutive_failures") or 0)
            if fails >= FAILURES_IN_A_ROW and not alerted:
                a = self.emit(rule, "critical", f"Data source failing: {src}",
                              f"{src} failed {fails} times in a row. Last error: {h.get('last_error') or 'n/a'}",
                              f"{fails} consecutive failures (>= {FAILURES_IN_A_ROW}), last failure {iso(h.get('last_failure'))}",
                              float(fails), float(FAILURES_IN_A_ROW), src, ["/#sources"], cooldown_key=f"cooldown:{rule['id']}:{src}")
                if a:
                    out.append(a)
                    self._set_state(key, True)
            elif alerted and fails == 0 and h.get("status") == "ok":
                a = self._store(rule["id"], rule["kind"], "info", f"Data source recovered: {src}",
                                f"{src} is working again (last success {iso(h.get('last_success'))}).",
                                "Source returned to OK after a failure alert", 0.0, float(FAILURES_IN_A_ROW), src, ["/#sources"])
                out.append(a)
                self._set_state(key, False)
        p = data("price") or {}
        if p:
            key = f"src:{rule['id']}:price_stale"
            alerted = self._get_state(key)
            if p.get("is_stale") and not alerted:
                a = self.emit(rule, "critical", "BTC price is stale",
                              f"The latest BTC price is {p.get('age_seconds')} s old (status {p.get('status')}).",
                              "Price older than the freshness limit - data may be delayed",
                              p.get("age_seconds"), None, p.get("provider") or "price API", ["/#sources"],
                              cooldown_key=f"cooldown:{rule['id']}:price_stale")
                if a:
                    out.append(a)
                    self._set_state(key, True)
            elif alerted and not p.get("is_stale"):
                out.append(self._store(rule["id"], rule["kind"], "info", "BTC price is fresh again",
                                       f"Price {_fmt(p.get('price'))} from {p.get('provider')} is current again.",
                                       "Price returned to fresh after a stale alert", p.get("age_seconds"), None,
                                       p.get("provider") or "price API", ["/#sources"]))
                self._set_state(key, False)
        return out

    def _check_risk_limit(self, rule: dict, data) -> list:
        pa = data("paper") or {}
        if not pa:
            return []
        state = self._get_state(f"rule:{rule['id']}") or {}
        out = []
        max_exp = float((pa.get("settings") or {}).get("max_exposure_pct") or 50)
        checks = {
            "daily_loss": (bool(pa.get("daily_limit_reached")), "SIMULATED: daily loss limit reached",
                           f"SIMULATED paper account: today's loss {pa.get('daily_loss_pct')}% reached the daily limit "
                           f"{(pa.get('settings') or {}).get('daily_loss_limit_pct')}%. New simulated buys are blocked.",
                           pa.get("daily_loss_pct"), (pa.get("settings") or {}).get("daily_loss_limit_pct")),
            "exposure": (float(pa.get("exposure_pct") or 0) > max_exp, "SIMULATED: exposure above the maximum",
                         f"SIMULATED paper account: exposure {pa.get('exposure_pct')}% is above the maximum {max_exp:g}%.",
                         pa.get("exposure_pct"), max_exp),
        }
        for name, (hit, title, msg, value, thr) in checks.items():
            if hit and not state.get(name):
                a = self.emit(rule, "warning", title, msg, f"SIMULATED risk limit: {name.replace('_', ' ')}",
                              value, thr, "paper trading (SIMULATED)", ["/#risk"], cooldown_key=f"cooldown:{rule['id']}:{name}")
                if a:
                    out.append(a)
                    state[name] = True
            elif not hit:
                state[name] = False
        self._set_state(f"rule:{rule['id']}", state)
        return out


class AlertMonitor:
    """Background thread: evaluate the alert rules every `interval` seconds. Never dies on an error."""

    def __init__(self, service: AlertService, interval: float = 60.0, first_delay: float = 10.0,
                 jobs: list[Callable[[], Any]] | None = None):
        """`jobs` run before the alert rules in every cycle (e.g. the SIMULATED paper stop/target check);
        each is isolated, so a failing job never stops the others or the alerts."""
        self.service = service
        self.interval = interval
        self.first_delay = first_delay
        self.jobs = list(jobs or [])
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.runs = 0
        self.errors = 0

    def run_once(self) -> None:
        for job in self.jobs:
            try:
                job()
            except Exception:
                self.errors += 1
                log.exception("Monitor job %s failed", getattr(job, "__name__", job))
        try:
            self.service.evaluate()
        except Exception:     # evaluate() already isolates rules; this is the last line of defence
            self.errors += 1
            log.exception("Alert monitor cycle failed")
        finally:
            self.runs += 1

    def _loop(self) -> None:
        if self._stop.wait(self.first_delay):
            return
        while not self._stop.is_set():
            self.run_once()
            if self._stop.wait(self.interval):
                return

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="AlertMonitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
