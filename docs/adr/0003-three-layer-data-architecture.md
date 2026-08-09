# ADR 0003: Three-Layer Data Architecture

## Status

Accepted

## Context

Point-in-time research needs to distinguish what was captured, what the platform currently considers a canonical fact, and what an analysis derived from those facts. Collapsing these concerns makes it easy to overwrite source history, treat local capture time as source release time, or feed an analytical transformation back into canonical storage without provenance.

Recovery evidence constrains the target concepts: artifacts, snapshots, and membership; versioned facts such as price corrections, macro observation versions, company fact versions, per-capture option observations, and news versions; and read-only research tools and Atlas exports. The target architecture needs one shared vocabulary for these responsibilities without requiring three physical databases per domain.

See the [architecture](../../ARCHITECTURE.md), [registry specification](../rebuild/SYSTEM_REGISTRY_SPEC.md), [store decision](0001-four-operational-sqlite-stores.md), and [registry decision](0002-single-machine-readable-registry.md).

## Decision

Classify every registered dataset into exactly one of three logical layers.

### Immutable evidence

Evidence records what was obtained, when, under which request scope, and with what identity. It includes sanitized request metadata, artifacts or content hashes, raw source time and precision, captures/snapshots, snapshot membership, and provenance.

Evidence is append-oriented. Materially changed source content produces new evidence. Retractions and corrections do not erase the earlier capture. For an if-new collector, semantic identity is evaluated in an ephemeral buffer first; an unchanged response creates no artifact, snapshot, membership, ingestion run, or canonical write.

### Canonical versioned facts

Canonical facts provide stable provider-neutral identities, normalized units and fields, explicit natural/version keys, availability, and traceability to evidence. Examples include instruments and dated identifiers, daily prices and correction versions, macro series and observation versions, SEC filing/fact versions, corporate actions, option contracts and per-capture surface observations, and news item versions.

Changed facts append versions according to the dataset's declared revision policy. Identical replay is a no-op. Current-state sources are not relabeled as historical vintages, and first local capture is not automatically first release.

### Derived research

Derived research transforms canonical inputs into returns, alignments, descriptions, regressions, indicators, event studies, walk-forward results, diagnostics, reports, caches, or Atlas files.

Every result declares input dataset versions or receipts, availability cutoff, transformations, fill/exclusion policy, warnings, and bounds. Derived results are read-only by default. Persistent materialization is allowed only as a separately registered derived dataset with explicit lineage and rebuild semantics.

The layers are logical and may coexist in one operational domain store. Flow is evidence to canonical to derived. A later layer never silently mutates an earlier layer or becomes its source. Atlas is a derived snapshot consumer and never a canonical database client.

## Consequences

Positive:

- Source capture and canonical interpretation can be audited independently.
- Corrections, revisions, retractions, and local-capture limitations remain visible.
- Point-in-time eligibility can be enforced from explicit availability evidence.
- Analytical functions remain composable and side-effect free.
- Atlas publication can be rebuilt without changing operational truth.
- Quality checks can identify whether a problem arose during fetch, normalization, or analysis.

Costs and constraints:

- Evidence and version history consume more space than current-state upserts.
- Dataset schemas need explicit identity, temporal, revision, and lineage contracts.
- Consumers must select latest, as_of, first_release, or local-capture semantics deliberately.
- Garbage collection and retention cannot discard evidence needed by active facts or published receipts.
- Derived caches and materializations need invalidation keyed by input/registry/store receipts.

## Alternatives

### Store only normalized current state

Rejected because it cannot explain corrections, point-in-time availability, source changes, or retractions.

### Two layers: raw and curated

Rejected because it conflates canonical facts with analytical outputs and encourages derived values to acquire unwarranted authority.

### A generic bronze/silver/gold model

Rejected as the primary vocabulary because immutable evidence, versioned canonical truth, and point-in-time derived research are more precise for this platform. The decision is compatible with similar physical organization where useful.

### Separate database per layer

Rejected because layers are semantic contracts, while the four stores are operational write/failure boundaries. Multiplying files would complicate transactions, locks, backup, and routing without improving ownership.

### Make every derived result persistent

Rejected because most analytical outputs are parameter-dependent and inexpensive to recompute. Persistence is an explicit exception with registered lineage.

## Acceptance evidence

- The system registry requires exactly one supported layer for every dataset.
- Fixtures trace each representative canonical row to source evidence and an ingestion outcome.
- Changed, unchanged, correction, revision, retraction, partial, and failure cases preserve the expected layer boundaries.
- Unchanged if-new fixtures prove zero persistent writes across all layers.
- Date-only and offset-aware values retain source precision; latest, as_of, first_release, and local-capture-only behavior are tested.
- Missing values retain explicit reasons; no default path silently fills, resamples, interpolates, converts currency, changes price variant, or mixes option captures.
- Derived tool results report inputs, cutoff, transformations, exclusions, warnings, truncation, and finite JSON values.
- Persistent derived datasets have declared lineage, invalidation, and deterministic rebuild tests.
- Atlas export reads registered datasets through read-only copies, records store receipts, validates a complete staged cohort, and cannot write back to operational stores.
- Store schemas and service APIs never rely on layer names as a substitute for dataset ownership or physical locking.
