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
[Stage 11 evidence](docs/rebuild/STAGE11_EVIDENCE.md).

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

## Current public boundary

The canonical registry is revision `2.10.0`, schema `1.7.0`. It preserves the
four local-private Stage 6 dashboard exposures, eight ordered disabled
`manual_fixture_only` jobs, and the one fixture-only manual JSON export,
`atlas.fixture_snapshot`, with reciprocal declarations on
`fixture.market.daily_prices`, `fixture.macro.gdp_vintages`,
`fixture.company.issuers`, and `fixture.news.items`. In addition to the
isolated Stage 9 declarations, it contains the private Stage 10 market-history
and Stage 11 BEA/EIA candidate declarations. None of those live-candidate
relations is exposed through public tools, dashboards, or Atlas exports.
The completed Stage 11 receipt remains frozen to its historical
`2.9.0`/`1.7.0` projection; revision `2.10.0` only makes the already
reviewed retry policy canonical and does not authorize another provider run.

The frozen Stage 7 rebuild keeps its historical `2.5.0`/`1.3.0`, zero-export
projection; the frozen Stage 6 rebuild uses `2.4.0`/`1.2.0`, jobs-empty
projection; and the frozen Stage 5 rebuild uses `2.3.0`/`1.1.0`. Their approved
deterministic evidence remains reproducible.

The platform still exposes all 57 reviewed public tool names through generated
schemas and closed read-only routing. The original `macro.get_series` and
`timeseries.describe` contracts retain their Stage 1 projection; the other 55
are explicit forward reconstructions. Unsupported fixture semantics return
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
