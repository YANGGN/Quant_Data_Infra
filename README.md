# Quant Data Infrastructure

This repository is a clean rebuild of a personal quant-data platform. The
current executable scope is the accepted, independently verified offline
Stage 2 foundation: four explicitly routed SQLite stores, reviewed synthetic market
and macro fixtures, a shared artifact/snapshot/quality/run-audit control
plane, point-in-time reads, two read-only tools, read-only health, and
WAL-safe online backup and restore. It has no live providers, scheduler jobs
or installation, exports, Atlas, promotion, credentials, destructive storage
operations, or default database fallback.

The target design and sequencing are documented in [ARCHITECTURE.md](ARCHITECTURE.md),
[ROADMAP.md](ROADMAP.md), and the [rebuild documentation index](docs/rebuild/README.md).
The new migration resources are deliberate reconstructions; they do not claim
byte-exact recovery of the lost application.

The preserved Stage 1 slice and its historical hashes are recorded in the
[Stage 1 acceptance evidence](docs/rebuild/STAGE1_EVIDENCE.md). The current
four-store foundation and its approved deterministic receipts are recorded in
the [Stage 2 acceptance evidence](docs/rebuild/STAGE2_EVIDENCE.md).

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

## Current public boundary

Only `macro.get_series` and `timeseries.describe` are validated public tools.
All 57 compatibility names remain reserved; no other public tool is validated
at this stage. Application hosts must construct `StoreMap.four_explicit(...)`
and supply all four paths. Public callers cannot select a database path, submit
SQL, or initialize a store. Stage 3 market and macro restoration remains gated
and unauthorized.
