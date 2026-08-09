# Offline Market/Macro Vertical Slice

Status: Accepted

## 1. Objective

Build the smallest offline system that proves the recovered architecture and
the proposed data contracts end to end:

1. initialize four independent temporary SQLite stores;
2. ingest controlled market daily-bar and revision-aware macro fixtures;
3. retain immutable evidence and append-only canonical versions;
4. answer deterministic `latest`, `as_of`, and `first_release` queries;
5. expose a fixed, read-only registry subset and a minimal read-only dashboard;
   and
6. rebuild from empty temporary directories with the same logical result.

This is an acceptance specification. It does not claim that the slice, erased
historical DDL, or full provider adapters exist.

Related documents:

- [System architecture](../../ARCHITECTURE.md)
- [Roadmap](../../ROADMAP.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [System registry](SYSTEM_REGISTRY_SPEC.md)
- [Tool platform](TOOL_PLATFORM_SPEC.md)
- [Test strategy](TEST_STRATEGY.md)
- [ADR 0001: four operational SQLite stores](../adr/0001-four-operational-sqlite-stores.md)
- [ADR 0002: single machine-readable registry](../adr/0002-single-machine-readable-registry.md)
- [ADR 0003: three-layer data architecture](../adr/0003-three-layer-data-architecture.md)
- [ADR 0004: point-in-time availability](../adr/0004-point-in-time-availability-model.md)
- [ADR 0005: composable tool core](../adr/0005-composable-tool-core.md)

## 2. Scope boundary

### 2.1 Included

- Python 3.11 standard-library runtime.
- Four explicitly supplied SQLite paths: market, macro, company, and news.
- Shared migration ledger, dataset registry, and ingestion-run foundation in
  each store.
- Market instrument identity, dated provider identifier, current daily-price
  projection, and append-only price correction versions.
- Macro series metadata, immutable source artifacts/snapshots, snapshot scope
  and membership, and revision-aware observation versions.
- Controlled UTF-8 CSV/JSON fixtures with fixed capture values and checked
  digests.
- Read-only repositories, two fixed agent tools, and one local dashboard view.
- Strict JSON, bounded results, deterministic ordering, explicit provenance,
  and temporary-path-only tests.

### 2.2 Excluded

- Network access, provider credentials, live smoke tests, polling, scheduling,
  or backfills.
- Claims that synthetic values are genuine FMP or Philadelphia Fed data.
- Company, news, options, SEC, GDP, rates, EIA, SOMA, or analytics beyond the
  shared empty-store foundation.
- The complete 57-tool manifest, the mature portal, Charts, Algorithms, Atlas,
  Parquet export, or production promotion.
- Legacy unified runtime compatibility; the accepted rebuild requires four
  distinct stores.
- Reconstruction of unknown byte-exact migration SQL or checksums.

The slice must not create or inspect operational default database files. A
missing explicit store map is a validation error.

## 3. Recovered names versus proposed schema

`plan.md` Section 20 recovers semantic migration order and physical names, but
not historical column DDL. The slice preserves recovered names where they are
in scope without pretending that proposed columns reproduce erased SQL.

| Store | Recovered names exercised by the slice |
| --- | --- |
| Every store | `schema_migrations`, `dataset_registry`, `ingestion_runs` |
| Market | `instruments`, `instrument_identifiers`, `prices_daily`, `prices_daily_versions`, `market_price_ingestion_requests` |
| Macro | `macro_series`, `macro_releases`, `macro_source_artifacts`, `macro_source_snapshots`, `macro_observation_versions`, `macro_snapshot_scopes`, `macro_snapshot_observation_membership` |

The company and news stores contain only the shared foundation for this slice.

Migration resources are ordered, content-checksummed, and immutable after
application. Initialization:

- enables foreign keys;
- uses a bounded busy timeout;
- enables WAL for writers where supported;
- applies each resource once;
- verifies every previously applied resource checksum before proceeding;
- fails closed on store-role mismatch, missing resource, order violation, or
  checksum mismatch; and
- is idempotent on an already current store.

Historical migration numbers 0000-0031 are not reassigned to guessed SQL. A
rebuild migration ledger must label deliberate reconstruction resources as
such until exact historical migration evidence is recovered.

Readers open a server-owned path with SQLite `mode=ro` and `query_only=ON`.
No API or tool accepts a file path or SQL string.

## 4. Fixture set

All values below are synthetic. Fixture metadata sets
`test_fixture=true` and `promotable=false`. Provider-like names exist only to
exercise identity boundaries.

Each fixture has a manifest entry containing:

- fixture ID and schema version;
- ingestion-family ID, evidence and canonical dataset IDs, store role, and
  canonical request scope;
- relative fixture resource name;
- exact SHA-256 of retained bytes;
- semantic identity digest;
- fixed `captured_at`;
- expected row and missing-value counts; and
- expected normalization warnings.

Changing fixture bytes requires an explicit golden-fixture review.

### 4.1 Market base fixture

Ingestion family: `fixture.market.daily_price_import`  
Evidence dataset: `fixture.market.daily_price_evidence`  
Identity dataset: `fixture.market.instruments`  
Canonical fact dataset: `fixture.market.daily_prices`  
Provider: `fixture_fmp`  
Request scope: symbols `SPY` and `^GSPC`, raw daily bars,
`2026-07-16` through `2026-07-17`.

| Provider symbol | Asset type | Trade date | Open | High | Low | Close | Volume | Available at |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| SPY | etf | 2026-07-16 | 635.00 | 639.00 | 633.00 | 638.00 | 70000000 | 2026-07-17 |
| SPY | etf | 2026-07-17 | 638.00 | 642.00 | 637.00 | 640.00 | 65000000 | 2026-07-18 |
| ^GSPC | index | 2026-07-16 | 6300.00 | 6340.00 | 6285.00 | 6325.00 | 3100000000 | 2026-07-17 |
| ^GSPC | index | 2026-07-17 | 6325.00 | 6360.00 | 6310.00 | 6350.00 | 3000000000 | 2026-07-18 |

Every row also carries `currency=USD`, `price_variant=raw`, a fixed
offset-aware capture, and its source row number. SPY and ^GSPC receive distinct
stable `instrument_id` values; provider symbols remain in
`instrument_identifiers` and are not the permanent identity.

Validation requires:

- nonempty provider and provider symbol;
- strict ISO trade date;
- finite OHLC values;
- `low <= min(open, close)` and `high >= max(open, close)`;
- `low <= high`;
- integral nonnegative volume;
- explicit price variant and currency; and
- no conflicting duplicate natural key.

Rows are sorted canonically after validation. Source order is retained only as
lineage.

### 4.2 Market correction fixture

The correction scope contains only the SPY bar for `2026-07-17`. It changes
close from `640.00` to `640.25` and declares availability
`2026-07-20T08:30:00-04:00`. The high remains `642.00`, so OHLC invariants hold.

Acceptance behavior:

- append one `prices_daily_versions` row;
- retain the original version unchanged;
- move the `prices_daily` current projection to the new version;
- link the new version to the old version;
- preserve the correction's artifact, snapshot, run, and availability; and
- do not tombstone any ^GSPC or other SPY row because this request scope is
  partial.

### 4.3 Macro first-vintage fixture

Ingestion family: `fixture.macro.rtdsm_employ_import`  
Evidence dataset: `fixture.macro.rtdsm_employ_evidence`  
Canonical fact dataset: `fixture.macro.rtdsm_employ`  
Series ID: `fixture:philadelphia_fed_rtdsm:EMPLOY`  
Provider: `fixture_philadelphia_fed`  
Frequency: monthly  
Unit/value representation: fixture employment index, level  
Vintage and availability: date-only `2026-06-12`.

| Period start | Period end | Value | Missing reason |
| --- | --- | ---: | --- |
| 2026-01-01 | 2026-01-31 | 100.0 | null |
| 2026-02-01 | 2026-02-28 | null | source_suppressed |

The explicit missing observation is retained. It is not zero and is not omitted
from a native series result.

### 4.4 Macro revised-vintage fixture

The second vintage is date-only `2026-07-10`:

| Period start | Period end | Value | Missing reason |
| --- | --- | ---: | --- |
| 2026-01-01 | 2026-01-31 | 101.0 | null |
| 2026-02-01 | 2026-02-28 | 98.5 | null |

It appends two observation versions and snapshot memberships. It neither
updates nor deletes the first-vintage rows. Fixture metadata explicitly marks
the `2026-06-12` vintage as a defensible first release so that
`first_release` can be tested without inferring it from ingestion order.

### 4.5 Negative fixtures

Small, separate resources cover:

- a conflicting duplicate market key;
- high below close;
- negative volume;
- NaN and infinity spellings;
- a naive availability datetime;
- macro value and missing reason both populated;
- macro value and missing reason both absent;
- unit/scale conflict within one series;
- invalid period ordering; and
- a truncated/partial snapshot that is not tombstone-authoritative.

Every negative fixture fails before canonical commit.

## 5. Ingestion workflow

Market and macro importers use the same ordered state machine:

1. **Resolve explicit context.** Validate the four temporary store roles,
   dataset registration, fixture schema, and bounded request scope.
2. **Read evidence.** Read the controlled resource as bytes, verify its
   manifest digest, and decode strictly. No network fallback exists.
3. **Normalize in memory.** Parse source fields into typed staged rows while
   retaining raw values and source row numbers.
4. **Validate the whole batch.** Validate keys, time precision, units,
   duplicates, OHLC/missingness invariants, and declared completeness.
5. **Compute semantic identity.** Hash canonical request scope, normalized
   meaning-bearing rows, completeness, and normalization version.
6. **Apply the no-write gate.** On a read-only connection, detect an existing
   semantic identity. If present, return an `unchanged` receipt without opening
   an ingestion run or writer transaction.
7. **Begin one short write transaction.** Recheck semantic identity after
   obtaining the write lock, then create the run and immutable evidence.
8. **Write canonical state.** Resolve stable identities, append new/changed
   versions, update only current projections, and write snapshot membership.
9. **Commit atomically.** Mark success and advance the dataset checkpoint in
   the same commit. Return a receipt with counts and evidence IDs.

A validation failure produces a structured error with source line and field but
no store mutation. A failure after the transaction begins rolls back evidence,
facts, memberships, and checkpoint together. An independently retained failed
run, if implemented for operations, has no artifact/fact membership and cannot
advance a checkpoint.

Backfill and incremental imports must eventually use this same path. The slice
does not add a special fixture-only mutation path.

## 6. Write policies

### 6.1 Market

The price natural key is:

`(instrument_id, trade_date, provider, price_variant, currency_segment)`.

For each staged row:

- no current key: append version 1 and create the current projection;
- same semantic payload: write no new fact version;
- changed payload: append the next correction version, link
  `supersedes_version_id`, and update the current projection; or
- key ambiguity/provider-identifier collision: fail the batch.

Availability, capture, artifact, snapshot, run, and source-row lineage are
retained on each version. Raw and adjusted variants are never substituted.

### 6.2 Macro

The observation identity includes:

`(series_id, period_start, period_end, dimensions, source_vintage_identity)`.

A new source vintage appends a new version for each represented period. A
changed payload under the same provider vintage appends a correction version
rather than rewriting the old payload. Exact row identity within a new snapshot
may reuse the canonical fact while adding snapshot membership.

The series registry declares:

- provider series identity;
- frequency, unit, value representation, scale, and dimensions;
- `supports_vintages=true`;
- supported modes `latest`, `as_of`, and `first_release`;
- `availability_basis=source_release`; and
- date-only precision behavior.

Missing values require an explicit reason. A unit or scale change is rejected
unless represented as a separately reviewed series/version contract.

## 7. Query services

Repositories accept owner-controlled store roles, never a path from the caller.
They use read-only/query-only connections, bound parameters, deterministic
ordering, and hard limits.

### 7.1 Daily-price query

Required selection:

- one stable instrument ID or one unambiguous provider/provider-symbol pair;
- explicit provider, price variant, and currency segment;
- inclusive start/end trade dates;
- mode `latest` or `as_of`;
- explicit cutoff for `as_of`; and
- limit from 1 through 10,000.

The result orders by trade date, then stable identity. It returns selected
version ID, OHLCV, effective/trade date, available/captured values and
precision, evidence IDs, and an audit. It performs no fill, resampling,
currency conversion, or raw/adjusted substitution.

### 7.2 Macro-series query

Required selection:

- one series ID;
- inclusive period-date range;
- mode `latest`, `as_of`, or `first_release`;
- cutoff for `as_of`;
- `completed_date` or explicitly requested
  `calendar_date_inclusive` mixed-precision policy; and
- limit from 1 through 10,000.

Each observation returns the native period, nullable value/missing reason,
unit metadata, vintage/effective/available/captured values and precision,
revision/supersession identity, quality flags, and evidence lineage.

For the fixture:

- `latest` selects the `2026-07-10` values;
- `as_of=2026-07-09` selects the `2026-06-12` vintage;
- `first_release` selects the `2026-06-12` vintage;
- `as_of=2026-06-12` includes the first vintage; and
- `as_of=2026-06-12T10:00:00-04:00` with `completed_date` excludes it.

## 8. Two-tool registry subset

The slice registers exactly two recovered tool names. Their schemas are a
reviewable subset, not a reconstruction claim for the final 57 schemas.

### 8.1 `macro.get_series`

Input schema has `additionalProperties=false` and fields:

| Field | Type | Rule |
| --- | --- | --- |
| `series_id` | string | required, exact ID |
| `start_date` | ISO date | required, inclusive |
| `end_date` | ISO date | required, inclusive and not before start |
| `vintage_mode` | enum | `latest`, `as_of`, or `first_release` |
| `as_of` | ISO date or offset-aware datetime | required only for `as_of` |
| `date_only_policy` | enum | `completed_date` or `calendar_date_inclusive` |
| `limit` | integer | 1-10,000 |

Output is a strict-JSON immutable TimeSeries envelope with metadata,
observations, missingness, provenance, warnings, and audit. No database path,
raw SQL, NaN, or infinity appears.

### 8.2 `timeseries.describe`

Input accepts one bounded TimeSeries envelope produced by
`macro.get_series` and `additionalProperties=false`. It returns deterministic
count, nonmissing count, missing count, minimum, maximum, arithmetic mean, first
period, last period, and an audit that retains the input lineage digest.

It does not fill missing observations or mutate storage. Empty and all-missing
series return null statistics with explicit warnings, never NaN.

The composition acceptance test passes a `macro.get_series` output directly to
`timeseries.describe` without lossy serialization or identifier lookup.

The registry endpoint reports API version, `execution=read_only`, fixed input
and output schemas, examples, bounds, and exactly these two slice tools.

## 9. Minimal dashboard and HTTP surface

The local dashboard remains dependency-light, binds to `127.0.0.1` by default,
and opens all stores read-only.

One Overview view is sufficient for this slice. It shows:

- four store roles and migration/integrity state;
- dataset registrations and last successful run;
- market fixture instruments and bounded daily bars;
- macro fixture series with a visible vintage-mode/cutoff selector;
- selected version/evidence IDs and precision;
- missing-value and truncation warnings; and
- the two-tool manifest and structured result preview.

The minimum fixed endpoints are:

- `GET /api/health`;
- `GET /api/price-series` with allowlisted filters;
- `GET /api/agent-tools`; and
- `POST /api/agent-tools/call`.

The call endpoint accepts strict UTF-8 JSON, enforces the 8 MiB request cap,
validates against the fixed registry schema, and returns structured errors
without stack traces. The browser has no SQL console, ingestion route, file
picker, or caller-selected database path.

Every response applies `Cache-Control: no-store`,
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, and the recovered self-only content security
policy. A test fingerprints all four stores before and after dashboard/tool use
and proves no mutation.

## 10. Expected state transitions

The exact surrogate-ID spelling is implementation-defined, but these logical
counts and deltas are acceptance requirements.

| Step | Expected result |
| --- | --- |
| Initialize four stores | Shared tables in all four; domain tables only in their owner; zero fixture facts |
| Import market base | 2 instruments, 2 provider identifiers, 4 current prices, 4 price versions, 1 successful run/artifact/snapshot |
| Replay market base | `unchanged`; zero row-count or fingerprint delta |
| Import market correction | 4 current prices, 5 price versions; 1 additional successful run/artifact/snapshot |
| Import macro first vintage | 1 series, 2 observation versions, 2 memberships, 1 successful run/artifact/snapshot |
| Replay first vintage | `unchanged`; zero row-count or fingerprint delta |
| Import macro revised vintage | 1 series, 4 observation versions, 4 total fixture memberships; 1 additional successful run/artifact/snapshot |
| Import any negative fixture | Structured failure; no canonical/evidence/checkpoint change |

If artifacts and snapshots are physically many-to-many, the logical
`one additional artifact/snapshot` expectations may be represented by their
equivalent recovered families, but lineage and delta assertions remain exact.

## 11. Deterministic rebuild procedure

The acceptance harness performs these steps without environment defaults:

1. create a new temporary root and four explicit database paths beneath it;
2. initialize the four stores from packaged migration resources;
3. verify migration checksums, foreign keys, WAL writer policy, and read-only
   reader enforcement;
4. verify all fixture file digests;
5. import base fixtures, exact replays, the market correction, and the revised
   macro vintage;
6. run the golden query matrix and the two-tool composition;
7. exercise the dashboard/API read-only checks;
8. run `PRAGMA integrity_check` and `PRAGMA foreign_key_check`;
9. generate a canonical logical manifest; and
10. repeat in a second fresh temporary root and compare manifests.

The logical manifest includes sorted registry declarations, applied migration
IDs/checksums, canonical rows and version lineage, evidence content identities,
and fixture-defined timestamps. It excludes absolute paths, SQLite page layout,
rowid allocation where not semantic, WAL bytes, and nondeterministic test-run
timings. Raw SQLite files are not required to be byte-identical.

## 12. Exit gate

The vertical slice is complete only when:

- every [Data and time contract](DATA_AND_TIME_CONTRACTS.md) used by the slice
  has executable evidence;
- two clean rebuilds have equal logical manifests;
- exact replay creates no run, artifact, snapshot, membership, fact, or
  checkpoint delta;
- a price correction and macro vintage append without history mutation;
- all three macro modes and both mixed-precision policies match golden results;
- explicit missingness survives storage, query, tool composition, and JSON;
- invalid or partial fixtures cannot cause facts or tombstones;
- all store access is explicitly routed, bounded, deterministic, and read-only
  outside ingestion;
- company and news stores initialize independently even though they remain
  data-empty;
- no live/default store, network, credential, scheduler, or provider code is
  touched; and
- the focused and full offline suites in
  [Test strategy](TEST_STRATEGY.md) pass.

Only after this gate should the roadmap add real adapters, broader registry
coverage, production-like backfills, or the mature portal.
