# Analytical Exports Specification

Status: Accepted

## Purpose

This specification defines an optional derived analytical-export layer without
changing the authority of the four operational SQLite stores. Parquet may be
introduced only as immutable, reproducible output after a recorded benchmark
shows value for named workloads. DuckDB may be used as an optional local
development and analytical dependency over those exports; it is never an
operational authority.

This document does not claim that Parquet export, DuckDB integration, or Atlas
publication has been implemented.

Related documents:

- [Architecture](../../ARCHITECTURE.md)
- [System registry specification](SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [Scheduling and locking](SCHEDULING_AND_LOCKING.md)
- [Roadmap](../../ROADMAP.md)
- [ADR 0007: SQLite authority with derived Parquet](../adr/0007-sqlite-authority-derived-parquet.md)

## Decision summary

- Market, macro, company, and news SQLite files remain the authoritative
  operational stores.
- No collector, migration, provenance record, revision sequence, tombstone, or
  restoration state is authoritative only in Parquet.
- A Parquet export is immutable derived evidence tied to exact source snapshots,
  semantic query IDs, schema IDs, and an export receipt.
- A benchmark and semantic-equivalence gate precede adoption for each workload.
- DuckDB is optional and non-authoritative. The core runtime and collectors MUST
  not require it.
- Atlas remains a separate read-only snapshot consumer and publication system.
  Producing an analytical export does not deploy or publish Atlas.

## Scope

This specification covers:

- export eligibility and benchmarking;
- read-only source capture and cross-store coordination;
- semantic IDs and source fingerprints;
- Parquet schema and partition rules;
- export manifests and receipts;
- exact staging, validation, and atomic promotion;
- immutable revision and invalidation behavior;
- optional DuckDB use;
- Atlas separation; and
- offline acceptance evidence.

It does not authorize a live database read, collector run, backfill, migration,
scheduler action, deployment, or deletion of an existing export.

## Authority model

### Authoritative operational state

Only the configured SQLite stores own:

- migration and dataset registries;
- ingestion runs;
- stable natural identities;
- immutable artifacts and captures;
- current rows plus version/revision history;
- availability evidence;
- snapshot scope and membership;
- tombstones and restoration; and
- operational backup and recovery state.

An export MUST be reproducible from an identified consistent SQLite source
snapshot and versioned export contract. If SQLite and an export disagree,
SQLite plus its migration/provenance evidence wins and the export is invalid.

### Derived analytical state

Derived Parquet files MAY contain a bounded projection, denormalized analytical
table, typed time series, feature panel, or research-ready relation. They MUST
not be written back into an operational store or presented as a new ingestion
source without a separate reviewed contract.

An export is immutable. A correction, new capture, schema change, exporter
change, or query-semantic change creates a new export revision.

## Adoption and benchmark gate

Parquet MUST NOT be added merely because it is a common analytical format. Each
candidate export family requires a benchmark plan approved before results are
observed.

The plan MUST name:

- representative source size and expected growth;
- exact SQLite baseline queries and indexes;
- candidate Parquet layout and compression;
- cold and warm workloads;
- selectivity, projection, scan, aggregation, and join shapes;
- correctness fixtures, including nulls, timestamps, decimals, revisions, and
  point-in-time filters;
- machine/runtime versions and cache-control procedure;
- latency, throughput, peak memory, output bytes, export time, and temporary
  disk metrics; and
- a predeclared material-improvement threshold and maximum acceptable
  regression.

Adoption requires all semantic checks to pass and at least one named priority
workload to meet its predeclared improvement threshold without violating the
resource ceiling for any required workload. Benchmark results, including
negative results, MUST be retained as an acceptance artifact. A failed or
ambiguous benchmark leaves SQLite-only analysis as the decision.

## Export definitions in the system registry

Each export definition MUST be server-owned and versioned in the system
registry. It MUST contain:

- stable semantic dataset ID;
- export-contract version;
- owner and description;
- authoritative logical source stores;
- fixed query/projection operation ID;
- point-in-time, vintage, availability, revision, missingness, unit, timezone,
  and ordering policies;
- typed output-schema ID;
- primary/natural row identity;
- deterministic partition and row-order rules;
- maximum rows, bytes, partitions, and runtime;
- included provenance fields and explicitly excluded sensitive/raw fields;
- benchmark decision and evidence reference;
- compatibility and lifecycle state; and
- registered consumers, including whether Atlas may consume it.

The public caller MUST NOT provide SQL, a database path, an output path, an
arbitrary table, a partition expression, or a credential.

## Semantic and revision identities

### Semantic dataset ID

The semantic dataset ID identifies meaning, not one materialization. It changes
when any of these change:

- source owner or natural identity;
- point-in-time/vintage/availability rule;
- revision or tombstone interpretation;
- units, frequency, timezone, or numeric coercion;
- missing-value policy;
- transformation, join, or aggregation definition; or
- output field meaning.

Display labels, compression level, file size, and row-group tuning do not by
themselves change semantic identity.

### Export revision ID

The export revision ID MUST be a canonical hash over:

- semantic dataset ID and export-contract version;
- output-schema ID and schema digest;
- exporter implementation/version digest;
- fixed query/projection operation ID and normalized parameters;
- exact source-store snapshot identities and fingerprints;
- requested cutoff/vintage mode;
- deterministic partition-plan version; and
- all upstream derived semantic IDs, if any.

Identical source snapshots and contracts MUST yield the same revision ID and
canonical manifest. Changed authority or meaning MUST yield a different ID.

## Source snapshot and fingerprint contract

### Read-only access

The exporter MUST receive host-selected paths. It MUST NOT discover a default
or accept a caller path. Each source is opened with SQLite `mode=ro` and
`PRAGMA query_only=ON`; export must never initialize or migrate a store.

The source fingerprint MUST include, at minimum:

- sanitized store alias and logical owner;
- database schema/application identity if defined;
- complete migration version, filename, and checksum set;
- consistent-snapshot method and snapshot instant;
- relevant dataset registry revision;
- last included ingestion/capture identifiers or another exact high-water mark;
- file/content fingerprint appropriate to the consistent snapshot; and
- point-in-time cutoff and availability/vintage mode.

A modification timestamp alone is not a source identity.

### Consistent per-store copies

An export reads immutable consistent copies, not raw copies of live WAL files.
The coordinator MUST:

1. resolve all source physical-store lock identities using the scheduling lock
   contract;
2. reject duplicate physical store identities and acquire the distinct set
   deterministically when non-overlap is required;
3. verify eligible collector-completion receipts and absence of an incomplete
   target run;
4. create a consistent temporary snapshot using SQLite's online backup API or
   an equivalently reviewed SQLite snapshot mechanism;
5. record the per-store snapshot identity and completion instant;
6. release writer locks as soon as the consistent copies exist; and
7. reopen only the copies in read-only/query-only mode for export.

No cross-store distributed transaction is implied. The manifest records each
store's own snapshot instant and the coordination window. It MUST state
`cross_store_atomic: false` unless a future design provides and proves a true
atomic mechanism. An export-time timestamp MUST not be mislabeled as one
single-database `as_of` across the four stores.

If a source receipt is failed, partial, missing where required, or newer than an
unreconciled write, export MUST fail or declare a registry-approved partial
scope. It MUST not guess completeness.

## Parquet representation contract

### Schema fidelity

The Parquet schema is generated from the registered typed export schema. It
MUST preserve:

- stable row/natural identity;
- dates as dates and instants as offset-aware normalized timestamps;
- original timezone/source representation when required for audit;
- decimal precision/scale where binary floating point would change meaning;
- units and frequency metadata;
- null versus zero/false/empty distinctions;
- explicit missing reason and quality flags where part of the source contract;
- availability, capture, revision, and source provenance needed by the export
  use case; and
- deterministic field order and compatible logical types.

The exporter MUST reject non-finite values unless the registered field contract
maps unavailable statistics to null with an explicit reason. It MUST not
silently fill, interpolate, resample, winsorize, revise, or drop observations.

Nested or repeated provenance MAY be normalized into companion Parquet
relations when that representation is registered. It MUST not be silently
stringified or flattened into ambiguous columns.

### Ordering and partitioning

Rows MUST have a total deterministic order defined by the registry. Set-like
fields and metadata collections also have stable ordering.

Partition keys MUST be low-cardinality, semantically stable, path-safe, and
declared in the export contract. Dynamic user-provided partitioning is
prohibited. Every partition has deterministic row order, row-group policy, and
filename derivation. Empty partitions are either omitted or represented by a
manifest entry according to one fixed rule.

Compression codec, level, row-group size, writer library, and library version
are physical-format metadata. They MUST be pinned for reproducible release
artifacts and recorded in the manifest.

## Export layout

An immutable export revision SHOULD use this logical layout:

```text
<export-root>/
  staging/<unique-export-attempt>/
  revisions/<semantic-dataset-id>/<export-revision-id>/
    manifest.json
    export-receipt.json
    schema.json
    data/<registered partitions and parquet files>
    checksums.sha256
  current/<semantic-dataset-id>  # atomic pointer/manifest, never authority
```

The concrete platform representation of `current` MAY be a small pointer file,
symlink, or registry record, but it MUST be updated atomically only after the
revision is complete. Consumers MUST be able to pin an exact revision instead
of following `current`.

## Staging and atomic promotion

Before writing, the exporter MUST resolve and prove that:

- the configured export root is an exact allowed directory;
- the staging target is a newly created unique child of the staging parent;
- the final revision target does not exist;
- staging and final revision directories are on a filesystem that supports the
  chosen atomic rename/promotion behavior; and
- no target is empty, `.`, a filesystem root, home, repository root, broad
  volume, operational data directory, or Atlas generated-output directory.

The exporter MUST never recursively clean a computed broad path. Failure
cleanup is limited to the exact proven unique staging child.

Promotion proceeds only after:

1. all files are closed;
2. row counts, min/max keys, schema, partitions, and bounds validate;
3. file and manifest checksums validate;
4. point-in-time, provenance, null, and finite-number audits pass;
5. the export receipt is final and internally consistent;
6. required flush/durability behavior completes; and
7. the exact final target is rechecked as absent.

The staging directory is then atomically renamed to the immutable revision
path. Only afterward is the optional `current` pointer atomically replaced.
Failure before revision promotion leaves the prior revision and pointer
unchanged. Failure after revision promotion but before pointer update leaves a
valid unreferenced immutable revision that can be reconciled from its receipt.

## Manifest and export receipt

### Manifest

The canonical strict-JSON manifest describes the reusable dataset. It MUST
include:

- semantic dataset and export revision IDs;
- lifecycle and completeness state;
- schema ID/digest and field metadata;
- point-in-time, availability, vintage, revision, missingness, and unit policy;
- source-store fingerprints and per-store snapshot instants;
- coordination window and cross-store atomicity statement;
- deterministic query/operation and exporter versions;
- partition/file inventory with rows, bytes, key ranges, and checksums;
- total rows/bytes and truncation/sampling state;
- provenance fields included and excluded;
- created instant and source/code revision when available; and
- compatible consumer contract versions.

### Export receipt

The private receipt describes one attempt and MUST include:

- attempt/run ID, plan digest, trigger type, start/finish, and outcome;
- resolved sanitized store aliases and lock order/waits;
- consistent-copy results and source receipt checks;
- rows read, accepted, rejected, and written by partition;
- validation, checksum, benchmark-policy, and promotion results;
- staging and final targets represented by safe aliases, not local paths;
- prior and new `current` revision IDs;
- warnings and bounded errors; and
- cleanup/reconciliation state.

The receipt MUST not contain credentials, raw paths, SQL, raw artifacts, or
provider payloads. It is atomically published and never overwritten.

## Invalidation and refresh

An export is invalid for reuse when any identity-bearing input differs,
including:

- a source migration/checksum set;
- source snapshot or high-water mark;
- dataset registry definition;
- semantic query, point-in-time, availability, or revision policy;
- typed schema or semantic dataset ID;
- exporter/primitive version;
- upstream derived export revision; or
- partition-plan version when it affects observable output.

Invalidation never mutates or deletes the old revision. A new attempt either
reuses an existing identical revision ID after complete checksum validation or
creates a new revision. Consumers following `current` see the new revision only
after atomic promotion; pinned consumers remain reproducible.

Physical-format-only changes MAY retain the semantic dataset ID but MUST create
a new export revision. If a source rollback or restoration changes authority,
the resulting revision links to that exact source state rather than overwriting
the prior export.

Retention and deletion are separate governance decisions and are not defined
by this export operation.

## Optional DuckDB use

DuckDB MAY be introduced only as a pinned optional development/analytics
dependency after the Parquet benchmark gate passes.

- It MUST NOT be imported by collectors, migrations, the core SQLite gateway,
  scheduler startup, or the required local portal runtime.
- It reads exact immutable local export revisions, not mutable `current` unless
  the caller first resolves and records the revision ID.
- It is non-authoritative and MUST NOT write back to SQLite or publish source
  provenance.
- Automatic extension installation, network access, remote object-store
  credentials, and unreviewed `ATTACH` behavior are disabled.
- Queries are server/developer-owned and bounded; public arbitrary SQL is not
  introduced by adopting DuckDB.
- Result semantics and strict-JSON validation remain governed by the typed
  analytical core, not DuckDB defaults.

DuckDB benchmark results MUST be compared with the SQLite baseline and, where
applicable, a dependency-light Parquet reader. Removing DuckDB must not make
the operational stores unreadable or uncollectable.

## Atlas separation

Quant Data Atlas is a separate, read-only, owner-only snapshot consumer. It is
not an operational database client and never writes back.

- An analytical Parquet export is not automatically an Atlas export.
- Atlas receives only registry-approved, credential-free, bounded projections.
- Local paths, secrets, raw artifacts, private receipts, unrestricted SQL, and
  internal-only fields MUST not appear in published data.
- Atlas-specific chunked JSON, manifest, schema metadata, sampling, and UI
  contracts are produced in a separate exact staging tree with a separate
  publication receipt.
- Missing, stale, partial, capped, and sampled datasets are labeled honestly.
- Atlas pins exact analytical/source revisions and records its own source code
  revision.
- Atlas deployment/private hosting is a separate explicit operation after
  snapshot validation and atomic promotion.

An Atlas failure cannot invalidate or mutate an analytical revision or an
operational SQLite store.

## Failure behavior

- Source unavailable, wrong-domain, migration-integrity, incomplete-receipt,
  lock-timeout, copy, query, bounds, schema, finite-number, checksum, and
  promotion failures are distinct outcomes.
- A failed export never updates `current`.
- A partial dataset is publishable only when the registry explicitly permits
  partial scope and the manifest identifies exact omissions; otherwise it
  fails.
- Retrying is safe only from a new unique staging directory. An existing final
  revision is reused solely after full identity and checksum validation.
- Cleanup is exact and recoverable; it never targets operational stores,
  repository roots, or previously promoted revisions.

## Acceptance evidence

The export layer remains Proposed until the applicable evidence is recorded.

### Authority and source safety

- Instrumented tests prove all source connections are read-only/query-only and
  never initialize or migrate.
- Raw live WAL copies are rejected; temporary source snapshots use the reviewed
  SQLite-consistent method.
- Four-store fixtures resolve physical locks according to the shared algorithm,
  and duplicate physical store identities fail closed.
- Cross-store manifests record per-store snapshot instants and never claim
  atomicity or one global `as_of` without proof.
- Operational SQLite remains sufficient when export and DuckDB packages are
  absent.

### Semantic equivalence

- Row identity, counts, ordering, dates/timestamps, decimals, units,
  availability, revisions, tombstones, restoration, nulls, quality flags, and
  provenance match independent SQLite fixture queries.
- Latest, historical `as_of`, and first-release exports cannot see future
  revisions.
- Range-invariance tests prove future source rows do not change an earlier
  point-in-time revision.
- Non-finite and ambiguous coercions fail closed.

### Determinism and invalidation

- Frozen identical inputs produce identical semantic IDs, revision IDs,
  manifests, schemas, partitions, row ordering, and checksums.
- Every identity-bearing source or semantic change produces a new revision.
- Physical-format changes create a new revision without falsely changing
  semantic identity.
- Old pinned revisions remain readable and unchanged after promotion.

### Atomicity and failure injection

- Tests interrupt every staging, validation, revision-promotion, and pointer
  update boundary.
- No pre-promotion failure creates a final revision or changes `current`.
- Post-revision/pre-pointer failure leaves a valid reconcilable revision and the
  old pointer.
- Existing final revisions are never overwritten.
- Cleanup proves its exact staging target remains inside the allowed parent.

### Benchmark and resource evidence

- The benchmark plan, environment, source fixture, thresholds, raw measurements,
  and conclusion are recorded before adoption.
- Correctness is identical across SQLite and the candidate reader.
- Runtime, memory, temporary disk, partitions, rows, and output bytes stay
  within declared bounds.
- A negative benchmark leaves the export disabled without affecting core
  functionality.

### DuckDB and Atlas

- Core installation and tests pass without DuckDB.
- Optional DuckDB tests prove no extension auto-install, network access,
  arbitrary public SQL, SQLite write-back, or authority claim.
- Atlas fixtures contain no local paths, secrets, raw artifacts, private
  receipts, or internal-only fields.
- Atlas staging/chunk/manifest validation and publication receipts are separate
  from analytical export receipts and operational runs.

## Change control

Changes to SQLite authority, semantic IDs, source fingerprinting, point-in-time
selection, Parquet schema, partitioning, invalidation, atomic promotion,
optional dependencies, or Atlas projections require integration-owner review.
Any proposal to make Parquet or DuckDB authoritative requires a new ADR and a
data-migration/recovery design; it cannot be inferred from successful
benchmarks.
