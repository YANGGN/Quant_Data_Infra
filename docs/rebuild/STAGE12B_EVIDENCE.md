# Stage 12B Evidence Record

Status: **Implemented — bounded offline fixture-only incremental collector independently verified. At this gate's closure, Stages 12C–12E were closed; a later separate decision authorizes Stage 12C contract/offline implementation in progress. Stages 12D–12E remain closed.**

Verification date: 2026-08-15

## 1. Purpose and authorized boundary

Stage 12B proves one bounded Market v1 incremental publication unit with an
injected fake response and explicit temporary fixture stores. It uses the
existing market capture/version/current model to demonstrate strict parsing,
complete-batch admission, semantic replay, append-only correction, current
pointer movement, atomic rollback, and physical-store isolation.

This evidence is not a live FMP result and is not an operational database
decision. No verification action read a credential, called a provider, used an
external network service, opened a project-default or retained SQLite store,
allocated a migration, promoted or cut over data, exposed a public consumer, or
installed, changed, started, or removed a scheduler.

The current canonical market default remains project-relative
`data/market.sqlite`, but Stage 12B neither opened nor created that path. All
SQLite publication rehearsals used fresh explicit roots beneath `/tmp`.

## 2. Frozen contract and digests

| Evidence | Retained value |
| --- | --- |
| Gate contract | `quant_data.stage12b_incremental_market_v1_fixture_gate` version `1.0.0` |
| Target profile | `stage12b_incremental_market_v1_fixture` |
| Canonical registry | schema `1.8.0`, revision `2.13.0` |
| Canonical registry source SHA-256 | `b39057d548d6ced6e7c0663ffafbee7e0c16c88a94baced584ff6949da7766f6` |
| Exact Stage 12A registry projection | schema `1.8.0`, revision `2.12.0`, source SHA-256 `80a41e9f124cccb85bbdda665e33b1ef408f538b7e50b499b630b5ce6c761e7e` |
| Stage 12B scope semantic SHA-256 | `91d202540e24d2f94a3adb10135dde0bb3ee0c787c801bf66e547d649a7a3dfb` |
| Stage 12B scope file SHA-256 | `671d0b62cbcc82c92eaf59fe129e4dad12f2f327995f24b81ffbb206d7645392` |
| Stage 12A scope semantic SHA-256 | `8266f431519913879797a14ea0baa781b6c7e0dfe78365a5ff0107e386a0ad29` |
| Stage 12A scope file SHA-256 | `12e9a7380ad22c9595d922c1a53320f3c52e368cb688adf1d97bf4d7c9a18bf6` |
| Stage 12A roster SHA-256 | `a81f62a5fe3710011e1fe6eabfe7fcd483726a23f2dc9b0a7e592b4c78327fbb` |
| Path-free Stage 12B evidence SHA-256 | `8daef487dd9eb42bf4c2711dd5682ffb21751d0c15efc3e71ea8c00e2365ef4e` |
| Golden-file SHA-256 | `0a02417afbabbd7f4b01f87db73da69be0eed955c3dea4b7f32071a04180a3ff` |

The canonical registry declares only the offline fixture collector
`market.stage12b.fmp_daily_incremental_fixture` and handler
`market.stage12b_fmp_daily_incremental_fixture`. Its exact projection removes
that collector and restores the accepted Stage 12A `2.12.0` registry before
older projections run.

The Stage 12B scope admits only the exact byte-bound Stage 12A scope and its
frozen 629-symbol roster: 519 equities, 95 reviewed non-Russell ETFs, and 15
reviewed major indexes. The implementation recomputes the Stage 12B semantic
digest from the in-memory typed scope so a forged dataclass cannot retain a
reviewed digest while changing a bound.

## 3. Implemented fixture collector

The collector accepts one frozen-roster symbol, one inclusive range of at most
seven calendar days, and one through five explicit reviewed session dates. Its
only transport seam is an injected fake. The registered bounds are one attempt,
65,536 response bytes, five rows, and 30 injected elapsed seconds, with no
automatic retry or backoff.

Preparation validates and normalizes a complete strict FMP-shaped OHLCV batch
before any write lock or SQLite transaction. Incoming row order does not affect
semantic identity. Identical semantic replay and reordered replay are total
no-writes. A new natural key appends one initial version; a changed key appends
one correction and advances the current pointer without rewriting prior
versions.

A changed key must have a local capture and availability timestamp strictly
later than the current version. Equal or backdated corrections fail before
publication, preserving the global point-in-time invariant.

The exported collector requires an explicit child of the system temporary
directory and rejects project/default/retained, relative, symlink, and hard-link
aliases. It binds prepared work to the fixture root and market store device,
inode, and link count. It revalidates those identities both before locking and
immediately after lock acquisition, before `sqlite3.connect`, closing
same-path replacement and lock-time replacement races.

Stage 12B reuses the existing
`market:0010_stage10_market_history` physical model only in initialized
fixture stores. It creates no migration and does not call a Stage 9, Stage 10,
or Stage 11 runner or transport.

## 4. Deterministic offline gate

The pure dry run uses two distinct explicit absent-or-empty roots. It emits a
strict, path-free plan and records zero fixture calls, SQLite opens, locks,
transactions, filesystem mutations, credential-environment reads, network
calls, subprocess calls, and scheduler actions.

The fixture rehearsal made 14 injected fake-transport calls and initialized
four temporary fixture-store roles. It made zero live-provider, external
network, credential, operational-store, scheduler, or Stage 9 through Stage 11
runner calls. Two independent roots produced identical logical evidence.

The accepted publication sequence was:

- first complete batch: three initial versions published;
- exact replay: unchanged with zero writes;
- reordered replay: unchanged with zero writes;
- changed overlap: one append-only correction published;
- one new date: one initial version published; and
- injected empty array: classified empty with zero writes.

The final market fixture contained four ingestion runs, three immutable
artifacts, three snapshots, one preseeded AAPL instrument, three captures, five
immutable price versions, and four current price rows. Integrity check returned
`ok`; foreign-key violations, duplicate current rows, invalid current pointers,
and broken supersession links were all zero. The maximum correction sequence
was two. Macro, company, and news fixture fingerprints remained unchanged.

Malformed JSON, a partial complete batch, a one-attempt 503 classification,
raw-byte/request rebinding, elapsed-time overflow, a backdated changed key,
lock failure, and injected commit failure all left the applicable store
fingerprint unchanged. Focused adversarial coverage also rejects malformed
rows, scope drift, raw Stage 12A byte drift, forged in-memory scope objects,
nonfinite elapsed time, hard-link aliases, pre-publication inode replacement,
and replacement during lock acquisition.

## 5. Independent findings and corrections

Independent review identified and reproduced the following boundary defects
before final acceptance:

- a changed key could be labeled available no later than its predecessor;
- the exported collector did not itself confine publication to a temporary
  fixture root;
- the Stage 12A semantic binding did not also enforce its exact source bytes;
- the declared 30-second fixture bound was not executable;
- hard-link aliases and prepared-state inode replacement were not rejected;
- a forged typed Stage 12B scope could retain the reviewed digest; and
- a same-path replacement during lock acquisition could occur after the first
  identity check but before SQLite opened the file.

Each defect received a narrow implementation correction and an adversarial
regression. The final lock-time replacement reproduction raised
`ConflictError`, made zero SQLite opens, and left both the parked original and
replacement stores with zero captures, versions, and current rows.

## 6. Primary implementation checks

Primary integration ran the focused scope, collector, Stage 12A/12B integration,
registry, historical projection, generated-artifact, and Stage 10 compatibility
checks throughout the correction cycle. The final collector-only correction
run passed 10 tests in 3.563 seconds:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  quant_data/market/stage12_incremental.py \
  tests/market/test_stage12_incremental.py

env -u FMP_API_KEY -u BEA_API_KEY -u EIA_API_KEY \
  -u QUANT_MARKET_DB_PATH -u QUANT_MACRO_DB_PATH \
  -u QUANT_COMPANY_DB_PATH -u QUANT_NEWS_DB_PATH \
  -u QUANT_SYSTEM_REGISTRY_PATH PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest -v tests.market.test_stage12_incremental
```

No primary test used a default or retained database, credential, provider,
external network service, or scheduler.

## 7. Independent verification

Verifier: SolUltra

Verification date: 2026-08-15

Findings: none unresolved.

The final independent checks produced:

| Check | Result |
| --- | --- |
| Stage 12A/B focused suite | 25 tests passed in 9.972 seconds |
| Historical registry and sealed Stage 10 compatibility | 42 tests passed in 38.438 seconds |
| Core and generated-tool regressions | 35 tests passed in 9.405 seconds |
| Complete environment-clean dependency-free suite | 576 tests passed in 689.145 seconds |
| Actual two-root Stage 12B CLI | Exit status 0; path-free evidence SHA-256 `8daef487dd9eb42bf4c2711dd5682ffb21751d0c15efc3e71ea8c00e2365ef4e`; default artifacts unchanged |
| Generated-artifact check | Clean |
| Post-closeout Markdown validation | 42 files and 354 local links checked; none missing |
| `git diff --check` | Clean |

The complete suite ran with FMP, BEA, EIA, and all `QUANT_*` credential/path
overrides unset:

```text
env -u FMP_API_KEY -u BEA_API_KEY -u EIA_API_KEY \
  -u QUANT_MARKET_DB_PATH -u QUANT_MACRO_DB_PATH \
  -u QUANT_COMPANY_DB_PATH -u QUANT_NEWS_DB_PATH \
  -u QUANT_SYSTEM_REGISTRY_PATH PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -t . -v
```

Verification used only explicit temporary SQLite fixtures. Existing full-suite
boundary tests used local loopback HTTP only; no external network service was
contacted. No live provider, credential, project-default or retained SQLite
store, promotion, public consumer, or scheduler was accessed.

Unrelated dirty FMP-news work and the pre-existing untracked `=1` entry were
preserved and remain outside this evidence record. Two generated
`.orig`/`.rej` failed-patch artifacts were inspected and removed before the
final clean status check.

## 8. Gate disposition

Stage 12B is **verified with stated limitations** as an offline fixture-only
incremental collector. It does not establish real FMP compatibility, live or
current market coverage, durable request intent, durable terminal-empty
evidence, restart/resume behavior, a real trading calendar, general gap
detection, operational storage, cutover, public exposure, or scheduling.

At this Stage 12B evidence closure, Stage 12C remained closed and required a
separate decision. A later decision authorizes **Stage 12C — contract/offline
implementation in progress** under the
[focused two-session contract](STAGE12C_MARKET_GAP_V1.md). This evidence does
not establish a provider call, target mutation, or Stage 12C verification.
Stages 12D and 12E remain closed. No completed Stage 9, Stage 10, or Stage 11
provider request may be repeated.

## Related contracts

- [Stage 12 Market v1 contract](STAGE12_MARKET_V1.md)
- [Stage 12B incremental collector contract](STAGE12B_INCREMENTAL_MARKET_V1.md)
- [Stage 12C two-session Market v1 gap contract](STAGE12C_MARKET_GAP_V1.md)
- [Stage 12A evidence record](STAGE12_EVIDENCE.md)
- [ADR 0009: Canonical market operational path](../adr/0009-canonical-market-operational-path.md)
- [Scheduling and physical-store locking](SCHEDULING_AND_LOCKING.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [Rebuild test strategy](TEST_STRATEGY.md)
