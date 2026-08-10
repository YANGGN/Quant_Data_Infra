# Quant Data Infrastructure

This repository is a clean rebuild of a personal quant-data platform. The
current executable scope includes the bounded offline Stage 5 composable tool
platform. Its primary fixture gate and independent SolUltra verification have
passed. It extends the independently verified Stage 4 company,
news, and options restoration and the independently verified Stage 2 and Stage
3 foundations of four explicitly routed SQLite stores, reviewed synthetic
fixtures, a shared artifact/snapshot/quality/run-audit control plane,
point-in-time reads, read-only health, and WAL-safe online backup and restore.
It has no live providers or network collection, scheduler jobs or installation,
exports, Atlas, promotion, hosting, credentials, destructive storage
operations, dashboard UI, default database fallback, or enabled live tools.

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
[Stage 5 acceptance evidence](docs/rebuild/STAGE5_EVIDENCE.md).

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
line. The primary gate and independent SolUltra verification passed. Stage 6
is the next authorized executable stage after this verified Stage 5 snapshot
is committed separately.

## Current public boundary

Registry revision `2.3.0` exposes all 57 reviewed public names through generated
schemas and closed read-only routing. The original `macro.get_series` and
`timeseries.describe` contracts retain their Stage 1 projection; the other 55
are explicit forward reconstructions. Unsupported fixture semantics return
`not_established`, and the live intraday name is capability-disabled.
Application hosts must construct `StoreMap.four_explicit(...)` and supply all
four paths. Public callers cannot select a database path, submit SQL, or
initialize a store. The registry still declares no jobs or exports. Stage 5 is
independently verified; Stage 6 is the next authorized executable stage after
the separate Stage 5 commit.
