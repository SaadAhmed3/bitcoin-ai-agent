# Global Intelligence layer: architecture and source verification (R1)

Written 2026-11-02 against v0.9.8, engine signal-1.8, `main` at `2125766`. R1 adds this document and no feature code.

The layer explains why Bitcoin moves. It is context only and never feeds the live signal, score, gates, strategies or paper trading. All its output carries the label "Context, not part of the signal".

## 1. Baseline

| Check | Result |
|---|---|
| Unit tests | 807 passed, 129 deselected, 1 warning (281.71 s) |
| e2e tests | 129 passed, 807 deselected (424.05 s) |
| Engine and news weight | `signal-1.8`, news weight 0.0 |
| Database | schema 15, 31 tables, 592 `news_items`, 48,264 BTCUSDT 1h candles |
| Forward tests | start day 2026-11-03 for all nine rows |

Fingerprints were read from the real database in read-only mode and compared with today's code. All are unchanged.

| Forward test | Stored fingerprint | Config hash |
|---|---|---|
| H1, H2, BH | `5e4b8a0ebba56ae0` | n/a |
| DCA | `6f3658518fdbc3db` | `f626cffd73fb1e10` |
| Trend | `432740ee3f1654a0` | `27ce4fee913591af` |
| Range | `cfac02664cd27ea9` | `3abb2ee3b4292819` |
| Breakout | `ec393d8c89687959` | `b4aa0a6c76185000` |
| Breakout-0.5R | `7d38ed28daa9085b` | `337072c89af639f9` |
| Rotation | `0e3d89b543d38528` | `f3de8ea9c08bc780` |

A fingerprint hashes code files only: `forward_tests.py` for H1, H2 and BH; the strategy's module plus `strategy_forward.py` for the rest. Breakout-0.5R also pins `breakout.py` by SHA-256. New files, new tables and new data cannot change a fingerprint. Editing a protected file can.

## 2. Source verification

Checked live on 2026-11-02 between about 07:00 and 07:17 UTC, with one request per endpoint and the User-Agent `bitcoin-ai-agent/0.9 (local research tool)`. No blocks were worked around and no e-mail address was sent. Robots results use the longest-match rule from RFC 9309, group `*`.

### Events and articles

| Source | Class | Evidence | Notes |
|---|---|---|---|
| Cointelegraph, The Block, Decrypt, Bitcoin Magazine RSS | **Works** | 30, 20, 39, 10 items | Existing feeds |
| CoinDesk RSS | **Works** | 25 items | No summaries, titles only |
| Google News RSS | **Works, terms concern** | 100 items | robots.txt says `Disallow: /`. See risk R1 |
| BBC World, BBC Business RSS | **Works** | 30, 46 items | The pages I could fetch state no explicit licence. Store headline and link only |
| Fed press releases (all, monetary) | **Works** | 20, 15 items | Official |
| SEC press releases RSS | **Works** | 25 items | SEC limits automated use to 10 requests/s and asks tools to declare themselves. Third-party guides say the declaration should include a contact e-mail; I did not confirm that on sec.gov |
| CFTC press RSS (general, enforcement) | **Works** | 10, 10 items | Titles only |
| Treasury press releases | **Partly: HTML only** | HTML list 200, robots allow | No press feed. `/rss.xml` is not press releases (newest item 2026-07-22); `/news/press-releases/feed` is 404 |
| GDELT DOC 2.0 API | **Unavailable from this connection** | HTTP 429 on 4 of 4 tries over 13 minutes, including after 90 s and 5 min pauses | The 5 s pacing was respected, and the 429 came after 11-13 s. The cause is unknown; it could be a limit shared with other users of this IP. Terms allow free use with a citation and link |
| GDELT raw 15-minute files | **Works (index only)** | `lastupdate.txt` 200 | File contents not parsed yet. The GKG file was about 3.1 MB per 15 minutes |
| Blockchain.com stats and charts | **Works** | hash rate, mempool | Official terms and limits not confirmed. A third party reports about one request per 10 s |

### Calendars

| Source | Class | Evidence | Notes |
|---|---|---|---|
| Fed FOMC calendar page | **Works** | 57 rows | Already in use |
| BEA release ICS | **Works** | 168 events, 58 upcoming, through 2027-12 | Times in UTC. Covers GDP and PCE ("Personal Income and Outlays"). Next: GDP second estimate and PCE on 2026-11-25 13:30 UTC |
| BEA `release_dates.json` | **Works** | 12,096 bytes | Linked from BEA's page as its machine-readable format |
| BEA RSS | **Blocked by robots** | robots `Disallow: /` on `apps.bea.gov` except listed paths | Not needed |
| BLS ICS and schedule page | **Blocked for bots** | HTTP 403: "bot activity that doesn't conform to BLS usage policy is prohibited" | This is the only official source for CPI and NFP dates. See decision D2 |
| BLS latest-numbers RSS | **Works, no schedule** | 1 item with latest CPI, payrolls, PPI | Shows what was released, not when the next release is |

**Correction to earlier work.** Phase 8 said BEA has no machine-readable schedule. That was wrong: BEA publishes both ICS and JSON. Fixed in R2.

Phase 8 also said setting `CALENDAR_CONTACT_EMAIL` unlocks BLS. That was never tested live, and the 403 page does not mention it. R2 removed the BLS fetch and that setting.

### Other markets

Last values below were read on Monday 2026-11-02.

| Series | FRED CSV: last date (days old) | Official source without a key | Official: last date |
|---|---|---|---|
| 10-year yield | `DGS10` 4.98 on 10-29 (4) | Treasury daily yield XML, **works** | 4.94 on 10-30 |
| Broad dollar index | `DTWEXBGS` 121.3202 on 10-23 (10) | Fed H.10 data download CSV, **works** | 121.3202 on 10-23, the same value |
| VIX | `VIXCLS` 17.20 on 10-29 (4) | Cboe history CSV, **works**, VIX terms not found | 19.42 on 10-30 |
| S&P 500 | `SP500` 7,743.98 on 10-28 (5) | None found | n/a |
| Brent | `DCOILBRENTEU` 123.10 on 10-27 (6) | EIA daily XLS, **downloads**, parser needed | Not parsed yet |
| WTI | `DCOILWTICO` 103.37 on 10-27 (6) | EIA daily XLS, **downloads**, parser needed | Not parsed yet |
| Gold | **Unavailable** | None found | n/a |

**FRED CSV: works, but its terms restrict this use.** The downloads return 200 and robots allows them. However:
- The FAQ says "Don't do any data mining, scraping or extraction of FRED data".
- The Terms say FRED content may not be "downloaded, scraped" or "incorporated in any other database" without permission, except as they expressly provide. Their robots and data-gathering clause is narrower, covering methods that are disruptive.
- The same FAQ endorses building an app "through our free API". So **the FRED API (free key) is the sanctioned automated route**. I did not test it because I have no key.
- `VIXCLS`, `SP500` and the Nasdaq series carry third-party copyright notices. The API terms require the owner's permission for anything other than your own personal use.

My reading is that a small daily pull into a local database is a grey area, so I recommend not relying on it. This is your decision (D1).

**Gold.** The LBMA series `GOLDAMGBD228NLBM` and `GOLDPMGBD228NLBM` now return 404. The other daily FRED series tagged gold are not a gold price, so they must not be shown as gold:
- `NASDAQQGLDI` is a Credit Suisse FLOWS strategy index.
- `NASDAQNQXAUGLD` is a gold miners index.
- `GVZCLS` is gold ETF volatility.

### Crypto markets

| Source | Class | Evidence |
|---|---|---|
| Binance ticker and mirror klines | **Works** | Live. robots disallows `/`, but these are documented public APIs already in use |
| Coinbase ticker | **Works** | Live |
| CoinGecko public API | **Works** | Live. robots disallows `/api/v3`, same reasoning. A third-party guide says attribution is required; the app's source notes already say so |

### Shown as "unavailable", never estimated

ETF flows, exchange flows, whale movements, a gold price, and consensus forecasts for CPI and other releases. I found no verified free source for any of these. Headlines quoting an ETF flow figure stay as headlines and are never extracted as data.

## 3. What already exists

| Area | Where | What it does |
|---|---|---|
| News | `news_collector.py`, `news_analyzer.py`, `news_service.py` | Six RSS feeds polled every 10 minutes into `news_items`; clustering, sentiment, impact, verification and freshness computed on read |
| Gate 5 | `signal_engine.news_blocks` | Blocks on high-impact news against the setup |
| Calendar | `calendar_service.py` | FOMC from the Fed, optional BLS, 14-day window, daily cache. Nothing in the signal, alerts or assistant reads it |
| Alerts | `alerts.py` | `AlertMonitor` thread, 60 s cycle, runs jobs then rules |
| Ollama | `ollama_service.py`, `assistant.py` | `quote_untrusted`, `check_ai_answer`, single-flight lock, fallback when offline |
| Database | `database/db.py` | Automatic backup before a schema change; additive migrations; append-only triggers (34 trigger references) |
| UI | `templates/index.html`, `static/js/app.js` | Hash-routed views (`#/news`, `#/system`), design system, dark and light |
| Sources panel | `SOURCE_DOCS`, `/api/sources` | Source documentation and health |

### Exactly what gate 5 reads

```
FEEDS -> NewsCollector -> news_items -> NewsService.report(hours=72) -> news_analyzer.analyse()
      -> SignalService.current() -> generate_signal(..., events) -> news_blocks(events, against)
```

Any new row in `news_items` can change gate 5, because every row gets clustered and can become a high-impact event.

**Your protected list does not cover this path.** I propose also freezing these files with a test: `news_collector.py`, `news_analyzer.py`, `news_service.py` and `signal_service.py`, plus the `FEEDS` list and `Database.insert_news`.

## 4. What is missing

- A store of events across sources (today clusters are recomputed on read and never saved)
- World, macro and official sources; country, category and status fields
- CPI, PCE, NFP and GDP dates; "released" versus "upcoming"
- Other-markets data
- Before and after BTC measurements per event, and the descriptive study
- Per-event AI summaries with article citations
- An impact assessment and accuracy tracker

## 5. Design

```
existing news_items --(read only)--+
new feeds (official, BBC, GDELT) --+--> intel_articles --> clustering + rules --> intel_events --> /api/intel/* --> page
official schedules --> extended calendar                                              |
official market data --> intel_market_points                                          +--> reactions (R3)
                                                                                      +--> summaries, assessments (R4)
```

The dependency runs one way: Intelligence may read existing data, and nothing existing reads Intelligence.

### Integration points

| Point | Plan |
|---|---|
| `app.py` | Build the services, add `/api/intel/*` routes, start one worker. No change to how signals or news are wired |
| `database/db.py` | Schema 16: new tables and methods only |
| `calendar_service.py` | Extend with BEA and a released/upcoming status, and fix the wrong BEA note |
| `index.html`, `app.js`, `app.css` | New `#/intel` view and nav item |
| `market_data.SOURCE_DOCS` | New entries, shown in System & sources |
| `security.RATE_LIMITS` | Limits for any new expensive route |
| `tests/test_security.py` | `ALLOWED_HOSTS` must gain the new hosts. This is a deliberate test update and I will state it in the report |

### Files to add

| Round | Files |
|---|---|
| R2 | `services/intel_sources.py`, `services/intel_events.py`, `services/intel_markets.py`, `services/intel_service.py`, `static/js/intel.js`, `tests/test_intel_*.py`, `tests/test_gate5_isolation.py`, `tests/e2e/test_intel_e2e.py`, fixtures |
| R3 | `services/intel_reactions.py`, tests |
| R4 | `services/intel_summaries.py`, `services/intel_impact.py`, `PREREGISTRATION_NEWS_IMPACT.md`, tests |

### New tables (additive, backup first)

| Round | Table | Rule |
|---|---|---|
| R2 | `intel_articles`, `intel_events`, `intel_event_articles` | Articles append-only; events updated only as a cluster grows |
| R2 | `intel_market_points` | Append-only |
| R2 | `intel_source_health` | Separate from `source_health` (see risk R4) |
| R3 | `intel_reaction_baselines`, `intel_reaction_outcomes` | Append-only, enforced by triggers, keyed to an article or macro release (changed from the original one row per event and horizon: events can be rebuilt) |
| R4 | `intel_summaries` | Cached output, each claim with article IDs |
| R4 | `intel_assessments`, `intel_assessment_scores` | Append-only, enforced by triggers |

### Files left untouched

Your protected list: `signal_engine.py`, `technical_analysis.py`, `backtesting.py`, `regime.py`, `forward_tests.py`, `strategy_forward.py`, `strategies/*`.

Proposed additions: `news_collector.py`, `news_analyzer.py`, `news_service.py`, `signal_service.py`, `paper_trading.py`, `alerts.py`, `assistant.py`, `ollama_service.py`.

New code may import helpers from these files, which changes nothing in them.

### Gate 5 isolation tests (R2)

1. Golden test: a fixed news fixture must give an identical `news.report` and an identical `news_blocks` decision before and after the intelligence pipeline runs.
2. Running the pipeline, including an article that would block if it reached gate 5, leaves `news_items` unchanged and the signal's gate keys unchanged.
3. Hash pins on the frozen files, and a pin on the `FEEDS` list.
4. A source scan: no `intel_*` module calls `insert_news` or writes `news_items`, and no existing module imports `intel_*` except `app.py`.

## 6. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Google News robots.txt disallows our existing feed.** It supplies 296 of 592 stored items. I also could not find current official terms for the feed | Existing issue, unchanged in this work. Removing it would change gate 5 inputs, so it needs a separate decision from you (D4) |
| R2 | New articles reaching gate 5 | Separate tables, plus the tests in section 5 |
| R3 | Slow fetches or Ollama stalling alerts. `AlertMonitor` runs jobs one after another in one thread, and a summary takes about 2 minutes on CPU, which would delay alerts and forward-test processing | Intelligence gets its own worker thread and never joins `jobs=[...]` |
| R4 | False critical alerts. The `source_failure` rule alerts on every `source_health` row, so a GDELT 429 written there would raise a critical alert every time. The assistant also reads those rows | Separate `intel_source_health` table, shown in System & sources. Alerts stay out of scope until R5 |
| R5 | GDELT stays blocked | R2 works without it. It stays optional, with back-off and a clear status. Raw files are a possible alternative (D3) |
| R6 | Macro data is late (up to 10 days for the dollar index) | Show the data date and delay on every value; never call it live |
| R7 | Copyright on third-party series | Personal local display only, keep notices, leave them out of exports unless you decide otherwise |
| R8 | Fragile HTML sources (Treasury list, FOMC page) | Fixture tests, layout-change detection, honest empty state |
| R9 | Misleading event study: events overlap, samples are small | Bootstrap intervals, small-sample flags, the stated "not causation" note, candle tests against look-ahead |
| R10 | Accuracy tracker invites over-reading | Pre-register the scoring rule first; DB triggers block edits; show sample size and the "small samples prove nothing" note |
| R11 | Prompt injection in article text | Reuse `quote_untrusted`, strict JSON, drop uncited claims, injection tests |
| R12 | Ollama contention with the assistant | One model on CPU: skip a summary when the assistant is busy, no parallel requests. The exact mechanism must not edit `assistant.py`, so this is settled in R4 |
| R13 | More write load on SQLite | WAL and a 10 s timeout already exist; small batched writes, tested under the worker |
| R14 | `news_analyzer` wording could pass as this layer's status | The new status rules are separate and tested on their own, including conflicting reports and old news |

## 7. Decisions

**Your decisions (R1 review, 2026-11-02), built in R2:**

1. Other markets: official sources (Treasury, Fed, EIA with `xlrd`, Cboe). VIX from Cboe accepted for personal non-commercial use, source labelled. S&P 500 unavailable unless an optional free `FRED_API_KEY` is set.
2. CPI and NFP: curated file `data/calendars/bls_2026_2027.json` with source URL and last-verified date, warning after 12 months. No live BLS fetching.
3. GDELT DOC API: optional, exponential back-off, own health table, clear status. No raw GKG files.
4. Google News: unchanged for now, so gate 5 inputs stay identical. See the proposal below.
5. Extra frozen files approved: `news_collector.py`, `news_analyzer.py`, `news_service.py`, `signal_service.py`, `paper_trading.py`, `alerts.py`, `assistant.py`, `ollama_service.py`.
6. Contact address: not chosen at R1 (the `[CHOOSE ONE]` was left in), so R2 used none. Your R2 review chose it: `CALENDAR_CONTACT_EMAIL` from `.env`, only in the SEC User-Agent, best-effort when missing. Built before R3.

**Your decisions (R2 review, 2026-11-02), built in R3:**

- Event time is the earliest publication among the event's articles. First-seen time is stored too. Reactions use publication time. Events with a publication time missing or later than first seen are flagged.
- All reactions are keyed to articles, not events.
- Events within 2 hours of a macro release are shown separately from the per-category statistics.

**R4 implementation choices:**

- Summaries use their own streaming request, because `services/ollama_service.py` is frozen and has no JSON format or way to stop. Rules are enforced on the output.
- Summaries have their own setting, `intel_ai_summaries`, on by default. The existing `ai_explanations` is off by default and controls assistant rewrites.
- An assessment is scored from when it was made, not from publication, so moves it could already see earn no credit.
- Only the first assessment of a story, made within 24 hours of publication, counts. The rest are stored and never counted.
- The 1-3 months label is scored at +90 days, and its historical input is the study's +30d row, the longest it has.
- Rules version 2 replaced version 1 before any horizon was scored. See Amendment 1 in `PREREGISTRATION_NEWS_IMPACT.md`.

**R3 implementation choices:**

- Flagged events are left out of the statistics and counted, not only labelled, because their time is absent or provably wrong.
- If any release type's dates are unavailable, or an event falls outside their span, the event is "macro unknown", not clean.
- Overlapping windows count as one episode. The bootstrap resamples whole episodes, and no interval is given below 5 episodes.
- A candle counts as closed only if it was fetched after it closed, because a row saved while forming keeps partial values.
- Macro reactions are keyed by release id and time, so a rescheduled release is measured at its new time.

**Proposal for later: replace Google News.** `news.google.com/robots.txt` says `Disallow: /` for general agents, and the feed supplies about half of the stored news (296 of 592 items on 2026-11-02). I could not find current official terms for the feed. A replacement could be BBC plus official press releases and, if it starts answering, GDELT.

This is **not** part of R2. Those items are inputs to gate 5, so removing the feed changes which news can block a trade. It therefore needs its own gate 5 proposal and your approval, ideally with a side-by-side comparison of which events each source set would have blocked.

**R2 changes to the plan above:**
- `intel_event_articles` is not append-only. Membership is derived, so better rules can rebuild it from the append-only articles. R3 must therefore key its stored reactions to articles rather than to event IDs, which change on a rebuild.
- `intel_market_state` was not needed; retry state uses the existing `kv_cache`.

**Original options considered before R2:**

**D1. Other-markets source.**
- **Recommended, option A: official sources, no key.** Treasury (10-year), Fed H.10 (dollar), EIA (Brent, WTI), Cboe (VIX). This avoids the FRED terms question and the 10-year and VIX values are a day fresher. It adds one small dependency, `xlrd`, to read EIA's XLS, unless you prefer a free EIA API key. S&P 500 needs either a free FRED API key or stays "unavailable". The Cboe terms for this file were not found, so please accept that risk or drop VIX.
- Option B: free FRED API key for all six. One simple source, personal use only, but you must register a key and the data can be a day older.
- Option C: FRED CSV as you first named it. Simplest, but it conflicts with the FAQ wording. I don't recommend it.
- Gold is "unavailable" under all three.

**D2. CPI and NFP dates**, which only BLS publishes and which BLS blocks for bots.
- **Recommended, option A: try the existing contact route once.** You choose an address for `CALENDAR_CONTACT_EMAIL`; I test it live once. If it still returns 403, it is reported as blocked.
- Option B: free FRED API key. Its release-dates endpoint should list these dates, but I have not tested it. It would be a secondary source for an official schedule.
- Option C: show CPI and NFP dates as "unavailable", with a link to the BLS schedule. BLS's RSS can still show the latest released figures.

**D3. GDELT.**
- **Recommended, option A: build it as optional and retry later,** with back-off and a clear status. R2 works without it.
- Option B: use the raw 15-minute files as well. They worked today, but mean about 3 MB per file for the GKG and more parsing code.

**D4. Google News feed.** Keep as is for now (recommended, so gate 5 inputs stay unchanged), or open a separate gate 5 proposal to replace it.

**D5. Frozen files.** Approve the extra frozen files in sections 3 and 5.

**D6. SEC feed contact.** If D2 gives a contact address, should the SEC feed declare it too? Without one, SEC stays a best-effort source using the app's plain User-Agent.

## 8. Other notes

- The project is at `F:\bitcoin-ai-agent`; `E:\` does not exist on this PC.
- The guide is at the project root (untracked, left as is), not in `docs\`.
- The guide's extracted text says forward tests started on "12 Nov 2026", while the database start day is 2026-11-03. This may be a footnote digit in the PDF text; I could not view the rendered page.
- `requirements.txt` says tested on Python 3.14.6, while the project `.venv` runs 3.13.12. All tests here ran on 3.13.12.

## 9. Sources for the terms

- [FRED legal notices and terms](https://fred.stlouisfed.org/legal/)
- [FRED API terms](https://fred.stlouisfed.org/docs/api/terms_of_use.html)
- [GDELT terms (About page)](https://www.gdeltproject.org/about.html)
- [EIA copyright and reuse](https://www.eia.gov/about/copyrights_reuse.php)
- [SEC rate control limits](https://www.sec.gov/oit/announcement/new-rate-control-limits)
- [BEA release schedule](https://www.bea.gov/news/schedule)
- [Cboe VIX historical data](https://www.cboe.com/tradable-products/vix/vix-historical-data/)
- [BBC News RSS page](https://www.bbc.co.uk/news/10628494)
