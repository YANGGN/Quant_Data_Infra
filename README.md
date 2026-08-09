# Quant Data Infrastructure

This repository is a clean rebuild of a personal quant-data platform. The
current executable scope is the authorized, offline Stage 1 vertical slice:
four explicitly routed SQLite stores, reviewed synthetic market and macro
fixtures, point-in-time reads, two read-only tools, and a loopback inspection
surface. It has no live providers, scheduler, exports, credentials, or default
database fallback.

The target design and sequencing are documented in [ARCHITECTURE.md](ARCHITECTURE.md),
[ROADMAP.md](ROADMAP.md), and the [rebuild documentation index](docs/rebuild/README.md).
The new migration resources are deliberate reconstructions; they do not claim
byte-exact recovery of the lost application.

The completed slice and its reproducible hashes are recorded in the
[Stage 1 acceptance evidence](docs/rebuild/STAGE1_EVIDENCE.md).

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
  --project-root "$PWD" \
  --store-root "$stage1_root/first" \
  --second-store-root "$stage1_root/second"
```

The command refuses a nonempty store root. It verifies migration and fixture
digests, exact no-write replays, corrections and revisions, the golden query
matrix, tool/HTTP equivalence, read-only store fingerprints, integrity, and
equal path-free logical manifests across both rebuilds. Its output is strict
JSON evidence.

## Current public boundary

Stage 1 exposes only `macro.get_series` and `timeseries.describe`; the accepted
57-name inventory remains reserved for later gated stages. Application hosts
must construct `StoreMap.four_explicit(...)` and supply all four paths. Public
callers cannot select a database path, submit SQL, or initialize a store.
