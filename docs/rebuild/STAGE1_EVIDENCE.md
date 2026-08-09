# Stage 1 Acceptance Evidence

Status: Implemented
Evidence date: 2026-08-09
Registry revision: `2026-08-09.stage1.3`

## Scope

This record covers only the accepted offline fixture slice in
[VERTICAL_SLICE_SPEC.md](VERTICAL_SLICE_SPEC.md). It is implementation-owner
evidence for independent verification; it does not authorize Stage 2, live
providers, scheduler installation, analytical exports, or promotion.

The six new migration resources are `fixture_validated`. They are deliberate
reconstructions and never claim `recovered_exact` parity with lost bytes or
historical checksums.

## Reproducible commands

From `/home/volatility/Python_Projects/Quant_Data_Infra` in Ubuntu/WSL:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
```

Result: `Ran 61 tests in 8.997s — OK`.

The documented two-rebuild CLI was also executed with two empty paths beneath
a Python `TemporaryDirectory`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root <explicit-empty-first-root> \
  --second-store-root <explicit-empty-second-root>
```

Result: exit `0`, empty stderr, strict-JSON evidence, and equal path-free
results from both rebuilds. No project `data/` directory or SQLite file was
created.

## Deterministic receipts

| Receipt | SHA-256 |
| --- | --- |
| Complete Stage 1 evidence | `9c91ceea6fd6589cb5897e9e923b629fec77e649005af55889cdeb5aadd57fb7` |
| Logical manifest | `827acd3a1a8f166ca019635797033df8dfddc1f088e67dfba974d666e5706a39` |
| Golden query/tool/HTTP results | `a0797bc537d25c7b5f8e558bddd425407b1bd7ceae9bb1bc2c6b72d31086f196` |

These values and all four fixture semantic identities are review-pinned in
[`tests/fixtures/stage1_golden.json`](../../tests/fixtures/stage1_golden.json).
The integration suite compares exact values rather than accepting any
well-shaped digest.

The logical manifest includes the canonical registry declarations, applied
migration IDs and checksums, schema objects, canonical rows, immutable evidence
and version lineage, and fixture-defined timestamps. It excludes store paths,
SQLite page/WAL bytes, and nonsemantic run timing.

## Golden final state

| Store | Fixture-defined rows after base, replay, and update |
| --- | --- |
| market | 2 instruments; 2 identifiers; 2 requests/runs; 4 current prices; 5 immutable price versions |
| macro | 1 series; 2 releases/artifacts/snapshots/scopes/runs; 4 immutable observation versions; 4 memberships |
| company | 0 ingestion runs; independently initialized control plane |
| news | 0 ingestion runs; independently initialized control plane |

## Gate coverage

- all four explicit stores use independent ledgers, WAL writer policy, foreign
  keys, role anchors, query-only readers, and reviewed migration checksums;
- registry validation proves exactly two active Stage 1 tools and 57 unique
  reserved compatibility names; the milestone is explicitly non-active and
  the composable `TimeSeries` schemas are deep-equal;
- fixture byte and semantic digests, test-only/non-promotable declarations,
  expected warnings, strict UTF-8, batch validation, stable identities, and
  transactional rollback are executable;
- exact market and macro replay produces no run, artifact, snapshot,
  membership, fact, checkpoint, or logical-fingerprint delta;
- market correction and macro vintage/correction history append without
  rewriting earlier versions;
- latest, `as_of`, `first_release`, both date-only policies, missingness, and
  future-update invariance have executable coverage;
- direct and HTTP tool results agree, full public payloads are lineage-bound,
  inputs and outputs are strict/bounded JSON, compact extreme exponents fail
  before expansion, the actual server is loopback-only, and every parsed HTTP
  verb receives sanitized JSON plus fixed security headers;
- Overview, health, price, tool-manifest, and tool-call reads leave all four
  store fingerprints unchanged; and
- invalid roots, duplicate physical paths, wrong-role stores, tampered
  migration resources/ledgers, inline transaction-control escapes, malformed
  fixtures, stale lineage digests, and hostile boundary inputs fail closed.

## Deliberate limitations

Stage 1 uses only reviewed synthetic SPY/^GSPC-style and RTDSM EMPLOY-style
fixtures. Company and news domain facts, live collection, the remaining 55
tools, scheduler operations, exports, and Atlas publication remain
unimplemented and gated by [ROADMAP.md](../../ROADMAP.md).
