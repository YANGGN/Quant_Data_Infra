# Stage 12B Incremental Market v1 Collector Contract

Status: **Implemented and independently verified — offline fixture-only**

Decision date: 2026-08-15

Executable scope: dependency-free, offline, fixture-only Stage 12B

## 1. Authority and purpose

The user explicitly authorized Stage 12B on 2026-08-15 after the completed
Stage 12A authority/path gate. This contract authorizes implementation and
offline verification of one new, bounded Market v1 incremental collector. It
does not authorize a live FMP request, a production-like population, a
project-local cutover, a public consumer, or a schedule.

The collector is deliberately new. It must not schedule, wrap, reopen,
re-request, or otherwise reuse the sealed Stage 10 backfill or
historical-extension runners, transports, or receipts. Stage 10 remains
immutable source evidence, not an executable incremental workflow.

Stage 12B is the second phase in the serial lifecycle defined by the
[Stage 12 Market v1 contract](STAGE12_MARKET_V1.md). Its purpose is to prove,
with injected fixtures and explicit temporary stores only, that a small
provider-native daily OHLCV unit can be classified, parsed, prepared, and
published safely through the already-existing market physical model. Passing
this phase does not authorize Stage 12C, 12D, or 12E.

## 2. Fixed identities and authority bindings

| Item | Required value or rule |
| --- | --- |
| Collector identifier | `market.stage12b.fmp_daily_incremental_fixture` |
| Registered handler identifier | `market.stage12b_fmp_daily_incremental_fixture` |
| Provider shape | FMP provider-native daily OHLCV; injected fixture response only |
| Price variant | `fmp_full_eod_v1` |
| Schedule eligibility | `manual_only`; fixture-only by this contract, not a scheduler interface |
| Retry policy | `max_attempts=1`, empty `transient_classes`, and no backoff |
| Resource bounds | `max_bytes=65536`, `max_requests=1`, `max_rows=5`, `max_seconds=30` |
| Current registry for this phase | revision `2.13.0`, schema `1.8.0` |
| Historical projection | Exact `2.13.0` to `2.12.0` projection, including the prior source SHA-256 `80a41e9f124cccb85bbdda665e33b1ef408f538b7e50b499b630b5ce6c761e7e` |
| Canonical current market default | `data/market.sqlite`; it is never opened or created by this phase |
| Stage 12A scope semantic SHA-256 | `8266f431519913879797a14ea0baa781b6c7e0dfe78365a5ff0107e386a0ad29` |
| Stage 12A scope file SHA-256 | `12e9a7380ad22c9595d922c1a53320f3c52e368cb688adf1d97bf4d7c9a18bf6` |
| Stage 12A roster SHA-256 | `a81f62a5fe3710011e1fe6eabfe7fcd483726a23f2dc9b0a7e592b4c78327fbb` |
| Frozen universe | Exactly 629 symbols: 519 equities, 95 reviewed non-Russell ETFs, and 15 reviewed major indexes |
| Storage model | Existing market migration `0010_stage10_market_history.sql` capture/version/current model, initialized only at explicit temporary fixture paths; no migration, table, trigger, or checksum change |

The Stage 12A scope and roster bindings are admission controls, not advisory
metadata. The collector must load and validate the closed scope before
accepting a request. It must bind a request symbol to its exact frozen asset
classification; asset type is not inferred from an FMP response.

`IWM`, `^RUT`, an unknown symbol, a duplicate or reordered roster, a changed
classification, or any other scope-digest mismatch fails closed before an
attempt. The current scope has no fallback to `data/market_data.sqlite`.

The integration owner alone owns the canonical registry and its historical
projection. This document reserves the exact revision boundary above; it does
not allocate a migration, alter a shared registry schema, or change another
accepted profile.

## 3. Bounded request unit

One invocation handles exactly one frozen-roster symbol and one inclusive date
range. The implemented request descriptor is closed to:

```text
provider: fmp
endpoint_path: /stable/historical-price-eod/full
symbol: one exact frozen provider symbol
from: strict ISO calendar date
to: strict ISO calendar date
price_variant: fmp_full_eod_v1
```

The request descriptor contains no credential, host override, URL override,
headers, pagination control, asset-type override, or caller-selected database
path. It is a reviewed description for fixture validation, not permission to
make an HTTP request.

The `from` and `to` dates are inclusive, ordered, and span no more than seven
calendar days. Each request also supplies an explicit reviewed fixture-session
set of one through five strict ISO dates. The set is unique, sorted, contained
in the requested inclusive range, and supplied as part of the fixture; the
collector must not infer a trading calendar, holiday, weekend rule, or missing
session from a date calculation. A complete nonempty fixture response contains
exactly one row for each of those explicitly supplied session dates.

No invocation may bundle symbols, extend a range, silently split a range, or
turn a failed unit into another request. The one-attempt registry policy is
observable: every result is classified after its first fake response, with no
automatic retry, delay, or backoff.

## 4. Fixture transport and FMP response contract

### 4.1 Fixture-only transport

The only permitted transport is an injected fake that returns a bounded,
immutable fixture response. It must receive the closed request descriptor and
must be observable in tests. The implementation must not import or instantiate
a live FMP transport, read `FMP_API_KEY` or any environment variable, inspect a
credential file, resolve DNS, open a socket, start a subprocess, or perform a
network request.

The fixture response records only the reviewed response classification,
status, media type, raw bytes, and an injected finite elapsed-seconds
observation. The request injects the local capture timestamp. Neither
descriptor may contain a secret, authorization header, signed URL,
machine-specific path, or invented provider timestamp. A body above
`max_bytes=65536` or a list above `max_rows=5` fails before publication.
The injected elapsed observation must be finite, nonnegative, and no greater
than `max_seconds=30`. This bounds fixture evidence; it does not authorize a
network timeout or a live wait.

### 4.2 Closed response shape

For a success fixture, status is exactly `200`, the media type normalizes to
`application/json`, and the body is one strict JSON array. A non-list body,
empty body, wrong status, wrong media type, malformed JSON, non-finite JSON
number, or resource-bound violation is a nonpublication outcome.

Every nonempty list element is an object with exactly these fields and no
others:

```text
symbol, date, open, high, low, close, volume, change, changePercent, vwap
```

For each row:

- `symbol` exactly equals the frozen request symbol;
- `date` is a strict ISO calendar date, occurs once, lies in the inclusive
  request range, and is one of the explicit fixture-session dates;
- `open`, `high`, `low`, `close`, `change`, `changePercent`, and `vwap` are
  finite JSON numbers, never booleans, strings, NaN, or infinity;
- OHLC values are nonnegative, `high` is not below any of open, low, or close,
  and `low` is not above any of open, high, or close;
- `volume` is a nonnegative integral JSON number within SQLite's supported
  signed-integer range; and
- duplicate dates, a mismatched symbol, a missing required date, or an
  unreviewed extra date rejects the whole unit.

Rows may arrive in a different provider order. The parser normalizes them into
strict ascending `trade_date` order before semantic comparison. A reordered
but otherwise identical valid response is semantic replay, not a correction.

An empty JSON array is permitted only as an injected fixture classification. It
causes zero persistent change and is not evidence that a real request had no
trading data. Durable request intent and durable terminal-empty evidence are
expressly deferred beyond Stage 12B.

### 4.3 Request-bound semantic identity and raw material

The semantic identity is closed to the normalized request scope,
normalization version, and normalized complete batch. It includes:

```text
request_scope
normalization_version
normalized_complete_batch
```

It deliberately excludes `captured_at` and provider `source_row_order`.
Changing only a local capture timestamp or the incoming order of otherwise
valid rows cannot create a correction. The normalized complete batch is in
strict ascending `trade_date` order and preserves all meaning-bearing closed
OHLCV values.

Raw response bytes are request-bound evidence material. Before any publication,
the implementation must reject identical raw bytes presented for two different
closed request descriptors, even if both descriptors are otherwise in scope.
It must not collapse such a conflict through a shared body digest or classify
it as unchanged. This rejection leaves every fixture store unchanged.

Conversely, byte differences caused only by permitted row ordering cannot
create a new canonical version when the normalized closed response is identical
for the same request descriptor. A successful semantic replay, including a
reordered response, creates zero capture, ingestion-run, fact, version,
current-pointer, marker, or receipt write.

## 5. Time, availability, and version semantics

`trade_date` is the date represented by the daily bar. It is not a close time,
publication time, capture time, or availability timestamp. FMP's list-row
shape in this phase supplies no provider release timestamp or vintage; the
collector must not derive one from a trade date, a local timezone, or a
conventional market-close time.

Each fixture injects an aware local capture timestamp with explicit offset or
`Z`. The implementation may retain it only as local-capture evidence and, where
the existing Market v1 model requires an availability value, must label that
basis as local capture rather than provider publication. Naive timestamps and
fabricated provider timestamps fail closed. Date-only values remain date-only.

For one accepted complete fixture unit, the existing `0010` physical model
must publish atomically:

1. the immutable capture/lineage material required by that existing model;
2. one initial version and current pointer for each new natural key; or
3. one append-only corrected version, a valid supersession link, and a
   forward current-pointer update for each changed natural key.

The natural-key and current/version rules are those already enforced by the
existing `0010` migration. This phase may not create a new Stage 12 relation,
reinterpret a stored value, update a prior immutable version, delete a current
row, generate a tombstone, or infer an unmentioned session.

An exact semantic replay and a reordered semantic replay are total no-writes.
A new valid key is an insertion. A changed valid OHLCV payload for an existing
key is a correction that appends rather than overwrites. A partial, malformed,
empty, conflict, lock, or injected commit-failure outcome makes no canonical or
evidence-layer change visible. No partial response can become a tombstone or a
complete-scope disappearance claim.

For each changed natural key, local capture and availability must be strictly
later than the current version. An equal or backdated changed key is a total
no-write conflict.

## 6. Preparation, locking, and fixture publication

Parsing, closed-scope validation, response validation, canonicalization,
semantic identity construction, and candidate staging finish before a physical
write lock or SQLite write transaction begins. The fixture transport, parser,
and any future retry-classification code must be outside a lock and transaction
boundary.

Only after a complete candidate exists may the collector resolve the explicit
temporary fixture-root and market-store physical identities, acquire the
existing physical-store write lock, revalidate semantic identity against
current fixture state, and begin a short publication transaction.

Prepared work is bound to the fixture-root and market-store device, inode, and
link count. Symlink and hard-link aliases are rejected. Both identities are
revalidated immediately before lock acquisition and immediately after lock
acquisition, before SQLite opens the file. The same lock rules, path identity,
ordering, and failure behavior described in the
[Scheduling and physical-store locking specification](SCHEDULING_AND_LOCKING.md)
apply. This collector has one logical store, `market`; no other store is
declared or touched.

The fixture gate uses two distinct explicit roots. Each root is a proper child
of the system temporary directory, is outside the project, is supplied by the
test or CLI invocation, and has no relation to a default store, retained Stage
10 candidate, or user-selected project-local path. A dry run does not open,
create, initialize, or lock either store. A fixture rehearsal may initialize
the existing model only beneath its explicit temporary root and must prove
that all created objects stay there.

No database lock or transaction may be held during fake-response delivery or
validation. A validation, lock, or commit failure rolls back the active unit;
there is no fallback store, cross-store write, marker, durable request-intent
record, or automatic resumption behavior.

## 7. Explicit exclusions and phase boundaries

Stage 12B excludes all of the following:

- live FMP access, API keys, environment configuration, provider diagnostics,
  network activity, and a real HTTP transport;
- Stage 9, Stage 10, and Stage 11 request, runner, transport, receipt, or
  retained-store use;
- default, candidate, non-production, project-local, or operational database
  access, including `data/market.sqlite` and `data/market_data.sqlite`;
- migration allocation, schema alteration, migration checksum change, or
  persistent state outside the already-existing temporary fixture model;
- durable pre-request intent, terminal-empty evidence, crash/restart/resume
  protocol, or a persistent cursor/checkpoint;
- real trading-calendar integration, general gap detection, historical
  backfill, gap-only population, a newly broadened universe, or new symbols;
- a public tool, dashboard, Atlas view, export, callable user-selected path,
  promotion, backup/cutover, or project-local operational proof; and
- scheduler proposal, installation, modification, removal, or start.

Durable intent and empty evidence, real trading-calendar semantics, and
general gap handling are later design decisions. A separately authorized Stage
12C may consider only new dates, instruments, or coverage; Stage 12D owns an
audited project-local cutover and repeated manual evidence; Stage 12E is only a
later market-only scheduling proposal.

## 8. Required exit evidence

Stage 12B is not complete until the following current executable evidence
exists. Prose, an old test count, or a successful manual code review cannot
substitute for these gates.

### 8.1 Static and contract gate

- The collector and handler identifiers above are the only new Stage 12B
  execution identities, and their registry admission is pinned to current
  revision `2.13.0` and schema `1.8.0`.
- The registry declares `manual_only`, `max_attempts=1`, empty transient
  classes, no backoff, and exact limits of 65,536 bytes, one request, five
  rows, and 30 seconds.
- The exact `2.13.0` to `2.12.0` projection reproduces revision, schema,
  canonical defaults, and the Stage 12A-pinned `2.12.0` source digest without
  exposing a 2.13 collector in historical profiles.
- The Stage 12A scope, file, and roster digests, the exact 629 roster order,
  classifications, excluded symbols, price variant, and no-fallback path rule
  are all validated before request construction.
- The semantic identity includes request scope, normalization version, and the
  normalized complete batch, while excluding local capture time and source row
  order.
- There is no new migration or modification to existing migration resources,
  checksums, shared store/path/lock primitives, public tool inventory, route
  manifest, or Stage 10 runner/transport/receipt code.
- Source-level and runtime checks prove no credential environment access,
  socket/DNS/HTTP/subprocess use, live-transport construction, default-path
  fallback, or retained-candidate access.
- Documentation links resolve and strict JSON fixtures and golden outputs are
  bounded, deterministic, credential-free, and free of absolute paths.

### 8.2 Pure dry-run gate

- A fresh invocation with an explicit project root and two distinct explicit
  absent-or-empty temporary roots emits a deterministic, strict, sanitized
  plan for one reviewed fixture unit.
- The plan contains the collector/handler identifiers, frozen symbol and asset
  type, inclusive range, explicit session count, attempt bound, phase, and
  sanitized lock/store declaration; it contains no credential, raw response,
  absolute path, provider body, or mutable receipt identifier.
- It performs zero fake-transport calls, SQLite opens/creates, migrations,
  locks, transactions, filesystem mutations, scheduler actions, environment
  credential reads, network operations, and Stage 10/11 access.
- The two supplied roots remain unchanged and cannot be aliases, descendants
  of the project, relative paths, or each other.

### 8.3 Offline fixture-rehearsal gate

- Two fresh, explicit, isolated temporary roots initialize the existing
  `0010` fixture model and produce equal path-free logical outcomes for the
  same fixed fixture sequence.
- A complete one-symbol, one-to-five-session valid fixture first publishes
  only market fixture state; all nonmarket stores remain unchanged.
- A second exact replay and a response whose valid rows are reordered each
  produce zero writes of every kind, including capture, ingestion run, version,
  current pointer, receipt, marker, and checkpoint.
- A new valid trade date appends an initial version. A changed valid payload
  for an existing date appends exactly one correction with an immutable
  predecessor and advances the current pointer without changing prior data.
- Injected malformed, partial, empty, duplicate-date, unexpected-symbol,
  unknown-field, out-of-range-date, omitted-session, non-finite, nonintegral
  volume, invalid-OHLC, wrong-status, wrong-media-type, resource-bound, raw
  bytes/conflicting request, lock, and commit-failure fixtures leave the
  relevant fixture store fingerprint unchanged.
- Instrumentation proves fake response delivery, parsing, and candidate
  preparation finish before the physical write lock and write transaction;
  the short publication transaction is atomic and rollback leaves no visible
  partial lineage.
- `max_attempts=1` is observable: every failure is classified once and makes
  no automatic second fake-transport call.

### 8.4 Adversarial and regression gate

- Adversarial requests reject `IWM`, `^RUT`, unknown symbols, classification
  drift, duplicate/reordered scope entries, an altered scope digest, invalid
  date range, more than seven calendar days, zero or more than five reviewed
  sessions, repeated sessions, and sessions outside the range.
- Adversarial time tests reject a naive capture timestamp and demonstrate that
  a date-only trade date cannot become an invented provider timestamp.
- Tests prove that the same raw body cannot be accepted under two different
  request descriptors, while semantically identical rows in a different order
  remain a total no-write for the same descriptor.
- Existing Stage 12A authority evidence, Stage 9 through Stage 11 historical
  registry projections, Stage 10 sealed-receipt guarantees, current
  `data/market.sqlite` default, and generated artifacts remain reproducible.
- The complete dependency-free suite passes with all provider credentials and
  `QUANT_*` database overrides unset. It uses no default or retained-candidate
  SQLite file and no network service.

### 8.5 Independent verification gate

After all implementation writers stop, an independent verifier must derive
checks from this contract, the Stage 12 contract and evidence, ADR 0009, the
data/time contract, scheduling/locking contract, and test strategy. The
verifier must inspect the actual workspace rather than worker summaries and
must report exact safe commands and results.

It must independently run focused static, dry-run, fixture, failure,
replay/reordering, correction, path-isolation, scope-admission, and historical
projection checks, plus the applicable full dependency-free suite. It must
confirm that no live transport, credential, provider, retained candidate,
default path, scheduler, migration, public consumer, or Stage 10 runner was
used. Missing test infrastructure is a finding, not a passing result.

## 9. Stop conditions

Stop the Stage 12B lane and return the decision to the integration owner if:

- an authority binding, roster digest, class count, registry projection, or
  canonical path conflicts with the accepted Stage 12A evidence;
- the proposed implementation requires a migration, a changed applied
  migration checksum, a shared semantic/path/lock primitive change, or a
  public-interface change not explicitly assigned to the integration owner;
- validation would require a live provider, credential, network, retained
  candidate, project-local/default store, or a real calendar source;
- durable intent, a terminal-empty record, gap/cursor semantics, restart
  behavior, or a retry policy becomes necessary to make a fixture pass;
- an attempt would use more than one physical request, hold a lock or
  transaction across response delivery/parsing, publish partial data, or
  weaken total no-write replay behavior; or
- a test needs a caller-selected database path, an unbounded response, an
  unknown FMP field, or an asset classification not pinned by the roster.

Such a condition is not permission to broaden Stage 12B. It requires a new
reviewed decision before implementation continues.

## 10. Nonclaims

This completed offline phase does not claim that Market v1 is complete,
current, survivorship-safe, adjusted, corporate-action-aware, complete for all
U.S. or delisted securities, or available at any historical close. It does not
claim live incremental operation, real FMP compatibility, a durable request or
empty-response audit, gap coverage, successful project-local storage, cutover,
public exposure, scheduling, or operational readiness. The implemented
collector remains fixture-only; no real provider response or project-default
store was exercised.

The only permitted evidence in this phase is deterministic offline fixture
evidence. Any live or project-local result requires the separately authorized
successor gate that owns it.

## Related contracts

- [Stage 12 Market v1 contract](STAGE12_MARKET_V1.md)
- [Stage 12B evidence record](STAGE12B_EVIDENCE.md)
- [Stage 12A evidence record](STAGE12_EVIDENCE.md)
- [ADR 0009: Canonical market operational path](../adr/0009-canonical-market-operational-path.md)
- [Scheduling and physical-store locking](SCHEDULING_AND_LOCKING.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [Rebuild test strategy](TEST_STRATEGY.md)
