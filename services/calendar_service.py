"""Economic calendar (Phase 8): verified upcoming US macro events from official, free, keyless sources.

Sources (researched Phase 8):
* FOMC meetings - Federal Reserve Board "Meeting calendars" page (federalreserve.gov, US-government work, public
  domain; the Board asks for attribution). There is no ICS/JSON feed for FOMC dates, so the page's dated
  meeting rows are parsed. Fetched at most once per day.
* GDP and PCE - the official BEA release calendar (iCalendar), fetched at most once per day.
* CPI and the jobs report - a curated file, data/calendars/bls_2026_2027.json, checked by a person against the official
  BLS schedule. The app never fetches bls.gov, because BLS blocks automated clients (HTTP 403). The page warns when the
  file was last verified more than 12 months ago.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from database import Database, now_ms
from services.http_client import HttpClient, ProviderError

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BEA_ICS_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
BEA_SCHEDULE_URL = "https://www.bea.gov/news/schedule"
BLS_SCHEDULE_URL = "https://www.bls.gov/schedule/"
CURATED_MAX_AGE_DAYS = 365
CACHE_S = 24 * 3600
RETRY_AFTER_FAILURE_S = 3600
WINDOW_DAYS = 14
ET = ZoneInfo("America/New_York")
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
BLS_KEEP = {"Consumer Price Index": "CPI", "Producer Price Index": "PPI", "Employment Situation": "Jobs report"}

_YEAR_RE = re.compile(r'<h4><a id="\d+">(\d{4}) FOMC Meetings</a></h4>')
_ROW_RE = re.compile(r'fomc-meeting__month[^>]*><strong>([^<]+)</strong></div>\s*'
                     r'<div class="fomc-meeting__date[^>]*>([^<]+)</div>')


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_fomc(page: str) -> list[dict]:
    """Scheduled FOMC meetings from the Fed calendar page. Notation votes and unscheduled items are skipped.
    The decision is announced on the LAST day; 2:00 p.m. ET is the usual statement time (not on the page)."""
    out = []
    years = [(m.start(), int(m.group(1))) for m in _YEAR_RE.finditer(page)]
    for m in _ROW_RE.finditer(page):
        year = next((y for pos, y in reversed(years) if pos < m.start()), None)
        month_txt, date_txt = html.unescape(m.group(1)).strip(), html.unescape(m.group(2)).strip()
        if year is None or "notation" in date_txt.lower() or "unscheduled" in date_txt.lower():
            continue
        sep = "*" in date_txt
        days = re.findall(r"\d{1,2}", date_txt)
        months = [MONTHS.get(p.strip()[:3]) for p in month_txt.split("/")]
        if not days or None in months:
            continue
        last_day, last_month = int(days[-1]), months[-1]
        first_day, first_month = int(days[0]), months[0]
        try:
            end = dt.datetime(year, last_month, last_day, 14, 0, tzinfo=ET)
            start = dt.date(year if first_month <= last_month else year - 1, first_month, first_day)
        except ValueError:
            continue
        at = int(end.timestamp() * 1000)
        out.append({
            "id": f"fomc-{end:%Y%m%d}", "event": "FOMC rate decision" + (" + projections (SEP)" if sep else ""),
            "kind": "FOMC", "country": "US", "importance": "high",
            "at": at, "at_utc": iso(at), "time_note": "2:00 p.m. ET is the usual statement time (the page lists dates only)",
            "meeting": f"{start:%b %d} - {end:%b %d, %Y}", "source": "Federal Reserve Board", "link": FOMC_URL,
        })
    return sorted(out, key=lambda e: e["at"])


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_bls_ics(text: str) -> list[dict]:
    """CPI, PPI and Employment Situation releases from the BLS iCalendar feed."""
    out, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            summary = cur.get("SUMMARY", "")
            short = next((v for k, v in BLS_KEEP.items() if summary.startswith(k)), None)
            if short and cur.get("DTSTART"):
                at = cur["DTSTART"]
                out.append({"id": f"bls-{short}-{at}", "event": summary, "kind": short, "country": "US",
                            "importance": "high", "at": at, "at_utc": iso(at), "time_note": "Release time from BLS",
                            "source": "U.S. Bureau of Labor Statistics", "link": BLS_SCHEDULE_URL})
            cur = None
        elif cur is not None and ":" in line:
            name, value = line.split(":", 1)
            key, *params = name.split(";")
            if key == "SUMMARY":
                cur["SUMMARY"] = value.replace("\\,", ",").replace("\\;", ";").strip()
            elif key == "DTSTART":
                tz = next((p.split("=", 1)[1] for p in params if p.startswith("TZID=")), None)
                try:
                    if value.endswith("Z"):
                        t = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
                    elif "T" in value:
                        t = dt.datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(tz) if tz else ET)
                    else:
                        t = dt.datetime.strptime(value, "%Y%m%d").replace(hour=8, minute=30, tzinfo=ET)
                    cur["DTSTART"] = int(t.timestamp() * 1000)
                except (ValueError, KeyError):
                    pass
    return sorted(out, key=lambda e: e["at"])


def parse_bea_ics(text: str) -> list[dict]:
    """GDP and PCE ("Personal Income and Outlays") from BEA's iCalendar file. Its times are in UTC."""
    out, seen = [], set()
    for line_block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", "\n".join(_unfold(text)), re.S):
        summary = re.search(r"^SUMMARY[^:]*:(.*)$", line_block, re.M)
        start = re.search(r"^DTSTART[^:]*:(\d{8}T\d{6})Z", line_block, re.M)
        if not summary or not start:
            continue
        title = summary.group(1).replace("\\,", ",").replace("\\;", ";").strip()
        kind = "GDP" if title.startswith("GDP (") else "PCE" if title.startswith("Personal Income and Outlays") else None
        if not kind:
            continue
        at = int(dt.datetime.strptime(start.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        if (kind, at) in seen:
            continue
        seen.add((kind, at))
        out.append({"id": f"bea-{kind}-{start.group(1)[:8]}", "event": title, "kind": kind, "country": "US", "importance": "high",
                    "at": at, "at_utc": iso(at), "time_note": "Official BEA release time",
                    "source": "U.S. Bureau of Economic Analysis", "link": BEA_SCHEDULE_URL})
    return sorted(out, key=lambda e: e["at"])


def load_bls_curated(path: str | Path, now: int) -> tuple[list[dict], dict]:
    """CPI and jobs-report dates from the curated file. Raises ValueError/KeyError/OSError when it is missing or broken."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    verified = dt.date.fromisoformat(data["last_verified"])
    hour, minute = (int(x) for x in data.get("release_time_et", "08:30").split(":"))
    url = data["source_url"]
    if not url.startswith("https://www.bls.gov/"):
        raise ValueError("source_url must be on www.bls.gov")
    events = []
    for kind, rel in data["releases"].items():
        for d in rel["dates"]:
            day = dt.date.fromisoformat(d["date"])
            t = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)
            at = int(t.timestamp() * 1000)
            events.append({"id": f"bls-{kind}-{d['date']}", "event": f"{rel['name']} for {d['period']}", "kind": kind, "period": d["period"],
                           "country": "US", "importance": "high", "at": at, "at_utc": iso(at),
                           "time_note": f"Official BLS schedule, curated file last verified {data['last_verified']}",
                           "source": "U.S. Bureau of Labor Statistics", "link": (data.get("schedule_urls") or {}).get(kind, url)})
    if not events:
        raise ValueError("no dates in the file")
    age = (dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).date() - verified).days
    return sorted(events, key=lambda e: e["at"]), {"last_verified": data["last_verified"], "stale": age > CURATED_MAX_AGE_DAYS,
                                                   "age_days": age, "source_url": url}


class CalendarService:
    def __init__(self, db: Database, http: HttpClient, clock: Callable[[], int] = now_ms,
                 user_agent: str = "btc-signal-lab/0.8 (local research tool)", bea: bool = False,
                 bls_curated_path: str | Path | None = None, health: Callable[..., None] | None = None):
        """bea / bls_curated_path add GDP, PCE, CPI and NFP. `health` records BEA's status (never source_health, whose rows
        raise source-failure alerts)."""
        self.db = db
        self.http = http
        self.clock = clock
        self.user_agent = user_agent
        self.bea = bea
        self.bls_curated_path = bls_curated_path
        self.health = health or (lambda source, ok, error=None: None)

    def _cached_fetch(self, key: str, fetch: Callable[[], list[dict]],
                      record: Callable[..., None] | None = None) -> tuple[list[dict] | None, str | None, int | None]:
        """Fetch at most once per day; after a failure wait an hour before trying again."""
        now = self.clock()
        record = record or (lambda source, ok, error=None: self.db.record_health(source, ok, error=error))
        hit = self.db.cache_get(key)
        if hit and now - hit[1] < CACHE_S * 1000:
            return hit[0]["events"], None, hit[1]
        fail = self.db.cache_get(key + ":failed")
        if fail and now - fail[1] < RETRY_AFTER_FAILURE_S * 1000:
            return (hit[0]["events"] if hit else None), fail[0], (hit[1] if hit else None)
        try:
            events = fetch()
        except (ProviderError, UnicodeDecodeError) as exc:
            record(key, False, error=str(exc))
            self.db.cache_set(key + ":failed", str(exc), now)
            return (hit[0]["events"] if hit else None), str(exc), (hit[1] if hit else None)
        record(key, True)
        self.db.cache_set(key, {"events": events}, now)
        return events, None, now

    def _fomc(self) -> list[dict]:
        body, _ = self.http.get_bytes(FOMC_URL, headers={"Accept": "text/html", "User-Agent": self.user_agent})
        events = parse_fomc(body.decode("utf-8", errors="replace"))
        if not events:
            raise ProviderError("federalreserve.gov: no meeting dates found (page layout changed?)")
        return events

    def _bea(self) -> list[dict]:
        body, _ = self.http.get_bytes(BEA_ICS_URL, headers={"Accept": "text/calendar", "User-Agent": self.user_agent})
        events = parse_bea_ics(body.decode("utf-8", errors="replace"))
        if not events:
            raise ProviderError("www.bea.gov: no GDP or PCE dates found (calendar format changed?)")
        return events

    def known_releases(self) -> dict:
        """Every stored release date, past and future, for the reaction study. Uses the same daily cache as upcoming()."""
        releases, warnings = [], []
        fomc, err, _ = self._cached_fetch("calendar:fomc", self._fomc)
        if err:
            warnings.append(f"FOMC: {err}")
        releases += fomc or []
        if self.bea:
            bea, err, _ = self._cached_fetch("calendar:bea", self._bea, record=self.health)
            if err:
                warnings.append(f"BEA: {err}")
            releases += bea or []
        if self.bls_curated_path:
            try:
                releases += load_bls_curated(self.bls_curated_path, self.clock())[0]
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                warnings.append(f"BLS: curated schedule unreadable ({type(exc).__name__})")
        return {"releases": [{k: e.get(k) for k in ("id", "kind", "at", "event", "link")} for e in releases], "warnings": warnings}

    def upcoming(self, days: int = WINDOW_DAYS, past_days: int = 0) -> dict:
        """Events in the next `days`, and released ones from the last `past_days`, each marked released or upcoming."""
        now = self.clock()
        horizon = now + days * 86_400_000
        low = now - max(3 * 3_600_000, past_days * 86_400_000)
        sources, events, warnings = [], [], []
        fomc, err, at = self._cached_fetch("calendar:fomc", self._fomc)
        sources.append({"source": "Federal Reserve Board (FOMC calendar)", "link": FOMC_URL,
                        "status": "ok" if fomc is not None and not err else ("cached" if fomc else "error"),
                        "fetched_at": at, "error": err})
        if err:
            warnings.append(f"FOMC calendar fetch failed: {err}" + (" - showing the cached copy." if fomc else ""))
        next_fomc = None
        for e in fomc or []:
            if e["at"] >= now - 3 * 3_600_000:
                next_fomc = next_fomc or e
            if low <= e["at"] <= horizon:
                events.append(e)
        if self.bea:
            bea, err, at = self._cached_fetch("calendar:bea", self._bea, record=self.health)
            sources.append({"source": "U.S. Bureau of Economic Analysis (release calendar)", "link": BEA_SCHEDULE_URL,
                            "status": "ok" if bea is not None and not err else ("cached" if bea else "error"),
                            "fetched_at": at, "error": err})
            if err:
                warnings.append(f"BEA calendar fetch failed: {err}" + (" - showing the cached copy." if bea else ""))
            events += [e for e in bea or [] if low <= e["at"] <= horizon]
        if self.bls_curated_path:
            try:
                bls, meta = load_bls_curated(self.bls_curated_path, now)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                sources.append({"source": "U.S. Bureau of Labor Statistics (curated schedule)", "link": BLS_SCHEDULE_URL, "status": "error",
                                "fetched_at": None, "error": f"The curated schedule file could not be read ({type(exc).__name__})."})
                warnings.append("CPI and jobs-report dates are unavailable: the curated schedule file could not be read.")
            else:
                sources.append({"source": "U.S. Bureau of Labor Statistics (curated schedule)", "link": BLS_SCHEDULE_URL,
                                "status": "stale" if meta["stale"] else "curated", "fetched_at": None, "last_verified": meta["last_verified"],
                                "error": "Dates from a curated file, not fetched live: BLS blocks automated clients."})
                if meta["stale"]:
                    warnings.append(f"CPI and jobs-report dates were last verified on {meta['last_verified']}, over 12 months ago. "
                                    "Check them against the BLS schedule.")
                events += [e for e in bls if low <= e["at"] <= horizon]
        else:
            sources.append({"source": "U.S. Bureau of Labor Statistics (curated schedule)", "link": BLS_SCHEDULE_URL,
                            "status": "not_configured", "fetched_at": None,
                            "error": "No curated schedule configured. BLS blocks automated clients, so its dates are never fetched."})
        events = [{**e, "status": "released" if e["at"] <= now else "upcoming"} for e in events]
        events.sort(key=lambda e: e["at"])
        if not events:
            if fomc is None:
                empty = "No free verified calendar source could be loaded right now."
            else:
                what = "macro" if self.bea or self.bls_curated_path else "FOMC"
                empty = f"No verified {what} events in the next {days} days."
        else:
            empty = None
        return {"events": events, "window_days": days, "generated_at": now, "next_fomc": next_fomc,
                "sources": sources, "warnings": warnings, "empty_reason": empty,
                "note": "Official US release schedules only. No consensus forecasts: no verified free source."}
