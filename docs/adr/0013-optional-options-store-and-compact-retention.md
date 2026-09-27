# ADR 0013: Optional options store and compact Theta retention

Status: Accepted user scope; implementation validation recorded separately.
Decision date: 2026-09-24

## Authority

The user explicitly approved discarding unneeded option source detail after
calculating the required statistics, and requested implementation and a SPY-first
fetch. This supersedes the earlier proposed permanent broad-response retention
for this new Theta dataset. Existing Alpaca evidence and every other store retain
their existing policies. No old data is deleted or migrated.

## Optional domain boundary

Register the private options domain in config/options_registry.json and
data/options.sqlite. This is an optional fifth physical store with its own
checksum-checked migration, dataset registry, physical-path writer lock,
immutable reader, health and SQLite online backup/restore methods.
The companion registry is the sole options declaration. It is a narrowly scoped
exception to ADR 0002's single-registry topology; the existing
config/system_registry.json and its four-store public contracts remain intact.
This avoids rewriting frozen profiles or making existing consumers depend on
an options database. Public options tools continue to describe Alpaca; no public
Theta tool or dashboard exposure is introduced in this increment.

The migration and domain registry are owned by the integration owner. No shared
store, migration, registry-validator or tool algorithm changes in this increment.
Options-specific tests and fresh independent verification are required before
canonical publication. Existing physical locking is reused without modification.

## Retention

New broad Theta inputs are temporary processing material. After the compact
capture commits and is verified through a separate immutable read, remove only
that capture's explicitly named temporary source artifacts and release its
in-memory broad rows. Failed/incomplete units retain their material for local
recovery. This introduces no recurring cleanup and does not scan/delete old roots.

Permanently retain extracted selected contract fields, selection reasons,
full/selected/remainder summaries, bounded leader references, source request
receipts/digests, field coverage, model parameters and transformation versions.
Extracts are not represented as original response bytes. source_replayable=false
is explicit: arbitrary new historical aggregates cannot be reconstructed from
these records. Corrections may need a separately authorized provider refetch.
A->B->A correction appends a third capture; immediate semantic replay changes
no canonical rows or database bytes. Source order alone is not material.

## Current acquisition semantics

SPY exact provider root only. Contract deliverables are not supplied by the
endpoint; retain provider_root_only_unverified and treat IV as a research proxy,
not proof of standard deliverables or executable option pricing.
All-chain summaries mean all observed SPY-root responses, not independently
reconciled OCC listings. Endpoint completion and catalogue completeness differ.

Use combined EOD/Greeks streams where present and separate OI. Full-chain Greeks
are transient here: this is a request-efficiency choice, not permanent retention
of every Greek. For older sessions with no combined response, use EOD prices
and explicitly missing Greeks. SPY stock history access was denied.

The historical price-only fallback uses the existing SPY close tool at version
2.0.0, with complete metadata and lineage retained. Its split-only basis is not
relabeled as a raw quote. Require a compatible near-expiry put/call midpoint
check, label the result as a reference proxy, and fail the session when the check
cannot be established. Neither this check nor a historical backfill proves
original point-in-time provider vintages. No historical signal API is exposed.

## Finite operational scope

SPY only, requested outer range 2012-06-01 through 2026-09-23. The 2012 probe was
denied; 2016-01-04 EOD prices succeeded. The implementation's initial history
manifest is 2016-01-04 through 2026-09-23, ordered newest first, using explicit
provider holiday calendars and weekday sessions.

Global allocation: 20,000 data requests including probes/metadata/fallbacks,
128 GiB received, 24 hours from the first current-run preflight, zero retries.
Use a 15 GiB SPY database cap and 20 GiB free-space reserve. No materialized
purchase, subscription change, terminal, recurring unit or scheduler activation
is authorized or introduced.

## Later scoped decision: Tier-1 expansion, 2026-09-24

After the SPY population completed, the user explicitly requested other major
ETFs with concurrent downloads. This extends the same optional options domain
and compact-retention policy to the other 14 previously planned Tier-1 roots.
Companion registry 1.1.0 and a new append-only migration ordinal 0002 admit the
additional roots while preserving SPY data and migration 0001. It does not
authorize repeating SPY. The [expansion receipt](../rebuild/THETA_ETF_EXPANSION_2026-09-24.md)
records the exact new finite allocation, compatibility and verification.
