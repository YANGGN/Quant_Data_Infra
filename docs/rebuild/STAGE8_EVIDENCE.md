# Stage 8 Evidence Record

Status: **Accepted for the bounded profile. Primary and independent offline
gates passed; unavailable in-app browser automation was explicitly waived by
the user on 2026-08-14.**

## 1. Purpose and status boundary

This record captures the primary and independent evidence for the bounded Stage
8 derived export and Quant Data Atlas work. Both offline gates pass. The
required in-app browser client failed before launch and no substitute browser
automation was used. On 2026-08-14 the user explicitly waived that external
limitation and accepted the bounded exit gate as complete. This disposition is
not evidence that the unavailable browser checks passed.

The user authorized only a deliberate forward reconstruction of one
synthetic-fixture Atlas snapshot. It is not a recovery claim for the historical
Atlas site, its lost source/package lock, or the recovered 13-dataset inventory.
SQLite remains the sole authoritative operational state for market, macro,
company, and news.

## 2. Bounded registered profile

| Contract | Authorized declaration |
| --- | --- |
| Registry | `2.6.0`, schema `1.4.0`, `validated` status |
| Export ID | `atlas.fixture_snapshot` version `1.0.0` |
| Semantic dataset ID | `atlas.fixture_snapshot.core_v1` |
| Owner / kind / format | `quant_data.atlas` / `atlas_snapshot` / strict JSON |
| Lifecycle | Manual-only, fixture-only, no network, no hosting, `fixture_validated` declaration |
| Consumer | `quant_data_atlas`, `static_read_only`, hosting disabled |
| Benchmark decision | `not_required_json_atlas_snapshot`; Parquet and DuckDB not adopted |
| Source mode | Complete physical-lock cohort followed by per-store SQLite online backups reopened query-only |
| Cross-store claim | Per-store receipts and coordination window only; `cross_store_atomic: false` |

The only reciprocal dataset declarations are:

1. `fixture.market.daily_prices`;
2. `fixture.macro.gdp_vintages`;
3. `fixture.company.issuers`; and
4. `fixture.news.items`.

No caller supplies SQL, a relation, a database path, a source path, a partition
expression, a credential, or an output root. The export root is host-selected
and must be explicit.

## 3. Projection and point-in-time contract

The projection is bounded to `market-prices`, `gdp-vintages`,
`company-issuers`, and `news-items`. They use registered ordering, unique row
identity, strict JSON schemas, and the following total bounds:

- 250 rows and 262144 bytes per ordered chunk;
- 5000, 1000, 1000, and 5000 rows respectively; and
- 12000 rows, 8 MiB, and 30 seconds for the whole snapshot.

The cutoff is an aware UTC export-start instant. Eligibility uses the shared
availability comparator at or before that cutoff. Source values carrying only
date precision retain that precision and use the registered `completed_date`
policy; the exporter must not invent a midnight timestamp. Future versions or
rows unavailable at the cutoff must not change the selected rows, chunk content,
or revision identity for the earlier cutoff.

The public fields are the registered projection fields only. In particular,
body text, summaries, source URLs, raw artifacts, private receipts, run IDs,
artifact IDs, snapshot IDs, credentials, filesystem/database paths, and SQL are
excluded.

## 4. Source-copy and publication contract

The exporter obtains the complete resolved physical lock set in deterministic
order, verifies required source receipts, creates one SQLite online backup per
store, releases writer locks once the copies exist, and then opens only those
copies read-only/query-only. It neither initializes nor migrates a source store.


`receipt_chain: complete_baseline_plus_validated_deltas` has a narrow meaning.
The selected checkpoint may be a validated partial correction only when the
audit proves a prior succeeded, validated, complete baseline and every selected
delta in the chain. Missing links, invalid snapshots, selected running work, or
an unreconciled later failure reject the export. `partial_scope: forbidden`
still requires all four registered projections; a partial correction receipt
is never treated as complete by itself.

The only permitted derived-output layout is an exact, host-selected root with:

```text
<export-root>/
  .staging/<unique-attempt>/{source-copies,public-payload}/
  revisions/atlas.fixture_snapshot/<immutable-revision>/public/
  current/atlas.fixture_snapshot.json
  private-receipts/
```

Public artifacts contain the strict manifest, schemas, checksums, bounded JSON
chunks, and static Atlas bundle. Private receipts are never public artifacts.
The revision identity binds the registered contract/schema/code/cutoff and
cutoff-scoped selected-row/chunk-plan material, rather than local paths,
attempt IDs, wall-clock time, or future-ineligible rows.

Validation finishes before promotion. An exact staging child may be cleaned only
after it is proven to be inside the allowed staging parent. A complete revision
is immutable; `current` is a convenience pointer updated atomically. Failure
before revision promotion preserves every prior final revision and pointer.
Failure after a revision but before pointer publication leaves a valid
unreferenced revision and retains the previous pointer. Failure to publish the
private receipt must not report success or leave a newly selected current
revision.

## 5. Static Atlas boundary

Atlas is a dependency-free static consumer. It reads a relative public manifest
and checksum-verified public chunks only; it has no API, operational-store,
credential, writable-connection, provider, scheduler, or arbitrary SQL path.
The static bundle reuses the Stage 6 token source and pinned local Inter 4.1
WOFF2/OFL assets. It must not fetch remote fonts, reference-site assets,
analytics, or trackers.

`sites/quant-data-atlas/.vite/` is surviving optimizer-cache metadata and is
not source. No package manager, framework lock, `.openai/hosting.json`, hosting,
deployment, or public release is part of this stage. A static site preview used
for verification is not a hosting or deployment action.

The UI must present snapshot identity, cutoff, per-store cohort limitation,
freshness, provenance, schema, pagination, and honest stale, unavailable,
truncated, and last-valid-snapshot failure states. It must preserve semantic
HTML, keyboard/focus access, reduced-motion treatment, responsive layout, and
safe DOM construction without dynamic HTML evaluation.

## 6. Primary gate evidence

The final primary candidate passed the exact dependency-free suite:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
```

Result: **319 tests in 243.566 seconds, OK**.

Focused gates also passed:

- Atlas source, exporter, publication, static-loader, and adversarial coverage:
  **37/37 in 31.642 seconds**;
- registry/profile/tool compatibility: **25/25 in 9.243 seconds**;
- clean Stage 8 integration: **3/3 in 29.139 seconds**;
- generated-artifact freshness, Node syntax, and `git diff --check`: passed.

The separate-process Stage 8 CLI used two explicit empty temporary roots:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data \
  --stage stage8 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root <empty-work-root-1> \
  --second-store-root <empty-work-root-2>
```

It returned exit 0, exactly one strict-JSON stdout line, and zero stderr. Reuse
of the now-nonempty roots returned exit 2 with one sanitized `conflict` error,
zero stdout, and byte/mode-identical roots.

The deterministic evidence pins are:

| Evidence | SHA-256 |
| --- | --- |
| Stage 8 evidence | `e1151f92176a6b5a9d57d18a215497baab7405187fa798537cd947be1dd80a3c` |
| Immutable revision | `b57c5202014bca64fafd39527b8d301a48aa3c928ba147527009874ecf4e0075` |
| Public manifest | `48a8b120f6e6adee6ea8cce5e03fec130bb139368b36923331b2c04f10310dba` |
| Public snapshot | `6c1a563f764d1e1a3f6894b62b67bc90a649c5e5964dfbdeadc37478d05d0b35` |
| Private receipt manifest | `b95844e8fd3b3429fc12f2fd9a39394a8bb709143ad56a673e50337223531cdf` |
| Publication manifest | `bff205754a3d9d26ace3d3bcd56bd0150a161abfaae09c5cf779e0db9eb486a8` |
| Export contract | `f6b8375e02f17613de5790f898410f222fd56ff8c3c70f4d0176a41c0c8ed56c` |
| Query contract | `b01d516ffd2f5985ade14c431b10442bb4c05b41df108a920f4ae68e6436aa24` |
| Schema contract | `195e56b85c441771798e9545f19ede91adde7c999791bb51948e2c7fe0ada23d` |
| Static loader | `1d4f1499d117a1bb89f4d2dcc17a999fc37ae918e2225bd4d64ac35c30d0fd4e` |
| Frozen Stage 7 evidence | `63a0179e1149b73afaa50aff586160c667262dd834fd38f0f0d5043c926fd1f7` |

The generated fixture snapshot contains four chunks and ten public rows
(market 4, macro 2, company 2, news 2). The gate proves unchanged source
fingerprints, query-only copies, cutoff/range invariance, immutable reuse,
receipt-before-pointer binding, prior-pointer preservation under failures, and
zero network, scheduler, hosting, Parquet, or DuckDB operations.

## 7. Browser and independent-verification status

A fresh generated snapshot was served only on loopback at
`http://127.0.0.1:36017/`. The required in-app browser client was attempted
twice by the primary agent. Both attempts failed before launch with
`node_repl kernel exited unexpectedly`; diagnostics reported
`windows sandbox failed: helper_unknown_error: setup refresh had errors` and
`stdout_eof`. The server was stopped cleanly. An independent third client
attempt failed before launch with the same diagnostics, before another server
was started. No alternate automation was used. Static Node hostile-input tests
and generated-snapshot integration pass, but responsive, keyboard, focus,
reduced-motion, zoom, and runtime-network browser observations remain unrecorded.

Independent SolUltra verification passed: the corrected pointer regressions
passed 2/2 in 3.504 seconds, the Atlas suite passed 37/37 in 31.588 seconds,
and the exact full suite passed 319/319 in 247.166 seconds. Fresh CLI output,
nonempty-root refusal, documentation pins, historical goldens, and final seals
also matched. The offline candidate is verified. On 2026-08-14 the user
explicitly waived the unavailable in-app browser-automation check and accepted
the bounded Stage 8 exit gate as complete. The unrecorded observations above
remain limitations, not passing evidence.
