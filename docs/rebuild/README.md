# Rebuild Documentation Index

Status: Accepted  
Purpose: navigation, authority, and change rules for the Quant Data
Infrastructure rebuild documents

## 1. Scope

This directory converts the historical recovery specification in
[plan.md](../../plan.md) into accepted implementation contracts. Stages 1
through 5 are implemented and independently verified. The bounded Stage 6
four-route local portal was accepted on 2026-08-10 with an explicit
browser-automation waiver. The bounded offline Stage 7 manual fixture
rehearsal is implemented and independently verified. The user has authorized
the bounded offline Stage 8 JSON Atlas/export implementation. Its primary
fixture gate and independent verification passed. On 2026-08-14 the user
explicitly waived unavailable in-app browser automation and accepted the
bounded Stage 8 exit gate as complete; this is not browser-pass evidence.
Later documented behavior remains closed until separately authorized
executable evidence passes.

The user has separately authorized one bounded Stage 9 preparation: a manual
FMP daily OHLCV slice for `SPY`, inclusive `2026-07-01` through `2026-07-31`,
in `/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607`. Its
primary and independent offline gates and bounded live receipt passed. It is
candidate-only and does not authorize a
scheduler, public exposure, Atlas inclusion, promotion, or store retirement.

The Stage 10 base population and eight-window historical extension are complete
private candidates in the exact isolated target. Their retained receipts have
been independently re-inspected and must not be re-requested. Stage 11 is also
complete as a private candidate in the same cohort: BEA, EIA retail, and EIA
weekly are published, its targeted checks passed, and its private completion
receipt is retained. Its provider requests must not be repeated.

On 2026-08-15 the user selected `data/market.sqlite` as the canonical
project-relative market default. Stage 12A is implemented and independently
verified as its offline authority/path gate, and Stage 12B is implemented and
independently verified as an offline fixture-only collector. The bounded
no-copy Stage 12C population is complete and independently verified under its
[two-session contract](STAGE12C_MARKET_GAP_V1.md) and immutable
[evidence record](STAGE12C_EVIDENCE.md). Registry `2.14.0`/schema `1.8.0`
binds 629 one-attempt units: 619 published complete, three successful-empty,
and seven narrowly sealed authorized HTTP 402 terminal outcomes. The final
project-local target has 4,237,131 current rows, 4,237,873 immutable versions,
and 5,210 captures with clean invariants.

Stage 12D is complete and independently verified under its
[no-transfer project-local adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md),
[ADR 0010](../adr/0010-stage12d-no-transfer-market-adoption.md), and immutable
[evidence record](STAGE12D_EVIDENCE.md). Exactly two canonical read-only proofs
produced distinct immutable receipts with one semantic proof and left the main,
WAL, SHM, and journal stamps unchanged. Independent reconciliation did not
reopen SQLite or compute a new full-database hash, and it is not a backup or
recovery proof. No second multi-gigabyte database, copy, move, replacement,
backup, migration, registry bump, promotion pointer, provider/network access,
public exposure, or scheduler change occurred. Stage 12E remains closed.
Registry `2.15.0`/schema `1.8.0` changed only the macro, company, and news
default basenames to `data/macro.sqlite`,
`data/company.sqlite`, and `data/news.sqlite`, matching the existing files.
No database file was copied, renamed, opened, or mutated, and the exact
historical projection restores Stage 12C/D registry `2.14.0`.

Registry `2.16.0`/schema `1.8.0` added the isolated
official GDP/CPI vintage migration, private evidence/canonical datasets, and
fixed BEA/BLS collectors. The canonical macro backfill and first refresh are
complete: 16 captures, four series, 2,136 releases, 4,012 versions, and 640
current observations passed integrity, lineage, raw-hash, duplicate, and
current-pointer checks. A non-persistent weekday 09:05 ET user timer refreshes
only current BEA/BLS inputs; the 14 historical archive requests are not
scheduled. Its exact projection restores `2.15.0`.

Registry `2.17.0`/schema `1.8.0` adds only the
isolated employment-vintage migration and private Philadelphia Fed historical
and BLS current collectors. The completed three-request backfill added 984
releases, 14,449 immutable versions, and 1,993 current observations across
payroll and unemployment, while preserving source-vintage labels without
invented release dates. A non-persistent first-Friday 10:05 ET timer fetches
only the two-series BLS current response; historical workbooks are not
scheduled. Its exact projection restores byte-exact registry `2.16.0`.

Registry `2.18.0`/schema `1.8.0` adds macro
ordinal 15 and two manual-only history collectors without a new dataset,
credential, public consumer, or scheduler. The completed one-time extension
reused five sealed responses, made six BLS requests, and brought official
all-items CPI back to `1947-01` and core CPI back to `1957-01`; Philadelphia
Fed NOUTPUT/ROUTPUT remain separate source-native GNP/GDP series. The retained
Stage 11 weekly crude cohort was adopted locally as 2,289 exact versions and
current rows with no provider request. Its exact projection restores
byte-exact registry `2.17.0`.

Registry `2.19.0`/schema `1.8.0` adds only the
manual-only `fmp.macro.gdp_cpi_release_calendar_history` collector, reusing
`fixture.macro.economic_calendar` and the existing `macro.official_vintages`
tool dependency. The completed, no-repeat FMP history covers exactly 56
contiguous windows of at most 90 days from `2013-01-01` through `2026-08-17`
and 941 normalized calendar events/versions. It adds no migration, dataset,
job, scheduler, public consumer, or surprise table; no timer was installed and
the two existing macro timers are unchanged. GDP surprise is calculated on
demand as one best-available record per quarter. It prefers an exact-date BEA
first release (`advance`, or source-native `initial`); when that consensus is
absent, it selects an exact-date `second`, then exact-date `third` release.
Every selection compares FMP consensus with the BEA actual from the same
release date and stage; FMP GDP actual is never used. Equivalent reviewed
aliases collapse only when their same-date values agree; conflicts fail
closed. Later stages are explicitly marked `is_fallback`. The current
canonical read returns all 55 quarters from `2012Q4` through `2026Q2`: 10
advance, one initial, 10 second, and 34 third; 54 have numeric surprises and
`2012Q4` is `missing_consensus`. CPI has 864 unchanged same-event results.
This derived policy adds no provider request or scheduler. Its exact
historical projection restores `2.18.0`, preserving the `2.18.0` to `2.17.0`
historical projection above.

Registry `2.20.0` adds only the reviewed
`fmp.macro.employment_release_calendar_refresh` declaration and establishes
payroll and unemployment surprise calculation from each same-reference-period
FMP actual and consensus. The reviewed bounded, non-persistent refresh timer is
installed, enabled, and active at 08:15 and 08:45 America/New_York on weekdays
under the user's explicit scheduler approval. Exact projection restores
byte-exact `2.19.0`.

The latest fully documented registry successor is `2.21.0`/schema `1.8.0`.
It adds macro
migration 0016, one private wholesale calendar evidence dataset, and one
manual-only collector. The completed one-time run retained all raw response
bytes and 42,890 rows from 56 contiguous `2013-01-01` through `2026-08-17`
windows. The original local replay made zero provider requests and normalized
323 v1 employment events/versions. Additive v2 payroll and v3 unemployment
local replays each wrote 162 reference-period-aware events and versions and now
support 324 on-demand employment surprises: 162 per series, with 321 `ok`, two
`missing_consensus`, and one `missing_actual`. Delayed releases map by explicit
reference month; unemployment `2025-10` remains `missing_actual` because FMP
supplied no actual. Identical second replays made zero requests and zero writes.
No public consumer, surprise table, job, or new timer was added, and the exact
historical projection restores byte-exact `2.20.0`.

On 2026-08-20 the user authorized a simpler derived CPI-surprise policy. FMP
calendar events supply both actual and consensus; the surprise consumer does
not query the retired BLS CPI original-release archive. Ordinary results use
one FMP event. A narrow repair may combine complementary rows only when CPI
kind, derived reference month, UTC event date, and unit match and the finite
actual and consensus values are each unique. The later event remains primary
and all source event-version lineage is retained. This repairs headline CPI
MoM for `2025-11` as `0.1 - 0.3 = -0.2`; same-side or otherwise incomplete
rows remain missing, and conflicts fail closed. The change is on-demand logic
only: it does not alter raw evidence, write a surprise table, call a provider,
migrate a store, or change a scheduler.

On 2026-08-21 the user accepted [ADR 0011: retire the proposed BLS CPI
original-release archive](../adr/0011-retire-proposed-bls-cpi-release-archive.md).
It rejects the never-active `2.22.0` candidate, including
`macro:0017_bls_cpi_release_archive`, its archive datasets and collector, and
all archive relations. The candidate is declarative working state, not evidence
of provider execution, canonical-store publication, public exposure, or
scheduler authority. The current accepted configuration is the exact
`2.21.0`/schema `1.8.0` registry: because no declarative content in that
accepted revision changes, discarding the unaccepted candidate does not create
a `2.23.0` successor. This narrow retirement does not alter the completed
`2.16.0` BLS annual-revision snapshots or its authorized current BLS refresh.

For any provider, credential, scheduler, canonical-store, migration,
promotion, retirement, deployment, public-exposure, or destructive task, read
the [current operating envelope](CURRENT_OPERATING_ENVELOPE.md) before the
specialized contract. The envelope consolidates current boundaries but grants
no new authority.

Two user decisions narrow the work:

- The lost Git history will not be searched for or reconstructed. A fresh
  baseline can be created when implementation begins.
- A dedicated provider-rights governance subsystem is out of scope.

Stages 4 through 7 were implemented sequentially with offline synthetic
fixtures. Stages 4 and 5 are independently verified, Stage 6 is accepted with
its explicit waiver, and Stage 7 is independently verified. All eight
Stage 7 jobs remain disabled and manual-fixture-only. Stage 8 is a deliberate
forward reconstruction of one manual, fixture-only JSON snapshot; it is not a
claim of recovered Atlas source/package parity or historical 13-dataset
coverage.

## 2. Document map

### Direction and sequencing

- [Recovery evidence and historical specification](../../plan.md)
- [Target architecture](../../ARCHITECTURE.md)
- [Implementation roadmap](../../ROADMAP.md)
- [Current operating envelope - mandatory operational routing](CURRENT_OPERATING_ENVELOPE.md)
- [Fast path development - default workflow](FAST_PATH_DEVELOPMENT.md)
- [Parallel execution and agent cadence - exception workflow](PARALLEL_EXECUTION.md)
- [Frozen pre-compression agent instruction ledger](AGENT_INSTRUCTION_LEDGER_2026-08-20.md)
- [Migration reconstruction map](MIGRATION_RECONSTRUCTION.md)
- [Stage 1 acceptance evidence](STAGE1_EVIDENCE.md)
- [Stage 2 acceptance evidence](STAGE2_EVIDENCE.md)
- [Stage 3 acceptance evidence](STAGE3_EVIDENCE.md)
- [Stage 4 acceptance evidence](STAGE4_EVIDENCE.md)
- [Stage 5 acceptance evidence](STAGE5_EVIDENCE.md)
- [Stage 6 acceptance evidence](STAGE6_EVIDENCE.md)
- [Stage 7 acceptance evidence](STAGE7_EVIDENCE.md)
- [Stage 8 evidence record](STAGE8_EVIDENCE.md)
- [Stage 9 evidence record](STAGE9_EVIDENCE.md)
- [Stage 10 evidence record](STAGE10_EVIDENCE.md)
- [Stage 11 evidence record](STAGE11_EVIDENCE.md)
- [Stage 12 Market v1 contract](STAGE12_MARKET_V1.md)
- [Stage 12 evidence record](STAGE12_EVIDENCE.md)
- [Stage 12B incremental Market v1 collector contract](STAGE12B_INCREMENTAL_MARKET_V1.md)
- [Stage 12B evidence record](STAGE12B_EVIDENCE.md)
- [Stage 12C Market v1 two-session gap contract](STAGE12C_MARKET_GAP_V1.md)
- [Stage 12C evidence record](STAGE12C_EVIDENCE.md)
- [Stage 12D no-transfer adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
- [Stage 12D evidence record](STAGE12D_EVIDENCE.md)

### Detailed contracts

- [Single system registry](SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [First vertical slice](VERTICAL_SLICE_SPEC.md)
- [Composable tool platform](TOOL_PLATFORM_SPEC.md)
- [UI visual direction](UI_VISUAL_DIRECTION.md)
- [Scheduling and physical-store locking](SCHEDULING_AND_LOCKING.md)
- [Derived analytical exports](ANALYTICAL_EXPORTS.md)
- [Test strategy and acceptance](TEST_STRATEGY.md)
- [Stage 12 Market v1 operationalization](STAGE12_MARKET_V1.md)
- [Stage 12B incremental Market v1 collector](STAGE12B_INCREMENTAL_MARKET_V1.md)
- [Stage 12C Market v1 two-session gap completion](STAGE12C_MARKET_GAP_V1.md)
- [Stage 12D project-local no-transfer operationalization](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)

### Architecture decisions

- [ADR-0001: Four operational SQLite stores](../adr/0001-four-operational-sqlite-stores.md)
- [ADR-0002: Single machine-readable registry](../adr/0002-single-machine-readable-registry.md)
- [ADR-0003: Three-layer data architecture](../adr/0003-three-layer-data-architecture.md)
- [ADR-0004: Point-in-time availability model](../adr/0004-point-in-time-availability-model.md)
- [ADR-0005: Composable tool core](../adr/0005-composable-tool-core.md)
- [ADR-0006: Lock by physical store](../adr/0006-lock-by-physical-store.md)
- [ADR-0007: SQLite authority and derived Parquet](../adr/0007-sqlite-authority-derived-parquet.md)
- [ADR-0008: Fresh store-local reconstruction migrations](../adr/0008-fresh-store-local-reconstruction-migrations.md)
- [ADR-0009: Canonical market operational path](../adr/0009-canonical-market-operational-path.md)
- [ADR-0010: Stage 12D no-transfer market adoption](../adr/0010-stage12d-no-transfer-market-adoption.md)
- [ADR-0011: Retire proposed BLS CPI original-release archive](../adr/0011-retire-proposed-bls-cpi-release-archive.md)

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
- `Authorized`: explicit executable scope is allowed, but its exit evidence is
  not yet complete.
- `Implemented`: supported by executable evidence and acceptance results.
- `Rejected`: considered and intentionally not adopted.

The current planning package is `Accepted`. Stages 1 through 5 are
implemented and independently verified. The bounded Stage 6 portal is accepted
with its browser-automation waiver. The bounded offline Stage 7 manual fixture
rehearsal passed its primary gate and independent SolUltra verification. Stage
8 is `Implemented` and accepted for its bounded offline JSON Atlas/export
profile after its primary fixture gate and independent review passed. On
2026-08-14 the user explicitly waived the unavailable in-app
browser-automation check. The waiver closes the bounded exit gate without
claiming that unrecorded browser checks passed.

Stage 9 is `Bounded live population independently verified` for its exact FMP
non-production slice only. Promotion and retirement remain excluded.
The retained Stage 10 base and extension populations are complete,
independently re-inspected private candidates; the missing point-in-time
pre-live gate record is disclosed rather than reconstructed. The exact Stage 11
population is complete as a private, non-production candidate. This does not
authorize promotion, public exposure, scheduling, or another provider request.

Stage 12A is `Implemented and independently verified` for the bounded offline
authority/path gate, and Stage 12B is `Implemented and independently
verified — offline fixture-only`. Stage 12C is `Implemented and independently
verified` as the exact bounded no-copy two-session population under its
[contract](STAGE12C_MARKET_GAP_V1.md) and
[evidence record](STAGE12C_EVIDENCE.md). Its final receipt is bound to registry
`2.14.0`/schema `1.8.0`; the evidence discloses the sealed HTTP 402
exception and the final `mode=ro&immutable=1` source-neutrality check without
claiming a database mutation from the prior `-shm` timestamp effect.
Stage 12D is `Implemented and independently verified — no-transfer read-only
adoption/freeze` under its
[contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md) and
[evidence record](STAGE12D_EVIDENCE.md). The evidence states the post-proof
no-reopen/no-new-full-hash limitation and makes no backup or recovery claim.
It has no transfer, migration, provider, credential, public-consumer, or
scheduler authority. Stage 12E remains closed.

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
