# Pre-registration: news impact accuracy tracker

Registered on 2026-11-02, before any tracker code exists and before any assessment was stored. The git history shows this file was committed first. It is pinned by SHA-256 in `services/intel_tracker.py`, and a test fails if it changes.

**This is research and context only. Nothing here reaches the live signal, score, gates, strategies or paper trading.**

Any change to these rules gets a new rules version, and results are never pooled across versions.

## The question

When the rule-based assessment labels a news event Bullish, Bearish or Neutral, is that label right more often than simple baselines? The score in the assessment is a heuristic and not a probability, so only labels are scored.

## What is stored

- One row per assessment, written at the moment it is made and never edited or deleted. Database triggers enforce this.
- Each row is keyed to the sorted set of article IDs, the publication time and the rules version, not to an event ID, because events can be rebuilt.
- If an event's article set changes, a new assessment is stored. The old one stays.

## Which assessments count

An assessment **counts** only if all of these hold:

1. It is from the current rules version (version 1).
2. It was made within 24 hours of the event's publication time. Older events are assessed for display but never count, so the app cannot look back at stories whose outcome is already known.
3. The publication time is present and not later than first seen.
4. It is the first assessment for its anchor article, the article with the earliest publication time. Later assessments of a growing story are stored but do not count, because they would be the same bet again.

## Horizons

| Label horizon | Scored at |
|---|---|
| 0-24h | +24 hours |
| 1-7d | +7 days |
| 1-3 months | +90 days |

The historical input for the 1-3 months label uses the study's +30d row, the longest it has. Scoring still happens at +90 days.

## How a horizon is scored

Closed 1h BTCUSDT candles from Binance only. A candle counts as closed only if its close time has passed and it was fetched after that.

- **Measured from the assessment, not from publication.** The base is the last candle closed at or before the assessment time. Measuring from publication would give credit for moves the assessment could already see.
- **Target:** the first candle closing at least the horizon after the assessment.
- **Return:** target close divided by base close, minus 1.
- **Normal move:** the median absolute return over the same window length, using candles from the 365 days up to the base candle only. It needs at least 30 samples.
- **Band:** X = 0.5 x the normal move.

| Label | Hit when |
|---|---|
| Bullish | return > +X |
| Bearish | return < -X |
| Neutral | -X <= return <= +X |
| Mixed, Uncertain | Not scored. Counted separately as coverage |

A missing base or target candle, or a missing normal move, makes that horizon unscorable. It is never filled from a neighbouring candle. It is reported as a count, and never counted as a hit or a miss.

## Baselines

Measured on exactly the same scored rows:

1. **Always Neutral:** the share of rows whose return fell inside the band.
2. **Random uniform:** 1 in 3. Every return falls in exactly one of the three zones, so a uniform random label is right one time in three.
3. **Random with the same label mix:** the chance of a hit if the labels were shuffled, keeping how often each label was used. It is the sum, over labels, of the share of rows with that label times the share of rows whose return landed in that label's zone.

## When a hit rate is shown

Per horizon, a hit rate is shown only with **at least 30 counted scored assessments and at least 20 separate episodes**. Below that, the page shows the sample size and says that small samples prove nothing.

- **Episodes:** assessments whose windows overlap count as one episode, because they are not separate observations of a BTC move.
- **Interval:** a 95% bootstrap interval of the hit rate, resampling whole episodes, with a fixed seed.
- **Claim:** the tracker says a horizon beats the baselines only if the lower end of that interval is above all three baselines. Otherwise it says the difference is not established.
- With three horizons compared, one could clear the bar by chance. A result needs to hold on later data before it means anything.

## Macro releases

An assessment is marked if its publication time or its assessment time is within 2 hours of a CPI, FOMC, jobs report, GDP or PCE release. Marked assessments are left out of the main hit rate and shown separately. If the release dates are incomplete or do not cover that time, the assessment is treated as marked. A window can still contain a later release; this is a known limit.

## What this cannot show

- Passing would show labels were useful as description in this period, not that they caused anything or will keep working.
- A rule-based label can be right for the wrong reason.
- Most events are expected to be Uncertain, so coverage will be low. A high hit rate on few labels is reported with its coverage.
- Nothing will be connected to the signal because of this tracker. Any such change would be a separate proposal needing approval.

## Amendment 1, 2026-11-02: rules version 2

The text above is unchanged. This amendment was added after the first live run and **before any horizon was scored: the database held 0 scores, so no outcome was known.**

The first live run stored 359 assessments under rules version 1. Real headlines showed that the labelling rules misread wording: "Why Bitcoin Didn't Crash" and "AI found a way to crash bitcoin nodes" were labelled Bearish because of the word "crash", and "Bitcoin ETFs sink into outflows" was read as BTC falling. A score of -86 from wording alone was also far too strong for weak evidence.

Rules version 2:

- A report counts as saying BTC moved only if the direction word directly follows the name, as in "Bitcoin drops". Negated or unrelated uses no longer count.
- Reports of BTC moving carry half the weight: 0.5 at 0-24h, 0.25 at 1-7d, none at 1-3 months.

**Only rules version 2 counts from now on, so item 1 under "Which assessments count" now reads version 2.** Version 1 rows stay stored, as this file requires, and are never counted or pooled. The hit rules, horizons, baselines, minimum samples and macro handling do not change.
