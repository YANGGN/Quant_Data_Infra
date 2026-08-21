# Stage 12 Market v1 Operationalization Contract

Status: Stages 12A and 12B implemented and independently verified within their offline boundaries; Stages 12C and 12D complete and independently verified; Stage 12E closed
Decision date: 2026-08-15
Executable scope: immutable completed Stage 12A/12B/12C/12D only; Stage 12E remains closed

## 1. Purpose

Stage 12 changes the rebuild from one-off candidate populations to a serial,
source-by-source operating lifecycle. Each source must pass its own contract,
offline gate, bounded manual proof, baseline or capture-forward population,
repeated incremental rehearsal, and separate scheduling gate before work moves
to the next source or domain.

Stage 12 begins with daily market prices because the retained Stage 10 cohort
already supplies a large, receipt-bound historical baseline. Stage 12 does not
repeat or rewrite any Stage 10 provider request or receipt.

## 2. Accepted path decision

The canonical project-local market operational path is:

`data/market.sqlite`

It is relative to the explicit project root and retains
`QUANT_MARKET_DB_PATH` as the narrowly scoped environment override. The prior
default `data/market_data.sqlite` is superseded for the market role and is not
a fallback, alias, or second operational market store.

The macro, company, and news defaults remain unchanged:

- `data/macro_data.sqlite`;
- `data/company_data.sqlite`; and
- `data/news_data.sqlite`.

Changing the declared default did not by itself validate, copy, rename,
promote, or open an existing SQLite file. The completed Stage 12C receipt now
binds the approved project-local target. Stage 12D is a no-transfer
adoption/freeze proof: it requires fixed-target physical identity, immutable
read-only verification, integrity, and receipt reconciliation, but no online
backup, copy, move, replacement, or promotion.

## 3. Market v1 frozen baseline

Stage 12A records the following as the bounded Market v1 baseline available
for later operationalization:

- provider: FMP;
- interval and values: provider-native daily open, high, low, close, and
  nonnegative volume;
- price variant: `fmp_full_eod_v1`;
- universe: the frozen 629-symbol Stage 10 roster comprising the reviewed
  current S&P 500, Nasdaq-100, and Dow 30 union, 95 reviewed non-Russell ETFs,
  and 15 reviewed major indexes;
- explicit classifications: 519 equities, 95 ETFs, and 15 indexes;
- roster authority: the sorted 629-symbol manifest was derived once from the
  immutable source-only Stage 10 resume artifact, whose raw SHA-256 is
  `1c0a9f941d829170e4085ef133df6343dadf32d975168ce43aa3724749553795`,
  and is bound to Stage 10 scope SHA-256
  `0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd`;
- history: each symbol's earliest provider-returned date through 2026-08-12,
  without inventing a common start date;
- retained base disposition: 621 completed symbols and eight retained
  failures;
- retained extension disposition: 3,970 completed windows, 1,004 successful
  empty windows, and 58 authorized terminal outcomes across nine tickers;
- retained relations: 4,235,893 current rows, 4,236,635 immutable versions,
  and zero missing current-version references; and
- availability: conservative local-capture semantics. A historical trade date
  is not treated as proof that this system knew the bar on that date.

Market v1 does not claim:

- historical constituent membership or survivorship-safe historical universes;
- full U.S. active or delisted-security coverage;
- adjusted prices, split/dividend reconstruction, or total returns;
- a common history start date;
- currency conversion;
- that every terminal Stage 10 outcome contains price history;
- public operational promotion or schedule; or
- public tool, dashboard, Atlas, or export exposure.

These are visible successor requirements, not implied completeness.

## 4. Artifact classification

| Artifact | Stage 12A classification |
| --- | --- |
| Retained Stage 10 external cohort and receipts | Immutable source evidence; read-only and no-repeat |
| `data/market.sqlite` path | Approved canonical market location; current file contents are not accepted by the path decision alone |
| Retained Stage 10 resume JSON | One-time read-only roster authority; runtime gate uses only its pinned digest |
| `data/market_data.sqlite` | Superseded market default; no fallback or compatibility alias |
| Other three operational store defaults | Unchanged and outside Stage 12A |

Stage 12A did not open or inspect current SQLite files. The separately
authorized [Stage 12D no-transfer adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
must bind the actual project-local file to the Stage 12C receipt with direct
physical identity, immutable read-only checks, targeted integrity, and
row/current-pointer reconciliation. It may not reopen the retained source or
perform a copy, transfer, replacement, SQLite online backup, migration, or
registry change.

## 5. Source lifecycle phases

### Stage 12A — authority and coverage freeze

Implemented and independently verified on 2026-08-15. It is dependency-free,
offline, and physical-path-free in its public evidence; approved project-relative
defaults remain explicit. It makes zero database-file open/create, provider,
credential, scheduler, copy, move, or delete action. See the
[Stage 12 evidence record](STAGE12_EVIDENCE.md).

It must validate:

- the exact Market v1 machine-readable scope;
- the canonical market path and unchanged other-store paths;
- the retained Stage 10 counts and explicit non-claims;
- the no-repeat/no-promotion boundary;
- the ordered successor phases below; and
- deterministic equal evidence from two explicit empty work roots without
  creating or changing either root.

### Stage 12B — incremental market collector

Implemented and independently verified — offline fixture-only. The focused
[Stage 12B incremental collector contract](STAGE12B_INCREMENTAL_MARKET_V1.md)
defines the new collector; it must never schedule, reopen, or repeat the sealed
Stage 10 backfill or receipts. The
[Stage 12B evidence record](STAGE12B_EVIDENCE.md) proves registry `2.13.0`,
schema `1.8.0`, and the existing `0010` capture/version/current model only
in explicit temporary fixture stores. It authorizes no live provider, API key,
network, default or retained store, migration, promotion, cutover, public
consumer, or scheduler action.

### Stage 12C — bounded two-session Market v1 gap completion

Status: **Implemented and independently verified — bounded manual no-copy
two-session completion.** The [focused contract](STAGE12C_MARKET_GAP_V1.md)
froze the 629-symbol roster, `AAPL` first, and only `2026-08-13` through
`2026-08-14`. It used the existing `data/market.sqlite` after exact retained
Stage 10 baseline proof, with no second full database copy. The immutable
[Stage 12C evidence](STAGE12C_EVIDENCE.md) records 629 one-attempt chains:
619 published complete, three successful empty, and seven narrowly authorized
HTTP 402 terminal outcomes. Its final receipt is SHA-256
`0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88`;
the final target has 4,237,131 current rows, 4,237,873 immutable versions,
and 5,210 captures with clean invariants. The Stage 12C provider work is
closed to repeat.

### Stage 12D — no-transfer project-local adoption/freeze

**Complete and independently verified.** The accepted
[Stage 12D no-transfer contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md),
[immutable evidence](STAGE12D_EVIDENCE.md), and
[ADR 0010](../adr/0010-stage12d-no-transfer-market-adoption.md) supersede only
the transfer-oriented consequence here. Exactly two fixed-target read-only
proofs produced distinct immutable receipts with one semantic proof, clean
fixed checks, and exact database/WAL/SHM/journal stamp neutrality; a third
proof is rejected. Independent reconciliation did not reopen SQLite or compute
a new full-database hash. No second database, copy, move, replacement,
promotion pointer, backup, migration, registry change, provider/network
access, public exposure, or scheduler change occurred.

### Stage 12E — market-only scheduling proposal

Closed. Stage 12D evidence is complete, but no decision has authorized a real
`market-close` job or its external task definition. Any proposal,
installation, first start, update, or removal remains a separate explicit
action. Other jobs stay disabled.

## 6. Stage 12A exit gate

Stage 12A is complete only when:

- the accepted ADR, architecture, registry specification, canonical registry,
  roadmap, agent policy, and tests agree on `data/market.sqlite`;
- historical Stage 1 through Stage 11 projections and receipts remain
  reproducible rather than being rewritten with the new default;
- the strict Market v1 scope rejects unknown fields, broadened coverage,
  secrets, absolute paths, altered counts, and opened successor phases;
- a two-root offline CLI run emits equal deterministic evidence and leaves both
  roots unchanged;
- the complete dependency-free suite passes; and
- independent verification reports no unresolved finding.

## 7. Stage 12B exit gate

Stage 12B is complete within its offline fixture boundary because its strict
scope and source-byte bindings, deterministic dry run, two-root publication
rehearsal, semantic no-write behavior, append-only correction semantics,
point-in-time ordering, physical-store identity checks, rollback behavior,
historical registry compatibility, full dependency-free suite, and independent
verification all passed. The result is recorded in
[Stage 12B evidence](STAGE12B_EVIDENCE.md).

No live provider, credential, default or retained store, migration, operational
population, cutover, public consumer, or scheduler action occurred.

Passing Stage 12A did not itself authorize later work. Stage 12B remains
complete only within its fixture boundary. Stage 12C is now complete under its
[focused contract](STAGE12C_MARKET_GAP_V1.md) and immutable
[evidence record](STAGE12C_EVIDENCE.md); it did not transfer or promote a
database. Stage 12D is complete and independently verified under its bounded
[no-transfer contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md) and
[evidence record](STAGE12D_EVIDENCE.md). It created only two immutable private
proof receipts and no data transfer or store mutation. Stage 12E remains
closed.
