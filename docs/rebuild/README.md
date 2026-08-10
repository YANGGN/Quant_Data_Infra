# Rebuild Documentation Index

Status: Accepted  
Purpose: navigation, authority, and change rules for the Quant Data
Infrastructure rebuild documents

## 1. Scope

This directory converts the historical recovery specification in
[plan.md](../../plan.md) into accepted implementation contracts. Stage 0 was
accepted and the offline Stage 1 slice, Stage 2 four-store foundation, and
bounded Stage 3 market/macro fixture scope were implemented on 2026-08-09.
Stage 3 also passed independent verification. The bounded Stage 4 company,
news, and options primary fixture gate and independent SolUltra verification
passed on 2026-08-10. Later documented behavior is not considered
implemented until separately authorized executable evidence passes.

Two user decisions narrow the work:

- The lost Git history will not be searched for or reconstructed. A fresh
  baseline can be created when implementation begins.
- A dedicated provider-rights governance subsystem is out of scope.

Stages 4 through 6 are authorized only as sequential, offline synthetic-fixture
work. Stage 4 is independently verified; Stage 5 is the next authorized
executable stage and Stage 6 remains closed behind its gate.

## 2. Document map

### Direction and sequencing

- [Recovery evidence and historical specification](../../plan.md)
- [Target architecture](../../ARCHITECTURE.md)
- [Implementation roadmap](../../ROADMAP.md)
- [Parallel execution and agent cadence](PARALLEL_EXECUTION.md)
- [Migration reconstruction map](MIGRATION_RECONSTRUCTION.md)
- [Stage 1 acceptance evidence](STAGE1_EVIDENCE.md)
- [Stage 2 acceptance evidence](STAGE2_EVIDENCE.md)
- [Stage 3 acceptance evidence](STAGE3_EVIDENCE.md)
- [Stage 4 acceptance evidence](STAGE4_EVIDENCE.md)

### Detailed contracts

- [Single system registry](SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [First vertical slice](VERTICAL_SLICE_SPEC.md)
- [Composable tool platform](TOOL_PLATFORM_SPEC.md)
- [UI visual direction](UI_VISUAL_DIRECTION.md)
- [Scheduling and physical-store locking](SCHEDULING_AND_LOCKING.md)
- [Derived analytical exports](ANALYTICAL_EXPORTS.md)
- [Test strategy and acceptance](TEST_STRATEGY.md)

### Architecture decisions

- [ADR-0001: Four operational SQLite stores](../adr/0001-four-operational-sqlite-stores.md)
- [ADR-0002: Single machine-readable registry](../adr/0002-single-machine-readable-registry.md)
- [ADR-0003: Three-layer data architecture](../adr/0003-three-layer-data-architecture.md)
- [ADR-0004: Point-in-time availability model](../adr/0004-point-in-time-availability-model.md)
- [ADR-0005: Composable tool core](../adr/0005-composable-tool-core.md)
- [ADR-0006: Lock by physical store](../adr/0006-lock-by-physical-store.md)
- [ADR-0007: SQLite authority and derived Parquet](../adr/0007-sqlite-authority-derived-parquet.md)
- [ADR-0008: Fresh store-local reconstruction migrations](../adr/0008-fresh-store-local-reconstruction-migrations.md)

## 3. Authority and conflict handling

Before executable artifacts exist, use this order:

1. Explicit user decisions.
2. Accepted ADRs.
3. `ARCHITECTURE.md` and the focused contract specifications.
4. `ROADMAP.md` for sequence and gates.
5. `plan.md` for recovery evidence and historical context.

Within `plan.md`, Sections 18–20 supersede earlier sections where they
conflict. A remembered historical behavior is not automatically a target
decision.

After implementation begins, validated runtime behavior, immutable applied
migrations, and executable schemas become evidence. If they conflict with an
accepted document, record and resolve the conflict; do not silently change
either history or semantics.

## 4. Status vocabulary

- `Proposed`: drafted but not approved for implementation.
- `Accepted`: approved as the current target contract.
- `Superseded`: replaced by a linked decision or specification.
- `Implemented`: supported by executable evidence and acceptance results.
- `Rejected`: considered and intentionally not adopted.

The current planning package is `Accepted`. Stage 1 and the offline Stage 2
foundation have recorded fixture-validated evidence, and Stage 2 has passed
independent verification. The bounded offline Stage 3 scope has passed its
primary fixture gate and independent SolUltra verification. The bounded Stage
4 scope has passed its primary fixture gate and independent SolUltra
verification. Stage 5 is the next authorized executable stage. Only a
sequentially authorized roadmap stage may be implemented.

## 5. Shared terminology

- **Evidence layer:** immutable provider response, controlled input, request
  metadata, capture time, content identity, and ingestion receipt.
- **Canonical layer:** normalized facts with stable identity, explicit version
  or current-state policy, availability, and evidence lineage.
- **Research layer:** derived features, panels, statistics, backtests, and
  exports tied to exact canonical inputs and code/configuration versions.
- **Effective time:** when a fact applies in the source domain.
- **Available time:** earliest defensible point when the system or researcher
  could know the fact.
- **Captured time:** when this system obtained the evidence.
- **As-of cutoff:** latest allowed availability boundary for a query.
- **Operational store:** authoritative SQLite database receiving canonical
  writes.
- **Derived export:** disposable, reproducible analytical representation such
  as Parquet or an Atlas snapshot.
- **Physical lock identity:** canonical identity derived from the resolved
  database file, independent of logical job or domain name.

## 6. Change workflow

1. Identify the affected contract and ADRs.
2. Update the decision first if architecture or semantics change.
3. Update the focused specification and its acceptance criteria.
4. Update the roadmap only if sequencing or gates change.
5. Preserve recovery evidence in `plan.md`; add a dated clarification rather
   than rewriting remembered history.
6. Check all relative links and terminology.

## 7. Planning-package acceptance

This documentation package is ready for implementation review when:

- every linked document exists and has an explicit status;
- no specification claims unimplemented behavior is present;
- store ownership and layer terminology are consistent;
- point-in-time and physical-lock contracts agree across documents;
- the vertical slice has deterministic offline acceptance criteria;
- the roadmap contains no live-data step before its prerequisites; and
- all open questions are assigned to a stage rather than hidden in prose.
