# Data and Time Contracts

Status: Accepted

## Purpose

This specification defines the data, evidence, versioning, and point-in-time
contracts that every rebuilt store, importer, query service, tool, export, and
research workflow must share. It is implementation guidance, not a claim that
the erased schema or code has already been recovered.

The later recovery evidence in `plan.md` Sections 18-20 controls where it
conflicts with earlier planning material. In particular:

- the operational topology is four SQLite stores;
- `schema_migrations`, `dataset_registry`, and `ingestion_runs` are recovered
  shared physical table names;
- `prices_daily` and `prices_daily_versions` are recovered market table names;
- `macro_observation_versions` and the macro artifact/snapshot families are
  recovered macro table names;
- migration 0014 introduced price correction versions; and
- semantically unchanged polling must write no run, artifact, snapshot, or
  membership row.

The byte-exact historical DDL, migration SQL, and migration checksums remain
unknown. This document must not be used to invent them.

Related specifications:

- [System architecture](../../ARCHITECTURE.md)
- [Roadmap](../../ROADMAP.md)
- [System registry](SYSTEM_REGISTRY_SPEC.md)
- [Tool platform](TOOL_PLATFORM_SPEC.md)
- [Vertical slice](VERTICAL_SLICE_SPEC.md)
- [Test strategy](TEST_STRATEGY.md)
- [ADR 0001: four operational SQLite stores](../adr/0001-four-operational-sqlite-stores.md)
- [ADR 0003: three-layer data architecture](../adr/0003-three-layer-data-architecture.md)
- [ADR 0004: point-in-time availability](../adr/0004-point-in-time-availability-model.md)
- [ADR 0005: composable tool core](../adr/0005-composable-tool-core.md)
- [ADR 0007: SQLite authority and derived Parquet](../adr/0007-sqlite-authority-derived-parquet.md)

## Normative language

`MUST` and `MUST NOT` are acceptance requirements. `SHOULD` identifies the
default unless a dataset-specific contract documents a stronger reason to
depart. `MAY` identifies an optional, caller-visible behavior.

## 1. The three data layers

The rebuild separates evidence, canonical data, and research products.

| Layer | Responsibility | Mutation rule |
| --- | --- | --- |
| Evidence | Provider bytes or controlled input, redacted request scope, response metadata, content identity, capture, and ingestion run | Append-only; an exact semantic replay is a no-write |
| Canonical | Stable entities, provider identifiers, observations, versions, snapshot membership, quality state, and tombstones | Insert or append a new version; never erase prior meaning |
| Research | Tool inputs and outputs, transformations, exclusions, warnings, code/schema/data fingerprints, and source-version lineage | Immutable artifact or reproducible ephemeral result |

Provider staging tables MAY exist between evidence and canonical data, but they
do not become a fourth authority. A staging row cannot substitute for retained
evidence or canonical lineage.

SQLite is authoritative for operational state. A Parquet, JSON, CSV, or Atlas
export is derived and must identify the authoritative store state from which it
was produced.

## 2. Stable identities

### 2.1 Entity identity is not a display label

The following application identities cross logical boundaries:

| Entity | Stable identity | Identifiers that must remain separate |
| --- | --- | --- |
| Market instrument | `instrument_id` | ticker, provider symbol, exchange symbol, display name |
| Macro series | `series_id` | provider series ID, display name, category |
| SEC issuer | CIK-backed issuer identity | ticker, company name, provider symbol |
| Filing | SEC accession number | form, report date, primary-document name |
| Option acquisition | immutable capture ID | request time, feed label, contract symbol |
| News item | provider/source item identity plus immutable local version | headline, URL, publication label |
| Source evidence | content or provider artifact identity plus request scope | file path, fetch URL, ingestion run |
| Research output | research artifact ID plus content fingerprint | filename, chart title, user-facing label |

A provider symbol MUST NOT be treated as permanent instrument or issuer
identity. Provider identifiers must carry their provider, validity interval or
observation boundary, confidence where applicable, and source evidence.

### 2.2 Natural keys

A canonical natural key includes every dimension that changes meaning. Typical
examples are:

- daily price: instrument, trade date, provider, price variant, and currency
  segment when currency is not invariant;
- macro observation: series, reference period, dimensions, source vintage or
  revision identity, and unit/value representation;
- membership: collection identity, member identity, effective interval, and
  snapshot scope;
- option surface row: immutable capture and contract identity; and
- news version: source item identity and version/retraction identity.

Provider, feed, price variant, unit, currency, environment, identifier segment,
or option capture MUST NOT be silently collapsed into a shorter key.

### 2.3 Cross-store identity

SQLite foreign keys stop at a file boundary. Cross-store joins MUST therefore:

1. use stable application identities, not display labels;
2. query each owner store read-only;
3. apply one declared availability cutoff and policy to every component;
4. bound every component result before joining in application memory; and
5. report unmatched or ambiguous identities instead of guessing.

No weak cross-file relationship is created merely because two rows currently
share a ticker.

## 3. Immutable evidence and semantic identity

### 3.1 Dataset registration

Every persisted dataset has a registry entry that declares at least:

- stable dataset ID and owning store;
- provider/source family and source dataset ID;
- canonical entity/fact family;
- expected frequency and dimensions;
- write policy: keyed current state, versioned, immutable capture, or complete
  snapshot;
- availability basis and supported vintage modes;
- completeness/tombstone authority;
- normalization and schema version;
- active state and quality warnings.

The registry declaration is part of the data contract. Import code cannot
quietly choose a different revision, availability, or tombstone policy.

### 3.2 Source artifact

An immutable source artifact records, as applicable:

- artifact identity and content digest;
- dataset ID;
- redacted locator and canonical request scope;
- media type, byte count, and compression/encoding;
- provider publication metadata as supplied;
- HTTP status and selected response metadata;
- local `captured_at`;
- raw payload location or retained payload, according to retention policy; and
- the normalization contract version that interpreted it.

Secrets, authorization headers, signed URLs, tokens, and unredacted query
credentials MUST NOT be persisted.

### 3.3 Snapshot or capture

An artifact is the evidence object. A snapshot or capture is the coherent
provider state interpreted from one or more artifacts. It records:

- its exact request scope;
- contributing artifact IDs;
- source, feed, and environment;
- whether membership is complete, partial, or unknown;
- available and captured boundaries;
- canonical row identities or snapshot membership; and
- validation outcome and quality warnings.

One immutable option surface, for example, has one capture, one underlying, one
resolved feed, and one environment. Rows from separate captures are never
combined into a synthetic surface.

### 3.4 Semantic no-write gate

The importer MUST compute dataset-specific semantic identity before it opens a
write run. Identity includes normalized observations and every meaning-bearing
request-scope field; it may exclude only reviewed volatile metadata.

If that identity already exists for the same dataset and scope, the result is
`unchanged` and the importer writes exactly zero:

- ingestion runs;
- source artifacts;
- source snapshots/captures;
- snapshot membership rows;
- canonical facts or versions; and
- checkpoints.

This is stronger than an upsert that happens to affect zero canonical rows.
The recovered source-specific exceptions in `plan.md` Section 20.3 remain the
authority for BLS, BEA, SOMA, EIA weekly, and Chicago Fed canonical identity.

Partial responses, parse errors, timeouts, rate limits, and transport failures
are failure or retryable outcomes. They are not proof of unchanged content and
never authorize a tombstone.

### 3.5 Ingestion run

A write-worthy acquisition receives one run identity. The run records its
dataset, command, redacted scope, start/completion values, status, fetched and
written counts, warnings/rejections, error summary, and code/schema versions.

Canonical rows, evidence lineage, snapshot membership, and the successful
checkpoint MUST commit atomically in one short transaction. A failed validation
or commit leaves none of them visible and does not advance a successful
checkpoint. A `partial` run is never silently represented as `succeeded`.

## 4. Canonical fact and version model

### 4.1 Required time and lineage fields

Not every fact uses every field, but each populated field has one meaning:

| Field | Meaning | Must not be used as |
| --- | --- | --- |
| `period_start` / `period_end` | Reference interval measured by an observation | Publication or capture time |
| `effective_at` | Date/time on which an event, classification, membership, or fact applies economically | Evidence that the value was knowable |
| `trade_date` | Market session date represented by a daily bar | Guaranteed close-publication instant |
| `vintage_at` | Provider-defined vintage/release identity | Local acquisition time |
| `published_at` | Source-claimed publication label when independently present | Local availability proof |
| `available_at` | Earliest defensible boundary at which this exact version may be used | Observation period or an invented timestamp |
| `captured_at` | When this installation obtained the evidence | Historical provider availability unless the dataset declares local-capture semantics |
| `ingested_at` | Local commit/audit time | Source release time |
| `supersedes_version_id` | Prior canonical version whose state this version changes | Permission to delete the prior version |
| `tombstone_at` | Availability boundary of an evidenced removal/retraction | Event/effective date unless the source proves both are the same |

The same string in two fields does not merge their meanings. A daily bar may
have `trade_date=2026-07-17`, a date-only source availability of
`2026-07-18`, and a local timestamp capture on `2026-07-20T09:15:00-04:00`.

### 4.2 Source and canonical values

Normalization MUST retain enough source representation to audit:

- source field name and raw value when normalization is not lossless;
- canonical value, unit, value representation, and scale;
- missing reason or status;
- dimensions and provider identity; and
- evidence and snapshot IDs.

Finite numeric values only are admitted to canonical numeric fields. JSON
outputs never contain NaN or infinity.

### 4.3 Corrections and supersession

A new natural key inserts an initial canonical version. A changed payload for
an existing key appends a new version and links to the version it supersedes.
The prior row remains immutable and queryable.

A current-state projection MAY point to the latest version for efficient
`latest` reads. Changing that pointer does not authorize mutation of the
version history. The recovered `prices_daily` / `prices_daily_versions` pair is
the market example; `macro_observation_versions` is the revision-aware macro
example.

A semantically identical row in a new, broader artifact may gain new snapshot
membership without gaining a duplicate fact version. An exact semantic replay
of the entire scope remains a total no-write under Section 3.4.

Version selection uses declared source revision/vintage order. Ingestion order
is only a tie-breaker for datasets explicitly declared to have local-capture
history; it cannot manufacture provider vintage history.

## 5. Temporal representation

### 5.1 Accepted precision

Calendar dates use strict ISO `YYYY-MM-DD`. Real timestamps use a canonical ISO
8601 datetime with an explicit numeric offset or `Z`. Naive datetimes are
invalid.

Storage and APIs preserve:

- the source value when retained for audit;
- the validated canonical representation;
- its declared precision: `date`, `datetime`, `inferred`, or `unknown`; and
- the source timezone/offset representation.

The system does not replace an offset-bearing timestamp with a different
timezone representation. It may derive an internal instant-ordering key for
comparison, but the stored and returned value remains unchanged.

Date-only data MUST NOT be expanded to midnight, noon, end of day, UTC, or a
server-local timestamp. An inferred value must state the inference rule and
cannot be relabeled as exact datetime precision.

### 5.2 Range filters

Start/end filters over reference periods or trade dates are inclusive calendar
date filters. They compare normalized date fields as dates. They do not compare
a monthly period label against a mixed Python datetime.

For an interval observation, a tool declares whether a requested date range
selects by period start, period end, containment, or overlap. The selected rule
is fixed in the tool schema and echoed in the audit.

### 5.3 Cutoff grammar and date-only policy

An `as_of` cutoff accepts either:

- a calendar date, interpreted as the inclusive source-local calendar date; or
- an offset-aware datetime, interpreted as an exact instant.

The result audit MUST echo `cutoff`, `cutoff_precision`,
`date_only_policy`, and `availability_basis`.

The accepted date-only comparison rules are:

| Availability value | Date cutoff | Datetime cutoff |
| --- | --- | --- |
| Date | Include when availability date is on/before cutoff date | Under `completed_date`, include only when availability date is before the cutoff's written calendar date |
| Offset-aware datetime | Include when its source-local calendar date is on/before cutoff date | Compare exact instants using the stated offsets |
| Inferred | Exclude unless the dataset contract names and the caller opts into an inference policy | Same |
| Unknown | Exclude | Exclude |

`completed_date` is the default for a timestamp cutoff against date-only
availability. It excludes a same-date row because the release time is unknown.
An explicitly requested `calendar_date_inclusive` policy MAY include it, but
the result must warn that same-day intraday safety is not established.

A date cutoff is deliberately a whole-calendar-date question. Including a
date-only value on that date does not assign it a timestamp.

## 6. Query modes

Each dataset declares which modes it defensibly supports.

| Mode | Selection rule | Required evidence |
| --- | --- | --- |
| `latest` | Newest currently stored non-superseded state for each natural reference key | Version order and active/tombstone state |
| `as_of` | Newest version whose declared availability satisfies the explicit cutoff and policy | Defensible `available_at` or declared local-capture basis |
| `first_release` | Earliest defensible source release for each reference period | Provider release/vintage identity and release-stage evidence |

Additional rules:

- `latest` is current stored knowledge, not historical truth.
- `as_of` does not use `effective_at`, `period_end`, file modification time, or
  ingestion time as a substitute for availability.
- A current-state-only source either supports explicitly labeled
  `local_capture_as_of` semantics or rejects `as_of` for dates before retained
  captures.
- The first row ever ingested is not automatically `first_release`.
- A provider's revised vintage imported later may be selected for `latest` but
  not for an earlier `as_of` cutoff.
- Revision analysis is ex-post unless its inputs are separately restricted by
  a point-in-time availability cutoff.

Every response reports the requested mode, actual mode, availability basis,
date-only policy, truncation state, warnings, and selected version IDs.

## 7. Retraction, tombstone, and restoration

A disappearance becomes a tombstone only when:

1. the source contract says the response is an authoritative complete snapshot
   for the exact scope;
2. the acquisition and parse succeeded completely;
3. the prior member lies inside that exact scope; and
4. the absence survives dataset-specific completeness validation.

An explicit provider retraction may also create a tombstone. A partial page,
changed request scope, error, timeout, 429, parse rejection, or missing
attachment never does.

A tombstone is an append-only version with evidence, availability, reason, and
the prior version identity. Queries at or after the tombstone no longer return
the item as active, but history queries can still show both states.

If the item later returns, restoration appends another active version that
supersedes the tombstone. Restoration does not delete or edit either earlier
state.

For membership snapshots, completeness and scope are identity-bearing.
`complete`, `partial`, and `unknown` snapshots cannot share a semantic identity
merely because their currently observed rows match.

## 8. Missingness and prohibited implicit transformations

Missing data remains an observation when the source explicitly reports a
period. It uses a null value plus a reason/status such as `not_reported`,
`suppressed`, `not_applicable`, `not_available`, or a source-specific code.
Absence of a row, source omission, parse rejection, and explicit null are
different states.

No storage, query, tool, or dashboard layer may silently:

- forward-fill or backfill;
- resample or interpolate;
- substitute adjusted prices for raw prices or the reverse;
- blend providers, feeds, price variants, currencies, units, identifier
  segments, or option captures;
- interpolate a yield curve;
- convert currency;
- combine instant and weighted-average share counts;
- turn warm-up or missing values into zero;
- use a current universe/classification as historical membership; or
- treat absent evidence as neutral evidence.

A supported fill, aggregation, conversion, classification, exclusion, or
alignment is an explicit parameter. Output lineage records the method, bounded
window, affected observation identities, and resulting warning. Unsupported
transformations fail closed.

## 9. Cross-store cutoff and snapshot contract

A composite result over market, macro, company, or news uses one immutable
query context:

- caller-supplied cutoff and precision;
- date-only comparison policy;
- per-dataset availability basis;
- requested identifiers, variants, providers, units, and dimensions;
- maximum rows/components; and
- one read transaction or immutable read copy per physical store.

The same cutoff text and policy are passed unchanged to every owner. A
component unavailable by that boundary remains missing; the composite must not
fall back to today's value.

SQLite does not provide one atomic transaction across four files. Composite
outputs therefore include per-store evidence:

- absolute store role, never a caller-selected path;
- migration-head/checksum-set fingerprint;
- data high-water mark or snapshot/capture IDs;
- read-start/read-completion values; and
- whether a coordinated collector-completion receipt or immutable store copy
  was used.

If coherent cross-store state cannot be established, the result is explicitly
`best_effort_multi_store` or fails when the tool requires atomic-cohort
evidence. Atlas publication requires coordinated, read-only copies and atomic
promotion; export time alone is not a shared data cutoff.

## 10. Research lineage

Every persisted research artifact, and every non-persisted tool result audit,
contains enough metadata to reproduce or reject the result:

- artifact/result identity and content digest;
- tool name, tool schema version, code version, and parameters;
- requested and actual vintage mode;
- cutoff, cutoff precision, date-only policy, and availability basis;
- owning store fingerprints and migration heads;
- dataset, artifact, snapshot/capture, fact-version, and membership IDs;
- source series/instruments and all provider/variant/unit/currency dimensions;
- transformation graph in execution order;
- missing-value, alignment, fill, exclusion, and truncation policies;
- warnings and point-in-time-safety assessment; and
- generated/captured timestamp with real precision.

Research artifacts are immutable. A rerun with different inputs, code,
registry schema, store fingerprint, or policy receives a different identity.
A cached result is reusable only when every identity-bearing input matches.

## 11. Acceptance examples

The values below are synthetic and establish behavior only.

### 11.1 Price correction

1. SPY raw bar version `p1` has `trade_date=2026-07-17`,
   `available_at=2026-07-18` with date precision, and close `640.00`.
2. A corrected payload appends `p2` with close `640.25`,
   `available_at=2026-07-20T08:30:00-04:00`, and
   `supersedes_version_id=p1`.
3. `latest` returns `p2`.
4. `as_of=2026-07-19` returns `p1`.
5. `as_of=2026-07-20T08:00:00-04:00` returns `p1`.
6. `as_of=2026-07-20T09:00:00-04:00` returns `p2`.
7. Exact replay of the corrected semantic payload writes no evidence, run,
   snapshot, price, or price-version row.

### 11.2 Date-only macro vintage

1. Synthetic RTDSM-style EMPLOY vintage `m1` is available as date-only
   `2026-06-12`.
2. `as_of=2026-06-12` includes `m1` because the cutoff is an inclusive date.
3. `as_of=2026-06-12T10:00:00-04:00` with `completed_date` excludes `m1`
   because same-day release time is unknown.
4. The same timestamp with explicit `calendar_date_inclusive` may include it,
   but the response is marked unsafe for same-day intraday use.
5. A later revised vintage changes `latest` and later `as_of` results;
   `first_release` continues to select `m1` if release evidence proves it was
   the first source release.

### 11.3 Missing value

A reported monthly period with null value and `missing_reason=suppressed` is
returned as one observation. It counts as missing, is not converted to zero,
and is not dropped merely to make aligned arrays equal length.

### 11.4 Tombstone and restoration

A complete authoritative snapshot omits an existing member and appends
tombstone `t1`. A later complete snapshot restores it with version `r1` that
supersedes `t1`. A cutoff before `t1` sees the active member; a cutoff between
`t1` and `r1` sees it inactive; a later cutoff sees `r1`. All three versions
remain auditable.

## 12. Conformance checklist

An implementation conforms only when evidence demonstrates all of the
following:

- stable entity IDs are separate from provider labels;
- artifacts and snapshots are immutable and request scope is redacted;
- semantic replay passes the zero-write gate;
- canonical corrections append and retain superseded versions;
- date-only, offset-aware, inferred, and unknown precision remain distinct;
- naive datetimes and non-finite values are rejected;
- `latest`, `as_of`, and `first_release` return fixture-proven results or reject
  unsupported modes;
- tombstone and restoration require complete-scope evidence;
- missing values remain explicit;
- every transformation and cross-store cutoff is caller-visible and audited;
- query/tool connections are read-only and bounded; and
- strict JSON output contains no NaN, infinity, database path, raw SQL, or
  secret.

The concrete evidence suite is specified in [Test strategy](TEST_STRATEGY.md).
