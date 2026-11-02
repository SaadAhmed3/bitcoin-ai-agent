"""Global Intelligence: other markets at daily resolution from official sources. Context, not part of the signal.

Every value is shown with its data date and delay. Series without a verified free source are "unavailable", never estimated.
The optional FRED_API_KEY only enables the S&P 500; it is sent to api.stlouisfed.org and never stored, logged or returned.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
from typing import Callable

import xlrd

from database import Database, now_ms
from services.http_client import ProviderError

log = logging.getLogger("btc_agent.intel")

LABEL = "Context, not part of the signal"
HOUR = 3_600_000
DAY = 24 * HOUR
REFRESH_MS = 6 * HOUR
RETRY_MS = HOUR
FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
H10_URL = ("https://www.federalreserve.gov/datadownload/Output.aspx?rel=H10&series=122e3bcb627e8e53f1bf72a1a09cfb81&lastobs=30"
           "&from=&to=&filetype=csv&label=include&layout=seriescolumn")


def treasury_url(year: int) -> str:
    return ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
            f"?data=daily_treasury_yield_curve&field_tdr_date_value={year}")


# ------------------------------------------------------------------------- parsers (raise ProviderError on a changed layout)
def parse_treasury_xml(text: str) -> list[tuple[str, float]]:
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", text, re.S):
        d = re.search(r"<d:NEW_DATE[^>]*>(\d{4}-\d{2}-\d{2})", entry)
        v = re.search(r"<d:BC_10YEAR[^>]*>([0-9.]+)<", entry)
        if d and v:
            out.append((d.group(1), float(v.group(1))))
    if not out:
        raise ProviderError("home.treasury.gov: no 10-year yields found (layout changed?)")
    return sorted(out)


def parse_h10_csv(text: str) -> list[tuple[str, float]]:
    """The broad dollar index is the first data column of the Fed's H.10 download; non-trading days are "ND"."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0]):
            try:
                out.append((row[0], float(row[1])))
            except ValueError:
                continue
    if "Nominal Broad Dollar Index" not in text or not out:
        raise ProviderError("federalreserve.gov: H.10 broad dollar index not found (layout changed?)")
    return sorted(out)


def parse_cboe_vix(text: str) -> list[tuple[str, float]]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or [c.strip().upper() for c in rows[0]][:1] != ["DATE"] or "CLOSE" not in [c.strip().upper() for c in rows[0]]:
        raise ProviderError("cdn.cboe.com: VIX history layout changed")
    i = [c.strip().upper() for c in rows[0]].index("CLOSE")
    out = []
    for r in rows[1:]:
        try:
            out.append((dt.datetime.strptime(r[0], "%m/%d/%Y").strftime("%Y-%m-%d"), float(r[i])))
        except (ValueError, IndexError):
            continue
    if not out:
        raise ProviderError("cdn.cboe.com: no VIX values found")
    return sorted(out)


def parse_eia_xls(content: bytes) -> list[tuple[str, float]]:
    try:
        book = xlrd.open_workbook(file_contents=content)
        sheet = book.sheet_by_name("Data 1")
        out = []
        for i in range(3, sheet.nrows):
            d, v = sheet.cell(i, 0), sheet.cell(i, 1)
            if d.ctype == xlrd.XL_CELL_DATE and v.ctype == xlrd.XL_CELL_NUMBER:
                out.append((dt.date(*xlrd.xldate_as_tuple(d.value, book.datemode)[:3]).isoformat(), float(v.value)))
    except (xlrd.XLRDError, ValueError, IndexError) as exc:
        raise ProviderError(f"www.eia.gov: unreadable price file ({type(exc).__name__})") from None
    if not out:
        raise ProviderError("www.eia.gov: no prices found (layout changed?)")
    return sorted(out)


def parse_fred_api(data: dict) -> list[tuple[str, float]]:
    obs = (data or {}).get("observations")
    if not isinstance(obs, list):
        raise ProviderError("api.stlouisfed.org: unexpected answer")
    out = []
    for o in obs:
        try:
            out.append((o["date"], float(o["value"])))
        except (KeyError, TypeError, ValueError):
            continue           # FRED marks missing days with "."
    return sorted(out)


# ------------------------------------------------------------------------- series
# max_normal_age_days: the oldest the latest value should be, in calendar days, just before the next release. It allows for a
# weekend and a one-day holiday. Beyond it the source is late or the fetch is failing, and the page says so.
EIA_NOTE = "U.S. government data, public domain. Source: U.S. Energy Information Administration."
MARKET_SERIES = [
    {"id": "us10y", "name": "US 10-year Treasury yield", "unit": "%", "source": "U.S. Treasury", "health": "market:treasury",
     "source_url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates",
     "delay_label": "Daily, published after the US close", "notes": "",
     "release_cadence": "Daily, each business day", "max_normal_age_days": 4},
    {"id": "usd_broad", "name": "US dollar index (broad, nominal)", "unit": "index", "source": "Federal Reserve H.10", "health": "market:h10",
     "source_url": "https://www.federalreserve.gov/releases/h10/current/",
     "delay_label": "Daily values, released weekly: often about a week behind", "notes": "",
     "release_cadence": "Weekly release, on Mondays, of daily values", "max_normal_age_days": 11},
    {"id": "vix", "name": "VIX volatility index", "unit": "index", "source": "Cboe", "health": "market:cboe",
     "source_url": "https://www.cboe.com/tradable-products/vix/vix-historical-data/",
     "delay_label": "Daily close, published after the close",
     "notes": "Cboe data, shown for personal non-commercial use only. Not included in exports.",
     "release_cadence": "Daily, each trading day", "max_normal_age_days": 4},
    {"id": "brent", "name": "Brent crude (spot)", "unit": "USD/barrel", "source": "U.S. EIA", "health": "market:eia",
     "source_url": "https://www.eia.gov/dnav/pet/hist/RBRTEd.htm",
     "delay_label": "Daily spot price, usually published about a week later", "notes": EIA_NOTE,
     "release_cadence": "Weekly release, on Wednesdays, of daily values", "max_normal_age_days": 9},
    {"id": "wti", "name": "WTI crude (spot)", "unit": "USD/barrel", "source": "U.S. EIA", "health": "market:eia",
     "source_url": "https://www.eia.gov/dnav/pet/hist/RWTCd.htm",
     "delay_label": "Daily spot price, usually published about a week later", "notes": EIA_NOTE,
     "release_cadence": "Weekly release, on Wednesdays, of daily values", "max_normal_age_days": 9},
    {"id": "sp500", "name": "S&P 500", "unit": "index", "source": "FRED (S&P Dow Jones Indices)", "health": "market:fred",
     "source_url": "https://fred.stlouisfed.org/series/SP500", "delay_label": "Daily close, published the next business day",
     "notes": "S&P Dow Jones Indices data retrieved through FRED, personal use only. Not included in exports.", "needs_key": True,
     "release_cadence": "Daily, each trading day", "max_normal_age_days": 4},
]
UNAVAILABLE = {
    "gold": {"name": "Gold", "reason": "No verified free gold price series. The LBMA series were removed from FRED, and the others "
                                       "tagged gold are miners, strategy or volatility indexes, not the gold price."},
}
SP500_NO_KEY = "Needs an optional free FRED API key: set FRED_API_KEY in .env, then restart the app."


class IntelMarkets:
    def __init__(self, db: Database, http, fred_key: Callable[[], str] = lambda: "", clock: Callable[[], int] = now_ms):
        self.db = db
        self.http = http
        self.fred_key = fred_key
        self.clock = clock

    def _fetch(self, sid: str) -> list[tuple[str, float]]:
        if sid == "us10y":
            year = dt.datetime.fromtimestamp(self.clock() / 1000, dt.timezone.utc).year
            body, _ = self.http.get_bytes(treasury_url(year))
            return parse_treasury_xml(body.decode("utf-8", errors="replace"))
        if sid == "usd_broad":
            body, _ = self.http.get_bytes(H10_URL)
            return parse_h10_csv(body.decode("utf-8-sig", errors="replace"))
        if sid == "vix":
            body, _ = self.http.get_bytes("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv")
            return parse_cboe_vix(body.decode("utf-8-sig", errors="replace"))[-30:]
        if sid in ("brent", "wti"):
            code = "RBRTEd" if sid == "brent" else "RWTCd"
            body, _ = self.http.get_bytes(f"https://www.eia.gov/dnav/pet/hist_xls/{code}.xls")
            return parse_eia_xls(body)[-30:]
        if sid == "sp500":
            data, _ = self.http.get_json(FRED_OBSERVATIONS, params={
                "series_id": "SP500", "api_key": self.fred_key(), "file_type": "json", "sort_order": "desc", "limit": "30"})
            return parse_fred_api(data)
        raise ProviderError(f"unknown series {sid}")

    def refresh(self, force: bool = False) -> dict:
        """Fetch what is due. Each series fails alone; the last stored value is kept and shown with its date."""
        now = self.clock()
        results = {}
        for s in MARKET_SERIES:
            if s.get("needs_key") and not self.fred_key():
                continue
            state = self.db.intel_market_state(s["id"])
            if not force and state and now < state["next_attempt_at"]:
                continue
            try:
                points = self._fetch(s["id"])
            except ProviderError as exc:
                msg = str(exc)
                self.db.intel_set_market_state(s["id"], now + RETRY_MS, msg[:300], now)
                self.db.intel_record_health(s["health"], False, msg, now, now + RETRY_MS, kind="market")
                results[s["id"]] = msg
                continue
            added = self.db.intel_add_market_points(s["id"], points, s["source"], now)
            self.db.intel_set_market_state(s["id"], now + REFRESH_MS, None, now)
            self.db.intel_record_health(s["health"], True, None, now, now + REFRESH_MS, kind="market", items=added)
            results[s["id"]] = added
        return results

    def latest(self) -> dict:
        now = self.clock()
        out = []
        for s in MARKET_SERIES:
            row = {k: s.get(k) for k in ("id", "name", "unit", "source", "source_url", "delay_label", "notes", "release_cadence")}
            pts = self.db.intel_market_points(s["id"], 2)
            state = self.db.intel_market_state(s["id"]) or {}
            row["fetch_error"] = state.get("last_error")
            if not pts:
                if s.get("needs_key") and not self.fred_key():
                    row.update(status="unavailable", value=None, reason=SP500_NO_KEY)
                else:
                    row.update(status="no_data", value=None, reason=state.get("last_error") or "Not fetched yet.")
                out.append(row)
                continue
            p = pts[0]
            age = (dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).date() - dt.date.fromisoformat(p["data_date"])).days
            stale = age > s["max_normal_age_days"]
            row.update(status="ok", value=p["value"], data_date=p["data_date"], age_days=age, change=None, change_pct=None,
                       previous_date=None, stale=stale,
                       stale_note=(f"Older than usual: {p['data_date']} is {age} days old, and this series is normally at most "
                                   f"{s['max_normal_age_days']} days old. The source may be late or the fetch failing.") if stale else None)
            if len(pts) > 1:
                prev = pts[1]
                row.update(change=p["value"] - prev["value"], previous_date=prev["data_date"],
                           change_pct=(p["value"] / prev["value"] - 1) * 100 if prev["value"] else None)
            out.append(row)
        for sid, u in UNAVAILABLE.items():
            out.append({"id": sid, "name": u["name"], "status": "unavailable", "value": None, "reason": u["reason"], "fetch_error": None})
        return {"series": out, "label": LABEL, "generated_at": now,
                "note": "Daily data only: not live prices, and not comparable with BTC's hourly moves."}

    def health(self) -> list[dict]:
        return [h for h in self.db.intel_health() if h["source"].startswith("market:")]

