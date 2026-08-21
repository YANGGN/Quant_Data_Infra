# Rebuild Test Strategy

Status: Accepted

## 1. Purpose

This strategy defines the evidence required to accept the rebuilt persistence,
ingestion, temporal, registry, tool, API, and dashboard contracts. It replaces
historical pass-count folklore with traceable behavior. No tests or passing
results are claimed here.

Related documents:

- [System architecture](../../ARCHITECTURE.md)
- [Roadmap](../../ROADMAP.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [Offline vertical slice](VERTICAL_SLICE_SPEC.md)
- [System registry](SYSTEM_REGISTRY_SPEC.md)
- [Tool platform](TOOL_PLATFORM_SPEC.md)
- [ADR 0001: four operational SQLite stores](../adr/0001-four-operational-sqlite-stores.md)
- [ADR 0002: single machine-readable registry](../adr/0002-single-machine-readable-registry.md)
- [ADR 0003: three-layer data architecture](../adr/0003-three-layer-data-architecture.md)
- [ADR 0004: point-in-time availability](../adr/0004-point-in-time-availability-model.md)
- [ADR 0005: composable tool core](../adr/0005-composable-tool-core.md)
- [ADR 0006: lock by physical store](../adr/0006-lock-by-physical-store.md)
- [ADR 0007: SQLite authority and derived Parquet](../adr/0007-sqlite-authority-derived-parquet.md)

## 2. Non-negotiable test rules

1. Offline CI makes no network request and needs no credential.
2. Every database test receives explicit paths beneath its own temporary
   directory.
3. Tests cannot fall back to `data/*.sqlite`, `QUANT_DB_PATH`, a user profile,
   an installed scheduler directory, or another live/default location.
4. Tests never mutate `plan.md`, migration resources already treated as
   applied, provider fixture inputs, or golden outputs during assertion.
5. Each test owns its process-visible environment changes and restores them.
6. Time, random IDs, and iteration order are controlled where they affect
   output.
7. Assertions inspect behavior and persisted invariants, not only row counts.
8. Strict JSON means RFC-compatible finite values; NaN and infinity are test
   failures.
9. Any state-changing command is separated from read-only repository, tool,
   API, dashboard, and export code paths.
10. A historical test count is context, never an acceptance target.

## 3. Isolated test harness

The common harness uses `tempfile.TemporaryDirectory` and constructs a
`StoreMap` with four explicit child paths:

- `market.sqlite`;
- `macro.sqlite`;
- `company.sqlite`; and
- `news.sqlite`.

Before initialization, the harness:

- removes relevant database-path environment variables from the child test
  context or points each one into the same temporary root;
- changes the process working directory to an unrelated empty temporary
  directory for path-independence tests;
- blocks socket connection functions for offline stages;
- injects a fixed clock and deterministic ID source where output identity
  depends on them; and
- records that no default database, WAL, SHM, log, export, or checkpoint path
  exists.

After the test, it asserts that every created filesystem object resolves inside
the test root. A test that creates `data/market.sqlite` or another default
path fails even if its value assertions pass.
Tests also prove that the superseded `data/market_data.sqlite` name is never
used as a silent fallback or compatibility alias.

Parallel tests receive separate roots. Tests that exercise writer locking use
separate connections/processes against one explicitly named temporary store;
they never share a default fixture database.

## 4. Test layers

| Layer | Primary evidence | Runs |
| --- | --- | --- |
| Unit | Parsers, canonicalizers, identity functions, time comparisons, validators, JSON sanitation | Every change |
| Migration | Clean initialization, order, checksum, rerun, tamper, rollback, split ownership | Every change |
| Contract | Registry schemas, strict inputs/outputs, bounds, error codes, lineage envelope | Every change |
| Repository | Latest/as-of/first-release selection, ordering, limits, read-only enforcement | Every change |
| Ingestion integration | Evidence-to-canonical transaction, replay, correction, partial/failure, checkpoint | Every change |
| Golden | Exact fixture query/tool/audit JSON and logical rebuild manifest | Every change |
| Property/invariant | Generated valid/invalid data, version graph, cross-store and SQLite integrity | Every change, bounded seed set |
| API/UI | Endpoint validation, security headers, structural rendering, no writes | Every change to public surface |
| Performance/resource | Hard bounds, query plans, pagination, busy timeout, response size | Main branch and release candidate |
| Live-provider smoke | Minimal current request, secret-safe diagnostics, no persistence unless explicitly testing an adapter | Manual/optional, never offline CI |

Focused suites must be runnable independently. A public schema, migration, or
cross-store change also runs the complete offline suite.

## 5. Fixtures

### 5.1 Fixture classes

The suite distinguishes:

- **synthetic contract fixtures**: small hand-reviewed values that prove edge
  semantics without asserting provider facts;
- **recorded provider-shape fixtures**: bounded, redacted responses used only to
  test a real adapter's parsing contract;
- **malformed fixtures**: one controlled defect per file; and
- **golden outputs**: normalized strict-JSON or canonical logical manifests.

The initial [vertical slice](VERTICAL_SLICE_SPEC.md) uses synthetic fixtures
only. Provider-shape fixtures enter with their adapter, not earlier.

### 5.2 Fixture manifest

Every input fixture has a manifest record with:

- fixture and dataset IDs;
- provider-shape version or `synthetic` marker;
- request-scope description;
- exact byte digest and canonical semantic digest;
- encoding/media type;
- fixed capture/publication/availability values with declared precision;
- expected input, normalized, missing, warning, and rejected counts; and
- redaction assertion.

Tests verify digests before parsing. Unknown fixture drift fails with an
instruction to review and update both input and golden output intentionally.

Fixtures contain no credential, authorization header, signed URL, personal
contact value, or machine-specific absolute path. Error-golden files likewise
use stable field/line codes rather than stack traces.

### 5.3 Golden serialization

Golden JSON uses:

- UTF-8;
- sorted object keys for fixture comparison;
- stable list ordering defined by each contract;
- finite JSON numbers and `null` for missing values;
- source-native ISO values;
- normalized test IDs/timestamps supplied by the fixture; and
- no absolute path, SQLite rowid, transient port, elapsed time, or unordered
  warning set.

Tests compare structured values before textual golden output so formatting
changes do not conceal a semantic difference.

## 6. Storage and migration tests

### 6.1 Clean and repeated initialization

For each of four roles:

1. initialize a new explicit temporary path;
2. assert the expected owner tables and shared tables;
3. assert `PRAGMA foreign_keys=ON` and bounded busy timeout;
4. assert a writer requests WAL where supported;
5. record migration IDs, order, and checksums;
6. initialize again; and
7. prove no schema, ledger, or data delta.

The four stores initialize independently. Company/news foundation tests do not
depend on market/macro initialization.

### 6.2 Checksum and order

- A byte change to a copied, already applied migration fails closed before any
  later migration.
- A missing predecessor, duplicate ID, duplicate order, unexpected store role,
  or resource loaded only because of the current working directory fails.
- Applied SQL is never silently replaced or re-checksummed.
- A syntax/constraint failure leaves neither partial DDL nor an applied-ledger
  row for that migration.
- Unknown exact historical SQL is labeled reconstruction work; tests do not
  bless guessed bytes as recovered checksums.

### 6.3 Connections

- Writer foreign-key violations fail.
- A second writer waits no longer than the configured bounded timeout and
  returns a stable busy error.
- Read repositories open `mode=ro` and set `query_only=ON`.
- `INSERT`, `UPDATE`, `DELETE`, DDL, writable pragma, and schema initialization
  attempts through a reader fail.
- A missing read-only database fails rather than creating one.
- Caller input cannot change the store path, attach a database, or inject SQL.

### 6.4 Database invariants

Every integration database must pass `PRAGMA integrity_check` and
`PRAGMA foreign_key_check`. Additional SQL assertions prove:

- one store role per file;
- no orphan artifact, snapshot, membership, fact version, or run references;
- unique stable and natural identities;
- at most one current projection per natural key;
- immutable-version triggers/guards where specified;
- acyclic, same-key supersession chains;
- current projections point to the chain head;
- tombstones/restorations alternate only through valid transitions; and
- owner-domain tables are absent from the wrong split store.

## 7. Time contract matrix

Unit and repository tests cover every cell below.

| Stored availability | Cutoff | Policy | Expected |
| --- | --- | --- | --- |
| `2026-06-12` date | `2026-06-11` date | inclusive date | excluded |
| `2026-06-12` date | `2026-06-12` date | inclusive date | included |
| `2026-06-12` date | `2026-06-12T23:59:59-04:00` | `completed_date` | excluded |
| `2026-06-12` date | `2026-06-13T00:00:00-04:00` | `completed_date` | included |
| `2026-06-12` date | `2026-06-12T10:00:00-04:00` | `calendar_date_inclusive` | included with warning |
| `2026-06-12T09:00:00-04:00` | `2026-06-12T08:59:59-04:00` | exact | excluded |
| `2026-06-12T09:00:00-04:00` | `2026-06-12T13:00:00Z` | exact | included |
| inferred | any exact cutoff | default | excluded |
| unknown | any cutoff | any | excluded |

Additional temporal tests:

- reject malformed dates, impossible dates, naive datetimes, and offset-free
  timestamps;
- retain `Z` or the original numeric offset in stored/returned representation;
- compare offset-aware timestamps by instant without changing serialization;
- never materialize midnight for a date-only value;
- round-trip fractional-second precision without adding digits;
- preserve `effective_at`, period, `vintage_at`, `available_at`,
  `captured_at`, and `ingested_at` as separate fields;
- apply inclusive start/end date filters to monthly observations in calendar
  year 2026; and
- reject an end date before a start date.

The output audit is asserted for cutoff text, cutoff precision,
`date_only_policy`, availability basis, requested/actual vintage mode, and
warnings.

## 8. Ingestion state matrix

Every version-aware adapter eventually implements the applicable rows.

| Scenario | Run/artifact/snapshot | Canonical result | Checkpoint/tombstone |
| --- | --- | --- | --- |
| First valid content | One successful lineage set | Insert initial facts/versions | Advance after commit |
| Exact semantic replay | Zero writes | No fact/version delta | No change |
| Byte change, same reviewed semantic identity | Zero writes when volatile difference is explicitly excluded | No delta | No change |
| Same artifact under changed meaning-bearing scope | New lineage | Scope-specific canonical handling | Advance only that scope |
| New natural key | New lineage | Append initial version | Advance |
| Corrected value | New lineage | Append version and supersession link | Advance |
| New macro vintage | New lineage | Append vintage versions/membership | Advance |
| Duplicate identical row in one batch | Dataset-declared dedupe or rejection, tested explicitly | Never duplicate facts | No ambiguous checkpoint |
| Conflicting duplicate key | Failure | Whole batch rolled back | No advance |
| Invalid OHLC/unit/date/non-finite value | Failure | Whole batch rolled back | No advance |
| Transport timeout/429 | Retryable failure | No facts or tombstones | No advance |
| Partial page/snapshot | Explicit partial outcome | Only policy-authorized rows, never inferred deletion | No complete-scope tombstone |
| Complete authoritative disappearance | New lineage | Append tombstone version | Advance complete scope |
| Later authoritative return | New lineage | Append restoration version | Advance complete scope |
| Commit failure | Failure | Evidence/facts/membership rolled back together | No advance |

For semantic daily gates, fixture tests preserve the recovered rules:

- BLS ignores only top-level `responseTime`;
- BEA excludes `UTCProductionTime` from parsed-table identity;
- SOMA identifies the latest summary-only release;
- EIA weekly includes requested-scope
  `(series_id, period, content_sha256)` identities; and
- Chicago Fed includes both parsed observations and artifact/request scope.

These tests may be marked pending until their adapters enter scope, but the
generic no-write contract is active in the initial slice.

## 9. Market contract tests

The synthetic SPY/^GSPC-style fixtures prove:

- deterministic stable instruments and separate provider identifiers;
- ETF and index asset types are not conflated;
- raw variant and USD segment are explicit;
- OHLC, finite-number, volume, date, duplicate, and sorted-output validation;
- four initial keys produce four current rows and four version rows;
- exact replay produces zero writes, including zero ingestion-run rows;
- a changed SPY close appends exactly one price version and retains the old
  version;
- latest and before/after-correction `as_of` cutoffs select expected version
  IDs;
- a partial correction scope does not tombstone unmentioned rows;
- range boundaries are inclusive;
- limit truncation is deterministic and disclosed; and
- no fill, resampling, adjusted/raw substitution, provider mixing, or currency
  conversion occurs.

Generated invariant tests create seeded valid OHLC tuples and mutate one
constraint at a time. All invalid mutations are rejected before commit.

## 10. Macro contract tests

The synthetic revision-aware EMPLOY-style fixtures prove:

- one registered series with fixed provider identity, unit, scale, frequency,
  and supported vintage modes;
- explicit nullable value plus missing reason survives ingestion and query;
- first vintage and revised vintage append immutable versions and membership;
- exact replay produces zero writes;
- `latest` returns the revised values;
- `as_of` before the revised availability returns the first vintage;
- `first_release` returns evidence-labeled first release, not the earliest
  ingestion row;
- date-only availability follows the matrix in Section 7;
- changed payload under one vintage appends a correction rather than updating;
- unsupported mode or availability basis fails clearly;
- unit/scale/dimension conflicts fail the batch; and
- a 2026 monthly start/end query returns the expected 2026 periods.

A source with only current local captures gets a separate fixture proving that
historical source `as_of` and `first_release` are rejected. It cannot reuse the
true-vintage golden output.

## 11. Tombstone, missingness, and transformation invariants

Property/invariant tests use fixed seeds and bounded generated cases; an
external property-testing dependency is not required.

For each generated version chain:

- version sequence is monotonic within its natural key;
- supersession links never cross keys or form a cycle;
- a cutoff selects at most one active version;
- inserting a later correction cannot change an earlier `as_of` result;
- an exact replay cannot change any cutoff result;
- tombstone requires complete-scope evidence;
- restoration cannot precede its tombstone; and
- `latest` equals the final active state while history retains all states.

For each generated missing series:

- null requires a reason;
- finite values do not carry a contradictory missing reason;
- absent rows and explicit missing observations remain distinguishable;
- describe/alignment counts remain internally consistent; and
- no implicit transform changes the number or value of native observations.

Tests explicitly reject silent forward fill, resampling, interpolation,
adjusted/raw substitution, provider/feed/unit/currency mixing, current
classification as historical truth, option-capture mixing, and warm-up-to-zero
conversion.

## 12. Registry, tool, and strict-JSON tests

The machine-readable registry is the single source for in-process and HTTP
validation. Contract tests assert:

- exact registered name set for the tested milestone;
- API/schema version and `execution=read_only`;
- input and output schemas, descriptions, examples, bounds, and warnings;
- `additionalProperties=false` on top-level tool inputs;
- required/conditional fields such as `as_of`;
- stable unknown-tool, invalid-input, unavailable-data, and internal-error
  envelopes;
- no raw SQL, database path, or unbounded collection parameter;
- identical normalized results through direct and HTTP execution; and
- no registry mutation after startup.

Strict-JSON tests recursively inspect every success/error output. They reject
NaN, positive/negative infinity, bytes, sets, non-string object keys,
implementation datetimes, stack traces, and secrets. Nullable statistics use
`null` plus warnings.

Composition tests pass the exact TimeSeries output of `macro.get_series` to
`timeseries.describe`. The second tool retains the first tool's lineage digest,
missingness, cutoff, and warnings and performs no storage write.

## 13. API and dashboard read-only tests

The server is started on loopback with four explicit temporary store paths.
Tests assert:

- all query connections are read-only/query-only;
- `GET /api/health` reports each actual store independently;
- filters, pagination, sorting, and table choices are allowlisted and bound;
- tool manifest and calls match in-process contracts;
- malformed UTF-8, malformed JSON, duplicate/unknown fields, oversized body,
  wrong method, and unavailable data receive stable errors;
- the 8 MiB request cap is enforced before parsing;
- responses carry `no-store`, nosniff, deny-frame, no-referrer, and recovered
  self-only CSP headers;
- the UI structurally renders nulls, warnings, provenance, truncation, and
  nested tool output;
- persistent navigation remains visible on every implemented page;
- the accepted shared visual tokens, locally bundled Inter font, responsive
  layouts, and representative visual-regression states match
  [the UI visual direction](UI_VISUAL_DIRECTION.md);
- keyboard navigation, focus visibility, reduced motion, contrast, and 200%
  zoom remain usable, and no reference-site or remote-font asset is requested;
- there is no ingestion, SQL-console, arbitrary table, or file-path route; and
- pre/post logical fingerprints of all stores are identical after every UI and
  tool test.

An attempted write through a repository, API handler, dashboard render, table
inspector, or export read path must fail the test even if rolled back.

## 14. Resource and performance tests

Hard limits are schema-visible and tested at the boundary and one past it:

- macro/native time-series observations: at most 10,000 per call;
- multi-series inputs: at most 20 where supported;
- technical-indicator specs: at most 20, returned bars at most 10,000, and
  output values at most 200,000 when that phase enters scope;
- every page/search: positive bounded limit and deterministic cursor/order;
- agent-tool request body: at most 8 MiB; and
- writer wait: bounded busy timeout.

At-limit requests either succeed within the documented contract or return a
structured truncation/limit error. One-past-limit requests fail before an
expensive query.

On fixed generated databases, query-plan tests require indexes for dominant
identity/date access paths and reject accidental full scans where a bounded
index lookup is part of the contract. Wall-clock budgets are recorded on
controlled CI hardware, with separate cold/warm measurements and generous
noise tolerance; local developer timing is not a correctness assertion.

Memory and response-size tests ensure a bounded query is not followed by an
unbounded application expansion. Wide/nested results are paginated or
explicitly capped rather than silently dropped.

## 15. Deterministic rebuild and backup tests

Two fresh temporary roots ingest the same fixture sequence. Their canonical
logical manifests must match after excluding nonsemantic path/page-layout
values. The manifest covers migration checksums, dataset declarations,
evidence semantic identities, snapshots, fact/version chains, memberships,
quality state, and golden query outputs.

Backup/restore tests:

1. reach a clean committed fixture state;
2. create a SQLite-safe backup of each store, including WAL-consistent content;
3. restore into four new explicit paths;
4. run integrity and foreign-key checks;
5. compare logical manifests and golden queries; and
6. prove readers remain read-only after restore.

Derived exports, when added, are rebuilt from explicit read-only SQLite copies.
Deleting an export and recreating it must not mutate SQLite or alter semantic
content.

## 16. CI stages

### Stage A - documentation and static contract

- required documents and relative links resolve;
- registry/fixture schemas parse;
- package resources load independently of working directory;
- Python compiles; and
- no generated database or secret-like fixture is tracked.

### Stage B - fast unit and contract

- time/identity/canonicalization/validation unit tests;
- strict-JSON and registry-schema tests;
- deterministic property seeds; and
- no network.

### Stage C - storage and vertical-slice integration

- all four migrations and connection policies;
- ingestion state matrix;
- repository golden queries;
- tool composition;
- database invariant checks; and
- clean rebuild manifest comparison.

### Stage D - API/UI and resource bounds

- loopback server with temporary stores;
- security/read-only tests;
- response and request limits;
- structural rendering/browser tests; and
- fixed-data query-plan/performance checks.

### Stage E - complete offline suite

Runs all restored domains and tools. This stage is required for migration,
registry, shared-time, cross-store, public-API, or export changes.

### Stage F - optional smoke and release rehearsal

Live-provider smoke tests are separately invoked, bounded, secret-safe, and
never required to pass offline CI. Release rehearsal uses new non-production
stores, then integrity, backup/restore, point-in-time, and deterministic
manifest checks. It never writes directly into an operational store.

## 17. Promotion gates

A rebuild candidate cannot be promoted until:

- Stages A-E pass from a clean checkout without network or defaults;
- migration order/checksums are reviewed and applied SQL is unchanged;
- four split stores initialize, back up, restore, and pass integrity checks;
- exact replay, correction, new vintage, failure, partial, tombstone, and
  restoration cases pass;
- date-only and offset-aware tests prove no fabricated precision;
- latest/as-of/first-release goldens match evidence;
- every tool/API result is bounded, deterministic, read-only, and strict JSON;
- cross-store queries report one cutoff plus per-store fingerprints;
- logical clean-rebuild manifests match;
- UI/API mutation fingerprints remain unchanged;
- operational promotion targets are new non-production paths; and
- unresolved warnings, unsupported vintage modes, and data gaps are visible,
  not waived by row-count similarity.

## 18. Traceability

Each normative requirement receives a stable test ID:

- `MIG-*` migration/storage;
- `ING-*` ingestion/no-write/versioning;
- `TIME-*` precision/cutoff/vintage;
- `MKT-*` market;
- `MAC-*` macro;
- `REG-*` registry/tool;
- `API-*` HTTP/UI/read-only;
- `INV-*` property/invariant;
- `PERF-*` resource bounds; and
- `DR-*` deterministic rebuild/backup.

Test names or metadata reference these IDs. CI publishes a compact matrix of
requirement ID, test, fixture digest, result, and artifact/log location. A gate
is satisfied by current executable evidence, not by prose or an old test count.
