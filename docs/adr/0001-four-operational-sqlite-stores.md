# ADR 0001: Four Operational SQLite Stores

## Status

Accepted

## Context

The platform spans market, macroeconomic, company, options, and news workloads with very different size, cadence, correction, and failure characteristics. One ever-growing SQLite file would couple unrelated writers, locks, migrations, backups, health, and recovery. At the other extreme, one database per provider or dataset would make canonical identity, operations, and cross-domain discovery unnecessarily fragmented.

Later recovery evidence confirms four mature operational domains and exact default paths. It also confirms that options belonged to the market domain and that company and news were not merely proposed filenames. SQLite cannot enforce foreign keys across files, so the split must be paired with stable application identities and bounded application-level composition.

See the [architecture](../../ARCHITECTURE.md), [registry specification](../rebuild/SYSTEM_REGISTRY_SPEC.md), and [three-layer decision](0003-three-layer-data-architecture.md).

[ADR 0009](0009-canonical-market-operational-path.md) supersedes only this
ADR's original market default-path field; the four-store decision and all
other ownership, override, locking, and backup semantics remain accepted.

## Decision

Use four operational SQLite stores:

| Store | Default path | Override | Scope |
| --- | --- | --- | --- |
| market | data/market.sqlite | QUANT_MARKET_DB_PATH | Instruments, identifiers, classifications, shared daily prices, and options |
| macro | data/macro.sqlite | QUANT_MACRO_DB_PATH | Calendar, GDP, macro observations and vintages, rates, liquidity, SOMA summaries, recession chronology, and energy |
| company | data/company.sqlite | QUANT_COMPANY_DB_PATH | SEC issuers, filings, facts, normalized fundamentals, corporate actions, shares, earnings, consensus, and guidance |
| news | data/news.sqlite | QUANT_NEWS_DB_PATH | Immutable captures, logical items and versions, labels, retractions, and search |

Registry `2.15.0` aligns these current basenames with the already-existing
project-local files. Historical registry projections retain the prior names
where required to reproduce accepted evidence; they are not runtime aliases.

The boundary is operational:

- each store has its own migration ledger, applied dataset registry, ingestion runs, WAL and foreign-key configuration;
- each resolved physical file has one write lock, backup, health, integrity, restore, and failure boundary;
- each dataset has exactly one owning store in the future system registry;
- network fetches occur outside transactions and writers use short store-local transactions;
- tools and dashboard routes resolve stores from dataset ownership and open them read-only; and
- cross-store joins use bounded result sets, stable identities, one explicit availability cutoff, and contributing-store receipts.

ETF, equity, and index prices share the market instrument master and daily-price fact family. Options are part of market, not a fifth store.

The legacy `data/quant_data.sqlite` and `QUANT_DB_PATH` pair is not an operational store. Unified runtime compatibility is retired. Missing split-store configuration must not fall back to it; any future one-shot import requires a separate decision and is not a runtime layout.

Physical resources, rather than logical names, determine coordination. The four configured store roles must resolve to four distinct physical files; aliasing fails closed before use.

## Consequences

Positive:

- High-volume market or company writes do not hold macro or news write locks.
- Migrations, backups, integrity checks, health, and restore drills can fail or progress independently.
- Dataset ownership and read routing are explicit.
- Domain-specific retention and collector cadence can evolve without turning every operation into a global event.
- Atlas and cross-domain tools can state exactly which store snapshots contributed.

Costs and constraints:

- SQLite cannot enforce cross-store foreign keys.
- Cross-domain queries require application composition and stable dated identifier mappings.
- There is no implicit atomic transaction or single as-of instant across four live stores.
- Operators must coordinate four migration ledgers, locks, backups, and health states.
- Path aliasing must be detected and rejected before a store is opened for work.

## Alternatives

### One unified SQLite database

Rejected as a runtime mode because it couples write contention, migrations, backup size, corruption/failure radius, and operational cadence.

### One database per provider

Rejected because provider boundaries are not canonical data boundaries. It would duplicate identities and force routine provider-neutral queries to become cross-database joins.

### One database per dataset or symbol

Rejected because it creates excessive files, migrations, locks, and routing metadata. In particular, price storage must not fragment by ticker or asset type.

### A client/server warehouse

Deferred. It adds operating complexity that is not justified for a local, single-operator, SQLite-centered research platform.

## Acceptance evidence

- A validated system registry declares exactly the four stores, paths, overrides, and every dataset owner.
- All four stores initialize and migrate independently at temporary paths with no fallback to live/default files.
- Each store contains and reconciles its own schema_migrations, dataset_registry, and ingestion_runs state.
- Concurrent-write tests prove independent stores do not share locks.
- Same-file, symlink, and normalized-path alias fixtures fail configuration validation.
- Cross-store query tests apply one availability cutoff, deterministic ordering, bounded in-memory joins, and store receipts.
- Missing or unhealthy stores degrade only their dependent routes and return structured dependency failures.
- WAL-aware backup and restore drills validate integrity and migration/dataset ownership for each store.
