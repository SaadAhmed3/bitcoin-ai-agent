"""Before R3: real EIA files, data freshness labels, the SEC contact address. No network: fake HTTP, fixed clocks.

tests/fixtures/eia_real holds the unmodified EIA workbooks of the 2026-10-28 release, gzipped. The expected values below were
read from the raw rows by a separate script, not by parse_eia_xls. The contact address in these tests is a placeholder.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
from pathlib import Path

import pytest
import xlrd
from conftest import FIXED_NOW
from test_phase8 import FakeHttp, make_app

from services.http_client import HttpClient, ProviderError
from services.intel_markets import MARKET_SERIES, IntelMarkets, parse_eia_xls
from services.intel_service import IntelService
from services.intel_sources import SOURCES, contact_user_agent, fetch_source

ROOT = Path(__file__).resolve().parents[1]
REAL = Path(__file__).parent / "fixtures" / "eia_real"
ADDRESS = "owner@example.com"
DAY = 86_400_000


def real(code: str) -> bytes:
    return gzip.decompress((REAL / f"{code}-release-2026-10-28.xls.gz").read_bytes())


def at(date: str, hour: int = 12) -> int:
    return int(dt.datetime.fromisoformat(f"{date}T{hour:02d}:00:00+00:00").timestamp() * 1000)


# ---------------------------------------------------------------- oil: real EIA files
BRENT_TAIL = [("2026-10-21", 117.37), ("2026-10-22", 125.39), ("2026-10-23", 128.30), ("2026-10-26", 128.12), ("2026-10-27", 123.10)]
WTI_TAIL = [("2026-10-21", 103.95), ("2026-10-22", 104.27), ("2026-10-23", 109.72), ("2026-10-26", 105.25), ("2026-10-27", 103.37)]


@pytest.mark.parametrize("code,tail", [("RBRTEd", BRENT_TAIL), ("RWTCd", WTI_TAIL)])
def test_parser_reads_the_real_eia_file_exactly(code, tail):
    assert parse_eia_xls(real(code))[-5:] == tail


@pytest.mark.parametrize("code,key,title", [("RBRTEd", "RBRTE", "Europe Brent Spot Price FOB"),
                                            ("RWTCd", "RWTC", "Cushing, OK WTI Spot Price FOB")])
def test_real_file_is_the_series_we_think_and_matches_its_own_contents_sheet(code, key, title):
    book = xlrd.open_workbook(file_contents=real(code))
    data = book.sheet_by_name("Data 1")
    assert data.cell_value(1, 1) == key and title in data.cell_value(2, 1) and "Dollars per Barrel" in data.cell_value(2, 1)
    contents = [v for i in range(book.sheet_by_name("Contents").nrows) for v in book.sheet_by_name("Contents").row_values(i)]
    assert "10/27/2026" in contents and "10/28/2026" in contents         # "Latest Data for" and "Release Date"
    assert parse_eia_xls(real(code))[-1][0] == "2026-10-27"


def test_app_maps_brent_and_wti_to_the_right_real_files(db):
    http = FakeHttp({"RBRTEd": real("RBRTEd"), "RWTCd": real("RWTCd")})
    m = IntelMarkets(db, http, clock=lambda: at("2026-11-02"))
    for sid in ("brent", "wti"):
        db.intel_add_market_points(sid, m._fetch(sid), "U.S. EIA", at("2026-11-02"))
    latest = {s["id"]: s for s in m.latest()["series"]}
    assert (latest["brent"]["value"], latest["brent"]["data_date"]) == (123.10, "2026-10-27")
    assert (latest["wti"]["value"], latest["wti"]["data_date"]) == (103.37, "2026-10-27")


def test_the_wide_spread_is_in_the_source_not_made_by_the_parser():
    """Brent minus WTI, from the raw files: about $3-5 in 2023-2025, about $20 and more since mid-September 2026."""
    brent, wti = dict(parse_eia_xls(real("RBRTEd"))), dict(parse_eia_xls(real("RWTCd")))
    spread = {d: brent[d] - wti[d] for d in brent.keys() & wti.keys()}
    assert round(spread["2026-10-27"], 2) == 19.73 and round(spread["2026-10-02"], 2) == 37.62
    assert round(spread["2026-08-26"], 2) == 4.31
    year = sorted(v for d, v in spread.items() if d.startswith("2025"))
    assert 3 < year[len(year) // 2] < 5


# ---------------------------------------------------------------- freshness labels
def seeded(db, today: str, latest: dict[str, str]):
    m = IntelMarkets(db, FakeHttp(), clock=lambda: at(today))
    for sid, date in latest.items():
        db.intel_add_market_points(sid, [(date, 100.0)], "t", at(today))
    return {s["id"]: s for s in m.latest()["series"]}


def test_every_series_declares_its_cadence_and_normal_lag():
    for s in MARKET_SERIES:
        assert s["release_cadence"] and isinstance(s["max_normal_age_days"], int), s["id"]
    by = {s["id"]: s for s in MARKET_SERIES}
    assert "Weekly" in by["usd_broad"]["release_cadence"] and "Monday" in by["usd_broad"]["release_cadence"]
    assert "Weekly" in by["brent"]["release_cadence"] and "Wednesday" in by["wti"]["release_cadence"]


def test_data_date_and_cadence_are_returned_and_the_dollar_index_of_oct_23_is_normal_on_nov_2(db):
    s = seeded(db, "2026-11-02", {"usd_broad": "2026-10-23"})["usd_broad"]
    assert s["data_date"] == "2026-10-23" and s["age_days"] == 10
    assert "Weekly" in s["release_cadence"]
    assert s["stale"] is False and s["stale_note"] is None


@pytest.mark.parametrize("sid,date,today,stale", [
    ("usd_broad", "2026-10-23", "2026-11-03", False),      # 11 days: a Monday holiday pushes the release to Tuesday
    ("usd_broad", "2026-10-23", "2026-11-04", True),       # 12 days: older than any normal weekly release
    ("brent", "2026-10-27", "2026-11-05", False),          # 9 days: released every Wednesday with data to the day before
    ("brent", "2026-10-27", "2026-11-06", True),
    ("wti", "2026-10-27", "2026-11-06", True),
    ("us10y", "2026-10-30", "2026-11-03", False),          # Friday's value, seen on Tuesday before the release
    ("us10y", "2026-10-30", "2026-11-04", True),
    ("vix", "2026-10-30", "2026-11-03", False),
    ("vix", "2026-10-30", "2026-11-04", True),
])
def test_series_older_than_its_normal_lag_is_flagged(db, sid, date, today, stale):
    s = seeded(db, today, {sid: date})[sid]
    assert s["stale"] is stale
    if stale:
        assert date in s["stale_note"] and str(s["age_days"]) in s["stale_note"] and "normally" in s["stale_note"]
    else:
        assert s["stale_note"] is None


def test_unavailable_series_carry_no_freshness_claim(db):
    latest = seeded(db, "2026-11-02", {})
    assert not latest["gold"].get("stale") and not latest["sp500"].get("stale")
    assert latest["brent"]["status"] == "no_data" and not latest["brent"].get("stale")


# ---------------------------------------------------------------- SEC contact address
class RecordingHttp(FakeHttp):
    def get_bytes(self, url, params=None, headers=None, max_bytes=5_000_000, follow_redirects=True):
        out = super().get_bytes(url, params, headers, max_bytes)
        self.calls[-1] = (url, dict(params or {}), dict(headers or {}), follow_redirects)
        return out


FEED = b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title><item><title>SEC charges a crypto firm</title><link>https://www.sec.gov/a</link><pubDate>Thu, 09 Oct 2025 08:00:00 GMT</pubDate></item></channel></rss>'
SEC = next(s for s in SOURCES if s["id"] == "sec_press")
BBC = next(s for s in SOURCES if s["id"] == "bbc_world")


def test_contact_goes_only_in_the_sec_user_agent():
    http = RecordingHttp({"sec.gov": FEED, "bbci": FEED})
    fetch_source(SEC, http, FIXED_NOW, contact=ADDRESS)
    fetch_source(BBC, http, FIXED_NOW, contact=ADDRESS)
    sec, bbc = http.calls
    assert ADDRESS in sec[2]["User-Agent"]
    assert ADDRESS not in json.dumps(bbc[1:3])
    assert ADDRESS not in sec[0] and ADDRESS not in json.dumps(sec[1])     # not in the URL or query


def test_only_sec_is_marked_to_receive_the_contact():
    assert [s["id"] for s in SOURCES if s.get("contact_user_agent")] == ["sec_press"]


def test_contact_request_never_follows_a_redirect_to_another_host():
    http = RecordingHttp({"sec.gov": FEED})
    fetch_source(SEC, http, FIXED_NOW, contact=ADDRESS)
    assert http.calls[0][3] is False


def test_real_client_refuses_the_redirect_and_does_not_retry_it():
    class Resp:
        status_code, content, headers = 302, b"", {"Location": "https://elsewhere.test/"}

    class Session:
        headers: dict = {}
        seen: list = []

        def get(self, url, **kw):
            self.seen.append(kw)
            return Resp()

    s = Session()
    client = HttpClient(retries=2, session=s, min_interval=0, sleep=lambda _: None)
    with pytest.raises(ProviderError) as e:
        client.get_bytes("https://www.sec.gov/x", headers={"User-Agent": f"x {ADDRESS}"}, follow_redirects=False)
    assert s.seen[0]["allow_redirects"] is False and len(s.seen) == 1
    assert ADDRESS not in str(e.value)


@pytest.mark.parametrize("missing", ["", "   ", None])
def test_missing_contact_is_best_effort_as_before(missing):
    http = RecordingHttp({"sec.gov": FEED})
    assert fetch_source(SEC, http, FIXED_NOW, contact=missing)
    assert "User-Agent" not in http.calls[0][2] and http.calls[0][3] is True


@pytest.mark.parametrize("bad", ["not-an-address", "a@b", "a b@example.com", "owner@example.com\r\nX-Evil: 1",
                                 "owner@example.com, other@example.com", "<owner@example.com>"])
def test_malformed_contact_is_ignored_never_sent(bad):
    assert contact_user_agent(bad) is None
    http = RecordingHttp({"sec.gov": FEED})
    fetch_source(SEC, http, FIXED_NOW, contact=bad)
    assert "User-Agent" not in http.calls[0][2]


def test_contact_never_reaches_health_errors_logs_or_any_route(config, db, caplog):
    config.calendar_contact_email = ADDRESS
    c, app = make_app(config, db)
    intel = app.config["BTC_INTEL"]
    assert intel.contact() == ADDRESS                                  # wired from the setting
    intel.http = RecordingHttp({"bbci": FEED, "sec.gov": ProviderError("www.sec.gov: HTTP 403")})
    intel.sources = [SEC, BBC]
    with caplog.at_level(logging.DEBUG):
        intel.refresh(force=True)
    assert ADDRESS in intel.http.calls[0][2]["User-Agent"]
    assert ADDRESS not in caplog.text
    with db.connect() as conn:
        for table in ("intel_source_health", "kv_cache", "settings", "intel_articles"):
            try:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            except Exception:
                continue
            assert ADDRESS not in json.dumps([list(r) for r in rows], default=str), table
    for url in ("/api/settings", "/api/sources", "/api/intel/overview", "/api/intel/events", "/api/intel/markets", "/api/calendar"):
        assert ADDRESS not in c.get(url).get_data(as_text=True), url
    assert c.get("/api/settings").get_json()["market"]["calendar_contact_email"]["value"] == "set"


def test_settings_says_not_set_without_it(config, db):
    config.calendar_contact_email = ""
    c, _ = make_app(config, db)
    assert c.get("/api/settings").get_json()["market"]["calendar_contact_email"]["value"] == "not set"


def test_config_repr_does_not_show_the_address(config):
    config.calendar_contact_email = ADDRESS
    assert ADDRESS not in repr(config)


def test_no_real_address_is_written_in_the_repo_files():
    """Only the placeholder domains may appear next to the setting's name."""
    for name in (".env.example", "README.md", "USER_GUIDE.md", "SECURITY.md", "CHANGELOG.md", "ARCHITECTURE_GLOBAL_INTEL.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("CALENDAR_CONTACT_EMAIL="):
                assert line.strip() == "CALENDAR_CONTACT_EMAIL=", name


def test_intel_service_default_has_no_contact(db):
    assert IntelService(db, http=FakeHttp(), sources=[], clock=lambda: FIXED_NOW).contact() == ""
