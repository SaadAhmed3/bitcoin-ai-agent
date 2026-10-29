"""Economic calendar (Phase 8): verified upcoming US macro events from official, free, keyless sources.

Sources (researched Phase 8):
* FOMC meetings - Federal Reserve Board "Meeting calendars" page (federalreserve.gov, US-government work, public
  domain; the Board asks for attribution). There is no ICS/JSON feed for FOMC dates, so the page's dated
  meeting rows are parsed. Fetched at most once per day.
* BLS releases (CPI, PPI, Employment Situation) - the official BLS iCalendar feed. BLS blocks anonymous
  automated clients (HTTP 403) and asks scripts to identify their owner, so it is OPTIONAL: set
  CALENDAR_CONTACT_EMAIL in .env to your own e-mail and the app identifies itself with it. Without it the BLS
  part is skipped and the UI says why. Nothing is bypassed.
* GDP (BEA) - BEA publishes no machine-readable schedule feed; not included.
"""
from __future__ import annotations

import datetime as dt
import html
import re
from typing import Callable
from zoneinfo import ZoneInfo

from database import Database, now_ms
from services.http_client import HttpClient, ProviderError

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BLS_SCHEDULE_URL = "https://www.bls.gov/schedule/"
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


class CalendarService:
    def __init__(self, db: Database, http: HttpClient, contact_email: Callable[[], str] = lambda: "",
                 clock: Callable[[], int] = now_ms, user_agent: str = "btc-signal-lab/0.8 (local research tool)"):
        self.db = db
        self.http = http
        self.contact_email = contact_email
        self.clock = clock
        self.user_agent = user_agent

    def _cached_fetch(self, key: str, fetch: Callable[[], list[dict]]) -> tuple[list[dict] | None, str | None, int | None]:
        """Fetch at most once per day; after a failure wait an hour before trying again."""
        now = self.clock()
        hit = self.db.cache_get(key)
        if hit and now - hit[1] < CACHE_S * 1000:
            return hit[0]["events"], None, hit[1]
        fail = self.db.cache_get(key + ":failed")
        if fail and now - fail[1] < RETRY_AFTER_FAILURE_S * 1000:
            return (hit[0]["events"] if hit else None), fail[0], (hit[1] if hit else None)
        try:
            events = fetch()
        except (ProviderError, UnicodeDecodeError) as exc:
            self.db.record_health(key, False, error=str(exc))
            self.db.cache_set(key + ":failed", str(exc), now)
            return (hit[0]["events"] if hit else None), str(exc), (hit[1] if hit else None)
        self.db.record_health(key, True)
        self.db.cache_set(key, {"events": events}, now)
        return events, None, now

    def _fomc(self) -> list[dict]:
        body, _ = self.http.get_bytes(FOMC_URL, headers={"Accept": "text/html", "User-Agent": self.user_agent})
        events = parse_fomc(body.decode("utf-8", errors="replace"))
        if not events:
            raise ProviderError("federalreserve.gov: no meeting dates found (page layout changed?)")
        return events

    def _bls(self) -> list[dict]:
        email = self.contact_email()
        body, _ = self.http.get_bytes(BLS_ICS_URL, headers={"Accept": "text/calendar",
                                                            "User-Agent": f"{self.user_agent} contact: {email}",
                                                            "From": email})
        return parse_bls_ics(body.decode("utf-8", errors="replace"))

    def upcoming(self, days: int = WINDOW_DAYS) -> dict:
        now = self.clock()
        horizon = now + days * 86_400_000
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
                if e["at"] <= horizon:
                    events.append(e)
        if self.contact_email():
            bls, err, at = self._cached_fetch("calendar:bls", self._bls)
            sources.append({"source": "U.S. Bureau of Labor Statistics (iCalendar)", "link": BLS_SCHEDULE_URL,
                            "status": "ok" if bls is not None and not err else ("cached" if bls else "error"),
                            "fetched_at": at, "error": err})
            if err:
                warnings.append(f"BLS calendar fetch failed: {err}")
            events += [e for e in bls or [] if now - 3 * 3_600_000 <= e["at"] <= horizon]
        else:
            sources.append({"source": "U.S. Bureau of Labor Statistics (iCalendar)", "link": BLS_SCHEDULE_URL,
                            "status": "not_configured", "fetched_at": None,
                            "error": "Optional: BLS asks automated clients to identify their owner. Set "
                                     "CALENDAR_CONTACT_EMAIL in .env to enable CPI, PPI and jobs-report dates."})
        events.sort(key=lambda e: e["at"])
        if not events:
            if fomc is None:
                empty = "No free verified calendar source could be loaded right now."
            else:
                empty = f"No verified FOMC{'/BLS' if self.contact_email() else ''} events in the next {days} days."
        else:
            empty = None
        return {"events": events, "window_days": days, "generated_at": now, "next_fomc": next_fomc,
                "sources": sources, "warnings": warnings, "empty_reason": empty,
                "note": "Official US release schedules only. GDP (BEA) has no machine-readable schedule feed."}
