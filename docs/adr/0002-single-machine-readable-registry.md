# ADR 0002: One Machine-Readable System Registry

## Status

Accepted

## Context

Store paths, migration order, dataset ownership, collectors, freshness, locks, tool schemas, dashboard allowlists, and Atlas exports affect many components. Defining them separately in Python registration code, scheduler scripts, dashboard routes, and exporter configuration invites drift. Store-local tables record applied runtime state but cannot safely define components that must be known before a database is opened.

The operational core is intended to remain Python 3.11 and standard-library-heavy. The registry therefore needs a simple, versioned representation that can be loaded and validated without importing handlers, opening a database, contacting a network, or installing a framework.

See the [detailed registry specification](../rebuild/SYSTEM_REGISTRY_SPEC.md), [architecture](../../ARCHITECTURE.md), [store decision](0001-four-operational-sqlite-stores.md), and [layer decision](0003-three-layer-data-architecture.md).

## Decision

Create one canonical, versioned, machine-readable system registry. The first implementation should use strict UTF-8 JSON so the Python standard library can load it. The registry is the declarative source of truth for:

- four store definitions and environment override names;
- ordered immutable migration resources and reviewed checksums;
- dataset identity, version, store owner, layer, physical relations, temporal policy, quality, and freshness;
- collector inputs/outputs, semantic no-write identity, mutation policy, bounds, and retry classes;
- jobs and their ordered collector/export steps;
- physical lock and backup needs derived from resolved output-store paths;
- fixed read-only tool schemas, dependencies, and workload bounds;
- dashboard routes, relation/filter/sort allowlists, and pagination; and
- analytical and Atlas export profiles.

The registry is declarative configuration. Store-local schema_migrations, dataset_registry, and ingestion_runs remain runtime evidence and are reconciled against it.

The loader:

- receives an explicit registry path/project root/environment mapping;
- never reads .env or stores secret values;
- rejects duplicate keys, unsupported versions, unsafe paths, empty or duplicate IDs, unsupported layers/formats, unknown references, invalid migration order, and incomplete temporal/freshness contracts;
- recursively requires strict object schemas for exposed tools and dashboard filters;
- rejects raw SQL, arbitrary relation, connection-string, and database-path arguments;
- resolves absolute store paths before deriving physical locks; and
- produces deterministic iteration and generated output.

Consumers fail closed when the registry or runtime reconciliation is invalid. Generated navigation, forms, ownership maps, scheduler inputs, and export manifests carry the registry version/hash and are never edited as an alternate source.

The registry describes reviewed handlers and migration resources; it does not generate executable Python or SQL from prose. An object marked proposed or unresolved cannot be run merely because it parses.

## Consequences

Positive:

- Dataset ownership and configuration have one reviewable source.
- Migration, collector, scheduler, tool, dashboard, health, and export behavior can be checked for referential consistency before startup.
- Physical lock collision and unsafe-path behavior become testable centrally.
- Documentation and guided forms can be generated deterministically.
- Store-local runtime drift is detectable rather than silently tolerated.

Costs and constraints:

- The schema, loader, validator, and compatibility policy become critical infrastructure.
- Registry changes require coordinated contract versioning and review.
- A malformed or stale registry intentionally prevents affected writes or exposure.
- Some application invariants still require handler and migration fixture tests; declaration alone is not proof.
- Generated artifacts need check-mode tests to prevent a second source of truth.

## Alternatives

### Registration exclusively in Python code

Rejected because discovering ownership or validating references would import application code, and other consumers would duplicate or introspect runtime behavior.

### Separate configuration per subsystem

Rejected because store, dataset, tool, scheduler, dashboard, and exporter declarations would drift.

### Store the registry only inside SQLite

Rejected because path and migration ownership are needed before opening or initializing a store, and four stores could disagree.

### YAML as the canonical format

Rejected for the initial core because Python 3.11 has no standard-library YAML parser. YAML may appear only as explicitly non-executable documentation.

### Generate the registry from migrations or handlers

Rejected because neither source expresses the complete cross-component contract, and generation would hide reviewable ownership decisions in implementation details.

## Acceptance evidence

- Python 3.11 standard library loads the canonical JSON without importing application modules or reading .env.
- Static fixtures cover duplicate keys, versions, types, IDs, references, cycles, paths, layers, formats, temporal/freshness fields, strict schemas, and prohibited path/SQL arguments.
- The four exact store defaults and environment names validate.
- Dataset, collector, job, tool, dashboard, and export dependencies resolve in deterministic order.
- Required stores and locks derive from dataset outputs; a same-file override produces one physical lock.
- Active migration resources exist, are ordered per store, and match reviewed checksums and store ledgers.
- Tool/API and dashboard schemas are bounded, read-only, and reconcile with registered dataset ownership.
- Atlas export profiles resolve explicit owner stores and produce a manifest carrying registry and dataset versions.
- Generated artifacts reproduce byte-for-byte in check mode and identify the registry version/hash.
- Proposed or unresolved declarations cannot execute; active startup fails on registry/runtime drift.
