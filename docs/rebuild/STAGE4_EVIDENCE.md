# Stage 4 Acceptance Evidence

Status: Implemented - fixture-validated and independently verified
Evidence date: 2026-08-10
Registry revision: `2.2.0`

## Scope

This record covers the bounded offline Stage 4 company, news, and options
restoration on top of the independently verified Stage 3 market/macro
foundation. The implementation uses only reviewed synthetic fixtures and
explicit temporary store roots. A read-only SolUltra verifier independently
passed this gate; Stage 5 is the next authorized executable stage.

The current registry has 30 fixture-validated migrations, 33 datasets, and 19
manual, non-network collectors. Stage 4 adds nine deliberate forward
reconstructions: market `0007` through `0008`, company `0003` through
`0007`, and news `0003` through `0004`. Their identities, resources, and
SHA-256 values are frozen in the
[migration reconstruction map](MIGRATION_RECONSTRUCTION.md). None claims
`recovered_exact` parity with lost SQL bytes or historical checksums.

The historical Stage 3 evidence is preserved under its derived `2.1.0`
registry profile. This record extends, rather than rewrites,
[Stage 3 acceptance evidence](STAGE3_EVIDENCE.md).

## Evidence status

The approved primary-gate values are pinned in
[`tests/fixtures/stage4_golden.json`](../../tests/fixtures/stage4_golden.json).
The integration test compares the complete selected evidence to that file; it
does not accept merely well-formed output.

### Reproducible WSL/Linux commands

From `/home/volatility/Python_Projects/Quant_Data_Infra` in Ubuntu/WSL, run
the two-root gate only with explicit empty roots beneath `/tmp`:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
first_root="$(mktemp -d /tmp/quant-data-stage4-first.XXXXXX)"
second_root="$(mktemp -d /tmp/quant-data-stage4-second.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data \
  --stage stage4 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root "$first_root" \
  --second-store-root "$second_root"
```

The command refuses a nonempty work root, creates no project `data/`
directory, and emits one strict-JSON evidence line. The focused and full offline
checks are:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m unittest \
  tests.test_stage4_migrations \
  tests.company.test_stage4_company \
  tests.market.test_stage4_options \
  tests.news.test_stage4_news \
  tests.test_stage4_integration -v
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m unittest discover \
  -s tests -t . -v
```

Recorded primary implementation results: the focused Stage 4 gate passed
`27/27`; the full offline suite passed `164/164`; and the fresh two-root CLI
exited zero, wrote zero stderr bytes, and emitted one deterministic JSON line.

### Approved deterministic receipts

| Receipt | SHA-256 |
| --- | --- |
| Complete Stage 4 two-root evidence | `c5759ca09c3fccd8f5c79c51131c5456c0357b928993dd6182417dba919aa7e7` |
| Preserved Stage 3 evidence | `94d3ffb95485ed3c2fea119ea56fdaa4db076f57eab08d204ea734ee893b3854` |
| Stage 4 fixture evidence | `f9e40538a4e2ea43533d65d5d5bebba3bc1046da78799ab2959c369ecfa1b587` |
| Golden query results | `4239480eb113eb6e3f69a8066914093fe5fe8f2d49432840a8df6211016f09ea` |
| Logical manifest | `28a840c38ff02b700872abfdb3933782952047c3af11220581c2a419064ab382` |
| Recorded two-root CLI stdout | `67cc6d2d5449e073cc5e17144b55424562520c47788d456009c11fd933b83b63` |

The source, backup, and restored cohorts each contain the four explicit stores.
The gate requires equal path-free logical manifests, unchanged source reads,
unchanged source during online backup, unchanged backup during restore, equal
restored golden queries, and equal restored health.

## Fixture-validated scope and gate coverage

- The registry still exposes exactly the two validated Stage 1 tools,
  `macro.get_series` and `timeseries.describe`; all 57 compatibility names
  remain reserved. It declares no jobs or exports. Stage 4 adds no dashboard
  and leaves the existing Stage 1 overview dashboard declaration unchanged.
- Company adds immutable SEC evidence, stable issuer identity, dated
  ticker/security links, joint-filing issuer membership, append-only filings,
  raw facts, metric mappings and fundamental versions, corporate actions,
  consensus, earnings events, and source-linked guidance. Tests cover ticker
  change, joint filing, CompanyFacts correction, date-only precision, distinct
  instant versus weighted-average share semantics, replay no-write, ambiguity
  rejection, and point-in-time reads.
- Options adds standard-deliverable contracts and synchronized capture cohorts
  for surface snapshots, underlying quotes, rates, dividends, expiry inputs,
  open interest, close prices, and bars. Tests reject mixed feed/environment
  cohorts, unknown contracts, and incoherent synchronized inputs before any
  write; nonstandard deliverables remain explicitly excluded.
- News adds immutable source artifacts and snapshots, stable item identity,
  append-only content versions, retractions, explicit missing/redacted content,
  source-lineaged labels, local-capture-aware reads, and a derived FTS5 search
  index that is never source truth. Exact semantic replay is a total no-op.
- All 17 Stage 4 fixtures are byte- and semantic-identity-pinned in the shared
  manifest, are synthetic test fixtures, and are non-promotable.
- The gate checks Stage 3-to-Stage 4 forward upgrade, repeated initialization,
  migration resource integrity and atomic rollback, the narrowly reviewed FTS5
  authorizer exception and shadow-table reconciliation, foreign keys,
  read-only mutation fingerprints, source/backup/restore equivalence, and two
  clean roots.

## Independent verifier status

Status: **Passed - verified.** A read-only SolUltra verifier independently
derived the Stage 4 checks from the user authorization, accepted contracts,
migration map, registry, golden evidence, and current workspace.

The initial verification pass found one high-severity gap: a malformed SEC
accession could pass importer validation and persist. The correction introduced
one canonical 10-2-6 accession validator across filing, fact, and non-null
guidance paths before coordinator execution. Independent repros then rejected
the filing, fact, and guidance cases with structured `invalid_request` /
`format` issues, zero run or failure rows, and byte-identical all-store
fingerprints. The valid joint filing still produced one filing with two issuer
memberships.

The final verifier reran the full offline suite (`164/164`), the focused
Stage 4 gate (`27/27`), and a fresh two-root Stage 4 CLI. The CLI exited zero,
emitted one strict-JSON line, wrote zero stderr bytes, and matched every
approved receipt above. It rehashed all 30 migrations and 46 fixtures, verified
the exact historical registry profiles, exercised migration/FTS tamper
handling, company/news/options point-in-time and no-write behavior,
source/backup/restore equality, query-only restored readers, excluded scope,
documentation links, and pre/post workspace stability.

A stale untracked patch-backup file found during correction verification was
removed after its exact path and hash were checked. The verifier confirmed that
this was the only intervening worktree change and sealed an artifact-free
188-file project snapshot. No project file was changed by the verifier.

`G4` is complete. Stage 5 is the next authorized offline synthetic-fixture
stage; Stage 6 remains closed until Stage 5 is independently verified and
committed.

## Deliberate boundary

Stage 4 uses no network or live provider, credentials, default database path,
scheduler or job installation, export or Atlas path, promotion, destructive
storage operation, new public tool or dashboard implementation, or hosted UI.
SQLite remains
authoritative; all executed evidence uses explicit temporary roots and reviewed
synthetic fixtures. Stage 5 is the next authorized stage; Stage 6 remains
strictly sequential behind Stage 5 independent verification and its separate
commit gate.
