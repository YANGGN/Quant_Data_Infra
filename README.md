# Quant Data Infrastructure

This repository is a clean rebuild of a personal quant-data platform. The
current executable scope includes the bounded offline Stage 7 manual job
rehearsal over the accepted Stage 6 local portal and the independently verified
Stage 1 through Stage 5 foundations. Stage 7 uses only reviewed synthetic
fixtures and explicit temporary roots. Its primary fixture gate and independent
SolUltra verification passed on 2026-08-11.

The portal remains local-only with four fixed routes: Overview (`/`), GDP
Vintages (`/gdp-vintages`), Tables (`/table-inspector`), and Agent Tools
(`/agent-tools`). Stage 7 adds eight disabled `manual_fixture_only` job
declarations, deterministic dry-run plans, fixture preparation before locks,
complete ordered physical-store lock sessions, bounded retries and active
timeouts, private receipt-last evidence, and backup/restore rehearsal. It adds
no live provider or runtime network access, scheduler installation/start,
exports, Atlas, promotion, hosting, credentials, default database fallback, or
destructive operations. Stage 8 remains closed.

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
remains closed.

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

## Current public boundary

The canonical registry is revision `2.5.0`, schema `1.3.0`. It preserves
the four local-private Stage 6 dashboard exposures and declares eight ordered,
disabled `manual_fixture_only` jobs with derived store sets and zero exports.
The frozen Stage 6 rebuild uses its historical `2.4.0`/`1.2.0`, jobs-empty
projection; the frozen Stage 5 rebuild uses `2.3.0`/`1.1.0`. Their approved
deterministic evidence remains reproducible.

The platform still exposes all 57 reviewed public tool names through generated
schemas and closed read-only routing. The original `macro.get_series` and
`timeseries.describe` contracts retain their Stage 1 projection; the other 55
are explicit forward reconstructions. Unsupported fixture semantics return
`not_established`, and the live intraday name is capability-disabled.
Application hosts must construct `StoreMap.four_explicit(...)` and supply all
four paths. Public callers cannot select a database path, submit SQL, or
initialize a store. Stage 7 jobs remain disabled and manual-fixture-only; the
canonical registry declares zero exports. Stage 6's browser-automation
limitation was explicitly accepted by the user. Stage 7 independent
verification passed on 2026-08-11. Stage 8 remains closed.
