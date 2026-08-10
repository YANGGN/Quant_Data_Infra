# Stage 2 Acceptance Evidence

Status: Implemented — fixture-validated
Evidence date: 2026-08-09
Registry revision: `2.0.0`

## Scope

This record covers the accepted offline four-store persistence foundation. It
extends the Stage 1 fixture state with ten fixture-validated store-local
migrations, a shared control plane, strict split-store routing and physical
identity locks, bounded capture-time composition, read-only health, and
WAL-safe online backup and restore.

This is not a historical-recovery claim. The reviewed migrations are deliberate
reconstructions and never `recovered_exact`. The historical
[Stage 1 acceptance evidence](STAGE1_EVIDENCE.md) remains unchanged; its
original scope and pins are not replaced by this record.

## Evidence status

### Implementation evidence

The approved fixture evidence is pinned in
[`tests/fixtures/stage2_golden.json`](../../tests/fixtures/stage2_golden.json).
The deterministic two-rebuild gate compares its full path-free evidence to that
fixture, rather than accepting merely well-shaped output.

The final post-correction offline suite was run as:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
```

Result: `Ran 112 tests in 17.126s — OK`.

Run the deterministic Stage 2 rebuild gate only with explicit empty work roots:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data \
  --stage stage2 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root <empty-work-root-1> \
  --second-store-root <empty-work-root-2>
```

It refuses a nonempty work root, emits strict JSON, and requires equal
path-free evidence from two clean rebuilds.

### Independent verifier status

Status: **Verified with stated limitations.** A read-only SolUltra verifier
independently reran the full `112`-test suite (`17.032s`), the two-root rebuild,
all seven deterministic pins, and the adversarial migration, registry,
ingestion, point-in-time, locking, health, backup/restore, and public-read
matrix. It reproduced and closed the four correction findings: normalized
stable IDs, callback transaction containment, physical snapshot-artifact
membership, and cutoff-filtered receipt lineage. Its before/after project,
`plan.md`, and preserved `.vite` fingerprints were unchanged.

Verification was intentionally offline. It did not exercise excluded live
providers, external network behavior, or any unauthorized Stage 3 capability.
Passing this gate records Stage 2 acceptance; it does not itself authorize
Stage 3.

## Approved deterministic receipts

| Receipt | SHA-256 |
| --- | --- |
| Complete Stage 2 evidence | `3501d94b16310bfe25c3e26c7d9f997707757cc0972d73e39cb52029b2ea633b` |
| Embedded Stage 1 fixture evidence under registry `2.0.0` | `92f9dd8ae112f1da6156e256510c013d21c86aef0dd2d83b81d58129f21d5d29` |
| Stage 1 golden query results under registry `2.0.0` | `8d8ee7164a6a77faaf7b6df8973d3d54dcdeb5473de7444ec47fc25ebc7f0f17` |
| Stage 2 golden query | `ce5fcc3365a00a1b5bf1595244ee697c3df498089ff559e22692b2382d79cb63` |
| Cross-store composition | `17b7ba8ccb291731f78920762835a1440f71cfb5e3fbc6d33afeace369b747ad` |
| Logical manifest | `f6c4bd262a0b5ea656c088b60c49658341d45f6d270a2641c3b7cc8c44f4cfa0` |

The source, backup, and restored cohorts each contain the four explicit stores
and have equal logical manifests. The gate also records that source reads do
not mutate a store, an online backup does not mutate its source cohort, a
restore does not mutate its backup cohort, and restored queries and composition
match source results.

## Fixture-validated foundation

- The registry is revision `2.0.0`, owns exactly four distinct operational
  stores, reserves exactly 57 compatibility names, and exposes only
  `macro.get_series` and `timeseries.describe` as validated public tools.
- The ten store-local migrations are fixture-validated: three each in market
  and macro, and two each in company and news. The four Stage 2 control-plane
  resources share SHA-256
  `144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b`;
  their identities, ordinals, and reconstruction status are recorded in the
  [migration reconstruction map](MIGRATION_RECONSTRUCTION.md).
- Every store carries the shared artifact, snapshot, quality-result,
  ingestion-run-output, and failure-audit control plane. Exact semantic replay
  remains a no-write outcome. Company and news are initialized only as empty
  domain foundations.
- Routing requires four explicit, physically distinct store identities. Locks
  use resolved physical identity and deterministic multi-store acquisition
  order; unified runtime fallback is retired.
- Cross-store composition uses a declared `captured_at` availability field,
  one explicit cutoff, and a coherent SQLite read transaction per contributing
  store. It reports `best_effort_multi_store` and `none_best_effort` cohort
  evidence instead of claiming cross-file atomicity.
- Health reconciliation opens registered stores read-only and query-only; it
  does not initialize or mutate them. SQLite online backup/restore is
  WAL-safe, verifies health and logical manifests, and refuses unsafe targets.
- The hardened physical contract accepts exactly `evidence`, `canonical`, and
  `derived` layers. A forward-preserving Stage 2 table rebuild aligns the
  SQLite constraint with that contract; it temporarily disables foreign-key
  enforcement only inside the atomic rebuild and requires `foreign_key_check`
  before the migration ledger advances.
- Immutable control-plane identities reject `INSERT OR REPLACE` even when
  recursive triggers are off; library connections enable recursive triggers.
  Running-run identities and failed run IDs cannot be reused, and health
  reconciles reviewed `sqlite_master` schema SQL rather than relation and
  trigger names alone.
- Registry-owned stable IDs use one lowercase namespaced grammar and reject
  traversal, whitespace, case drift, and non-normalized separators even when
  every reciprocal reference is rewritten consistently.
- The ingestion coordinator denies callback transaction and savepoint control;
  failed escape attempts roll back the logical batch before one durable,
  path-free failure audit is recorded. A successful run must link its selected
  artifact through immutable snapshot membership. A validated bounded
  `partial` correction snapshot remains distinct from a `partial` run outcome,
  which cannot be published as succeeded.
- Composition filters rows, receipt lineage IDs, high-water state, and receipt
  hashes through the same declared availability cutoff. A pre-history cutoff
  therefore exposes neither facts nor future evidence identities.

## Deliberate boundary

Stage 2 does not add live providers, scheduler installation or jobs, exports,
Atlas, promotion, destructive storage operations, or broader Stage 3 market
and macro restoration. Stage 3 remains gated and unauthorized.
