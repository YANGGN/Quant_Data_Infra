# Theta historical options: access findings, storage proposal and implementation plan

> Implementation update: the later user instruction approved compact source
> disposal and SPY-first implementation/fetch. [ADR 0013](../adr/0013-optional-options-store-and-compact-retention.md)
> and the [implementation receipt](THETA_SPY_IMPLEMENTATION_2026-09-24.md)
> supersede the planning-only/retention restrictions below for this exact scope.
> Other ETFs and the broad stock universe remain future work.


Date: 2026-09-24
Status: Draft for implementation; live API connectivity verified; no options database created.

Current direction, updated 2026-09-24: the user chose volatility signals and
stock/ETF ranking, then Tier 1 index ETF options with a compact daily strike
grid. The user now asks to add round-number/activity selections and summaries
of contracts omitted from detailed storage. The
[Tier 1 proposal](THETA_TIER1_ETF_OPTIONS_PLAN_2026-09-24.md) supersedes this
document's initial acquisition and pilot sequence. It proposes up to 300
detailed contracts per ETF/session plus broad-chain EOD/OI summaries.
The fifth-store design below remains the foundation. Compact aggregate-input
retention is a proposed scoped policy change: this document's permanent raw
retention remains the default until that decision is recorded. No broader
inputs or existing evidence may be silently dropped to fit a storage estimate.

## 1. Outcome and scope

The configured `THETA_DATA_API` credential successfully authenticated to Theta.
Ten bounded data requests succeeded from Ubuntu/WSL through the official
`thetadata==1.0.11` Python SDK. Theta Terminal and Java were unnecessary for
this query-based workflow. The direct client requires Python 3.12 or later;
an isolated Python 3.12.3 environment is installed at
`.local/theta-discovery-20260924/venv`.
The SDK's missing `python-dotenv` dependency was installed separately and must
be pinned alongside it. Production Python and dependencies were not changed.
See [Theta's direct-client documentation](https://thetadata.net/docs/Python-Library/Getting-Started.html).

The current request authorizes access verification, necessary local setup,
data investigation and a draft implementation plan. It does not start the
full-universe acquisition, create a recurring unit, migrate existing stores,
or purchase a higher subscription.

Assumption pending user preference: daily end-of-day data across all available
calls, puts, strikes and expirations, plus daily open interest and provider
Greeks/IV when available. There is no initial DTE or moneyness restriction.
Intraday bars and every-tick history require their own measured size and
workload decision.

### Measured access evidence

The probe performed one authentication and ten data requests, zero retries,
in approximately 20 seconds. It retained 397,479 bytes of SDK response
envelopes. Limits were ten data requests, five minutes and 20 MiB of serialized
response envelopes. No canonical SQLite database was opened.

| Request family | Scope | Result |
| --- | --- | --- |
| Expiration catalogue | AAPL / SPY | 837 / 2,131 expiration entries |
| EOD prices and quotes | 2026-09-22, expiry 2026-09-25, all strikes/both rights | AAPL 158 rows; SPY 342 rows |
| Open interest | AAPL, 2026-09-22, expiry 2026-09-25 | 158 rows |
| EOD Greeks, methodology version 1 | AAPL, same session/expiry | 158 rows |
| Quote-date catalogue | AAPL expiry 2026-09-23; SPY expiry 2026-09-22 | 11 dates each |
| Additional EOD expirations | AAPL expiry 2026-09-23; SPY expiry 2026-09-22 | 96 / 314 rows |

Private evidence: [probe results](../../.local/theta-discovery-20260924/result.json)
and [offline assessment](../../.local/theta-discovery-20260924/assessment.json).
Serialized provider response messages and inspection JSON are retained beside
them. These are discovery artifacts, not canonical published facts.
The saved artifacts were checked for the configured API key; none contained it.
Authentication tokens and account identity were not persisted.

**History limit remains unverified.** The authentication response returned
numeric options-subscription code `2`, without a named tier. The probe did
not resolve that enum and conservatively sampled recent expirations; it made
no oldest-date price request. Expiration catalogues reach 2012-06-01, but
catalogue visibility does not prove entitlement to prices from that date.

Theta's [subscription documentation](https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html)
currently lists options Value from 2020-01-01, Standard from 2016-01-01,
and Pro from 2012-06-01. Those are documentation limits, not a verified
earliest-access result for this account or every symbol. The implementation
preflight must verify the account floor with small explicit historical requests.

## 2. What the data actually contains

The live EOD response has contract root/symbol, expiration, strike and right;
report creation and last-trade timestamps; OHLC, volume and trade count; bid
and ask prices/sizes, exchange IDs and condition codes. It does not include
open interest. The EOD quote is the provider's closing-report NBBO, rather
than a guaranteed snapshot at exactly 16:00.
Theta documents report generation around 17:15 New York time; observed report
timestamps were approximately 17:15–17:16.
[EOD reference](https://thetadata.net/docs/operations_python/option_history_eod.html).

In the first AAPL sample, 74 of 158 contracts had zero trades and zero OHLC;
106 of 342 SPY contracts did too. Many still had usable quotes. Preserve the
raw zeros, while canonical trade-price fields carry a `no_eligible_trade`
state instead of presenting zero as an executed closing price. A zero bid
does not itself mean the contract is worthless; preserve it with quote quality.

Open interest supplies its own reporting timestamp. It normally arrives around
06:30 ET and describes the end of the preceding trading session. Store both
the report date/time and the position's effective trading date. Missing reports
must not become zero. [Open-interest reference](https://thetadata.net/docs/operations_python/option_history_open_interest.html).

The Greeks sample includes IV, Greeks, IV error and underlying price/timestamp.
These are provider model outputs and must remain distinguishable from observed
prices. The probe explicitly selected methodology `version=1`; this is not a
decision to adopt it for production. Production must pin the chosen methodology,
rate/dividend inputs and underlying-price basis. A mutable `latest` default
must not silently change an existing series.
[Greeks reference](https://thetadata.net/docs/operations_python/option_history_greeks_eod.html).

The SDK converts encoded prices to binary floats: observed values included
`55.550000000000004`. Canonical identity and price conversion must use
the protobuf mantissa/scale before this conversion, not float-based string keys.
The response does not establish contract deliverable, multiplier, exercise
style or settlement style. Unknown metadata must remain unknown, especially
for adjusted contracts and ticker changes.

## 3. Dedicated options.sqlite ownership

Use `data/options.sqlite` as the authoritative store for the new Theta
historical option datasets. Keep market instrument identity in `market.sqlite`;
reference its stable instrument IDs through a dated, evidence-backed mapping.
Keep the existing Alpaca datasets and their consumers in `market.sqlite`.
Moving or retiring them is separate work.

This is an explicit extension of the current topology, which hard-codes four
stores in `quant_data/stores.py`, registry validation and shared consumers.
The user's requested dedicated database is the design direction. Implementation
must record a narrowly scoped successor to ADR 0001 and update architecture,
registry/schema, routing, migrations, backup/restore and health behavior
together. Do not merely create an unregistered fifth file.

The new store has its own migration ledger, dataset registrations, write lock,
WAL, integrity checks and recovery boundary. Missing options storage should
disable only dependent options operations. Existing four-store tools continue
to work without requiring an options database. Applied migration files and
historical registry snapshots remain byte-identical.

### Proposed logical tables

Names below are proposed, not migration allocations.

| Family | Purpose and main key |
| --- | --- |
| Shared control-plane tables | Existing migration, dataset, ingestion, artifact, snapshot and quality conventions |
| `option_underlying_mappings` | Stable market instrument ID, Theta root, mapping validity/knowledge dates, source evidence, resolution status |
| `option_contracts` / `option_contract_metadata_versions` | Compact surrogate ID; provider root, expiration, exact strike, call/put and contract segment; separately versioned deliverable/settlement metadata |
| `theta_response_chunks` and artifact membership | Content-addressed compressed provider payloads and decoding metadata, bound to sanitized request identity and capture |
| `option_eod_versions` | Contract, session date, provider/feed, report definition and immutable content version; prices, quote fields and quality states |
| `option_eod_current` | Small keyed pointers to latest versions; same fact dimensions as the version table |
| `option_open_interest_versions` / current pointers | Contract, report timestamp, effective session, OI and availability; separate from same-date EOD |
| `option_greeks_eod_versions` / current pointers | Contract/session plus methodology and input identity; IV, errors, Greeks and underlying evidence |
| `option_daily_coverage` | Underlying/session/endpoint and requested scope, expected versus received contracts where knowable, complete/partial/no-data status |
| `option_universe_snapshots` and members | Exact acquisition universe and identity mapping used for each run |

No table per ticker or year. Use integer surrogate contract/version IDs for
large fact tables; retain stable external IDs and natural-key constraints.
Candidate indexes are `(contract_id, session_date)`,
`(underlying_id, session_date, expiration, right)` through appropriate
joins/projections, and cutoff/version lookups. Benchmark before retaining
expensive secondary indexes or duplicating full current rows.

Represent expiration/session as dates. Store real timestamps in UTC with
source-zone/precision metadata; keep date-only labels as dates. Missing
trade timestamps represented by provider midnight sentinels need a documented
missingness rule, not an assertion of midnight trading.

Store strikes with exact decimal identity, preferably integer thousandths
only after validating representability; reject rather than round unsupported
precision. Preserve source price mantissa/scale and normalize exactly.
Do not assume every option represents 100 shares. Unknown/nonstandard
deliverables can be retained but remain analytically excluded until resolved.

### Evidence, revisions and time

Retain exact compressed message payload bytes plus their compression/header
metadata, chunk ordering and hash. The discovery `.protobuf` files are
SDK-reserialized envelopes, not a claim of original wire-byte capture.
A production transport must establish and test the evidence boundary before
dataframe conversion. Coalesce messages into bounded per-request containers;
the discovery's many small files are not the production storage layout.
Compressed evidence BLOBs in SQLite keep the default backup self-contained;
include their cost in the benchmark.

Use separate semantic identities for facts and capture scopes. A repeated
unchanged unit creates zero canonical/control-plane rows. Changed source
facts append versions; partial responses never replace complete coverage or
tombstone missing contracts. Operational attempt receipts live outside
canonical semantic data.

Preserve provider event/report time, source-publication evidence when known,
local capture time and ingestion time separately. Backfilled data acquired
today cannot pretend to have been locally known in 2016. Distinguish
retrospective historical market observations from strict stored-as-of views;
do not claim original-vintage correction history that Theta does not supply.
Greeks and OI must meet their own availability cutoffs before composition.

SQLite remains authoritative. Any later Parquet/DuckDB acceleration is derived
and follows ADR 0007's benchmark and equivalence gate. Full-universe tick data
may require a different storage decision; do not promise it fits this daily
design.

## 4. Universe, coverage and acquisition

Freeze a read-only snapshot of the existing selected equity universe and
retained ETFs using established market readers. The September 11 recorded
price cohort had 2,248 equities plus 111 ETF/index instruments, but this
investigation did not recount live canonical membership. Report the actual
equity, ETF and index counts at execution; index options are an explicit
separate category, not automatically inferred from index-price tickers.

Translate dated provider roots, including symbol changes, rather than assuming
FMP ticker strings equal Theta roots. Discover expired contracts, not just
today's chain. Explicitly classify no listed options, not yet listed,
delisted, unmapped root, subscription restriction, missing provider data and
failed request. Today's selected universe is survivorship-biased for historical
research; a point-in-time changing universe is a separate requirement.

Index option roots can encode settlement differences, for example SPX versus
SPXW. Preserve root distinctions and map to the underlying separately.
[Theta symbology](https://thetadata.net/docs/Articles/Data-And-Requests/Symbology.html).

For each root and endpoint, derive the permitted date set from confirmed
entitlement, provider availability, instrument/contract history and the fixed
run cutoff. Do not substitute the earliest expiration for the earliest
observation date. Historical catalogues are acquisition aids, not proof that
every listed item was knowable at an earlier strategy cutoff.

Use deterministic work units:
`provider + endpoint + root + expiration/all-expiry scope + date window +
request options + schema/methodology version`.
Prefer bounded symbol/day all-expiration requests when supported by the
account; otherwise partition by expiration and bounded date window. Validate
wildcards and multi-day limits in the pilot. For EOD Greeks, all-expiration
wildcards are documented as day-by-day requests.

Theta's [concurrency documentation](https://thetadata.net/docs/Articles/Data-And-Requests/Concurrent-Requests.html)
lists 2/4/8 concurrent requests for Value/Standard/Pro, shared account-wide.
Start conservatively and use one account-wide limiter, with limits verified
for the direct client. Fetch outside locks; publish through one writer into
short atomic batches. Use backpressure so downloaded responses cannot outrun
publication or exhaust disk.

Reuse queue design principles from `collection_queue.py`, but do not fake
gRPC status and protobuf bodies as its current HTTP/JSON response contract.
Add a typed Theta transport adapter with deadlines, compressed and decompressed
byte limits, row caps and final stream-status verification. A partially
received stream is incomplete even if earlier rows parsed.

Requests progress through planned, attempted, response-complete, validated,
published and verified states. A crash after response retention resumes local
publication without a network refetch. Record an attempted unit before sending;
an uncertain network outcome stops that unit for reconciliation. No hidden
SDK retries, authentication refresh loops or replays of finished work.
Schema/auth/entitlement failures stop the affected workload.

Flat files are not a historical-backfill shortcut: the documented offering
requires Pro and exposes only the latest seven calendar days. They cover the
whole market, and do not include Greeks. Do not download broad market files
to satisfy a limited-symbol historical run.
[Flat-file constraints](https://thetadata.net/docs/Flat-Files/Getting-Started.html).

## 5. Capacity gate

Observed WSL filesystem free space was approximately **654.3 GiB**. This is
a point-in-time filesystem value, not a guarantee of Windows host capacity
or future availability. Recheck both before launch.

A transparent scenario, not a measured forecast:
2,350 underlyings × 500 contracts/day × 2,500 sessions =
2.94 billion contract-days. At an assumed 200–400 bytes per stored contract-day,
that alone is roughly 0.55–1.10 TiB, before retained source evidence, OI,
Greeks, extra indexes, WAL and backup headroom. The contract average and
historical optionability are unknown; a measured pilot must replace them.

The two one-expiration samples are not representative whole-chain sizing
evidence. Benchmark liquid ETFs, large/medium/small optionable equities,
a recent IPO and an adjusted/root-change case across old, middle and recent
history. Measure full-chain rows per day, compressed payload bytes, actual
SQLite pages and indexes, OI/Greeks overhead, ingest throughput and query
latency. Report an estimate range and a hard disk reserve.

If the projected daily corpus does not fit with recovery space, present the
measured choices: more storage, a deliberately smaller first tranche, or a
separate analytical-storage design decision. Do not silently trim strikes,
expiration horizons, delisted contracts or history.

## 6. Implementation sequence and acceptance

1. **Settle the data contract and topology.** Record the requested fifth
   options store, daily-first scope, instrument ownership and retained Alpaca
   behavior. Confirm granularity, named subscription and precise history floor.
   Produce an exact current-universe manifest with unresolved mappings.
   Update the applicable ADR/architecture and registry compatibility plan;
   keep migration IDs unallocated until implementation.

2. **Build the provider adapter and offline parser.** Pin the isolated runtime
   and dependency lock. Reuse the named credential reader for
   `THETA_DATA_API`; pass the key explicitly and prevent loading unrelated
   `.env` variables. Suppress/redact the SDK's authentication logs, which can
   contain session/account details. Capture typed raw payloads, decode exact
   numeric representations, and enforce byte/time/final-status bounds.

3. **Implement the registered store and publisher.** Add the options store
   declaration and dataset ownership, allocate new store-local migrations,
   update routing/health/backup/restore, and implement append-only versions,
   no-write replay, coverage and quality handling. Build only against explicit
   empty temporary roots initially. Never edit an applied migration or
   repurpose the old Alpaca option tables.

4. **Run a finite representative pilot after its manifest is agreed.**
   A proposed starting budget is at most 12 underlyings, 12 trading sessions
   each spread across the permitted history, three data families, and at most
   600 provider requests including discovery, bounded to two hours and
   2 GiB retained responses. It must count actual expiration partitions
   before launch; if the manifest exceeds the cap, reduce the explicit pilot
   sample rather than silently add requests. No automatic retries.
   Include explicit tiny oldest-date checks. Measure capacity and validate
   that EOD/OI/Greeks timestamps align under the declared semantics.

5. **Prepare and execute the full backfill in finite batches.**
   Freeze the final root/date/request manifest, byte/storage projection,
   concurrency, duration, end-date and no-repeat rules. Initialize
   `data/options.sqlite` only at this authorized implementation step.
   Process deterministic resumable chunks from earliest supported history
   to the cutoff. Reconcile published scope and counts after each tranche,
   with a complete/partial/not-optionable coverage report per underlying.

6. **Expose bounded read tools and consider refresh scheduling.**
   Add explicit historical-chain and contract-series readers with cutoff,
   quality, source and coverage metadata; no caller SQL or file paths.
   Daily incremental collection is a separate proposed activation decision
   after the backfill is accepted. Do not install/start a recurring unit as
   part of this draft or repurpose an existing job.

### Validation impact map

Changed behavior at implementation: a fifth registered store plus Theta
acquisition/publication. Affected boundaries: core store enumeration, registry
ownership, migration validation, locks, health and backup/restore; existing
market/macro/company/news callers and options readers.

Focused fixtures must cover exact numeric decoding, zero-trade/valid-quote
days, missing OI, previous-session OI availability, DST/half days, expired and
adjusted contracts, ticker/root ambiguity, unverified metadata, Greeks
methodology changes, out-of-scope responses, interrupted streams, no-data
versus error, request/byte/deadline budgets, crash resume without refetch,
zero-write replay, correction versions, missing-store isolation and restore.

A shared five-store routing/validator change is a concrete core-invariant
trigger for the full offline suite, plus direct-consumer regressions and
independent verification on a stable integrated baseline. No such implementation
has happened in this discovery; no production safety claim follows from the
ten successful provider samples.

## 7. Current handoff

Completed: isolated direct client installation, one successful authentication,
ten successful small requests, real response/schema inspection, credential
non-disclosure check, and this draft.

Not performed: earliest-price access verification, full-universe mapping,
size/throughput benchmark, database creation, migrations, canonical writes,
scheduler changes, provider retries or full backfill. Named subscription and
granularity preference remain pending. Proceeding with the plan assumes EOD
first; that assumption does not authorize execution of the proposed pilot.
