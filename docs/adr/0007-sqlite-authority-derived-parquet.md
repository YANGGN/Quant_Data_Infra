# ADR 0007: Keep SQLite Authoritative and Make Parquet Derived

## Status

Accepted

## Context

The system's operational semantics live in four SQLite stores. They include
migration integrity, ingestion runs, append-only artifacts and captures,
revision histories, availability, snapshot membership, tombstones, and
restoration. These relationships are transactional and are required for
point-in-time research and recovery.

Some analytical workloads may benefit from a columnar immutable representation.
Parquet and DuckDB are plausible tools, but adopting either as a new authority
would create dual-write, invalidation, migration, provenance, and recovery
problems before a measured need has been established. Atlas is also a separate
snapshot consumer rather than an operational store.

Related documents:

- [Architecture](../../ARCHITECTURE.md)
- [Analytical exports specification](../rebuild/ANALYTICAL_EXPORTS.md)
- [Scheduling and locking specification](../rebuild/SCHEDULING_AND_LOCKING.md)
- [System registry specification](../rebuild/SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](../rebuild/DATA_AND_TIME_CONTRACTS.md)
- [Roadmap](../../ROADMAP.md)

## Decision

Market, macro, company, and news SQLite databases will remain the sole
authoritative operational stores.

Parquet MAY be added only as an immutable derived analytical snapshot for a
registered semantic dataset after a predeclared benchmark and semantic-equivalence
gate passes. Every revision will bind to exact consistent SQLite source
snapshots, migration/checksum sets, semantic query and schema IDs, exporter
version, deterministic partitions, checksums, and an export receipt.

Derived revisions will be created under a unique exact staging directory,
fully validated, and atomically promoted to a never-overwritten revision path.
An optional `current` pointer is convenience state, not authority. Changes in
source or semantics create a new revision; they do not mutate the old one.

DuckDB MAY be a pinned optional development/analytical dependency over exact
local immutable Parquet revisions. It will not be required by collectors,
migrations, operational queries, scheduling, or the core local portal. It will
not write back to SQLite or expose public arbitrary SQL.

Atlas will remain a separate bounded, credential-free, read-only snapshot and
publication pipeline with its own staging, validation, and receipt.

## Consequences

### Positive

- Operational recovery and point-in-time authority remain in one transactional
  system per store.
- Analytical layout can be optimized without dual-write collectors.
- Immutable revisions are reproducible and easy for consumers to pin.
- Benchmarking prevents an optional dependency and format from being adopted
  without measured benefit.
- DuckDB can be removed without affecting collection or operational access.
- Atlas cannot accidentally become a live database client or write-back path.

### Negative

- Exported analytics are not immediately current and require invalidation and
  promotion machinery.
- Source snapshots, manifests, receipts, and checksums add storage and process
  overhead.
- Cross-store exports cannot claim a single atomic commit time; consumers must
  understand per-store snapshot instants.
- Some workloads continue to use SQLite when a benchmark does not justify an
  export.

### Risks and mitigations

- **Risk:** consumers treat Parquet as authoritative. **Mitigation:** authority
  is explicit in manifests and APIs; discrepancies invalidate the export and
  SQLite wins.
- **Risk:** a stale export is silently reused. **Mitigation:** revision IDs bind
  source fingerprints, migration checksums, semantics, schema, and exporter
  versions.
- **Risk:** raw WAL copying produces an inconsistent source. **Mitigation:** use
  SQLite's reviewed consistent snapshot/online backup method and shared
  physical-store locks when non-overlap is required.
- **Risk:** promotion exposes a partial dataset. **Mitigation:** unique staging,
  full checksums/audits, immutable final targets, and atomic pointer update.
- **Risk:** DuckDB adds network or extension behavior. **Mitigation:** keep it
  optional, pinned, local, extension-disabled, and outside public arbitrary SQL.
- **Risk:** Atlas leaks private operational detail. **Mitigation:** a separate
  allowlisted projection and publication receipt exclude paths, secrets, raw
  artifacts, and private receipts.

## Alternatives

### Make Parquet the operational source of truth

Rejected because append-only revisions, memberships, tombstones, transactional
ingestion, migration checksums, and recovery remain better defined in SQLite.

### Replace SQLite with DuckDB

Rejected because it expands the operational dependency and migration surface
without benchmark or recovery evidence and does not remove the need for
transactional provenance contracts.

### Dual-write SQLite and Parquet from collectors

Rejected because partial failure would create two authorities and require a
distributed publication protocol.

### Never permit analytical exports

Not selected because immutable columnar snapshots may provide measured value
for scans and reproducible research. The benchmark gate permits that value
without precommitting to it.

### Let Atlas read live SQLite directly

Rejected because Atlas is a separate static/read-only consumer and must not
access live local files, credentials, or private backend interfaces.

## Acceptance evidence

This decision is accepted only when:

- core collection, operational queries, and tests work with no Parquet or
  DuckDB dependency installed;
- a predeclared benchmark shows a material benefit for each enabled export
  family and retains negative results;
- independent fixtures prove row identity, time, units, missingness,
  availability, revisions, tombstones, restoration, and provenance are
  equivalent to exact SQLite queries;
- identical sources and contracts generate identical immutable revisions;
- every authority or semantic change invalidates reuse and creates a new
  revision;
- failure-injection tests prove staging and promotion never expose partial data
  or overwrite a prior revision;
- cross-store manifests record per-store snapshot instants and explicitly deny
  unproved global atomicity;
- optional DuckDB tests prove no operational requirement, extension/network
  auto-installation, arbitrary public SQL, or write-back; and
- Atlas uses a separate bounded projection, staging tree, validation contract,
  and publication receipt with no private data leakage.
