# ADR 0011: Retire the Proposed BLS CPI Original-Release Archive

## Status

Accepted -- 2026-08-21

## Context

The accepted 2026-08-20 CPI-surprise policy takes both actual and consensus
from FMP economic-calendar events. It deliberately does not require an
official BLS original-release page to calculate or validate a CPI surprise.

A working, never-active registry candidate `2.22.0` nevertheless proposed a
separate BLS CPI original-release archive: migration
`macro:0017_bls_cpi_release_archive`, resource
`quant_data/migrations/macro/0017_bls_cpi_release_archive.sql`, evidence
and canonical datasets `macro.bls.cpi_release_archive_evidence` and
`macro.bls.cpi_release_archive`, collector
`bls.macro.cpi_release_archive_history`, handler
`macro.bls_cpi_release_archive_history`, and relations
`bls_cpi_release_archive_captures`, `bls_cpi_release_archive_releases`, and
`bls_cpi_release_archive_facts`. The candidate proposed original-release
pages from 2013-01 through 2026-07, four Table A CPI actuals, and an explicit
not-published state. Ignored scratch captures, acquisition journals, publisher
code, and a pre-publication macro-store backup prove that the abandoned
workflow was exercised, but they are not an accepted registry activation or
completion record.

This is distinct from completed registry `2.16.0`: its 14-file BLS CPI
annual-revision backfill and its tightly bounded current BLS refresh remain
accepted historical work and are not an original-release archive.

## Decision

CPI surprise calculation is FMP-only. An ordinary result uses actual and
consensus from one FMP event. A repair may combine complementary rows only
when CPI kind, derived reference month, UTC event date, and unit match and the
finite actual and consensus values are each unique. The later event remains
primary and all contributing FMP event-version lineage is retained. Same-side
or incomplete rows remain missing, and conflicts fail closed. No BLS
original-release archive may validate, fill, replace, or otherwise alter the
CPI result or its lineage.

Retire the `2.22.0` archive candidate completely. Remove its migration,
resource, datasets, collector, handler, relations, tests, registry references,
and generated artifacts. Do not replace it with a compatibility shim, fallback,
archived-input query, renamed migration, or deferred collector.

The accepted registry remains the exact `2.21.0`/schema `1.8.0` configuration.
`2.22.0` never crossed the registry activation lifecycle, so discarding its
working candidate is not a new declarative revision and does not require a
synthetic `2.23.0`. This conclusion requires proof that the current canonical
macro store contains neither the candidate migration nor its relations and
that no accepted activation or completion record supersedes `2.21.0`. If such
state or a superseding accepted record exists, stop: applied migration
resources and historical evidence are immutable, and a separate
forward-retirement decision is required.

This decision leaves registry `2.16.0` BLS annual-revision snapshots, its
current BLS refresh, the completed `2.18.0` deep-history extension, and the
accepted `2.19.0` through `2.21.0` FMP calendar evidence/replay work intact.

## Canonical absence proof

On 2026-08-21 the user explicitly authorized one immutable, read-only check of
the fixed `data/macro.sqlite` target. The check held a single-link regular-file
descriptor opened with `O_RDONLY`, `O_CLOEXEC`, and `O_NOFOLLOW`; connected
only through `/proc/self/fd` with `mode=ro&immutable=1`; enabled and verified
SQLite `query_only`; and allowed only fixed reads of `store_metadata`,
`schema_migrations`, and `sqlite_schema`.

The store identified itself as `macro`. Its bounded ledger result contained
the exact accepted `macro:0016_fmp_calendar_wholesale_evidence` row at ordinal
16, registry revision `2.21.0`, with SHA-256
`78dc02d34c0489c3f1fe4b7847870a18955606b1f47a3309ed8464ee9f3bbb4d`.
No migration ID, ordinal, or resource collision for `0017` existed, and no
archive capture, release, fact, trigger, or index relation existed. Main,
WAL, SHM, and journal physical stamps were identical before and after; all
three sidecars were absent. The check made no write, receipt, backup,
checkpoint, provider request, credential read, or scheduler change.

This proof establishes the current canonical absence required above. It does
not erase or reinterpret the ignored scratch acquisition evidence, and it does
not claim that the abandoned publisher was never invoked before stopping or
restoration.

## Consequences

- CPI output is independent of archive availability, release-page parsing, and
  archive-specific missing/not-published states.
- No `0017` migration allocation, macro dataset, raw evidence table, provider
  request, scheduler, public consumer, or surprise table is authorized.
- The decision record retains the candidate's identity and scope without
  representing it as accepted execution or canonical history.

## Non-authorization

This decision does not authorize a provider request, credential use, scheduler
change, canonical-store open or migration, historical-population repeat, public
exposure, promotion, retirement of a completed store, or destructive action.

## Related records

- [Rebuild documentation index](../rebuild/README.md)
- [Current operating envelope](../rebuild/CURRENT_OPERATING_ENVELOPE.md)
- [System registry specification](../rebuild/SYSTEM_REGISTRY_SPEC.md)
- [Migration reconstruction map](../rebuild/MIGRATION_RECONSTRUCTION.md)
- [ADR 0008: fresh store-local reconstruction migrations](0008-fresh-store-local-reconstruction-migrations.md)
- [ADR 0004: point-in-time availability model](0004-point-in-time-availability-model.md)
