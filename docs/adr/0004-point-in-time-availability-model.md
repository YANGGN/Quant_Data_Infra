# ADR 0004: Point-in-Time Availability Model

## Status

Accepted

## Context

Quant research needs to answer two different questions:

1. what value is currently stored for an economic period or market session; and
2. what exact version was defensibly usable at a historical cutoff.

Those questions cannot be answered from an event date or observation period
alone. The recovered system spans:

- daily market bars with later price corrections;
- true macro vintage sources such as RTDSM;
- current-state sources for which only local capture history exists;
- date-only releases whose intraday publication time is unknown;
- exact offset-bearing publication/capture values;
- immutable filings, news, and option captures;
- retractions, snapshot disappearance, and restorations; and
- composite research over four independent SQLite files.

Earlier planning text sometimes described current keyed price facts as updates.
Later recovery evidence controls: migration 0014 added price correction
versions, and recovered physical tables include `prices_daily_versions`.
Section 20.3 of `plan.md` also recovers a semantic no-write gate: unchanged
content creates no ingestion run, artifact, snapshot, membership, or fact row.

The erased byte-exact DDL and migration checksums are not available. This ADR
chooses prospective behavior; it does not label proposed schema as recovered.

Related documents:

- [System architecture](../../ARCHITECTURE.md)
- [Roadmap](../../ROADMAP.md)
- [Data and time contracts](../rebuild/DATA_AND_TIME_CONTRACTS.md)
- [Vertical slice](../rebuild/VERTICAL_SLICE_SPEC.md)
- [Test strategy](../rebuild/TEST_STRATEGY.md)
- [System registry](../rebuild/SYSTEM_REGISTRY_SPEC.md)
- [Tool platform](../rebuild/TOOL_PLATFORM_SPEC.md)
- [ADR 0001: four operational SQLite stores](0001-four-operational-sqlite-stores.md)
- [ADR 0003: three-layer data architecture](0003-three-layer-data-architecture.md)
- [ADR 0005: composable tool core](0005-composable-tool-core.md)

## Decision

### 1. Model distinct temporal meanings

Canonical rows keep distinct fields rather than one overloaded timestamp:

| Field | Decision |
| --- | --- |
| `period_start` / `period_end` | The reference interval measured by an observation |
| `effective_at` | When an event, membership, classification, or fact applies economically |
| `trade_date` | The source market-session date represented by a daily bar |
| `vintage_at` | Provider-defined release/vintage identity |
| `published_at` | Source-claimed publication value, when independently supplied |
| `available_at` | Earliest defensible boundary for using this exact canonical version |
| `captured_at` | When this installation obtained the evidence |
| `ingested_at` | Local commit time for operations/audit |
| `supersedes_version_id` | The prior immutable version changed by this version |
| `tombstone_at` | Availability boundary of an evidenced removal/retraction |

No field is silently substituted for another. In particular:

- an observation period or event date is not availability;
- publication is not local capture;
- local capture is not historical source availability unless the dataset
  explicitly declares local-capture semantics; and
- ingestion order is not source vintage order.

### 2. Preserve source precision and representation

Calendar dates are strict ISO `YYYY-MM-DD`. A real datetime is ISO 8601 with an
explicit offset or `Z`. Naive datetimes are rejected.

The canonical value and its precision (`date`, `datetime`, `inferred`, or
`unknown`) are stored and returned. Offset-bearing values retain their source
offset representation. Date-only values are never expanded to midnight or
converted to UTC.

Implementations may derive an internal instant-ordering key for two
offset-aware datetimes. They may not replace the stored/returned representation
or fabricate an ordering instant for date-only data.

### 3. Make availability basis a dataset capability

Each dataset registration declares one availability basis:

- `source_release`: supported by defensible release/publication evidence;
- `source_vintage`: a provider vintage encodes revision history and has a
  defensible availability boundary;
- `local_capture`: history begins when this installation captured each state;
- `inferred`: a reviewed inference is retained and labeled; or
- `unknown`: point-in-time selection is unsupported.

It also declares supported modes: `latest`, `as_of`, and/or
`first_release`.

A current-state source with retained captures can support
`local_capture_as_of`. It cannot present a current value as source-historical
truth. A series with unknown availability supports `latest` only unless a
later reviewed contract adds evidence.

### 4. Use one explicit cutoff grammar

`as_of` accepts:

- a date, interpreted as the inclusive source-local calendar date; or
- an offset-aware datetime, interpreted as an exact instant.

The same cutoff text and policy are passed unchanged through tool, service, and
repository layers.

Mixed precision follows these rules:

- date availability versus date cutoff: include when availability date is on
  or before the cutoff;
- datetime availability versus date cutoff: include when the datetime's
  source-local calendar date is on or before the cutoff;
- datetime availability versus datetime cutoff: compare exact instants using
  their stated offsets;
- date availability versus datetime cutoff: default to `completed_date`,
  which includes only dates strictly before the cutoff's written calendar
  date; and
- inferred or unknown availability: exclude unless an explicit, supported
  inference policy is requested.

The optional `calendar_date_inclusive` policy may include date-only
availability on the same written date as a datetime cutoff. It must add a
warning and mark same-day intraday safety as unestablished.

This rule treats a date cutoff as a whole-calendar-date question; it does not
assign a clock time to either operand.

### 5. Select versions by evidence, not convenience

For each natural reference key:

- `latest` selects the newest currently stored state under declared source
  revision order;
- `as_of` filters to versions eligible under `available_at`, availability
  basis, cutoff precision, and date-only policy, then selects the newest
  eligible source revision; and
- `first_release` selects the earliest defensible provider release/stage.

The first row captured or ingested is not automatically a first release.
Unsupported modes fail with a structured capability error.

Every result returns selected version IDs and an audit containing:

- requested and actual mode;
- cutoff and cutoff precision;
- date-only policy;
- availability basis;
- source artifact/snapshot/capture identity;
- dataset/store fingerprints;
- truncation and missingness; and
- warnings and point-in-time-safety assessment.

### 6. Append corrections, tombstones, and restorations

Canonical versions are immutable.

- A changed value appends a version and links to the version it supersedes.
- A current projection may move to the new version, but the prior version is
  never overwritten or deleted.
- An exact semantic replay is a total no-write.
- A disappearance becomes a tombstone only after a successful authoritative
  complete snapshot or explicit provider retraction for the exact scope.
- Partial/error/timeout/rate-limit outcomes never authorize tombstones.
- A later return appends a restoration version that supersedes the tombstone.

As-of selection applies the same availability rules to corrections,
tombstones, and restorations.

### 7. Preserve explicit missingness

An explicit missing observation is a canonical row with null value and a
reason/status. It is distinct from a period absent from source scope, a parse
rejection, and a tombstone.

Native queries do not fill, resample, interpolate, substitute price variants,
blend providers/units/currencies, or turn missing values into zero. Any
supported transformation is an explicit input and appears in lineage.

### 8. Apply one context across stores

A composite market/macro/company/news query uses one immutable query context:

- cutoff text and precision;
- date-only policy;
- dataset-specific availability basis;
- provider/variant/unit/currency dimensions; and
- component limits.

Each owner store is opened read-only. Because four SQLite files do not share an
atomic transaction, the result reports per-store migration/data fingerprints
and read boundaries. It fails when the requested analysis requires a coherent
cohort that cannot be established; otherwise it labels the result
`best_effort_multi_store`.

Missing components remain missing. A composite never substitutes today's
value for a value unavailable at the cutoff.

## Consequences

### Positive

- Historical queries have a precise, auditable meaning.
- Date-only sources do not acquire fictional intraday precision.
- True source vintages and local-capture histories cannot be confused.
- Price and macro corrections remain reproducible at earlier cutoffs.
- Retraction/restoration history survives without destructive updates.
- Tool composition can carry one common temporal and lineage envelope.
- Cross-store research exposes coherence limits instead of hiding them.

### Costs and constraints

- Version, artifact, snapshot, and membership tables consume more storage than
  current-state upserts.
- Importers must identify semantic equality and completeness before writing.
- Queries need deterministic version selection and mixed-precision logic.
- Some same-day date-only observations are unavailable to intraday research
  under the conservative default.
- Some datasets can support only `latest` or local-capture as-of behavior.
- Cross-store results need fingerprints/receipts and cannot claim one SQLite
  transaction across all domains.
- Current projections and caches must be invalidated by semantic version
  identity, not only by row counts or file modification times.

These costs are intentional: the alternative is silent look-ahead or
irreproducible research.

## Alternatives considered

### Use effective/event date as availability

Rejected. It makes filings, corporate actions, macro releases, corrections,
and news appear knowable before evidence supports them.

### Use local capture for every source

Rejected as a universal model. It discards defensible provider release/vintage
history and makes a backfill look newly created. Local capture remains a
supported, explicitly labeled basis for current-state sources.

### Normalize every temporal value to UTC

Rejected. It fabricates an instant for date-only values and loses source-local
representation. Exact offset-bearing datetimes can still be ordered by instant
without rewriting their stored form.

### Treat a date as midnight or end of day

Rejected. Both invent a time. The chosen calendar-date and
`completed_date` policies state comparison semantics without serializing a
fictional timestamp.

### Overwrite current facts

Rejected. It prevents correction history, earlier as-of results, and source
audit. A mutable current projection is permitted only when backed by immutable
versions.

### Define first release as first local row

Rejected. Backfill order is operational accident, not release-stage evidence.

### Join stores using ticker and current value

Rejected. Tickers change and current values can violate the requested cutoff.
Stable identities, one cutoff, bounded read-only queries, and per-store
evidence are required.

### Build a full generic bitemporal database abstraction first

Deferred. The selected fields and version rules cover the recovered domains
without a speculative framework. A future abstraction must preserve this
contract and prove migration compatibility.

## Acceptance evidence

This ADR moves beyond Proposed only when current executable evidence includes:

| Evidence ID | Required proof |
| --- | --- |
| `TIME-001` | Strict date and offset-aware datetime parsing; naive/malformed values rejected |
| `TIME-002` | Source representation and precision round-trip without fabricated midnight or timezone rewrite |
| `TIME-003` | Full date/date, date/datetime, datetime/date, datetime/datetime comparison matrix |
| `TIME-004` | `completed_date` same-day exclusion and explicit `calendar_date_inclusive` warning |
| `TIME-005` | Inclusive 2026 monthly period-range regression |
| `ING-001` | Exact semantic replay writes zero run, artifact, snapshot, membership, fact, and checkpoint rows |
| `ING-002` | Changed price and macro payloads append versions while prior versions remain byte-for-byte unchanged |
| `ING-003` | Partial/error input cannot tombstone; complete removal and restoration append valid state transitions |
| `MAC-001` | Synthetic revision fixture yields different expected latest, as-of, and first-release results |
| `MKT-001` | Price correction fixture yields expected version before and after its availability boundary |
| `INV-001` | Generated supersession chains are acyclic and earlier as-of outputs are invariant under later inserts |
| `REG-001` | Tool/API audit echoes cutoff, precision, policy, basis, selected versions, and warnings in strict JSON |
| `API-001` | Read-only tool/dashboard execution produces no store fingerprint change |
| `DR-001` | Two clean temporary rebuilds produce equal logical manifests |

The detailed cases and promotion gates are in
[Test strategy](../rebuild/TEST_STRATEGY.md). Until that evidence exists, callers
must treat this ADR and all dependent point-in-time claims as proposed.
