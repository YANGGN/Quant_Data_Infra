# Retained-data research tools

Implemented as additive, local read-only contracts at user request on
2026-09-28. Registry 2.94.0 / catalog 2.38.0 adds eight names and four explicit
successor versions, for 98 logical names and 194 contracts. The exact 2.93.0
predecessor, earlier versions and frozen Stage 5 contract remain intact.

## Agent examples

Send each envelope separately to `bin/quant-data-tools call` on standard input,
using the [shared WSL launcher](../LOCAL_AGENT_TOOLS.md). Dates are examples,
not a promise of current coverage. Replace transcript capture IDs with IDs
returned by `company.get_transcript_history@1.0.0`.

### company.get_earnings_calendar@2.0.0

```json
{"api_version":"1.0","tool":"company.get_earnings_calendar","tool_version":"2.0.0","arguments":{"symbols":["AAPL"],"start_date":"2026-09-01","end_date":"2026-09-28"}}
```

### company.get_earnings_setup@2.0.0

```json
{"api_version":"1.0","tool":"company.get_earnings_setup","tool_version":"2.0.0","arguments":{"symbol":"AAPL","start_date":"2026-09-01","end_date":"2026-09-28"}}
```

### company.get_consensus_history@2.0.0

```json
{"api_version":"1.0","tool":"company.get_consensus_history","tool_version":"2.0.0","arguments":{"symbol":"AAPL","start_date":"2026-01-01","end_date":"2027-12-31","period":"quarter"}}
```

### company.get_estimate_revisions@2.0.0

```json
{"api_version":"1.0","tool":"company.get_estimate_revisions","tool_version":"2.0.0","arguments":{"symbol":"AAPL","start_date":"2026-01-01","end_date":"2027-12-31","period":"quarter"}}
```

### options.compare_implied_realized@1.0.0

```json
{"api_version":"1.0","tool":"options.compare_implied_realized","tool_version":"1.0.0","arguments":{"symbol":"SPY","start_date":"2026-09-01","end_date":"2026-09-28"}}
```

### company.compare_transcripts@1.0.0

```json
{"api_version":"1.0","tool":"company.compare_transcripts","tool_version":"1.0.0","arguments":{"symbol":"AAPL","before_capture_id":"equibles_transcript_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","after_capture_id":"equibles_transcript_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
```

### company.screen_fundamentals@1.0.0

```json
{"api_version":"1.0","tool":"company.screen_fundamentals","tool_version":"1.0.0","arguments":{"symbols":["AAPL"],"criteria":[{"metric":"netmargin","minimum":0}],"rank_by":"netmargin"}}
```

### research.get_watchlist_changes@1.0.0

```json
{"api_version":"1.0","tool":"research.get_watchlist_changes","tool_version":"1.0.0","arguments":{"symbols":["AAPL"],"since":"2026-09-21T00:00:00Z","as_of":"2026-09-28T00:00:00Z"}}
```

### research.get_event_response_distribution@1.0.0

```json
{"api_version":"1.0","tool":"research.get_event_response_distribution","tool_version":"1.0.0","arguments":{"symbol":"AAPL","start_date":"2026-09-01","end_date":"2026-09-28"}}
```

### market.get_breadth@1.0.0

```json
{"api_version":"1.0","tool":"market.get_breadth","tool_version":"1.0.0","arguments":{"symbols":["SPY","QQQ"],"start_date":"2026-09-01","end_date":"2026-09-28"}}
```

### data.get_research_coverage@1.0.0

```json
{"api_version":"1.0","tool":"data.get_research_coverage","tool_version":"1.0.0","arguments":{"symbols":["AAPL"],"start_date":"2026-09-01","end_date":"2026-09-28"}}
```

### data.get_collection_plan@1.0.0

```json
{"api_version":"1.0","tool":"data.get_collection_plan","tool_version":"1.0.0","arguments":{"limit":100}}
```

## Shared behavior

- Optional timezone-aware `as_of` uses the host clock when omitted and cannot
  exceed it. It limits local capture/availability, not historical public release.
- Dates are inclusive. General spans are at most 366 calendar days; consensus
  target-period spans at most 3,661 days. Those target dates are separate from
  capture dates. `since` is exclusive; the effective cutoff is inclusive.
- Symbol cohorts are explicit: maximum 20 normally, 10 for watchlist changes,
  50 for breadth. Inputs reject duplicate symbols and caller SQL/paths.
- Outputs have at most 2,000 typed records, no series, and an 8 MiB response cap.
  Limits fail explicitly; there is no hidden provider request or silent sample
  truncation. The existing host deadline and cancellation remain in force.
- Nested record fields use JSON strings with `_json` suffixes. Preserve source
  capture/version IDs, source dictionaries, receipt, research contract, and
  warnings. Cross-store reads are not one atomic snapshot.
- Options use only Theta's optional `data/options.sqlite`, covering SPY, QQQ,
  IWM, DIA and the eleven sector ETFs. Missing options never use Alpaca.
- U.S. cash-session calculations use the existing 2024–2026 calendar. Broader
  date coverage is not asserted. Breadth is an explicit cohort, not a historical
  index-membership backtest. Source adjustment vintages may differ; preserve
  price audit and provenance warnings.

## Feature semantics

**Earnings:** Latest eligible FMP capture membership per endpoint/symbol is
read coherently. A non-null EPS or revenue actual (including zero) marks a
reported event; otherwise a saved date is unconfirmed. Announcement dates and
consensus fiscal targets are never joined by guessing. The briefing includes
independent prior drafts, recent tagged news, observed prices and a saved
forward-P/E publication only if that publication's cutoff is eligible. Context
records expose whether security identities match or remain unestablished.

**Consensus:** Up to 128 eligible saved captures and 4,000 source rows / 4 MiB
per analyst read. A recent `since` limits capture history and retains one
preceding capture for a baseline. Repeated capture membership and changed value
versions remain distinguishable. Revisions compare adjacent same-identity,
same-target, same-period rows. Unknown/changed currency suppresses numeric
changes; EPS also needs a known matching estimate basis. Percent change divides
by the absolute baseline; zero baselines have no percentage. Raw values and
exclusions remain visible. Analyst counts do not establish a fixed analyst
population. History may include bounded latest grades and target context, with
explicit omission counts beyond 20 rows per context endpoint.

**Implied versus realized:** `horizon_days` is 7, 30 (default), or 90 calendar
days. Realized volatility uses complete trailing cash sessions, log returns,
sample standard deviation (ddof 1) and square-root-of-252 annualization. A close
at/before the lower bound supplies the first return. Missing sessions or
nonpositive prices suppress the calculation. IV/RV ratios are null when RV is
zero. Spread percentiles use only earlier comparable rows in the requested
sample. IV is a selected-anchor estimate; this trailing comparison is not a
forward variance risk premium or a trading recommendation.

**Transcript comparison:** Select two distinct calls for the same retained
security, in chronological order. Uses existing saved draft sections and their
original turn references; no new model extraction. Exact normalized labels and
periods determine matches. Guidance point/range deltas also require matching
units, qualifiers, schema and acceptable automatic quality. Human review and
automatic assessment remain separate. Omission means absence from a saved
summary, not proof management stopped discussing something. Citation excerpts
are capped at 1,200 characters and 40 cited turns per call; truncation is marked.

**Fundamentals:** Sharadar ARQ is the default; ARY/ART/MRQ/MRY/MRT remain
separate. Latest eligible report rows and source-native metrics are retained.
Revenue growth requires an exact prior calendar-year period, compatible report
span and currency, and positive prior revenue. Criteria are transparent
minimum/maximum bounds; missing values fail a criterion. Ranks are competition
ranks across supplied peers with the same dimension, period and currency;
ties share a rank, a singleton has no percentile. Optional `calendar_date`
requires that selected period. Optional forward-P/E and EPS proxies and a
21-observed-session price return require an evidenced cross-source instrument
link. Proxy/price ranks also share the metric's observation date. These optional
metrics retain their distinct meanings and source dates; price-window calendar
completeness is not asserted for the observed-session return.

**Watchlist changes:** Review at most 31 days. Stable IDs bind the symbol,
domain and source version so overlapping reviews can deduplicate. Company
changes use new capture/analysis availability. News scans are bounded to 2,000
retained articles per symbol and fail if incomplete. Fundamentals expose latest
visible versions in the chosen dimension (at most 100 observations), not every
intermediate revision. Prices/options use the review date window plus seven
calendar days, so older historical corrections outside that window are not
claimed. Every requested domain reports coverage or an explicit gap.

**Event responses:** Select reported earnings for one symbol, or an exact
`event_name` with `event_source: "macro"`. Maximum 200 events and five horizons
of 1–20 sessions. Date-only and during/after-open events use the next full
session; a timezone-aware pre-open release can use that session. Timezone-
unverified timestamps use their source date and the next session,
with that assumption explicitly labeled; no intraday timezone is inferred.
Returns are split-adjusted closes excluding distributions; gaps and event/price identity
mismatches exclude a window. Volume compares the response-window average with
20 prior sessions. Available ETF options compare baseline and final-session
ATM IV30. Summaries include counts, exclusions, mean/median, positive fraction
and lower-order-statistic quantiles. Overlapping windows are counted. This is
retrospective descriptive analysis, not causal or benchmark-adjusted returns.

**Breadth:** Moving-average windows 2–252 (up to three); high/low windows
2–252; leadership windows 1–252. Defaults are 20/50/200, 252 and 20 respectively.
Each measure reports its own eligible denominator. Prior-window highs/lows
exclude the current close. Leaders are the top quintile including ties;
persistence counts consecutive eligible sessions within the loaded sample.
A missing constituent does not become a flat return or a negative vote.

## Supporting tools

**Coverage:** Retained ranges, capture freshness, identities, counts, supported
calendar gaps and presence of workflow inputs. `inputs_available` is not a
quality certification. Ambiguous company identities are explicit. Structured
draft counts do not imply approval. News coverage refers to the publication-date
window; fundamentals report up to 100 eligible periods and expose truncation.

**Collection plan:** Reads the host's fixed, private Equibles queue/account
files and an existing daily receipt if present. Optional `date` is today through
seven days ahead; `limit` is 1–500 (default 100), with explicit truncation.
Reports persisted due dates, phase, lane, blocking/backoff and saved account
quota. Queue eligibility is separate from quota availability. An order hint
assumes one request per task; actual work can take multiple requests. Normal
execution may reclassify the queue using the earnings calendar. Configured
02:00 America/Toronto slots do not prove live timer activation. No requests are
reserved or executed and no state is written.

## Validation scope

Focused offline tests cover calculations, capture membership, cutoff exclusion,
identity/basis gaps, citations, queue projection, read-only fingerprints, public
CLI calls, discovery/version selection, exact predecessor preservation and
reproducible generated contracts. Fixtures use temporary stores; live providers,
operational collection and deployment are outside this change.

Recorded outcome: all 27 new tests and 55 existing reader/platform checks pass
across the focused run and correction rerun. Two older assertions in
`test_sharadar_company_tools` and `test_forward_pe_tools` still compare historical
migration inventories to the current inventory; both already fail against the
saved starting registry. This change leaves migration declarations unchanged.
The frozen v1 catalog, exact 2.93 predecessor, generated-artifact check and real
launcher discovery are verified. No full-suite or live-provider claim is made.
