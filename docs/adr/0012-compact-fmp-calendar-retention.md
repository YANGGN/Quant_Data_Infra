# ADR 0012: Compact Future FMP Calendar Retention

## Status

Accepted -- 2026-08-24

## Context

Migration `macro:0016_fmp_calendar_wholesale_evidence` preserves each
material FMP economic-calendar response as an immutable response body plus all
of its raw rows. Exact semantic replay already writes nothing, but any material
change in a response causes the complete batch to be retained again. A
900-event response with one changed event therefore adds another response body
and 900 raw rows.

That representation remains valid historical evidence for the completed
backfill and local replays. Rewriting or deleting it would violate applied
migration and evidence immutability. It is unnecessarily expensive for future
recurring current-window polls.

FMP's generic calendar feed does not provide a stable provider event ID or an
authoritative cancellation signal. Current membership and locally observed
correction history therefore need separate representations.

## Decision

Keep all migration 0016 captures and rows immutable and readable. Remove the
legacy evidence dataset from the live wholesale collector's output binding,
but do not delete, migrate, compact, or reinterpret its stored history.

Add forward-only macro migration
`macro:0018_fmp_calendar_incremental_events` after the accepted GDI migration
at ordinal 17. Future material polls use three compact pieces:

1. one immutable receipt describing the changed batch and its control-plane
   lineage, without copying the full response into the immutable artifact;
2. one append-only raw event version for each new or changed event; and
3. one replaceable, non-authoritative latest-response cache row for the stable
   `fmp_us` feed.

The conservative event identity is `provider + country + event_at +
event_name + currency`. Unit and all other provider fields remain versioned
content. Identical duplicate event objects in one response collapse; two
different objects with the same conservative identity fail before a
transaction.

The source semantic identity continues to cover the request scope,
normalization version, and complete normalized batch. The persistence identity
also includes the predecessor receipt for a material transition. This makes an
immediate equivalent replay a zero-write while ensuring A to B to A appends a
third correction instead of being mistaken for the first A.

The singleton cache is the authority for the Inspector's current membership,
including disappearance from the latest response. It is not immutable evidence
and is replaced only by a material batch. The Inspector's explicit history
mode combines validated legacy changes with append-only 0018 event versions.
A rescheduled event has a new identity; absence is not converted into a
tombstone because the source provides no authoritative cancellation contract.

Registry `2.40.0` owns the receipt/cache relations in
`macro.fmp.economic_calendar_incremental_evidence` and the event/version
relations in `macro.fmp.economic_calendar_incremental_events`. The existing
collector ID outputs those two datasets. Its request bound, credential
resolver, one-attempt behavior, physical-store lock, and established scheduler
cadence do not change.

## Consequences

- An unchanged poll adds no persistent rows and does not rewrite the cache.
- A response with one changed event normally adds one receipt, one event
  version, and the ordinary small control-plane records, then replaces the
  singleton cache.
- Full response bytes are retained once as current operational replay state,
  not once per material historical batch.
- Legacy 0016 history remains available without a backfill, rewrite, or
  destructive cleanup.
- History records locally observed corrections, not source-publication
  vintages or authoritative cancellations.

## Non-authorization

This decision does not authorize a provider request, credential use, canonical
store migration or write, historical-population repeat, scheduler change,
deployment, public exposure, or deletion/compaction of migration 0016 data.

Canonical publisher construction requires the exact reviewed migration-0018
ledger entry and all four incremental relations. Without them, the calendar
refresh fails closed before credential resolution or a provider request.

## Operational activation

On 2026-08-24 the user explicitly authorized applying only migration 0018 to
the canonical macro store. The reviewed one-store runner applied it to
`data/macro.sqlite` at `2026-08-25T01:31:39.017401Z`. The post-check found
the exact ordinal-18 ledger row, four empty new tables, three indexes, twelve
triggers, registry-2.40 dataset ownership, clean integrity and foreign keys,
and unchanged legacy counts of 62 captures and 47,754 rows. The publisher's
pre-network gate then passed. No provider request, credential read, historical
population, scheduler change, or migration-0016 cleanup occurred.

## Related records

- [Rebuild documentation index](../rebuild/README.md)
- [Current operating envelope](../rebuild/CURRENT_OPERATING_ENVELOPE.md)
- [System registry specification](../rebuild/SYSTEM_REGISTRY_SPEC.md)
- [Migration reconstruction map](../rebuild/MIGRATION_RECONSTRUCTION.md)
- [Scheduling and locking](../rebuild/SCHEDULING_AND_LOCKING.md)
- [ADR 0008: fresh store-local reconstruction migrations](0008-fresh-store-local-reconstruction-migrations.md)
