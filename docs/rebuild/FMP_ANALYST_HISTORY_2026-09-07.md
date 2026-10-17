# FMP analyst history population — September 7, 2026

## Authority and scope
The user's current instruction authorizes adding AAPL/MSFT analyst recommendations
and price targets, backfilling all available related history, then populating the
existing single-name equity universe. This supersedes the September 6 analyst
deferral for this finite population. Existing timers and subscriptions are unchanged.

The completed pilot used 20 single-attempt FMP requests (18 valid JSON responses,
two invalid-JSON TipRanks responses), followed by two explicitly scoped legacy
price-target requests, both successful. No retries. Private attempt/result ledgers
are under `.local/fmp-analyst-history-20260907/`. Responses use the existing
`company-market-refresh/blobs` content-addressed evidence store. The two TipRanks
failures lack response-status/reference metadata because the initial helper parsed
before recording that metadata; they are excluded from publication.

The subsequent universe phase freezes the retained 519 equity instruments, resolves
only existing issuer/CIK identities using one SEC ticker discovery, and excludes
AAPL/MSFT (pilot evidence is reused). Aggregate limits are 15,000 attempts including
SEC, 1 GiB response bytes, two hours cumulative execution, 1 MiB per FMP response (8 MiB for the single SEC discovery),
30 seconds total wall time per request, at least 0.4 seconds between starts, and no retry or
redirect. Attempt records are durable before each request. Annual/quarter estimates
use 100 rows and at most 10 pages each, stopping on short, empty, repeated, or
non-progressing pages. Each remaining endpoint gets one request per eligible symbol:
grades, grades-historical, grades-consensus, price-target-consensus,
price-target-summary, earnings, and legacy price-target. Unsupported or absent
coverage is recorded, never fabricated. 401/403/429 stop the batch; 402 disables
that endpoint for the remaining batch. No TipRanks repeat.

## Storage and time
Company migration `company:0009_fmp_analyst_history` owns immutable captures,
versioned source-native observations, and capture membership. Existing company
datasets, public tools, and other stores keep their contracts. A bounded local
repository reader provides current or cutoff-constrained records.

Forecast target-period dates, dated source events, and undated current aggregates
are distinct. Availability is the actual local capture timestamp. Earlier fiscal
target periods do not establish historical estimate vintages. Date-only values stay
date-only. Currency and accounting basis remain SOURCE_UNSPECIFIED when absent.
Prior retained estimate/earnings captures may be seeded with their original capture
timestamps before newer pilot evidence, without another provider call.

Raw JSON bytes and pointers are retained. Indistinguishable duplicated source events
are collapsed with an explicit warning. Grades have no stable provider event ID;
their full source event facts define identity. Missing, null, or empty recommendation
actions remain exactly as supplied with `source_recommendation_action_missing`;
firm, new grade, and event date must still be valid. Price-target events use publication,
source URL, analyst and firm identity. Forecast periods and historical rating
distributions have separate keys; changed values append versions. Earnings
lastUpdated is transport/source refresh metadata and is excluded from semantic
revision identity, while preserved in raw payloads.
When a single earnings response contains conflicting values for one event date,
the entire ambiguous date group is excluded from normalized observations with
`conflicting_earnings_dates_excluded`; all raw rows remain retained. Other dates
in that response remain publishable. No conflicting value is selected as truth,
and an ambiguous/empty group does not delete previously retained observations.
The same rule applies to conflicting analyst estimates within a single response:
the entire ambiguous forecast-period group is excluded with
`conflicting_estimate_periods_excluded`, preserving its raw evidence and any
previously retained observation. Annual and quarterly periods remain distinct.

Exact semantic replay, including overlapping pages whose latest records originate
from different captures, produces zero canonical change. Corrections require
strictly increasing local capture time, normalized to fixed-microsecond UTC for
exact ordering while preserving the source event timestamp. Empty responses do not delete history.
Publication reparses prepared bytes and validates issuer scope before writes,
under the established physical company lock and ingestion coordinator.

## Validation and activation
Shared migration/registry changes require the full offline suite and a fresh
independent reviewer on the stable integrated change before canonical migration
or publication. Applied migration bytes remain unchanged. Activation receipts,
coverage, request accounting, gaps and replay checks are recorded after execution.


## Pilot activation evidence

Company migration `company:0009_fmp_analyst_history` was applied once at
`2026-09-07T06:50:51.596529Z` under the exact registry 2.73 predecessor projection.
Its recorded SHA-256 is
`a9a2bd45d22ef17a4c2af3ca24b89c4cc95bcbb574fa43f4a942bd4e77453dc4`.
The jointly reviewed registry 2.74 baseline passed all 1,668 unique offline test
cases across reconciled executions, with no missing or skipped cases. Independent
review accepted the preserved original execution receipts, affected-case rechecks,
source hashes, and both company migration allocations before activation.
This is a reconciled full-inventory pass, not one uninterrupted successful run.

All 26 retained pilot captures published successfully: 4,140 observation versions,
4,126 distinct observations, and 4,366 capture memberships. Existing non-analyst
company-table counts were unchanged across publication. Twenty retained-response
replays produced zero canonical change: every company-table count and the database
SHA-256, size, and modification time matched before and after. All 16 bounded
reader/cutoff checks passed, integrity was `ok`, and foreign-key checks were empty.

| Stored history | AAPL | MSFT |
| --- | --- | --- |
| Annual estimate target periods | 28; 1996-09-27 to 2030-09-27 | 34; 1998-06-30 to 2031-06-30 |
| Quarterly estimate target periods | 117; 1995-06-30 to 2028-09-27 | 135; 1996-12-31 to 2030-06-30 |
| Recommendation events | 1,793; from 2012-02-08 | 969; from 2012-03-16 |
| Monthly recommendation distributions | 93; from 2018-12-01 | 91; from 2019-01-01 |
| Individual price-target events | 259; from 2021-06-11 | 271; from 2021-04-22 |
| Earnings events/estimates | 165; from 1985-09-30 | 165; from 1985-09-30 |
| Current recommendation consensus, target consensus, target summary | One of each | One of each |

Fourteen AAPL estimate observations retain two locally captured versions. Earlier
fiscal target periods in this table do not establish historical estimate-vintage
availability. Consensus aggregates remain current snapshots with actual local
capture timestamps.

Private execution evidence is retained under `.local/fmp-analyst-history-20260907/`:
`activation-gate.json`, `pilot-activation.json`, and `pilot-verification.json`.
The initial private wrapper stopped after successful migration because it expected
only newly applied IDs from an API that returns the full migration list. The
immutable diagnostic confirmed head 0009 and zero analyst rows; corrected private
orchestration resumed publication without another migration or provider call.
The original diagnostic and failed wrapper log remain retained.

The shared registry/generator and shared operating documents are coordinated with
the transcript task. Its company 0010 activation follows this completed pilot;
the retained universe publication follows its quiet post-write handoff.

## Universe population evidence

The retained-only universe publication completed on September 7, 2026 after the
transcript task returned its audited company window. It made no migration or
provider call. Together with the pilot, `data/company.sqlite` now holds **516 of 519
retained equity symbols**, representing 513 issuers, **397,285 distinct observations**,
**397,299 observation versions**, 4,971 immutable captures, and 397,525 capture memberships.

| Dataset | Symbols with data | Distinct observations | Earliest source period/event |
| --- | ---: | ---: | --- |
| Annual analyst estimates | 516 | 13,595 | 1993-12-31 |
| Quarterly analyst estimates | 516 | 53,353 | 1992-05-31 |
| Earnings events/estimates | 516 | 64,297 | 1985-08-31 |
| Recommendation events | 515 | 181,149 | 2011-12-08 |
| Current recommendation consensus | 515 | 515 | Current snapshot |
| Monthly recommendation distributions | 516 | 44,054 | 2018-12-01 |
| Individual price-target events | 512 | 39,298 | 2021-04-12 |
| Current price-target consensus | 512 | 512 | Current snapshot |
| Current price-target summary | 512 | 512 | Current snapshot |

AVB, EA, and EQR were excluded because the retained identity evidence did not confirm
their ticker-to-existing-issuer mappings. FMP returned empty recommendation events
and consensus for ERIE, and empty price-target events/consensus/summary for
BF-B, ERIE, L, and NWS. These are explicit gaps; no identities, values, or history were
invented. Recommendation events and current consensus cover 515 symbols; historical
distributions cover 516. Price-target datasets cover 512 symbols.

The universe capture made 4,962 requests: 4,961 FMP requests and one SEC ticker
discovery. All returned HTTP 200, totaling 116,585,138 bytes (111.18 MiB) in
3,707.60 seconds of the bounded capture unit. With 22 pilot FMP requests, the task
made 4,984 external requests total, with no retries. The two invalid-JSON pilot
TipRanks responses remain excluded. Estimate pagination exhausted returned pages;
two additional empty pages (AIZ, BXP) were marked non-progressing and added no rows.

The retained-only publication processed 4,959 eligible responses: 4,945 succeeded and
14 empty responses made no canonical change. It took 1,125.66 seconds. There were
zero parse errors and zero foreign-key violations. The company 0001–0010 ledger and
every non-analyst company-table count were unchanged, including the transcript data.

The final immutable audit passed full integrity and foreign-key checks after the
bulk write. Thirteen representative retained-response replays, including estimates,
earnings, recommendations and targets, then produced zero canonical change.
Every company-table count, migration row, and the complete database SHA-256, size,
and modification time remained identical. This byte-identical proof preserves the
integrity result without repeating the expensive scan. Pilot verification separately
passed 20 replays and 16 reader/cutoff checks.

Quality warnings remain attached to captures: 65 earnings responses and two TXT
estimate responses contain conflicting date/period groups; those whole groups were
excluded while original raw rows and any prior observations were preserved. One
META recommendation has its missing action preserved. Exact duplicated source
rows are collapsed with a warning. Missing analyst names, currency, and estimate
basis remain unspecified rather than inferred.

The earliest local capture is `2026-09-05T03:48:28.495245Z`. Forecast target periods
reach 1992 across the universe, but they do not provide 1992 estimate vintages. The
local version history records actual retained captures; undated consensus is a
current snapshot. No daily FMP analyst refresh was installed or triggered.

The data lives in `company_fmp_analyst_captures`,
`company_fmp_analyst_observation_versions`, and
`company_fmp_analyst_capture_membership`. The bounded internal reader is
`quant_data.company.fmp_analyst_history.read_analyst_history`; it supports endpoint,
cutoff, version inclusion, and pagination. Existing public consensus-tool bindings
were not repointed.

Final private receipts: `universe-publication.json`, `universe-verification.json`,
and `completion-receipt.json` under `.local/fmp-analyst-history-20260907/`.
The finite population is complete with the stated gaps. A repeat, a new identity
mapping population, or a recurring FMP refresh requires separately scoped authority.
