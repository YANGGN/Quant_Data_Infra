# Quant Data Infrastructure

This repository is a clean rebuild of a personal quant-data platform. The
Stages 1 through 5 are independently verified; the bounded Stage 6 portal is
accepted; and the bounded Stage 7 manual rehearsal is independently verified.
Stage 7 uses only reviewed synthetic fixtures and explicit temporary roots. Its
primary fixture gate and independent SolUltra verification passed on
2026-08-11.

The bounded offline Stage 8 implementation adds one fixture-only static JSON
Atlas snapshot. It is a deliberate forward reconstruction, not a claim of
recovered historical Atlas or 13-dataset parity. Its primary offline fixture
gate and independent offline verification passed on 2026-08-11. On 2026-08-14
the user explicitly waived the unavailable in-app browser-automation check and
accepted the bounded Stage 8 exit gate as complete; the waiver is not a claim
that the unrecorded browser checks passed.

The user has authorized a narrow Stage 9 preparation for one manual FMP
end-of-day OHLCV slice: `SPY`, inclusive `2026-07-01` through `2026-07-31`,
in the exact non-production root
`/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607`. It has one
live-enabled, manual-only collector, one request and no retry, and uses the
name `FMP_API_KEY` only as a process-environment variable. Its primary and
independent offline gates and bounded live population receipt passed. Provider
licensing remains the operator's account-specific responsibility.
It does not authorize a scheduler, public exposure, Atlas inclusion,
operational promotion, or old-store retirement.

The bounded Stage 10 base population and eight-window historical extension are
complete in
`/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1`.
Their retained private candidate receipts have been independently re-inspected;
the provider requests must not be repeated. Stage 11 uses the macro store in
that same isolated cohort. Its BEA, EIA retail, and EIA weekly phases are
published, its targeted checks passed, and its private completion receipt is
retained. No Stage 11 provider request may be repeated. The result remains a
private, non-production candidate with no promotion or public exposure.

On 2026-08-15 the user selected `data/market.sqlite` as the canonical
project-relative market default. Stage 12A is implemented and independently
verified as the authority/path gate, and Stage 12B is implemented and
independently verified as an offline fixture-only collector. The bounded
manual no-copy Stage 12C population is now complete and independently verified
under its [two-session contract](docs/rebuild/STAGE12C_MARKET_GAP_V1.md) and
immutable [evidence record](docs/rebuild/STAGE12C_EVIDENCE.md). Registry
`2.14.0`/schema `1.8.0` binds 629 one-attempt chains: 619 published
complete, three successful-empty, and seven narrowly sealed authorized HTTP 402
terminal outcomes. The final project-local target has 4,237,131 current rows,
4,237,873 immutable versions, and 5,210 captures with clean invariants.
Stage 12D is complete and independently verified under its
[no-transfer project-local adoption/freeze contract](docs/rebuild/STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
and immutable [evidence record](docs/rebuild/STAGE12D_EVIDENCE.md). Its exact
two canonical read-only proofs produced distinct receipts with one semantic
proof and left the database, WAL, SHM, and journal stamps unchanged. The
independent post-proof reconciliation did not reopen SQLite or compute a new
full-database hash; this is not backup or recovery evidence. Stage 12D created
no second database, copy, move, replacement, backup, migration, registry bump,
promotion pointer, provider/network access, public exposure, or scheduler
change. Registry `2.14.0`/schema `1.8.0` and `data/market.sqlite` remain
authoritative. Stage 12E remains closed.

### FMP credentials for future authorized runs

Future FMP runners that are otherwise authorized by their focused contract use
`quant_data.credentials.read_project_credential` automatically immediately
before permitted transport. A nonblank process-environment `FMP_API_KEY` wins;
only when it is absent or blank may the helper resolve that single named key
from the exact canonical project-root `.env` file. The helper does not import
other keys into the process environment, load arbitrary dotenv files, expand
or interpolate variables or commands, or log or persist secrets. Do not paste
API keys into chat, command lines, receipts, or sidecars. This credential
delivery rule does not authorize a provider, scope, retry, scheduler, or any
other work that the focused contract does not already authorize.

The portal remains local-only with four fixed routes: Overview (`/`), GDP
Vintages (`/gdp-vintages`), Tables (`/table-inspector`), and Agent Tools
(`/agent-tools`). Stage 7 adds eight disabled `manual_fixture_only` job
declarations, deterministic dry-run plans, fixture preparation before locks,
complete ordered physical-store lock sessions, bounded retries and active
timeouts, private receipt-last evidence, and backup/restore rehearsal. It adds
no live provider or runtime network access, scheduler installation/start,
exports, Atlas, promotion, hosting, credentials, default database fallback, or
destructive operations.

The authorized Stage 8 scope is one manual, fixture-only JSON Atlas snapshot
built from explicit synthetic store roots and query-only SQLite online-backup
copies. It validates a bounded cohort into an immutable derived revision with a
private receipt and atomic `current` pointer, but it cannot promote operational
stores. It neither adopts Parquet or DuckDB nor hosts/deploys Atlas; no live
provider, scheduler, default-path, or destructive behavior is authorized. Its
primary fixture gate and independent offline verification passed. The bounded
exit gate was accepted on 2026-08-14 with an explicit waiver for the unavailable
in-app browser-automation check. Browser observations remain unrecorded.

The target design and sequencing are documented in [ARCHITECTURE.md](ARCHITECTURE.md),
[ROADMAP.md](ROADMAP.md), and the [rebuild documentation index](docs/rebuild/README.md).
The new migration resources are deliberate reconstructions; they do not claim
byte-exact recovery of the lost application.

The preserved Stage 1 slice and its historical hashes are recorded in the
[Stage 1 acceptance evidence](docs/rebuild/STAGE1_EVIDENCE.md). The current
four-store foundation and its approved deterministic receipts are recorded in
the [Stage 2 acceptance evidence](docs/rebuild/STAGE2_EVIDENCE.md). The bounded
Stage 3 primary-gate receipts are recorded in the
[Stage 3 acceptance evidence](docs/rebuild/STAGE3_EVIDENCE.md). The Stage 4
primary-gate receipts and independent-verifier result are recorded in the
[Stage 4 acceptance evidence](docs/rebuild/STAGE4_EVIDENCE.md). The Stage 5
typed-tool primary gate is recorded in the
[Stage 5 acceptance evidence](docs/rebuild/STAGE5_EVIDENCE.md). The bounded
Stage 6 receipts and accepted verification state are recorded in
the [Stage 6 acceptance evidence](docs/rebuild/STAGE6_EVIDENCE.md). The bounded
Stage 7 acceptance and independent-verification receipts are recorded in the
[Stage 7 acceptance evidence](docs/rebuild/STAGE7_EVIDENCE.md).
The Stage 8 offline evidence and accepted browser-automation waiver are tracked in the
[Stage 8 evidence](docs/rebuild/STAGE8_EVIDENCE.md).
The bounded Stage 9 population and independently verified receipt are in
[Stage 9 evidence](docs/rebuild/STAGE9_EVIDENCE.md).
The retained Stage 10 base and historical-extension receipts are in
[Stage 10 evidence](docs/rebuild/STAGE10_EVIDENCE.md). The completed private
Stage 11 candidate and its receipt are in
[Stage 11 evidence](docs/rebuild/STAGE11_EVIDENCE.md). The completed no-transfer
adoption/freeze proofs are in
[Stage 12D evidence](docs/rebuild/STAGE12D_EVIDENCE.md).

The [Stage 12 Market v1 contract](docs/rebuild/STAGE12_MARKET_V1.md),
[Stage 12B incremental collector contract](docs/rebuild/STAGE12B_INCREMENTAL_MARKET_V1.md),
and [canonical-path ADR](docs/adr/0009-canonical-market-operational-path.md)
record the current boundary; [Stage 12 evidence](docs/rebuild/STAGE12_EVIDENCE.md) remains immutable Stage 12A evidence.

## Validate Stage 1

Run the dependency-free offline suite from the WSL project root:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
```

Run the deterministic two-rebuild harness with two explicit clean roots:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
stage1_root="$(mktemp -d)"
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data \
  --stage stage1 \
  --project-root "$PWD" \
  --store-root "$stage1_root/first" \
  --second-store-root "$stage1_root/second"
```

The command refuses a nonempty store root. It verifies migration and fixture
digests, exact no-write replays, corrections and revisions, the golden query
matrix, tool/HTTP equivalence, read-only store fingerprints, integrity, and
equal path-free logical manifests across both rebuilds. Its output is strict
JSON evidence.

## Validate Stage 2

Run the deterministic Stage 2 two-rebuild gate with two explicit empty work
roots:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data \
  --stage stage2 \
  --project-root "$PWD" \
  --store-root <empty-work-root-1> \
  --second-store-root <empty-work-root-2>
```

The Stage 2 gate rebuilds the Stage 1 fixture state under each work root, adds
the four-store control plane, verifies source reads are non-mutating, and
requires deterministic source, backup, and restored evidence. It refuses a
nonempty work root and emits strict JSON. The final full-suite command is the
same dependency-free command above; its current implementation result and
separate independent-verifier status are recorded in the
[Stage 2 acceptance evidence](docs/rebuild/STAGE2_EVIDENCE.md).

## Validate Stage 3

Run the deterministic Stage 3 two-rebuild gate with two explicit empty work
roots under `/tmp`:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
first_root="$(mktemp -d /tmp/quant-data-stage3-first.XXXXXX)"
second_root="$(mktemp -d /tmp/quant-data-stage3-second.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -m quant_data \
  --stage stage3 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root "$first_root" \
  --second-store-root "$second_root"
```

The gate upgrades a verified Stage 2 cohort under each work root, imports only
the 25 reviewed Stage 3 synthetic fixtures, and compares strict path-free
evidence from both roots. It verifies replay and rejected-capture no-write
behavior, point-in-time results, source-read/backup/restore non-mutation, and
equal restored health. It refuses a nonempty work root and emits strict JSON.
The primary fixture gate and independent SolUltra verification have passed.

## Validate Stage 4

Run the deterministic Stage 4 two-rebuild gate with two explicit empty work
roots under `/tmp`:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
first_root="$(mktemp -d /tmp/quant-data-stage4-first.XXXXXX)"
second_root="$(mktemp -d /tmp/quant-data-stage4-second.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -m quant_data \
  --stage stage4 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root "$first_root" \
  --second-store-root "$second_root"
```

The gate upgrades a verified Stage 3 cohort under each work root, imports only
the 17 reviewed Stage 4 synthetic fixtures (seven company, five options, and
five news), and compares strict path-free evidence from both roots. It verifies
repeat initialization, migration/resource integrity, exact replay no-write
behavior, point-in-time reads, source-read/backup/restore non-mutation, and
equal restored health. It refuses a nonempty work root and emits strict JSON.
The primary fixture gate and independent SolUltra verification have passed.

## Validate Stage 5

Run the deterministic Stage 5 gate with two explicit empty work roots:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data \
  --stage stage5 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root <empty-work-root-1> \
  --second-store-root <empty-work-root-2>
```

The gate rebuilds Stage 4, validates all 57 generated contracts, compares
direct and HTTP calls over source and restored stores, and proves reads remain
mutation-neutral. It refuses nonempty roots and emits one strict-JSON evidence
line. The primary gate and independent SolUltra verification passed. The
bounded Stage 6 portal offline gate has since passed; the user explicitly
accepted the unavailable automated browser check.

## Validate Stage 6

Run the deterministic Stage 6 gate with two explicit empty work roots:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data \
  --stage stage6 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root <empty-work-root-1> \
  --second-store-root <empty-work-root-2>
```

The gate rebuilds the frozen Stage 5 compatibility cohort under each root,
projects the canonical Stage 6 registry, and exercises the four fixed pages,
registered JSON APIs, and bundled local assets over both source and restored
stores. It refuses nonempty roots, proves read-only store fingerprints, checks
for zero runtime network assets, and emits deterministic strict-JSON evidence.
The bounded offline gate passed. The user explicitly accepted the unavailable
automated browser check and authorized bounded offline Stage 7 work. Stage 8
is implemented under its own bounded offline contract and its primary fixture
gate and independent offline verification passed. Its bounded exit gate was
accepted on 2026-08-14 with the explicit browser-automation waiver.

## Validate Stage 7

Run the deterministic Stage 7 gate with two explicit empty work roots:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data   --stage stage7   --project-root /home/volatility/Python_Projects/Quant_Data_Infra   --store-root <empty-work-root-1>   --second-store-root <empty-work-root-2>
```

The gate rebuilds Stage 6, validates all eight disabled fixture-only job plans,
exercises mocked outcome, receipt, lock, timeout, backup/restore, and real
unchanged-replay evidence, and emits one path-free strict-JSON line. A pure
manual-wrapper plan can be inspected without opening stores or writing state:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m   quant_data.operations.manual_job   --project-root /home/volatility/Python_Projects/Quant_Data_Infra   --store-root <explicit-temporary-store-root>   --state-root <explicit-temporary-private-state-root>   --job market-close   --dry-run
```

The non-dry wrapper is restricted to explicitly initialized synthetic-fixture
stores; it is not a live-provider or scheduler command. The primary Stage 7
gate passed 277 offline tests, and the independent verifier reran all 277 tests
in 170.731 seconds with no findings.

## Validate Stage 8

Run the deterministic Stage 8 gate with two explicit empty work roots:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data \
  --stage stage8 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root <empty-work-root-1> \
  --second-store-root <empty-work-root-2>
```

The gate rebuilds the frozen Stage 7 cohort, captures four query-only SQLite
copies, and publishes one immutable fixture-only JSON snapshot plus the
dependency-free static Atlas. The primary gate passed 319 offline tests and
the two-root CLI emitted evidence SHA
`e1151f92176a6b5a9d57d18a215497baab7405187fa798537cd947be1dd80a3c`.
Independent offline verification passed. The in-app browser client failed
before launch and no alternate browser automation was substituted. On
2026-08-14 the user explicitly waived that external limitation and accepted the
bounded exit gate as complete. Responsive, keyboard, focus, reduced-motion,
zoom, and runtime-network browser observations remain unrecorded.

## Stage 9 status -- bounded live population independently verified

The Stage 9 harness is deliberately network-blocked. It exercises the exact
FMP-shaped `SPY` July-2026 request scope against isolated temporary roots,
then checks replay, reconciliation, online backup/restore, a scratch-only
correction, and a candidate-only receipt:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
first_root="$(mktemp -d /tmp/quant-data-stage9-first.XXXXXX)"
second_root="$(mktemp -d /tmp/quant-data-stage9-second.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data \
  --stage stage9 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root "$first_root" \
  --second-store-root "$second_root"
```

This is not a live-provider command and cannot repeat the completed provider
request. The primary gate records offline evidence SHA
`1851168a239c2b3e344db77d20439a1e1a8264d0eae65c950e33f4dd06c583c4`.
The bounded live receipt already passed and is recorded in
[Stage 9 evidence](docs/rebuild/STAGE9_EVIDENCE.md).

## Stage 10 status -- retained candidate populations complete

The immutable base receipt covers the frozen 629-symbol roster: 621 completed
symbols and eight retained failures. The immutable extension closes all 5,032
planned windows as 3,970 complete, 1,004 successful empty responses, and 58
authorized terminal outcomes across nine tickers. Read-only inspection matched
the receipts and no provider request was repeated. These populations remain
private, non-production, and candidate-only; see
[Stage 10 evidence](docs/rebuild/STAGE10_EVIDENCE.md).

## Stage 11 status -- retained private candidate complete

The final bounded resume skipped the completed BEA and retail phases and made
one successful weekly `PET.WCESTUS1.W` request. The retained macro candidate
contains 635 BEA, 1,136 EIA retail, and 2,289 EIA weekly current rows with
matching version counts. Targeted integrity, foreign-key, duplicate, and
current-pointer checks passed and the private completion receipt was
published. No Stage 11 request may be repeated; see
[Stage 11 evidence](docs/rebuild/STAGE11_EVIDENCE.md).

## GDP/CPI source vintages -- populated and scheduled

The canonical `data/macro.sqlite` now stores the bounded official vintage
scope for BEA real/nominal GDP and BLS all-items/core CPI. The clean backfill
published 16 immutable captures, four series, 2,136 releases, 4,012 versions,
and 640 current observations. GDP history begins at `2002Q1`; the BLS annual
revision snapshots begin at `2008-01`. Raw payload hashes, integrity, foreign
keys, duplicates, correction sequences, and current pointers were reconciled.

`quant-data-macro-vintages.timer` is enabled for weekdays at 09:05
America/New_York. Each run fetches only the current BEA workbook and current
BLS API response, makes no retry, applies no migration, and writes nothing
when normalized values are unchanged. It does not repeat the 14-file archive
backfill. Installation and inspection commands are in
[the systemd README](deploy/systemd/README.md).

## Employment source vintages -- populated and scheduled

The same canonical macro store now also retains Philadelphia Fed RTDSM payroll
and unemployment vintage matrices plus the current two-series BLS response.
The one-time backfill published three captures and added 984 release identities,
14,449 immutable versions, and 1,993 current observations. Payroll covers
`1939-01` through `2026-07`; unemployment covers `1948-01` through `2026-07`.
RTDSM source-vintage labels are preserved without inventing an exact release
date, and their availability remains the local capture time.

`quant-data-employment-vintages.timer` is enabled for the first Friday of each
month at 10:05 America/New_York. It is non-persistent and fetches only the
current BLS payroll/unemployment response: no credential, retry, migration, or
historical-workbook refetch. A manual outside-window service check completed
with zero requests. The next scheduled run is `2026-09-04 10:05 EDT`.

## Deep macro history and weekly crude adoption -- complete

The one-time macro-history extension reused five sealed source responses and
made exactly six new credential-free BLS requests. All-items CPI now reaches
`1947-01`, core CPI reaches `1957-01`, and the Philadelphia Fed `NOUTPUT` and
`ROUTPUT` matrices are stored as separate source-native GNP/GDP level series.
The extension added 11 captures, 1,165 releases, 14,577 versions, and 1,980
current observations. Exact source-vintage labels are retained without
inventing release dates.

The retained Stage 11 weekly crude cohort was then adopted locally with no
provider request: one capture and 2,289 versions/current observations from
`1982-08-20` through `2026-08-07`. No new timer was installed; the two existing
macro timers remain the only scheduled macro collectors. The pre-change backup
is `data/.macro-backups/macro.sqlite.pre-history-extension-20260817T1243-0400`
with SHA-256 `5fbf583e24ad62e146c850797e39140a204e018ae029e5f39137381f3fcbd58d`.

## FMP GDP/CPI release-calendar history and surprises -- complete

Registry `2.19.0`/schema `1.8.0` adds one manual-only collector,
`fmp.macro.gdp_cpi_release_calendar_history`. It reuses
`fixture.macro.economic_calendar` and the existing `macro.official_vintages`
tool dependency; it adds no migration, dataset, job, scheduler, or physical
surprise table. The completed manual history used exactly 56 contiguous windows
of at most 90 days from `2013-01-01` through `2026-08-17`, retaining 941 FMP
events and 941 versions: 216 core MoM, 217 core YoY, 214 headline MoM, 217
headline YoY, and 77 GDP. The reviewed target alias map is exact, with no fuzzy
target matching, and ignores bare CPI, GDP Price Index, and GDP Consumer Spending labels.

Historical FMP consensus uses the explicit `event_at_utc` availability
assumption. GDP surprise is computed on demand as one best-available record per
quarter. It prefers a BEA first release on the exact FMP event date
(`advance`, or source-native `initial`); when that consensus is absent, it
selects an exact-date `second`, then exact-date `third` release. Every
selection compares FMP consensus with the BEA actual from the same release date
and stage; FMP GDP actual is never used. Equivalent reviewed aliases on the
same date collapse only when their values agree; conflicts fail closed.
Later-stage selections are explicitly marked `is_fallback`. The current
canonical read returns all 55 quarters from `2012Q4` through `2026Q2`: 10
advance, one initial, 10 second, and 34 third. Fifty-four have numeric surprises
and `2012Q4` is `missing_consensus`. CPI has 864 results. Ordinary CPI uses
actual and consensus from the same FMP event version. This derived mapping adds
no migration, physical surprise table, provider request, or scheduler.

Under [ADR 0011](docs/adr/0011-retire-proposed-bls-cpi-release-archive.md),
CPI surprise calculation is FMP-only: an ordinary result takes actual and
consensus from one FMP event, while a narrow repair may combine complementary
rows only when CPI kind, derived reference month, UTC event date, and unit
match and each finite side is unique. The later event remains primary and
source event-version lineage is retained. Same-side or incomplete rows remain
missing, conflicts fail closed, and no BLS original-release archive may
validate, fill, replace, or otherwise affect a CPI result. The proposed
`2.22.0` archive candidate was never activated; its retirement does not alter
the completed `2.16.0` BLS annual-revision snapshots or current BLS refresh.
Before the run, `data/macro.sqlite` was backed up to
`data/.macro-backups/macro.sqlite.pre-fmp-consensus-20260817T1507-0400` (mode
`0600`, SHA-256
`c257c332b83682d82dde921964a0000edb4a9250ea118831c046a1c63dfcad6d`). Integrity,
foreign-key, lineage, and duplicate checks are clean. The manual history is
complete and must not be repeated; no timer was installed and the two existing
macro timers remain unchanged.

The user-authorized incremental calendar refresh code reuses one FMP response
for both GDP/CPI and employment. It is bounded to 08:15 and 08:45
America/New_York on weekdays, one request, and no retry. The reviewed
non-persistent `quant-data-fmp-macro-calendar.timer` is installed, enabled, and
active under the user's explicit scheduler approval. Each family has an
independent semantic identity, and the refresh retains wholesale raw evidence
before local normalization. Payroll and unemployment surprises remain
on-demand calculations. Both use the actual and consensus from the same
reference-period FMP release row. Official employment vintages remain
available for inspection but are not used in either surprise calculation.

Registry `2.21.0` adds migration
`macro:0016_fmp_calendar_wholesale_evidence`, a private raw-evidence dataset,
and one manual-only wholesale collector. The completed one-time run covered all
56 contiguous windows from `2013-01-01` through `2026-08-17` and retained
every response byte plus all 42,890 rows in 56 immutable captures. The recovery
backup `data/.macro-backups/macro.sqlite.pre-fmp-wholesale-20260818T1918-0400`
has mode `0600` and SHA-256
`44d4420cb2569f792bf19489febcf68a16c2d3916cad9893905c31aee681269b`.

The original zero-network local replay normalized 323 v1 employment
events/versions: 161 payroll and 162 unemployment. One-time additive v2 and v3
repairs replayed the same 56 immutable captures with zero provider requests
and wrote 162 reference-period-aware payroll plus 162 unemployment events and
versions. It now exposes 324 on-demand employment surprises: 162 for each
series, with 321 `ok`, two `missing_consensus`, and one `missing_actual`.
Delayed releases now map by their explicit reference month; unemployment
`2025-10` remains `missing_actual` because FMP supplied only consensus. An
identical second replay of each version wrote nothing. The payroll recovery
backup is
`data/.macro-backups/macro.sqlite.pre-payroll-reference-replay-20260820T1740-0400`
(mode `0600`, SHA-256
`8bb8441440458667267a4a608b88eeab9c1f4777f1e691bb53698f60fef2093e`).
The unemployment recovery backup is
`data/.macro-backups/macro.sqlite.pre-unemployment-reference-replay-20260820T2001-0400`
(mode `0600`, SHA-256
`bfad4cf56c53c69ca3d5951ca93af6aecf582831834636c059ca941bc15f1d73`).
Integrity and lineage checks are clean. No provider request, new timer, public
route, physical surprise table, or caller-selected database path was added;
the exact `2.21.0` projection restores byte-exact `2.20.0`.

The never-active `2.22.0` candidate
`macro:0017_bls_cpi_release_archive` is rejected by ADR 0011 and is not a
registry successor. Exact `2.21.0` remains the accepted configuration; no
archive dataset, collector, relation, migration, or archive-derived CPI
lineage remains in scope.

## Current public boundary

The canonical registry is revision `2.21.0`, schema `1.8.0`. It preserves the
four local-private Stage 6 dashboard exposures, eight ordered disabled
`manual_fixture_only` jobs, and the one fixture-only manual JSON export,
`atlas.fixture_snapshot`, with reciprocal declarations on
`fixture.market.daily_prices`, `fixture.macro.gdp_vintages`,
`fixture.company.issuers`, and `fixture.news.items`. In addition to the
isolated Stage 9 declarations, it contains the private Stage 10 market-history
and Stage 11 BEA/EIA candidate declarations. None of those live-candidate
relations is exposed through public tools, dashboards, or Atlas exports.
The completed Stage 11 receipt remains frozen to its historical
`2.9.0`/`1.7.0` projection. Revision `2.10.0` only made its reviewed retry
policy canonical; revision `2.11.0` adds the bounded FMP stock-latest news
fixture contract without proving or authorizing full live news coverage; and
revision `2.12.0` changes only the current market default to
`data/market.sqlite`. Exact historical projections restore
`data/market_data.sqlite`; the former name is not a current fallback or alias.
Revision `2.13.0` adds only the bounded Stage 12B fixture collector declaration:
it uses the existing `0010` model in explicit temporary fixture stores, has no
new migration or public exposure, and does not authorize a provider, API key,
network, default or retained store, promotion, cutover, or scheduler. Its exact
historical projection restores `2.12.0` before reproducing Stage 12A and earlier
evidence. None of those revisions authorizes another Stage 9–11 provider run.

Revision `2.14.0` adds only the bounded Stage 12C manual collector declaration;
its exact projection restores `2.13.0` before Stage 12B and earlier evidence.
The declaration itself is not live evidence; the completed bounded population
is recorded in [Stage 12C evidence](docs/rebuild/STAGE12C_EVIDENCE.md). The
named-only credential fallback was delivery policy only, not a registry or
provider-scope expansion, and no Stage 9-11 provider work is reopened.

Revision `2.15.0` changes only the remaining current store defaults to
`data/macro.sqlite`, `data/company.sqlite`, and `data/news.sqlite`, matching
the existing project-local files. It performs no database copy, rename, open,
or mutation. Its exact historical projection restores `2.14.0` and the prior
three `_data` declarations for Stage 12C/D and earlier evidence.

Revision `2.16.0` adds only `macro:0013_live_gdp_cpi_vintages`, the private
GDP/CPI evidence and canonical datasets, and their two fixed official-source
collectors. It adds no public tool, dashboard, export, credential, or caller
path. Its exact historical projection removes those declarations and restores
`2.15.0`.

Revision `2.17.0` adds only `macro:0014_live_employment_vintages` and the
private Philadelphia Fed historical and BLS current employment collectors.
It reuses the two private official-vintage datasets, adds no public consumer or
credential, and preserves raw evidence plus version/current lineage. Its exact
historical projection removes migration 0014 and those two collectors and
restores byte-exact revision `2.16.0`.

Revision `2.18.0` adds only `macro:0015_live_macro_history_extension` and two
manual-only history collectors. It reuses the private official-vintage
datasets, adds no credential, public consumer, or scheduler, and projects
byte-exactly back to `2.17.0`.

Revision `2.19.0` adds only
`fmp.macro.gdp_cpi_release_calendar_history`, reusing the established calendar
relation and official-vintages tool dependency. It adds no migration, dataset,
job, scheduler, public consumer, or surprise table; its exact historical
projection restores `2.18.0`.

Revision `2.20.0` adds only
`fmp.macro.employment_release_calendar_refresh` and its reciprocal binding to
the existing economic-calendar dataset. It adds no migration, dataset, job,
timer, physical surprise table, or new route; its exact historical projection
restores byte-exact revision `2.19.0`.

Revision `2.21.0` adds only
`macro:0016_fmp_calendar_wholesale_evidence`, the private
`macro.fmp.economic_calendar_evidence` dataset, and
`fmp.macro.us_economic_calendar_wholesale`. Its two physical relations retain
immutable response bytes and complete raw-row projections. It adds no public
consumer, job, scheduler, export, or surprise table; its exact historical
projection restores byte-exact revision `2.20.0`.

The frozen Stage 7 rebuild keeps its historical `2.5.0`/`1.3.0`, zero-export
projection; the frozen Stage 6 rebuild uses `2.4.0`/`1.2.0`, jobs-empty
projection; and the frozen Stage 5 rebuild uses `2.3.0`/`1.1.0`. Their approved
deterministic evidence remains reproducible.

The frozen compatibility inventory remains all 57 reviewed public tool names.
The active registry exposes 59 read-only tools: that frozen surface plus
`market.get_available_ticker` and `market.get_price_series`. The original
`macro.get_series` and `timeseries.describe` contracts retain their Stage 1
projection; the other 55 frozen tools are explicit forward reconstructions.
Agents in another local project should use the fixed subprocess interface in
[Local Agent Tools](docs/LOCAL_AGENT_TOOLS.md), not direct SQLite access.
Unsupported fixture semantics return
`not_established`, and the live intraday name is capability-disabled.
Application hosts must construct `StoreMap.four_explicit(...)` and supply all
four paths. Public callers cannot select a database path, submit SQL, or
initialize a store. Stage 7 jobs remain disabled and manual-fixture-only in
their historical projection. The Stage 8 export has no public path, SQL,
relation, or connection argument and no live-store/UI connection. Stage 6's
browser-automation limitation was explicitly accepted by the user; Stage 7
independent verification passed on 2026-08-11. Stage 8's primary offline gate
and independent verification passed on 2026-08-11, and its bounded exit gate
was accepted on 2026-08-14 with an explicit browser-automation waiver.
Stage 9 is not a public boundary: its populated exact FMP slice remains
non-production, manual-only, candidate-only, and independently verified.
Stage 10 also remains private, non-production, and candidate-only. Stage 11 is
complete only as a private, non-production candidate and has no public
consumer or promotion authority. Registry declarations are not evidence of
live completion; the retained receipt is.

Stage 12A remains immutable offline authority/coverage evidence, and Stage 12B
remains fixture-only evidence. Stage 12C is complete only as the exact private
bounded no-copy population recorded in its
[evidence record](docs/rebuild/STAGE12C_EVIDENCE.md); it does not create a
general HTTP 402 rule, transfer or promote a database, expose a public
consumer, or authorize a scheduler. The final neutral source check used
`mode=ro&immutable=1` after a zero-WAL precondition; the disclosed earlier
`-shm` timestamp effect was not a database mutation. Stage 12D is complete and independently verified under its
[no-transfer adoption/freeze contract](docs/rebuild/STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
and [evidence record](docs/rebuild/STAGE12D_EVIDENCE.md). Its two read-only
proofs were filesystem-neutral and created only their immutable private
receipts. Stage 12E remains closed.
