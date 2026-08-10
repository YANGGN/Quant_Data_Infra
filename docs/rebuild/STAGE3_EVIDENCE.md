# Stage 3 Acceptance Evidence

Status: Implemented — fixture-validated and independently verified
Evidence date: 2026-08-09
Registry revision: `2.1.0`

## Scope

This record covers the bounded offline Stage 3 market and macro restoration on
top of the independently verified Stage 2 four-store foundation. It records
both the primary fixture gate and the subsequent independent verifier result;
it does not itself authorize Stage 4.

The current registry has 21 fixture-validated migrations, 19 datasets, and 14
collectors. Stage 3 adds eleven deliberate forward reconstructions: market
`0004` through `0006` and macro `0004` through `0011`. Their identities,
resource paths, and SHA-256 values are frozen in the
[migration reconstruction map](MIGRATION_RECONSTRUCTION.md). None claims
`recovered_exact` parity with lost SQL bytes or historical checksums.

The historical Stage 2 evidence is preserved under its derived `2.0.0` registry
profile. This Stage 3 record therefore extends, rather than rewrites,
[Stage 2 acceptance evidence](STAGE2_EVIDENCE.md).

## Evidence status

The approved primary-gate values are pinned in
[`tests/fixtures/stage3_golden.json`](../../tests/fixtures/stage3_golden.json).
The integration test compares the complete selected evidence to that file; it
does not accept merely well-formed output.

### Reproducible WSL/Linux commands

From `/home/volatility/Python_Projects/Quant_Data_Infra` in Ubuntu/WSL, run the
two-root gate only with explicit empty roots beneath `/tmp`:

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

The command refuses a nonempty work root, creates no project `data/` directory,
and emits strict JSON evidence. The focused and full offline checks are:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -m unittest \
  tests.test_stage3_integration -v
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -m unittest discover \
  -s tests -t . -v
```

Recorded primary implementation results: Stage 3 integration `2/2` passed and
the full offline suite `135/135` passed. Both execute only against explicit
temporary roots and reviewed fixture resources.

### Approved deterministic receipts

| Receipt | SHA-256 |
| --- | --- |
| Complete Stage 3 two-root evidence | `94d3ffb95485ed3c2fea119ea56fdaa4db076f57eab08d204ea734ee893b3854` |
| Preserved Stage 2 evidence | `3501d94b16310bfe25c3e26c7d9f997707757cc0972d73e39cb52029b2ea633b` |
| Stage 3 fixture evidence | `7012b0d3c0c66d1f13008f227d3fd3758151310914dfae078bef0195e1424519` |
| Golden query results | `bc96b310b1a0db88f8f9a1c97e8ea48356dd10a0f2f288e1251679e2285d737a` |
| Logical manifest | `7f666a76c43c2493f8f0fbb1b904a3c519cdbc705f2cb7ac26cc5c8a8cae6523` |

The source, backup, and restored cohorts each contain the four explicit stores.
The gate requires equal path-free logical manifests, unchanged source reads,
unchanged source during backup, unchanged backup during restore, equal restored
queries, and equal restored health.

## Fixture-validated scope and gate coverage

- The registry remains at exactly two validated public tools,
  `macro.get_series` and `timeseries.describe`; all 57 compatibility names are
  reserved. It declares no jobs or exports.
- Market adds immutable catalog capture evidence, canonical instruments and
  dated provider identifiers, effective-dated classifications, and controlled
  universe membership. The fixture set validates exactly the nine recovered FMP
  index identities, preserves canonical identity across identifier changes, and
  permits a membership tombstone only from an authoritative complete universe
  snapshot. A later complete snapshot can restore membership.
- Macro adds a generic catalog/dimension/observation contract, GDP vintage
  provenance, Treasury current-state and correction history, an economic
  calendar, aggregate-only SOMA evidence and summaries, EIA electricity-retail
  and weekly-fundamentals evidence/facts, and completed U.S. recession periods.
  Date-only precision remains a date, and latest, `as_of`, and defensible
  `first_release` fixture results are distinct where supported.
- The 25 new synthetic Stage 3 fixtures, together with four preserved Stage 1
  fixtures, are hash- and semantic-identity-pinned in the shared manifest. An
  incomplete or falsely complete EIA retail capture is rejected before
  coordinator mutation; complete pagination, row totals, and scope must agree.
  An exact replay and volatile-only BLS/BEA metadata changes are no-write
  outcomes.
- The gate checks the Stage 2-to-Stage 3 forward upgrade, repeated
  initialization, migration/resource integrity, foreign keys, read-only query
  fingerprints, source/backup/restore equivalence, and two clean roots.

## Independent verifier status

Status: **Passed — verified with stated limitations.** A read-only SolUltra
verifier independently derived the Stage 3 checks from the user authorization,
accepted contracts, migration map, registry, golden, and current workspace. It
reported no blocking, major, minor, or correctness findings.

The verifier reran the complete offline suite (`135/135`, `54.088s`) and a
fresh two-root Stage 3 CLI (`exit 0`, one strict JSON line, zero stderr). It
matched all five approved receipts, rehashed all 21 migration and 29 fixture
resources, verified the exact current and historical registry profiles, and
independently exercised migration tamper failure, market and macro temporal and
no-write behavior, query-only source/backup/restore cohorts, documentation
links, excluded scope, and pre/post workspace stability. No project file was
changed during verification.

The limitation is deliberate: this result covers only the authorized
synthetic, offline Stage 3 boundary. Live providers, schedulers, exports,
promotion, and Stage 4 remain unverified and unauthorized. `G3` is complete;
Stage 4 still requires separate explicit authorization.

## Deliberate boundary

Stage 3 uses no network or live provider, credentials, default database path,
scheduler or job installation, export or Atlas path, promotion, destructive
storage operation, new public tool, company/news domain restoration, options
work, or CUSIP-level SOMA storage or source path. SQLite remains authoritative;
all executed evidence uses explicit temporary roots and reviewed synthetic
fixtures.
